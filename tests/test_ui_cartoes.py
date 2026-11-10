"""Aba "Cartões" (F6), "Gastei no mês", painel "Próximos meses" e o final do cartão na interface. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from meu_cofre import cadastros, faturas, recorrentes
from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.aba_cartoes import AbaCartoes, situacao_da_fatura, titulo_da_fatura
from meu_cofre.ui.componentes import GradeResponsiva, RotuloElidido
from meu_cofre.ui.dialogo_contas import DialogoContas
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.graficos import OPCAO_FLUXO, OPCAO_PROXIMOS, OPCAO_ROSCA
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 29)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _compra(conn, cartao, valor, dia, descricao="Compra", parcelas=1):
    return lanc.registrar(
        conn, entrada=False, valor_centavos=valor, descricao=descricao, categoria_id=id_cat(conn, "Compras pessoais"),
        metodo="cartao", conta_id=cartao, data=dia, parcelas=parcelas,
    )


def _visa(conn):
    """Cartão do dono: fecha dia 1, vence dia 10, final 1234 (o "Cartão de crédito" do seed vira Caixa Visa)."""
    cartao = id_conta(conn, "Cartão de crédito")
    conn.execute("UPDATE contas SET nome = 'Caixa Visa' WHERE id = ?", (cartao,))
    cadastros.editar_cartao(conn, cartao, banco="Caixa", dia_fechamento=1, dia_vencimento=10, final="1234")
    return cartao


def _aba(conn):
    aba = AbaCartoes(conn, lambda: HOJE)
    aba.resize(1100, 900)
    aba.show()
    QApplication.processEvents()
    return aba


def _janela(conn, tamanho=(1500, 900)):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(*tamanho)
    j.show()
    QApplication.processEvents()
    return j


def _textos(widget):
    return [l.text() for l in widget.findChildren(QLabel)]


# --- Aba Cartões ----------------------------------------------------------------------------------


def test_sem_cartao_mostra_estado_vazio(app, conn):
    cadastros.desativar_conta(conn, id_conta(conn, "Cartão de crédito"))
    aba = _aba(conn)
    assert aba._vazio.isVisibleTo(aba) and aba._vazio.titulo.text() == "Nenhum cartão"
    assert "Contas" in aba._vazio.dica.text()
    assert not aba._cartao_destaque.isVisibleTo(aba) and not aba._cartao_lista.isVisibleTo(aba)
    assert aba._seletor is None


def test_cartao_sem_compras_tem_so_a_fatura_aberta(app, conn):
    _visa(conn)
    aba = _aba(conn)
    assert not aba._vazio.isVisibleTo(aba)
    assert aba._cartao_destaque.titulo.text() == "CAIXA VISA •••• 1234"
    assert aba._fechada.valor.text() == "—" and not aba.botao_pagar.isVisibleTo(aba)
    assert aba._aberta.valor.text() == "R$ 0,00" and aba.pilula_aberta.text() == "fecha em 3 dias"
    assert [c.referencia for c in aba.cards()] == ["2026-11"]


def test_fatura_fechada_em_destaque_e_aberta(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15), "Setembro")  # fatura de outubro, vence 10/10 (venceu)
    _compra(conn, cartao, 8000, date(2026, 10, 15), "Outubro")  # fatura de novembro, aberta
    aba = _aba(conn)
    assert aba._fechada.rotulo.text() == "FATURA DE OUTUBRO"
    assert aba._fechada.valor.text() == "R$ 300,00"
    assert "vence 10/10" in aba._fechada.apoio.text()
    assert aba.pilula_fechada.text().endswith("venceu há 19 dias")
    assert aba.botao_pagar.isVisibleTo(aba)
    assert aba._aberta.rotulo.text() == "FATURA ABERTA · NOVEMBRO"
    assert aba._aberta.valor.text() == "R$ 80,00" and "fecha 01/11" in aba._aberta.apoio.text()
    # aberta, fechada: nessa ordem; todas recolhidas
    assert [c.referencia for c in aba.cards()] == ["2026-11", "2026-10"]
    assert not any(c.corpo.isVisibleTo(aba) for c in aba.cards())


def test_situacoes_da_fatura(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 10000, date(2026, 9, 15))
    f = faturas.faturas_do_cartao(conn, cartao, HOJE)[0]
    assert situacao_da_fatura(f, date(2026, 10, 1)) == ("a pagar", "atencao")
    assert situacao_da_fatura(f, date(2026, 10, 3)) == ("vence em 7 dias", "atencao")
    assert situacao_da_fatura(f, date(2026, 10, 9)) == ("vence amanhã", "atencao")
    assert situacao_da_fatura(f, date(2026, 10, 10)) == ("vence hoje", "atencao")
    assert situacao_da_fatura(f, date(2026, 10, 11)) == ("venceu há 1 dia", "ruim")
    assert titulo_da_fatura(f, HOJE) == "Fatura de outubro"
    assert titulo_da_fatura(f, date(2027, 1, 5)) == "Fatura de outubro de 2026"


def test_card_expande_e_mostra_parcela_destacada(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 60000, date(2026, 10, 15), "Cadeira presidente", parcelas=6)
    _compra(conn, cartao, 3250, date(2026, 10, 20), "Almoço")
    aba = _aba(conn)
    card = aba.card("2026-11")
    assert not card.corpo.isVisibleTo(aba)
    aba._alternar("2026-11")
    QApplication.processEvents()
    card = aba.card("2026-11")
    assert card.corpo.isVisibleTo(aba) and card.seta.text() == "▾"
    por_descricao = {l.descricao.texto(): l for l in card.linhas}
    assert por_descricao["Cadeira presidente"].parcela.text() == "1/6"
    assert por_descricao["Cadeira presidente"].parcela.isVisibleTo(aba)
    assert por_descricao["Cadeira presidente"].valor.text() == "R$ 100,00"
    assert not por_descricao["Almoço"].parcela.isVisibleTo(aba)
    assert por_descricao["Almoço"].valor.text() == "R$ 32,50"
    assert aba.card("2026-12").linhas[0].parcela.text() == "2/6"  # a próxima fatura tem a 2/6
    assert "Compras de 01/10 a 31/10" in _textos(card)


def test_marcar_como_paga_pelo_formulario(app, conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 123456, date(2026, 9, 15), "Setembro")
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)

    assert not aba.form.isVisibleTo(aba)
    aba.botao_pagar.click()
    QApplication.processEvents()
    assert aba.form.isVisibleTo(aba) and not aba.botao_pagar.isVisibleTo(aba)
    assert aba.form.valor.text() == "1.234,56"  # o total da fatura, editável
    assert aba.form.conta.currentData() == inter and aba.form.conta.currentText() == "Inter"  # padrão Inter
    assert aba.form.data.date().toPython() == HOJE  # data de hoje
    assert [aba.form.conta.itemText(i) for i in range(aba.form.conta.count())] == ["Inter", "Caixa"]  # só correntes

    aba.form.valor.setText("1.234,56")
    assert aba.form.confirmar()
    QApplication.processEvents()
    assert avisos == ["Fatura de outubro paga: R$ 1.234,56"]
    assert not aba.form.isVisibleTo(aba)
    f = faturas.faturas_do_cartao(conn, cartao, HOJE)[0]
    assert (f.situacao, f.total_centavos, f.pago_centavos, f.data_pagamento) == ("paga", 123456, 123456, HOJE)
    assert aba._fechada.valor.text() == "R$ 1.234,56" and aba.pilula_fechada.text() == "✓ paga"
    assert not aba.botao_pagar.isVisibleTo(aba)
    assert aba.link_pagas.isVisibleTo(aba)
    assert aba.card("2026-10") is None  # pagas ficam recolhidas atrás do link


def test_pagamento_parcial_mostra_o_restante_e_deixa_pagar_de_novo(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 123456, date(2026, 9, 15), "Setembro")
    aba = _aba(conn)
    aba.botao_pagar.click()
    aba.form.valor.setText("1.000,00")  # pagou menos
    assert aba.form.confirmar()
    QApplication.processEvents()
    f = faturas.faturas_do_cartao(conn, cartao, HOJE)[0]
    assert (f.situacao, f.restante_centavos) == ("parcial", 23456)
    assert aba._fechada.valor.text() == "R$ 234,56"  # o que falta
    assert "Pago R$ 1.000,00 · falta R$ 234,56" in aba._fechada.apoio.text()
    assert aba.pilula_fechada.text() == "! parcial"
    assert aba.botao_pagar.isVisibleTo(aba)  # continua podendo pagar
    assert aba.card("2026-10").situacao.text() == "! parcial" and aba.card("2026-10").botao_pagar is not None

    aba.botao_pagar.click()
    QApplication.processEvents()
    assert aba.form.valor.text() == "234,56"  # pré-preenchido com o restante
    assert aba.form.confirmar()
    QApplication.processEvents()
    f = faturas.faturas_do_cartao(conn, cartao, HOJE)[0]
    assert (f.situacao, len(f.pagamentos)) == ("paga", 2)


def test_desfazer_logo_apos_pagar(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    aba = _aba(conn)
    assert not aba.aviso_pago.isVisibleTo(aba)
    aba.botao_pagar.click()
    assert aba.form.confirmar()
    QApplication.processEvents()
    assert aba.aviso_pago.isVisibleTo(aba) and aba.botao_desfazer_agora.isVisibleTo(aba)
    assert aba.texto_aviso_pago.text() == "Fatura de outubro paga ✓"
    aba.recarregar()  # recarga externa (a janela faz isso) não tira o aviso
    assert aba.aviso_pago.isVisibleTo(aba)

    aba.botao_desfazer_agora.click()
    QApplication.processEvents()
    assert not aba.aviso_pago.isVisibleTo(aba)
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 0
    assert aba.botao_pagar.isVisibleTo(aba)


def test_aviso_de_pagamento_parcial_e_some_na_proxima_acao(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    aba = _aba(conn)
    aba.botao_pagar.click()
    aba.form.valor.setText("100,00")
    assert aba.form.confirmar()
    QApplication.processEvents()
    assert aba.texto_aviso_pago.text() == "Pagamento de R$ 100,00 registrado · falta R$ 200,00"
    aba.mostrar_pagas(True)  # qualquer outra ação
    QApplication.processEvents()
    assert not aba.aviso_pago.isVisibleTo(aba)


def test_formulario_de_pagamento_valida_e_cancela(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    aba = _aba(conn)
    aba.botao_pagar.click()
    aba.form.valor.setText("abc")
    assert not aba.form.confirmar() and "inválido" in aba.form.erro.text() and aba.form.valor.property("erro") is True
    aba.form.valor.setText("0")
    assert not aba.form.confirmar() and "maior que zero" in aba.form.erro.text()
    aba.form.valor.setText("10")
    assert aba.form.erro.text() == "" and aba.form.valor.property("erro") is False
    aba.form.botao_cancelar.click()
    QApplication.processEvents()
    assert not aba.form.isVisibleTo(aba) and aba.botao_pagar.isVisibleTo(aba)
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 0


def test_pagar_pelo_botao_da_lista_abre_o_formulario_em_cima(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 8, 15), "Agosto")
    _compra(conn, cartao, 40000, date(2026, 9, 15), "Setembro")
    aba = _aba(conn)
    assert aba._fechada.rotulo.text() == "FATURA DE SETEMBRO"  # a de vencimento mais antigo
    assert "+ 1 fatura fechada a pagar" in aba._fechada.apoio.text()
    aba._alternar("2026-10")
    aba.card("2026-10").botao_pagar.click()
    QApplication.processEvents()
    assert aba.form.isVisibleTo(aba) and "OUTUBRO" in aba.form.titulo.text()
    assert aba.form.valor.text() == "400,00"


def test_desfazer_pagamento_pede_confirmacao(app, conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 10))
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.mostrar_pagas(True)
    aba._alternar("2026-10")
    QApplication.processEvents()
    card = aba.card("2026-10")
    assert card.situacao.text() == "✓ paga" and card.linhas[0].pago.text() == "✓"
    assert card.botao_desfazer.isVisibleTo(aba) and not card.confirmando_desfazer

    card.botao_desfazer.click()
    assert aba.card("2026-10").confirmando_desfazer
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 1  # ainda não desfez
    aba.card("2026-10").botao_nao.click()
    assert not aba.card("2026-10").confirmando_desfazer
    aba.card("2026-10").botao_desfazer.click()
    aba.card("2026-10").botao_sim.click()
    QApplication.processEvents()
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 0
    assert len(avisos) == 1 and "desfeito" in avisos[0]
    assert aba.card("2026-10").situacao.text().endswith("venceu há 19 dias")


def test_seletor_de_cartao_com_dois_cartoes(app, conn):
    visa = _visa(conn)
    inter = cadastros.criar_cartao(conn, "Inter Black", "Inter", 25, 5, "5678")
    _compra(conn, visa, 1000, date(2026, 10, 15), "da Visa")
    _compra(conn, inter, 2000, date(2026, 10, 15), "da Inter")
    aba = _aba(conn)
    assert aba._seletor is not None and aba._seletor.isVisibleTo(aba)
    assert aba._seletor.atual() == "Caixa Visa •••• 1234"
    assert aba.cartao_atual == visa and not aba.card("2026-11").corpo.isVisibleTo(aba)
    aba._seletor.clicar("Inter Black •••• 5678")
    QApplication.processEvents()
    assert aba.cartao_atual == inter
    assert aba._cartao_destaque.titulo.text() == "INTER BLACK •••• 5678"
    assert aba._fechada.rotulo.text() == "FATURA DE NOVEMBRO"  # fecha 25, vence 5: a de 15/10 já fechou
    assert aba._fechada.valor.text() == "R$ 20,00" and aba.pilula_fechada.text() == "! vence em 7 dias"


def test_cartao_desativado_com_fatura_em_aberto_continua_na_aba(app, conn):
    visa = _visa(conn)
    elo = cadastros.criar_cartao(conn, "Caixa Elo")
    _compra(conn, elo, 5000, date(2026, 10, 3))
    cadastros.desativar_conta(conn, elo)
    aba = _aba(conn)
    assert [n for _i, n in aba._cartoes] == ["Caixa Visa •••• 1234", "Caixa Elo (desativado)"]
    elo_sem_divida = cadastros.criar_cartao(conn, "Velho")
    cadastros.desativar_conta(conn, elo_sem_divida)
    aba.recarregar()
    assert len(aba._cartoes) == 2 and visa


# --- Janela principal -------------------------------------------------------------------------------


def test_tres_abas_e_navegacao_do_mes(app, conn):
    _visa(conn)
    j = _janela(conn)
    assert j.abas.atual() == "Início" and j.aba_atual == "inicio" and j.rotulo_mes.isVisibleTo(j)
    j.abas.clicar("Cartões")
    assert j.aba_atual == "cartoes" and j.aba_cartoes.isVisibleTo(j) and not j.rotulo_mes.isVisibleTo(j)
    j.mostrar_aba("recorrentes")
    assert j.aba_atual == "recorrentes" and j.abas.atual() == "Assinaturas/fixos"
    j.mostrar_aba("inicio")
    assert j.rotulo_mes.isVisibleTo(j)


def test_pagar_fatura_atualiza_a_tela_inicial(app, conn):
    inter = id_conta(conn, "Inter")
    lanc.registrar(conn, entrada=True, valor_centavos=500000, descricao="Salário", categoria_id=id_cat(conn, "Salário"),
                   metodo="pix", conta_id=inter, data=date(2026, 10, 5))
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    j = _janela(conn)
    assert (j._em_conta.valor.text(), j._devo.valor.text()) == ("R$ 5.000,00", "R$ 300,00")
    j.mostrar_aba("cartoes")
    j.aba_cartoes.botao_pagar.click()
    assert j.aba_cartoes.form.confirmar()
    QApplication.processEvents()
    assert (j._em_conta.valor.text(), j._devo.valor.text()) == ("R$ 4.700,00", "R$ 0,00")
    assert "paga" in j.statusBar().currentMessage()


def test_gastei_no_mes_no_resumo(app, conn):
    inter, cartao = id_conta(conn, "Inter"), id_conta(conn, "Cartão de crédito")
    for valor, metodo in ((10000, "pix"), (15000, "debito")):
        lanc.registrar(conn, entrada=False, valor_centavos=valor, descricao="x", categoria_id=id_cat(conn, "Mercado"),
                       metodo=metodo, conta_id=inter, data=date(2026, 10, 5))
    _compra(conn, cartao, 30000, date(2026, 10, 6))
    _compra(conn, cartao, 9900, date(2026, 11, 5))  # novembro: fora do mês
    j = _janela(conn)
    assert j._gastei.valor.text() == "R$ 550,00"
    assert j._gastei.apoio.text().replace(" ", " ") == "R$ 250 já saiu da conta · R$ 300 no cartão"


def test_resumo_livre_em_cima_e_tres_colunas_ou_uma(app, conn):
    j = _janela(conn, (1500, 900))
    grade = j._cartao_resumo.findChild(GradeResponsiva)
    assert grade.colunas() == 3  # o "Livre" fica fora da grade, em destaque
    assert j._livre.valor.property("heroi") is True
    j.resize(700, 900)
    QApplication.processEvents()
    assert grade.colunas() in (1, 3)


def test_recentes_mostram_o_final_do_cartao(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 5000, date(2026, 10, 20))
    j = _janela(conn)
    assert any(r.texto() == "Compras pessoais · Caixa Visa •••• 1234" for r in j.findChildren(RotuloElidido))


def test_dialogo_de_lancamento_mostra_o_final_do_cartao(app, conn):
    visa = _visa(conn)
    cadastros.criar_cartao(conn, "Inter Black", final="5678")
    d = DialogoLancamento(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    assert [d.cartao.itemText(i) for i in range(d.cartao.count())] == ["Caixa Visa •••• 1234", "Inter Black •••• 5678"]
    ids = _compra(conn, visa, 7000, date(2026, 10, 20))
    detalhe = DialogoLancamento(conn, ids[0], hoje=HOJE)
    assert "Caixa Visa •••• 1234" in detalhe._texto_detalhes(lanc.obter(conn, ids[0]))
    cadastros.desativar_conta(conn, visa)
    d2 = DialogoLancamento(conn, ids[0], hoje=HOJE)
    assert d2.cartao.currentText() == "Caixa Visa •••• 1234 (desativado)"


# --- Painel "Próximos meses" ---------------------------------------------------------------------------


def _comprometer(conn):
    inter, cartao = id_conta(conn, "Inter"), id_conta(conn, "Cartão de crédito")
    _compra(conn, cartao, 60000, date(2026, 10, 15), "Cadeira", parcelas=6)
    recorrentes.criar(conn, descricao="Aluguel", valor_centavos=-120000, conta_id=inter, categoria_id=id_cat(conn, "Moradia"),
                      metodo="boleto", dia_do_mes=5, inicio=date(2026, 9, 5), assinatura=False)


def test_painel_proximos_vazio(app, conn):
    j = _janela(conn)
    j.mostrar_painel(OPCAO_PROXIMOS)
    QApplication.processEvents()
    proximos = j._painel_grafico.proximos
    assert proximos._pilha.currentWidget() is proximos._vazio
    assert proximos._vazio.titulo.text() == "Nada comprometido nos próximos meses"


def test_painel_proximos_barras_e_tooltip(app, conn):
    _comprometer(conn)
    j = _janela(conn)
    j.mostrar_painel(OPCAO_PROXIMOS)
    QApplication.processEvents()
    barras = j._painel_grafico.proximos._barras
    assert j._painel_grafico.currentWidget() is j._painel_grafico.proximos
    assert len(barras._meses) == 6 and barras._meses[0]["longo"] == "Nov/2026"
    assert barras.dica_do_mes(0) == "Nov/2026 · R$ 1.300 (cartão R$ 100 · contas R$ 1.200)"
    assert barras.dica_do_mes(5) == "Abr/2027 · R$ 1.200 (cartão R$ 0 · contas R$ 1.200)"
    assert barras.grab().toImage().pixelColor(barras.width() // 2, barras.height() - 40).alpha() > 0  # desenhou algo
    assert j._cartao_painel.subtitulo.text().startswith("Próximos 6 meses")
    # os botões de mês não valem pra essa opção
    assert not j.rotulo_mes.isVisibleTo(j) and not j.botao_mes_anterior.isVisibleTo(j)
    j.seletor_painel.clicar(OPCAO_FLUXO)
    assert j.rotulo_mes.isVisibleTo(j) and j._cartao_painel.subtitulo.text() == "Outubro de 2026"


def test_graficos_antigos_continuam_inteiros(app, conn):
    """Regressão (F5): fluxo e rosca precisam ter o gráfico entregue à vista, também depois de trocar de opção."""
    inter = id_conta(conn, "Inter")
    for valor, cat in ((5000, "Mercado"), (3000, "Transporte")):
        lanc.registrar(conn, entrada=False, valor_centavos=valor, descricao="x", categoria_id=id_cat(conn, cat),
                       metodo="pix", conta_id=inter, data=date(2026, 10, 5))
    _comprometer(conn)
    j = _janela(conn)
    painel = j._painel_grafico
    for opcao in (OPCAO_PROXIMOS, OPCAO_ROSCA, OPCAO_FLUXO, OPCAO_PROXIMOS, OPCAO_FLUXO):
        j.seletor_painel.clicar(opcao)
        QApplication.processEvents()
    assert painel.fluxo._vista.chart() is not None and len(painel.fluxo._vista.chart().series()) >= 3
    assert len(painel.rosca._vista.chart().series()) == 1 and painel.rosca._vista.chart().series()[0].count() == 4
    j.atualizar()  # recarregar com os três painéis não pode cair
    QApplication.processEvents()
    assert painel.fluxo._vista.chart() is not None and painel.rosca._vista.chart().series()[0].count() == 4


# --- Contas e cartões: final e edição -----------------------------------------------------------------


def _contas(conn):
    d = DialogoContas(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_adicionar_cartao_com_final(app, conn):
    d = _contas(conn)
    d.iniciar_adicao()
    d.novo_nome.setText("Caixa Visa")
    QTest.keyClicks(d.novo_final, "12a345")  # só dígitos, no máximo 4
    assert d.novo_final.text() == "1234"
    assert d.adicionar_cartao()
    c = conn.execute("SELECT * FROM contas WHERE nome = 'Caixa Visa'").fetchone()
    assert c["final"] == "1234"
    assert any(l == "Caixa Visa •••• 1234" for l in _textos(d))


def test_editar_banco_dias_e_final_do_cartao(app, conn):
    cartao = id_conta(conn, "Cartão de crédito")
    d = _contas(conn)
    mudancas = []
    d.alterado.connect(mudancas.append)
    d.pedir_editar(cartao)
    QApplication.processEvents()
    d.editar_banco.setText("Caixa")
    d.editar_final.setText("9876")
    d.editar_fechamento.setCurrentIndex(d.editar_fechamento.findData(1))
    d.editar_vencimento.setCurrentIndex(d.editar_vencimento.findData(10))
    assert d.salvar_edicao()
    c = conn.execute("SELECT * FROM contas WHERE id = ?", (cartao,)).fetchone()
    assert (c["banco"], c["final"], c["dia_fechamento"], c["dia_vencimento"]) == ("Caixa", "9876", 1, 10)
    assert mudancas == ["Cartão atualizado: Cartão de crédito"]
    assert any("fecha dia 1" in t and "vence dia 10" in t for t in _textos(d))
    # editar de novo vem preenchido e dá pra limpar
    d.pedir_editar(cartao)
    assert d.editar_final.text() == "9876" and d.editar_fechamento.currentData() == 1
    d.editar_final.setText("")
    d.editar_fechamento.setCurrentIndex(0)
    assert d.salvar_edicao()
    assert conn.execute("SELECT final, dia_fechamento FROM contas WHERE id = ?", (cartao,)).fetchone()[:] == (None, None)


def test_mudar_fechamento_de_cartao_com_compras_pede_confirmacao(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    d = _contas(conn)
    d.pedir_editar(cartao)
    QApplication.processEvents()
    d.editar_fechamento.setCurrentIndex(d.editar_fechamento.findData(25))
    assert not d.salvar_edicao()  # não grava: mostra o aviso
    QApplication.processEvents()
    assert d.aviso_editar.isVisibleTo(d) and not d.barra_editar.isVisibleTo(d)
    assert any("em qual fatura as compras antigas caem" in t for t in _textos(d.aviso_editar))
    assert conn.execute("SELECT dia_fechamento FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == 1
    d.botao_aviso_cancelar.click()
    assert conn.execute("SELECT dia_fechamento FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == 1

    d.pedir_editar(cartao)
    QApplication.processEvents()
    assert not d.aviso_editar.isVisibleTo(d)
    d.editar_vencimento.setCurrentIndex(d.editar_vencimento.findData(15))
    assert not d.salvar_edicao()
    d.botao_aviso_salvar.click()
    assert conn.execute("SELECT dia_vencimento FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == 15


def test_mudar_so_o_banco_ou_cartao_sem_compras_salva_direto(app, conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    d = _contas(conn)
    d.pedir_editar(cartao)
    d.editar_banco.setText("Outro")  # dias iguais: sem aviso, mesmo com compras
    assert d.salvar_edicao()
    outro = cadastros.criar_cartao(conn, "Sem uso", dia_fechamento=5, dia_vencimento=12)  # sem compras
    d._carregar_cartoes()
    d.pedir_editar(outro)
    d.editar_fechamento.setCurrentIndex(d.editar_fechamento.findData(20))
    assert d.salvar_edicao()
    assert conn.execute("SELECT dia_fechamento FROM contas WHERE id = ?", (outro,)).fetchone()[0] == 20


def test_editar_cartao_cancelar_nao_grava(app, conn):
    cartao = id_conta(conn, "Cartão de crédito")
    d = _contas(conn)
    d.pedir_editar(cartao)
    d.editar_final.setText("1111")
    d.cancelar_acao()
    assert conn.execute("SELECT final FROM contas WHERE id = ?", (cartao,)).fetchone()[0] is None
    assert not d.salvar_edicao()  # sem edição aberta, não faz nada


def test_grade_responsiva_distribui_em_colunas(app):
    from PySide6.QtWidgets import QLabel as L

    grade = GradeResponsiva(largura_minima_item=100, espaco=10)
    for i in range(4):
        grade.adicionar(L(str(i)))
    grade.resize(500, 50)
    grade.show()
    QApplication.processEvents()
    assert grade.colunas() == 4
    grade.resize(330, 50)
    QApplication.processEvents()
    assert grade.colunas() == 2  # 3 + 1 fica torto: prefere 2 × 2
    grade.resize(150, 50)
    QApplication.processEvents()
    assert grade.colunas() == 1


# --- Rosca "Para onde foi" ----------------------------------------------------------------------


def _compra_cat(conn, cartao, valor, dia, categoria):
    return lanc.registrar(
        conn, entrada=False, valor_centavos=valor, descricao=categoria, categoria_id=id_cat(conn, categoria),
        metodo="cartao", conta_id=cartao, data=dia,
    )


def test_rosca_da_fatura_aberta_tem_grafico_com_a_serie(app, conn):
    cartao = _visa(conn)
    _compra_cat(conn, cartao, 5000, date(2026, 10, 10), "Mercado")
    _compra_cat(conn, cartao, 3000, date(2026, 10, 11), "Transporte")
    _compra_cat(conn, cartao, 2000, date(2026, 10, 12), "Lazer")
    aba = _aba(conn)
    series = aba.rosca._vista.chart().series()
    assert len(series) == 1 and series[0].count() == 3
    assert aba._cartao_rosca.titulo.text() == "Para onde foi"
    assert aba._cartao_rosca.subtitulo.text() == "Fatura de novembro · Caixa Visa •••• 1234"
    assert aba._seg_fatura is None  # só existe a aberta: sem seletor
    aba.recarregar()  # redesenhar não pode cair nem perder a série
    assert aba.rosca._vista.chart().series()[0].count() == 3


def test_rosca_alterna_entre_aberta_e_fechada_e_acompanha_o_pagamento(app, conn):
    cartao = _visa(conn)
    _compra_cat(conn, cartao, 30000, date(2026, 9, 15), "Mercado")  # fatura de outubro (fechada)
    _compra_cat(conn, cartao, 8000, date(2026, 10, 15), "Lazer")
    _compra_cat(conn, cartao, 2000, date(2026, 10, 16), "Transporte")
    aba = _aba(conn)
    assert aba._seg_fatura is not None and aba._seg_fatura.atual() == "Aberta"
    assert aba.rosca._vista.chart().series()[0].count() == 2
    assert aba._cartao_rosca.subtitulo.text().startswith("Fatura de novembro")

    aba._seg_fatura.clicar("Fechada")
    QApplication.processEvents()
    assert aba._cartao_rosca.subtitulo.text().startswith("Fatura de outubro")
    assert aba.rosca._vista.chart().series()[0].count() == 1

    aba.pedir_pagamento("2026-10")
    aba.form.valor.setText("300,00")
    assert aba.form.confirmar()
    QApplication.processEvents()
    assert aba._seg_fatura is None  # a fechada foi paga: sobra só a aberta
    assert aba._cartao_rosca.subtitulo.text().startswith("Fatura de novembro")
    aba.desfazer_pagamento("2026-10")
    assert aba._seg_fatura is not None and aba._seg_fatura.atual() == "Aberta"


def test_rosca_vazia_e_troca_de_cartao(app, conn):
    cartao = _visa(conn)
    outro = cadastros.criar_cartao(conn, "Nubank", "Nubank", 20, 28, "9999")
    _compra_cat(conn, cartao, 5000, date(2026, 10, 10), "Mercado")
    aba = _aba(conn)
    assert aba.rosca._vista.chart().series()[0].count() == 1
    aba.selecionar_cartao(outro)
    QApplication.processEvents()
    assert aba.rosca._pilha.currentWidget() is aba.rosca._vazio
    assert aba.rosca._vazio.titulo.text() == "Nenhuma compra nessa fatura ainda"
    assert "Nubank" in aba._cartao_rosca.subtitulo.text()
    aba.selecionar_cartao(cartao)
    assert aba.rosca._pilha.currentWidget() is aba.rosca._conteudo
