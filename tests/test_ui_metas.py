"""Aba "Metas" (F7): números do topo, lista, guardar/retirar, diálogo com prévia, simulador e recentes. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication, QMessageBox

from meu_cofre import lancamentos as lanc
from meu_cofre import metas, relatorios
from meu_cofre.ui import tema
from meu_cofre.ui.aba_metas import AbaMetas, texto_do_plano
from meu_cofre.ui.componentes import BarraProgresso, ItemLancamento
from meu_cofre.ui.dialogo_meta import DialogoMeta, mes_ano, reais, texto_atrasos
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _lancar(conn, entrada, centavos, data, cat, descricao="x"):
    lanc.registrar(
        conn, entrada=entrada, valor_centavos=centavos, descricao=descricao, categoria_id=id_cat(conn, cat),
        metodo="pix", conta_id=id_conta(conn, "Inter"), data=data,
    )


def _historico(conn):
    """3 meses completos: salário 5000, essencial 2000, lazer 1000 -> sobra 2000/mês."""
    for m in (7, 8, 9):
        _lancar(conn, True, 500_000, date(2026, m, 5), "Salário")
        _lancar(conn, False, 200_000, date(2026, m, 10), "Moradia")
        _lancar(conn, False, 100_000, date(2026, m, 15), "Lazer")


def _meta(conn, nome="Cadeira", valor=150_000, modo="prazo", prazo=date(2027, 3, 1), mensal=None, reserva=False):
    return metas.criar(
        conn, HOJE, nome=nome, valor_alvo_centavos=valor, modo=modo, prazo=prazo if modo == "prazo" else None,
        mensal_centavos=mensal, reserva=reserva,
    )


def _aba(conn, tamanho=(1400, 900)):
    a = AbaMetas(conn, lambda: HOJE)
    a.resize(*tamanho)
    a.show()
    QApplication.processEvents()
    return a


def _janela(conn, tamanho=(1400, 900)):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(*tamanho)
    j.show()
    QApplication.processEvents()
    return j


def _nomes(aba):
    return [c.nome.texto() for c in aba.cartoes()]


# --- Componentes -----------------------------------------------------------------------------------


def test_barra_de_progresso_limita_de_0_a_1(app):
    b = BarraProgresso(0.4)
    assert b.valor() == 0.4
    b.definir(1.7)
    assert b.valor() == 1.0
    b.definir(-1)
    assert b.valor() == 0.0
    b.resize(200, 10)
    assert not b.grab().isNull()  # pinta sem quebrar, inclusive vazia e cheia
    b.definir(1.0, apagada=True)
    assert not b.grab().isNull()


def test_item_de_meta_tem_marcador_neutro_e_valor_sem_sinal(app):
    item = ItemLancamento(1, "Guardado: Cadeira", "Guardar em meta", "Inter", "hoje", -30_000, meta=True)
    assert item.valor.text() == "R$ 300,00"
    assert item.detalhe.texto() == "Meta · Inter"
    normal = ItemLancamento(2, "Mercado", "Mercado", "Inter", "hoje", -30_000)
    assert normal.valor.text() == "−R$ 300,00"


# --- A aba ------------------------------------------------------------------------------------------------


def test_aba_vazia_mostra_estado_vazio_e_zeros(app, conn):
    aba = _aba(conn)
    assert not aba._vazio.isHidden()
    assert aba.cartoes() == []
    assert aba._guardado.valor.text() == "R$ 0,00"
    assert aba._sobra_media.apoio.text() == "aparece depois do primeiro mês completo"
    assert aba._cartao_reserva.isHidden()  # sem gastos essenciais, não há o que sugerir
    assert aba.botao_encerradas.isHidden()


def test_numeros_do_topo(app, conn):
    _historico(conn)
    a = _meta(conn, "Cadeira", valor=150_000)  # 250/mês
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)  # 1000/mês
    metas.guardar(conn, a, 30_000, id_conta(conn, "Inter"), HOJE)
    aba = _aba(conn)
    assert aba._guardado.valor.text() == "R$ 300,00"
    assert aba._sobra_media.valor.text() == "R$ 2.000,00"
    assert aba._sobra_media.apoio.text() == "média dos últimos 3 meses"
    assert aba._comprometido.valor.text() == "R$ 1.200,00"  # 200 (1200 restantes em 6 meses) + 1000
    assert "2 metas ativas" in aba._comprometido.apoio.text()
    assert aba._livre.valor.text() == "R$ 800,00"


def test_sem_mes_completo_a_sobra_aparece_depois_do_primeiro(app, conn):
    _lancar(conn, True, 300_000, date(2026, 10, 5), "Salário")
    aba = _aba(conn)
    assert aba._sobra_media.apoio.text() == "aparece depois do primeiro mês completo"


def test_lista_em_ordem_de_prioridade_com_barra_percentual_e_plano(app, conn):
    _historico(conn)
    a = _meta(conn, "Cadeira", valor=150_000)
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)
    metas.guardar(conn, a, 60_000, id_conta(conn, "Inter"), HOJE)
    aba = _aba(conn)
    assert _nomes(aba) == ["Cadeira", "Viagem"]
    c = aba.cartao_da_meta(a)
    assert c.barra.valor() == pytest.approx(0.4) and c.percentual.text() == "40%"
    assert c.guardado.text() == "R$ 600,00 de R$ 1.500,00"
    assert c.plano.texto() == "R$ 150/mês até mar/2027"  # (1500 - 600) / 6 meses
    assert c.pilula.text().endswith("cabe na sobra") and c.pilula.property("variante") == "bom"
    assert not c.botao_sobe.isEnabled() and c.botao_desce.isEnabled()
    assert aba.cartoes()[1].botao_sobe.isEnabled() and not aba.cartoes()[1].botao_desce.isEnabled()
    assert c.pilula_reserva.isHidden()


def test_pilulas_de_viabilidade_parcial_e_nao_cabe(app, conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Reserva de emergência", valor=1_200_000, modo="mensal", mensal=120_000, reserva=True)
    v = _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=150_000)
    c = _meta(conn, "Cadeira", valor=100_000, modo="mensal", mensal=10_000)
    aba = _aba(conn)
    reserva, viagem, cadeira = aba.cartoes()
    assert not reserva.pilula_reserva.isHidden()
    assert viagem.pilula.text() == "! atrasa até mai/2027" and viagem.pilula.property("variante") == "atencao"
    assert "Só sobram R$ 800" in viagem.detalhe.text() and not viagem.detalhe.isHidden()
    assert cadeira.pilula.text().endswith("não cabe agora") and cadeira.pilula.property("variante") == "ruim"
    assert reserva.detalhe.isHidden()


def test_meta_cumprida_destaca_concluir(app, conn):
    m = _meta(conn, "Cadeira", valor=50_000)
    metas.guardar(conn, m, 50_000, id_conta(conn, "Inter"), HOJE)
    aba = _aba(conn)
    c = aba.cartao_da_meta(m)
    assert c.pilula.text().endswith("meta cumprida") and c.percentual.text() == "100%"
    assert c.botao_concluir.objectName() == "BotaoPrimario" and c.botao_guardar.objectName() == "BotaoSecundario"
    assert "já dá pra concluir" in c.plano.texto()


def test_sugestao_de_reserva_aparece_ate_criar_a_reserva(app, conn):
    _historico(conn)
    aba = _aba(conn)
    assert not aba._cartao_reserva.isHidden()
    assert aba.reserva_texto.text() == "Reserva de emergência: R$ 12.000,00"
    assert "6 meses dos seus gastos essenciais (R$ 2.000/mês)" in aba.reserva_apoio.text()
    aba.botao_reserva.click()
    d = aba.dialogo
    assert d.nome.text() == "Reserva de emergência" and d.valor.text() == "12000,00" and d.reserva.isChecked()
    assert d.salvar()
    QApplication.processEvents()
    assert aba._cartao_reserva.isHidden()
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["Reserva de emergência"]
    assert metas.listar(conn, HOJE)[0].reserva


def test_guardar_pelo_formulario_da_meta(app, conn):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    m = _meta(conn, "Cadeira")
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    c = aba.cartao_da_meta(m)
    assert c.form.isHidden() and not c.botao_retirar.isEnabled()
    c.botao_guardar.click()
    QApplication.processEvents()
    assert not c.form.isHidden() and c.tipo_form == "guardar"
    assert c.form.conta.currentText() == "Inter"
    c.form.valor.setText("300")
    assert c.form.confirmar()
    assert metas.guardado(conn, m) == 30_000
    assert relatorios.em_conta(conn, HOJE) == 70_000
    assert avisos == ["Guardado R$ 300,00 em Cadeira"]
    nova = aba.cartao_da_meta(m)
    assert nova.guardado.text() == "R$ 300,00 de R$ 1.500,00" and nova.form.isHidden() and nova.botao_retirar.isEnabled()
    assert aba._guardado.valor.text() == "R$ 300,00"


def test_formulario_sugere_o_mensal_e_mostra_erros_inline(app, conn):
    m = _meta(conn, "Cadeira", valor=150_000)  # sem sobra: o formulário sugere o que a meta pede por mês
    aba = _aba(conn)
    c = aba.cartao_da_meta(m)
    c.botao_guardar.click()
    assert c.form.valor.text() == "250,00"
    for texto, trecho in (("", "maior que zero"), ("abc", "inválido"), ("0", "maior que zero")):
        c.form.valor.setText(texto)
        assert not c.form.confirmar()
        assert trecho in c.form.erro.text()
    c.form.valor.setText("10")
    assert c.form.erro.text() == "" and c.form.valor.property("erro") is False


def test_retirar_nao_passa_do_guardado_e_o_erro_fica_no_formulario(app, conn):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    m = _meta(conn)
    metas.guardar(conn, m, 20_000, id_conta(conn, "Inter"), HOJE)
    aba = _aba(conn)
    c = aba.cartao_da_meta(m)
    c.botao_retirar.click()
    assert c.tipo_form == "retirar" and c.form.valor.text() == ""
    c.form.valor.setText("250")
    assert c.form.confirmar()
    assert "R$ 200,00 guardados" in c.form.erro.text()  # o formulário continua aberto, com o motivo
    assert not c.form.isHidden() and metas.guardado(conn, m) == 20_000
    c.form.valor.setText("50")
    assert c.form.confirmar()
    assert metas.guardado(conn, m) == 15_000
    assert relatorios.em_conta(conn, HOJE) == 100_000 - 15_000


def test_so_um_formulario_aberto_por_vez(app, conn):
    a, b = _meta(conn, "A"), _meta(conn, "B")
    aba = _aba(conn)
    aba.abrir_form(a, "guardar")
    aba.abrir_form(b, "guardar")
    assert aba.cartao_da_meta(a).form.isHidden() and not aba.cartao_da_meta(b).form.isHidden()


def test_subir_e_descer_prioridade(app, conn):
    _meta(conn, "A")
    b = _meta(conn, "B")
    aba = _aba(conn)
    aba.cartao_da_meta(b).botao_sobe.click()
    assert _nomes(aba) == ["B", "A"]
    aba.cartoes()[0].botao_desce.click()
    assert _nomes(aba) == ["A", "B"]


def test_concluir_arquivar_e_reativar_com_encerradas_recolhidas(app, conn):
    a, b, c = _meta(conn, "A"), _meta(conn, "B"), _meta(conn, "C")
    metas.guardar(conn, a, 10_000, id_conta(conn, "Inter"), HOJE)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.cartao_da_meta(a).botao_concluir.click()
    assert "Ainda há R$ 100,00 guardados" in avisos[-1]
    aba.cartao_da_meta(b).botao_arquivar.click()
    assert _nomes(aba) == ["C"]  # recolhidas
    assert aba.botao_encerradas.text() == "Mostrar concluídas e arquivadas (2)"
    aba.botao_encerradas.click()
    assert _nomes(aba) == ["C", "A", "B"]
    concluida = aba.cartao_da_meta(a)
    assert concluida.pilula.text().endswith("concluída") and concluida.botao_retirar.isEnabled()
    assert not concluida.botao_reativar.isHidden() and concluida.botao_guardar.isHidden()
    assert aba.cartao_da_meta(b).botao_retirar.isHidden()  # nada guardado: sem Retirar
    concluida.botao_reativar.click()
    assert _nomes(aba) == ["C", "A", "B"] and aba.cartao_da_meta(a).botao_guardar.isHidden() is False
    aba.botao_encerradas.click()
    assert aba.botao_encerradas.text() == "Mostrar concluídas e arquivadas (1)"


def test_texto_do_plano(conn):
    prazo = metas.obter(conn, _meta(conn, "A", prazo=date(2027, 3, 1)), HOJE)
    assert texto_do_plano(prazo, HOJE) == "R$ 250/mês até mar/2027"
    mensal = metas.obter(conn, _meta(conn, "B", valor=600_000, modo="mensal", mensal=100_000), HOJE)
    assert texto_do_plano(mensal, HOJE) == "R$ 1.000/mês · pronta em mar/2027"
    vencida = metas.obter(conn, mensal.id, HOJE)
    vencida.modo, vencida.prazo = "prazo", date(2026, 9, 1)
    assert texto_do_plano(vencida, HOJE) == "prazo venceu em set/2026 · faltam R$ 6.000"


# --- Diálogo ----------------------------------------------------------------------------------------------


def test_dialogo_nova_meta_por_prazo_mostra_o_mensal_e_se_cabe(app, conn):
    _historico(conn)
    d = DialogoMeta(conn, hoje=HOJE)
    d.nome.setText("Cadeira")
    d.valor.setText("1500")
    d.mes.setCurrentIndex(d.mes.findData(3))
    d.ano.setCurrentIndex(d.ano.findData(2027))
    assert d.modo_atual == "prazo" and not d._bloco_prazo.isHidden() and d._bloco_mensal.isHidden()
    assert d.previa_destaque.text() == "Guardar R$ 250 por mês"
    assert d.previa_detalhe.text() == "até mar/2027 · 6 meses"
    assert d.previa_pilula.text().endswith("cabe na sobra")
    assert "R$ 2.000" in d.previa_explicacao.text()
    assert d.previa_atrasos.isHidden()  # não há outras metas
    assert d.salvar()
    meta = metas.listar(conn, HOJE)[0]
    assert (meta.nome, meta.valor_alvo_centavos, meta.modo, meta.prazo) == ("Cadeira", 150_000, "prazo", date(2027, 3, 1))


def test_dialogo_por_mensal_mostra_a_data(app, conn):
    _historico(conn)
    d = DialogoMeta(conn, hoje=HOJE)
    d.nome.setText("Viagem")
    d.valor.setText("4800")
    d.modo.clicar("Mensal")
    assert d.modo_atual == "mensal" and d._bloco_prazo.isHidden() and not d._bloco_mensal.isHidden()
    assert "por mês pra ver" in d.previa_detalhe.text()  # falta dizer o mensal: só a dica
    d.mensal.setText("800")
    assert d.previa_destaque.text() == "Fica pronta em mar/2027"
    assert d.previa_detalhe.text() == "guardando R$ 800 por mês · 6 meses"
    assert d.salvar()
    meta = metas.listar(conn, HOJE)[0]
    assert (meta.modo, meta.mensal_centavos, meta.prazo) == ("mensal", 80_000, None)


def test_dialogo_previa_diz_quais_metas_atrasam(app, conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)  # 1000
    _meta(conn, "Cadeira", valor=300_000, modo="mensal", mensal=60_000)  # 600
    d = DialogoMeta(conn, hoje=HOJE, nome="Reserva de emergência", valor_centavos=1_000_000, mensal_centavos=50_000, reserva=True)
    assert d.modo_atual == "mensal"
    assert d.previa_atrasos.text() == "Isso atrasa: Cadeira (+1 mês)"
    d.reserva.setChecked(False)  # sem passar na frente, ninguém atrasa
    assert d.previa_atrasos.text() == "Não atrasa nenhuma outra meta."
    d.mensal.setText("900")
    assert d.previa_pilula.property("variante") == "atencao"  # só sobram 400
    assert "Só sobram R$ 400" in d.previa_explicacao.text()


def test_dialogo_previa_sem_dados_mostra_dica_e_nao_quebra(app, conn):
    d = DialogoMeta(conn, hoje=HOJE)
    assert "Digite o valor" in d.previa_detalhe.text() and d.previa_pilula.isHidden()
    d.valor.setText("abc")
    assert "Digite o valor" in d.previa_detalhe.text()
    d.valor.setText("1000")
    assert d.previa_destaque.text() == "Guardar R$ 142,86 por mês"  # prazo padrão: abr/2027 (7 meses); a conta sai mesmo sem histórico
    assert d.previa_pilula.text().endswith("não cabe agora")
    assert "não é positiva" in d.previa_explicacao.text()


def test_dialogo_erros_inline_sem_gravar(app, conn):
    d = DialogoMeta(conn, hoje=HOJE)
    assert not d.salvar()
    assert "nome" in d.erro_nome.text() and "valor" in d.erro_valor.text().lower()
    d.nome.setText("X")
    d.valor.setText("10")
    d.modo.clicar("Mensal")
    assert not d.salvar() and "por mês" in d.erro_prazo.text()
    d.mensal.setText("zzz")
    assert not d.salvar() and "inválido" in d.erro_prazo.text()
    d.modo.clicar("Prazo")
    d.ano.setCurrentIndex(d.ano.findData(2026))
    d.mes.setCurrentIndex(d.mes.findData(9))
    assert not d.salvar() and "já passou" in d.erro_prazo.text()
    d.mes.setCurrentIndex(d.mes.findData(10))
    assert d.erro_prazo.text() == ""
    assert d.salvar()
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["X"]


def test_dialogo_edita_meta_existente_com_guardado_na_previa(app, conn):
    _historico(conn)
    m = _meta(conn, "Cadeira", valor=150_000)
    metas.guardar(conn, m, 60_000, id_conta(conn, "Inter"), HOJE)
    d = DialogoMeta(conn, m, hoje=HOJE)
    assert d.windowTitle() == "Editar meta" and d.botao_salvar.text() == "Salvar alterações"
    assert (d.nome.text(), d.valor.text()) == ("Cadeira", "1500,00")
    assert (d.mes.currentData(), d.ano.currentData()) == (3, 2027)
    assert "Já guardado: R$ 600" in d.previa_nota.text()
    d.nome.setText("Cadeira gamer")
    d.valor.setText("3000")
    assert d.previa_destaque.text() == "Guardar R$ 400 por mês"  # (3000 − 600) / 6
    assert d.salvar()
    assert metas.obter(conn, m).nome == "Cadeira gamer" and metas.guardado(conn, m) == 60_000


def test_dialogo_esconde_reserva_se_ja_existe_outra(app, conn):
    outra = _meta(conn, "Reserva", valor=1_000_000, reserva=True)
    assert DialogoMeta(conn, hoje=HOJE).reserva.isHidden()
    assert not DialogoMeta(conn, outra, hoje=HOJE).reserva.isHidden()


def test_dialogo_de_meta_inexistente_levanta_valueerror(app, conn):
    with pytest.raises(ValueError):
        DialogoMeta(conn, 999, hoje=HOJE)


def test_textos_auxiliares():
    assert mes_ano(date(2027, 3, 1)) == "mar/2027"
    assert reais(15_000) == "R$ 150" and reais(15_034) == "R$ 150,34"
    assert texto_atrasos([metas.Atraso(1, "Viagem", 2), metas.Atraso(2, "Cadeira", None), metas.Atraso(3, "Tela", 1)]) == (
        "Viagem (+2 meses), Cadeira (deixa de caber), Tela (+1 mês)")


def test_aba_abre_dialogos_e_recarrega_ao_salvar(app, conn):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.botao_nova.click()
    assert aba.dialogo is not None and aba.dialogo.meta_id is None
    aba.dialogo.nome.setText("Cadeira")
    aba.dialogo.valor.setText("1500")
    assert aba.dialogo.salvar()
    QApplication.processEvents()
    assert avisos == ["Meta criada: Cadeira"] and _nomes(aba) == ["Cadeira"] and aba._vazio.isHidden()
    meta_id = aba.cartoes()[0].id_meta
    aba.cartoes()[0].botao_editar.click()
    assert aba.dialogo.meta_id == meta_id and aba.dialogo.nome.text() == "Cadeira"
    aba.dialogo.valor.setText("2000")
    assert aba.dialogo.salvar()
    assert aba.cartoes()[0].guardado.text() == "R$ 0,00 de R$ 2.000,00"


# --- Simulador --------------------------------------------------------------------------------------------


def test_simulador_sem_valor_mostra_a_dica(app, conn):
    s = _aba(conn).simulador
    assert not s.dica.isHidden() and s.blocos.isHidden()
    s.valor.setText("abc")
    assert "números" in s.erro.text() and not s.erro.isHidden() and s.blocos.isHidden()
    s.valor.setText("")
    assert s.erro.isHidden() and not s.dica.isHidden()


def test_simulador_compara_a_vista_e_parcelado(app, conn):
    _historico(conn)  # sobra 2000, Livre 6000
    _meta(conn, "Viagem", valor=1_200_000, modo="mensal", mensal=100_000)
    s = _aba(conn).simulador
    s.valor.setText("1500")
    s.parcelas.setCurrentIndex(s.parcelas.findData(10))
    assert s.dica.isHidden() and not s.blocos.isHidden()
    assert s.a_vista.destaque.text() == "Cabe no Livre agora"
    assert s.parcelado.destaque.text() == "10x de R$ 150,00"
    assert "até jul/2027 · total R$ 1.500,00" in s.parcelado.linhas.itemAt(0).widget().text()
    assert "R$ 2.000 → R$ 1.850" in s.parcelado.linhas.itemAt(1).widget().text()
    assert s.parcelado.pilula.text().endswith("não atrasa nenhuma meta")


def test_simulador_a_vista_guardando_e_parcelado_que_atrasa(app, conn):
    _historico(conn)
    _meta(conn, "Viagem", valor=1_200_000, modo="mensal", mensal=100_000)  # livre 1000/mês
    s = _aba(conn).simulador
    s.valor.setText("9000")
    s.parcelas.setCurrentIndex(s.parcelas.findData(6))
    s.acrescimo.setText("600")
    assert s.a_vista.destaque.text() == "≈ 9 meses guardando"
    assert "pronto em jun/2027" in s.a_vista.linhas.itemAt(0).widget().text()
    assert s.parcelado.destaque.text() == "6x de R$ 1.600,00"  # (9000 + 600) / 6
    assert s.parcelado.pilula.property("variante") == "atencao"
    assert any("Atrasa: Viagem (+" in s.parcelado.linhas.itemAt(i).widget().text() for i in range(s.parcelado.linhas.count()))
    assert "+R$ 600" in s.parcelado.linhas.itemAt(0).widget().text()


def test_simulador_sem_sobra_nao_cabe_e_criar_como_meta_abre_o_dialogo(app, conn):
    aba = _aba(conn)
    s = aba.simulador
    s.valor.setText("2000")
    assert s.a_vista.destaque.text() == "Sem sobra livre pra juntar"
    assert s.a_vista.pilula.property("variante") == "ruim" and not s.nota.isHidden()  # sem dados: estimativa
    s.botao_criar.click()
    d = aba.dialogo
    assert d is not None and d.valor.text() == "2000,00" and d.modo_atual == "prazo"


def test_criar_como_meta_do_simulador_sugere_o_mensal_livre(app, conn):
    _historico(conn)
    aba = _aba(conn)
    s = aba.simulador
    s.valor.setText("9000")
    s.botao_criar.click()
    d = aba.dialogo
    assert d.valor.text() == "9000,00" and d.modo_atual == "mensal" and d.mensal.text() == "2000,00"


# --- Janela principal -------------------------------------------------------------------------------------


def test_janela_tem_a_quarta_aba_metas(app, conn):
    j = _janela(conn)
    assert j.abas._botoes.keys() >= {"Início", "Assinaturas/fixos", "Cartões", "Metas"}
    j.abas.clicar("Metas")
    assert j.aba_atual == "metas" and j._paginas.currentWidget() is j.aba_metas
    j.mostrar_aba("inicio")
    assert j.aba_atual == "inicio"


def test_guardar_na_aba_atualiza_em_conta_e_recentes_na_janela(app, conn):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    m = _meta(conn, "Cadeira")
    j = _janela(conn)
    assert j._em_conta.valor.text() == "R$ 1.000,00"
    j.aba_metas.abrir_form(m, "guardar")
    c = j.aba_metas.cartao_da_meta(m)
    c.form.valor.setText("300")
    c.form.confirmar()
    assert j._em_conta.valor.text() == "R$ 700,00"
    assert j._livre.valor.text() == "R$ 700,00"  # o Livre não conta com o guardado
    assert j._gastei.valor.text() == "R$ 0,00"
    itens = j.findChildren(ItemLancamento)
    guardado = next(i for i in itens if i.descricao.texto() == "Guardado: Cadeira")
    assert guardado.valor.text() == "R$ 300,00" and guardado.detalhe.texto() == "Meta · Inter"
    assert [i.descricao.texto() for i in itens].count("Guardado: Cadeira") == 1  # só o lado da conta
    assert "Guardado R$ 300,00 em Cadeira" in j.statusBar().currentMessage()


def test_clicar_numa_transferencia_de_meta_leva_pra_aba_metas(app, conn, monkeypatch):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    m = _meta(conn)
    metas.guardar(conn, m, 10_000, id_conta(conn, "Inter"), HOJE)
    j = _janela(conn)
    abertos = []
    monkeypatch.setattr(j, "_abrir_dialogo", lambda *a: abertos.append(a))
    item = next(i for i in j.findChildren(ItemLancamento) if i.descricao.texto() == "Guardado: Cadeira")
    item.clicado.emit(item.id_lancamento)
    assert j.aba_atual == "metas" and abertos == []
    salario = next(i for i in j.findChildren(ItemLancamento) if i.descricao.texto() == "x")  # lançamento comum
    j.mostrar_aba("inicio")
    salario.clicado.emit(salario.id_lancamento)
    assert len(abertos) == 1 and j.aba_atual == "inicio"


def test_graficos_do_inicio_nao_mudam_ao_guardar(app, conn):
    _historico(conn)
    _lancar(conn, False, 20_000, date(2026, 10, 3), "Mercado")
    m = _meta(conn)
    antes = (relatorios.gastei_no_mes(conn, 2026, 10, ate=HOJE), relatorios.gastos_top_categorias(conn, 2026, 10).to_dict("records"))
    metas.guardar(conn, m, 50_000, id_conta(conn, "Inter"), date(2026, 10, 10))
    j = _janela(conn)
    assert (relatorios.gastei_no_mes(conn, 2026, 10, ate=HOJE), relatorios.gastos_top_categorias(conn, 2026, 10).to_dict("records")) == antes
    assert j._gastei.valor.text() == "R$ 200,00"


def test_aba_cabe_em_940_sem_corte_horizontal(app, conn):
    _historico(conn)
    _meta(conn, "Reserva de emergência", valor=1_200_000, modo="mensal", mensal=120_000, reserva=True)
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=150_000)
    aba = _aba(conn, (940, 1000))
    assert not aba.horizontalScrollBar().isVisible()
    for c in aba.cartoes():
        assert c.width() <= aba.viewport().width()
