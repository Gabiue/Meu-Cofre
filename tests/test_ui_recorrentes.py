"""Assinaturas/fixos na interface (F5): aba, diálogo de edição, "Repetir todo mês" e abas da janela. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from meu_cofre import lancamentos as lanc
from meu_cofre import recorrentes as rec
from meu_cofre.ui import tema
from meu_cofre.ui.aba_recorrentes import AbaRecorrentes, texto_situacao
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.dialogo_recorrente import DialogoRecorrente
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _criar(conn, descricao="Streaming", valor=-3990, conta="Cartão de crédito", metodo="cartao", dia=25,
           inicio=date(2026, 9, 1), assinatura=True, cat="Assinaturas"):
    return rec.criar(
        conn, descricao=descricao, valor_centavos=valor, conta_id=id_conta(conn, conta),
        categoria_id=id_cat(conn, cat), metodo=metodo, dia_do_mes=dia, inicio=inicio, assinatura=assinatura,
    )


def _janela(conn, tamanho=(1400, 900)):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(*tamanho)
    j.show()
    QApplication.processEvents()
    return j


def _aba(conn, tamanho=(1400, 900)):
    a = AbaRecorrentes(conn, lambda: HOJE)
    a.resize(*tamanho)
    a.show()
    QApplication.processEvents()
    return a


def _nomes(aba):
    return [l.nome.texto() for l in aba.linhas()]


@pytest.fixture
def varias(conn):
    _criar(conn, "Vídeo", dia=7)
    _criar(conn, "Música", valor=-2190, dia=28)
    _criar(conn, "Aluguel", valor=-150000, conta="Inter", metodo="boleto", dia=25, assinatura=False, cat="Moradia")
    _criar(conn, "Salário", valor=520000, conta="Inter", metodo="pix", dia=5, assinatura=False, cat="Salário")
    rec.gerar_pendentes(conn, HOJE)


# --- Aba ----------------------------------------------------------------------------------------


def test_aba_vazia_mostra_o_estado_vazio_com_dica(app, conn):
    aba = _aba(conn)
    assert aba._vazio.isVisibleTo(aba)
    assert aba._vazio.titulo.text() == "Nenhuma assinatura ou fixo ainda"
    assert "Repetir todo mês" in aba._vazio.dica.text()
    assert aba.linhas() == [] and aba._saidas.valor.text() == "R$ 0,00"
    assert not aba.botao_encerradas.isVisibleTo(aba)


def test_aba_numeros_e_grupos(app, conn, varias):
    aba = _aba(conn)
    assert aba._saidas.valor.text() == "R$ 1.561,80"  # 150000 + 3990 + 2190
    assert aba._ano.valor.text() == "R$ 18.741,60"
    assert aba._ano.valor.property("destaque") is True
    assert aba._entradas.valor.text() == "R$ 5.200,00"
    assert "1 cobrada · R$ 39,90" in aba._este_mes.apoio.text()  # só o vídeo (dia 7) já foi cobrado
    assert "2 a cobrar · R$ 1.521,90" in aba._este_mes.apoio.text()
    # cartão antes da conta corrente; dentro do grupo, por nome
    assert _nomes(aba) == ["Música", "Vídeo", "Aluguel", "Salário"]
    linhas = {l.nome.texto(): l for l in aba.linhas()}
    assert linhas["Vídeo"].tipo.text() == "assinatura" and linhas["Aluguel"].tipo.text() == "fixo"
    assert linhas["Vídeo"].valor.text() == "−R$ 39,90" and linhas["Salário"].valor.text() == "+R$ 5.200,00"
    assert linhas["Vídeo"].situacao.text() == "✓ cobrada"
    assert linhas["Música"].situacao.text() == "cobra em 8 dias"
    assert linhas["Vídeo"].quando.texto() == "todo dia 7 · Assinaturas"
    assert linhas["Aluguel"].quando.texto() == "todo dia 25 · Boleto · Moradia"
    assert linhas["Vídeo"].historico.texto() == "desde set/2026 · R$ 80 até hoje"  # 2 cobranças de 39,90


def test_aba_filtros(app, conn, varias):
    aba = _aba(conn)
    aba.filtro.clicar("Assinaturas")
    assert _nomes(aba) == ["Música", "Vídeo"] and aba._entradas.valor.text() == "R$ 0,00"
    aba.filtro.clicar("Fixos")
    assert _nomes(aba) == ["Aluguel", "Salário"] and aba._saidas.valor.text() == "R$ 1.500,00"
    aba.filtro.clicar("Ambos")
    assert len(aba.linhas()) == 4


def test_filtro_sem_resultado_mas_com_dados_diz_qual_falta(app, conn):
    _criar(conn, assinatura=True)
    aba = _aba(conn)
    aba.filtro.clicar("Fixos")
    assert aba._vazio.isVisibleTo(aba) and aba._vazio.titulo.text() == "Nenhum gasto fixo por aqui"


def test_situacoes():
    assert texto_situacao("cobrada", None) == ("cobrada", "feito")
    assert texto_situacao("prevista", 3) == ("cobra em 3 dias", "neutra")
    assert texto_situacao("prevista", 1) == ("cobra amanhã", "neutra")
    assert texto_situacao("pausada", None) == ("pausada", "atencao")
    assert texto_situacao("encerrada", None)[0] == "encerrada"


def test_pausar_e_retomar_pela_aba(app, conn, varias):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    musica = next(l for l in aba.linhas() if l.nome.texto() == "Música")
    assert musica.botao_pausa.text() == "Pausar"
    musica.botao_pausa.click()
    assert avisos == ["Pausado: Música"]
    musica = next(l for l in aba.linhas() if l.nome.texto() == "Música")
    assert musica.situacao.text() == "! pausada" and musica.botao_pausa.text() == "Retomar"
    assert aba._saidas.valor.text() == "R$ 1.539,90"  # a pausada sai da conta
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'Música' AND status = 'previsto'").fetchone()[0] == 0

    musica.botao_pausa.click()
    assert avisos[-1] == "Retomado: Música"
    musica = next(l for l in aba.linhas() if l.nome.texto() == "Música")
    assert musica.situacao.text() == "cobra em 8 dias"  # o previsto do mês voltou


def test_encerrar_pede_confirmacao_na_propria_linha(app, conn, varias):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    video = next(l for l in aba.linhas() if l.nome.texto() == "Vídeo")
    video.botao_encerrar.click()
    assert video.confirmando and avisos == []
    video.botao_nao.click()
    assert not video.confirmando and rec.obter(conn, 1)["ativa"] == 1
    video.botao_encerrar.click()
    video.botao_sim.click()
    assert rec.obter(conn, 1)["ativa"] == 0 and avisos == ["Encerrado: Vídeo. O histórico fica."]
    assert "Vídeo" not in _nomes(aba)
    assert aba.botao_encerradas.isVisibleTo(aba) and aba.botao_encerradas.text() == "Mostrar encerradas (1)"


def test_mostrar_encerradas_recolhido_por_padrao(app, conn, varias):
    rec.encerrar(conn, 1, HOJE)
    aba = _aba(conn)
    assert "Vídeo" not in _nomes(aba)
    aba.botao_encerradas.click()
    assert "Vídeo" in _nomes(aba) and aba.botao_encerradas.text() == "Esconder encerradas"
    video = next(l for l in aba.linhas() if l.nome.texto() == "Vídeo")
    assert video.situacao.text() == "encerrada" and not video._acoes.isVisibleTo(aba)  # sem ações
    assert aba._saidas.valor.text() == "R$ 1.521,90"  # encerrada não conta nos números
    aba.botao_encerradas.click()
    assert "Vídeo" not in _nomes(aba)


def test_so_encerradas_nao_mostra_vazio_quando_expandidas(app, conn):
    rid = _criar(conn)
    rec.encerrar(conn, rid, HOJE)
    aba = _aba(conn)
    assert aba._vazio.isVisibleTo(aba)
    aba.mostrar_encerradas(True)
    assert not aba._vazio.isVisibleTo(aba) and _nomes(aba) == ["Streaming"]


def test_aba_sem_corte_em_940px(app, conn, varias):
    aba = _aba(conn, (940, 1000))
    assert aba._colunas_numeros == 2
    assert not aba.horizontalScrollBar().isVisible()
    for l in aba.linhas():
        assert l.width() <= aba.viewport().width()
        assert l.situacao.isVisible() and l.valor.isVisible()
    aba.resize(1400, 900)
    QApplication.processEvents()
    assert aba._colunas_numeros == 4


def test_linha_estreita_poe_as_acoes_embaixo(app, conn, varias):
    aba = _aba(conn, (640, 900))
    linha = aba.linhas()[0]
    assert linha._acoes.y() > linha.nome.y()  # embaixo do texto
    assert linha._acoes.x() + linha._acoes.width() <= linha.width()


def test_editar_pela_aba_abre_o_dialogo_e_atualiza(app, conn, varias):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    linha = next(l for l in aba.linhas() if l.nome.texto() == "Música")
    linha.botao_editar.click()
    d = aba.dialogo
    assert isinstance(d, DialogoRecorrente) and d.descricao.text() == "Música"
    d.valor.setText("29,90")
    assert d.salvar()
    assert avisos == ["Salvo: Música (R$ 29,90), todo dia 28"]
    assert next(l for l in aba.linhas() if l.nome.texto() == "Música").valor.text() == "−R$ 29,90"


# --- Diálogo de edição ---------------------------------------------------------------------------


def _editar(conn, rid):
    d = DialogoRecorrente(conn, rid, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_dialogo_recorrente_preenche_os_campos(app, conn):
    rid = _criar(conn, "Vídeo", dia=7)
    d = _editar(conn, rid)
    assert d.windowTitle() == "Editar assinatura ou fixo"
    assert (d.tipo.atual(), d.valor.text(), d.descricao.text()) == ("Saída", "39,90", "Vídeo")
    assert (d.categoria.currentText(), d.metodo.atual(), d.dia.currentData()) == ("Assinaturas", "Cartão", 7)
    assert d.conta.currentText() == "Cartão de crédito" and d.assinatura.isChecked()
    assert "o que já aconteceu fica igual" in d.aviso.text()
    d.close()


def test_dialogo_recorrente_erros_inline_e_nao_grava(app, conn):
    rid = _criar(conn)
    d = _editar(conn, rid)
    d.valor.setText("abc")
    d.descricao.setText("  ")
    assert not d.salvar()
    assert d.erro_valor.text().startswith("!") and d.erro_descricao.text() == "!  Diga o que é."
    assert rec.obter(conn, rid)["descricao"] == "Streaming"
    d.valor.setText("0")
    d.descricao.setText("Ok")
    assert not d.salvar() and "maior que zero" in d.erro_valor.text()
    d.close()


def test_dialogo_recorrente_salva_mudancas_so_pro_futuro(app, conn):
    rid = _criar(conn, "Streaming", dia=25)
    rec.gerar_pendentes(conn, HOJE)  # set realizado, out previsto
    d = _editar(conn, rid)
    recebido = []
    d.gravado.connect(recebido.append)
    d.valor.setText("49,90")
    d.descricao.setText("Streaming 4K")
    d.dia.setCurrentIndex(d.dia.findData(28))
    d.assinatura.setChecked(False)
    d.metodo.clicar("Pix")
    assert d.rotulo_onde.text() == "Conta" and d.conta.currentText() == "Inter"
    assert d.salvar() and d.result() == d.DialogCode.Accepted
    r = rec.obter(conn, rid)
    assert (r["descricao"], r["valor_centavos"], r["dia_do_mes"], r["assinatura"], r["conta"], r["metodo"]) == (
        "Streaming 4K", -4990, 28, 0, "Inter", "pix")
    setembro, outubro = [dict(r) for r in conn.execute("SELECT * FROM lancamentos ORDER BY data")]
    assert setembro["descricao"] == "Streaming" and setembro["valor_centavos"] == -3990
    assert (outubro["descricao"], outubro["valor_centavos"], outubro["data"]) == ("Streaming 4K", -4990, "2026-10-28")
    assert recebido == ["Salvo: Streaming 4K (R$ 49,90), todo dia 28"]


def test_dialogo_recorrente_troca_para_entrada(app, conn):
    rid = _criar(conn)
    d = _editar(conn, rid)
    d.tipo.clicar("Entrada")
    d.categoria.setCurrentIndex(d.categoria.findText("Salário"))
    d.metodo.clicar("Pix")
    assert d.salvar()
    assert rec.obter(conn, rid)["valor_centavos"] == 3990 and rec.obter(conn, rid)["categoria"] == "Salário"


def test_dialogo_recorrente_dinheiro_esconde_a_conta(app, conn):
    rid = _criar(conn)
    d = _editar(conn, rid)
    d.metodo.clicar("Dinheiro")
    assert d._bloco_onde.isHidden() and d.info_destino.text() == "Sai da Carteira"
    assert d.salvar() and rec.obter(conn, rid)["conta"] == "Carteira"


def test_dialogo_recorrente_inexistente(app, conn):
    with pytest.raises(ValueError):
        DialogoRecorrente(conn, 999, hoje=HOJE)


# --- Janela: abas e geração ------------------------------------------------------------------------


def test_abas_trocam_o_conteudo_e_escondem_o_mes(app, conn):
    j = _janela(conn)
    assert j.aba_atual == "inicio" and j.rotulo_mes.isVisibleTo(j)
    assert j.abas.atual() == "Início" and j.botao_novo.isVisible() and j.botao_contas.isVisible()
    j.abas.clicar("Assinaturas/fixos")
    assert j.aba_atual == "recorrentes" and not j.rotulo_mes.isVisibleTo(j)
    assert j.aba_recorrentes.isVisibleTo(j) and j.botao_novo.isVisible() and j.botao_contas.isVisible()
    j.abas.clicar("Início")
    assert j.aba_atual == "inicio" and j.rotulo_mes.isVisibleTo(j) and not j.aba_recorrentes.isVisibleTo(j)
    j.mostrar_aba("recorrentes")
    assert j.aba_atual == "recorrentes" and j.abas.atual() == "Assinaturas/fixos"
    j.close()


def test_abrir_o_app_e_atualizar_geram_as_recorrentes(app, conn):
    rid = _criar(conn, "Vídeo", dia=7, inicio=date(2026, 8, 1))
    j = _janela(conn)  # abrir: gera ago, set e out
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE recorrente_id = ?", (rid,)).fetchone()[0] == 3
    assert [l.nome.texto() for l in j.aba_recorrentes.linhas()] == ["Vídeo"]
    outra = _criar(conn, "Música", dia=3, inicio=date(2026, 10, 1))
    j.atualizar()
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE recorrente_id = ?", (outra,)).fetchone()[0] == 1
    assert len(j.aba_recorrentes.linhas()) == 2
    j.close()


def test_previsto_nao_aparece_nos_recentes_mas_entra_no_livre(app, conn):
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 1000000 WHERE nome = 'Inter'")
    _criar(conn, "Aluguel", valor=-150000, conta="Inter", metodo="boleto", dia=28, assinatura=False, cat="Moradia",
           inicio=date(2026, 10, 1))
    j = _janela(conn)
    itens = [j._lista.itemAt(i).widget() for i in range(j._lista.count())]
    assert itens == []  # previsto de 28/out: ainda não aconteceu
    assert j._livre.valor.text() == "R$ 8.500,00"
    j.close()


def test_recorrente_nos_recentes_tem_o_simbolo(app, conn):
    _criar(conn, "Vídeo", dia=20, inicio=date(2026, 10, 1))
    lanc.registrar(conn, entrada=False, valor_centavos=1000, descricao="Pão", categoria_id=None, metodo="pix",
                   conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 19))
    j = _janela(conn)
    itens = {i.descricao.texto(): i for i in (j._lista.itemAt(k).widget() for k in range(j._lista.count()))}
    assert itens["Vídeo"].detalhe.texto().startswith("↻ ")
    assert not itens["Pão"].detalhe.texto().startswith("↻")
    j.close()


# --- Janela de lançamento: Repetir todo mês ------------------------------------------------------------


def _dialogo(conn, id_lancamento=None):
    d = DialogoLancamento(conn, id_lancamento, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_repetir_comeca_desmarcado_e_so_existe_no_modo_novo(app, conn):
    d = _dialogo(conn)
    assert not d.repetir.isChecked() and d.repetir.isVisibleTo(d) and not d.assinatura.isVisibleTo(d)
    d.close()
    lid = lanc.registrar(conn, entrada=False, valor_centavos=100, descricao="x", categoria_id=None, metodo="pix",
                         conta_id=id_conta(conn, "Inter"), data=HOJE)[0]
    d = _dialogo(conn, lid)
    assert not d.repetir.isVisibleTo(d) and not d._bloco_recorrente.isVisibleTo(d)  # avulso: nada de recorrência
    d.close()


def test_repetir_mostra_todo_dia_ao_vivo_e_o_e_assinatura(app, conn):
    d = _dialogo(conn)
    d.data.setDate(QDate(2026, 10, 5))
    d.repetir.setChecked(True)
    assert d.texto_repetir.text() == "todo dia 5" and d.assinatura.isVisibleTo(d)
    d.data.setDate(QDate(2026, 10, 31))
    assert d.texto_repetir.text() == "todo dia 31"
    d.repetir.setChecked(False)
    assert d.texto_repetir.text() == "" and not d.assinatura.isVisibleTo(d)
    d.close()


def test_e_assinatura_ja_marcada_com_a_categoria_assinaturas(app, conn):
    d = _dialogo(conn)
    d.categoria.setCurrentIndex(d.categoria.findText("Assinaturas"))
    d.repetir.setChecked(True)
    assert d.assinatura.isChecked()
    d.assinatura.click()  # o dono desmarca: a categoria não força de novo
    d.categoria.setCurrentIndex(d.categoria.findText("Moradia"))
    d.categoria.setCurrentIndex(d.categoria.findText("Assinaturas"))
    assert not d.assinatura.isChecked()
    d.close()
    d = _dialogo(conn)
    d.categoria.setCurrentIndex(d.categoria.findText("Moradia"))
    d.repetir.setChecked(True)
    assert not d.assinatura.isChecked()
    d.close()


def test_repetir_e_parcelas_nao_valem_juntos(app, conn):
    d = _dialogo(conn)
    d.metodo.clicar("Cartão")
    d.parcelas.setCurrentIndex(2)  # 3x
    assert not d.repetir.isEnabled() and "parcelada" in d.repetir.toolTip()
    d.parcelas.setCurrentIndex(0)
    assert d.repetir.isEnabled()
    d.repetir.setChecked(True)
    assert not d.parcelas.isEnabled() and "Repetir todo mês" in d.parcelas.toolTip()
    d.repetir.setChecked(False)
    assert d.parcelas.isEnabled()
    d.close()


def test_salvar_com_repetir_cria_lancamento_e_recorrente(app, conn):
    d = _dialogo(conn)
    d.metodo.clicar("Cartão")
    d.valor.setText("39,90")
    d.descricao.setText("Streaming")
    d.categoria.setCurrentIndex(d.categoria.findText("Assinaturas"))
    d.data.setDate(QDate(2026, 10, 7))
    d.repetir.setChecked(True)
    assert d.assinatura.isChecked()
    mensagens = []
    d.gravado.connect(mensagens.append)
    assert d.salvar()
    assert mensagens == ["Salvo: Streaming (R$ 39,90) · repete todo dia 7"]
    (l,) = [dict(r) for r in conn.execute("SELECT * FROM lancamentos")]
    r = rec.obter(conn, l["recorrente_id"])
    assert (r["dia_do_mes"], r["assinatura"], r["valor_centavos"], r["conta"]) == (7, 1, -3990, "Cartão de crédito")
    assert rec.gerar_pendentes(conn, HOJE) == 0  # o mês já está feito
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1


def test_salvar_sem_repetir_nao_cria_recorrente(app, conn):
    d = _dialogo(conn)
    d.valor.setText("10")
    d.descricao.setText("Pão")
    assert d.salvar()
    assert conn.execute("SELECT COUNT(*) FROM recorrentes").fetchone()[0] == 0


def test_salvar_e_lancar_outro_zera_o_repetir(app, conn):
    d = _dialogo(conn)
    d.valor.setText("10")
    d.descricao.setText("Academia")
    d.repetir.setChecked(True)
    assert d.salvar_e_lancar_outro()
    assert not d.repetir.isChecked() and not d.assinatura.isVisibleTo(d)
    assert conn.execute("SELECT COUNT(*) FROM recorrentes").fetchone()[0] == 1
    d.close()


def test_editar_lancamento_gerado_mostra_a_recorrencia_e_gerencia(app, conn):
    rid = _criar(conn, "Vídeo", dia=7, inicio=date(2026, 10, 1))
    rec.gerar_pendentes(conn, HOJE)
    lid = conn.execute("SELECT id FROM lancamentos WHERE recorrente_id = ?", (rid,)).fetchone()[0]
    d = _dialogo(conn, lid)
    assert d.texto_recorrente.text() == "Repete todo dia 7 · assinatura"
    assert d._bloco_recorrente.isVisibleTo(d) and not d.repetir.isVisibleTo(d)
    pedidos = []
    d.gerenciar_recorrente.connect(pedidos.append)
    d.botao_gerenciar.click()
    assert pedidos == [rid] and d.result() == d.DialogCode.Rejected


def test_gerenciar_abre_a_aba_na_janela(app, conn):
    rid = _criar(conn, "Vídeo", dia=7, inicio=date(2026, 10, 1))
    j = _janela(conn)
    lid = conn.execute("SELECT id FROM lancamentos WHERE recorrente_id = ?", (rid,)).fetchone()[0]
    d = j._abrir_lancamento(lid)
    QApplication.processEvents()
    d.botao_gerenciar.click()
    assert j.aba_atual == "recorrentes" and j.dialogo is None
    j.close()
