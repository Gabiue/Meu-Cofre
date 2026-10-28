"""Diálogo de lançamento (F3): novo, erros inline, salvar e lançar outro, editar e excluir. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _dialogo(conn, id_lancamento=None):
    d = DialogoLancamento(conn, id_lancamento, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def _preencher(d, valor="32,50", descricao="Almoço"):
    d.valor.setText(valor)
    d.descricao.setText(descricao)


def _todos(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM lancamentos ORDER BY id")]


def _lanc(conn, **kw):
    base = dict(entrada=False, valor_centavos=3250, descricao="Almoço", categoria_id=id_cat(conn, "Alimentação fora"),
                metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5))
    base.update(kw)
    return lanc.registrar(conn, **base)


# --- Novo ---------------------------------------------------------------------------


def test_estado_inicial(app, conn):
    d = _dialogo(conn)
    assert d.tipo.atual() == "Saída" and d.metodo.atual() == "Pix"
    assert d.valor.hasFocus() and d.valor.placeholderText() == "0,00"
    assert d.data.date() == QDate(2026, 10, 20) and d.data.displayFormat() == "dd/MM/yyyy"
    assert d.conta.currentText() == "Inter"
    assert d.isModal() and d.botao_salvar.isDefault() and not d.botao_excluir.isVisible()
    assert d.height() <= d.screen().availableGeometry().height()  # tela baixa: o formulário rola
    assert d.width() == 520
    d.close()


@pytest.mark.parametrize("texto,centavos", [("32,50", 3250), ("32.50", 3250), ("1.234,56", 123456), ("40", 4000)])
def test_formatos_de_valor(app, conn, texto, centavos):
    d = _dialogo(conn)
    _preencher(d, texto)
    assert d.salvar()
    assert _todos(conn)[0]["valor_centavos"] == -centavos


def test_saida_pix_grava_na_conta_escolhida(app, conn):
    d = _dialogo(conn)
    d.conta.setCurrentIndex(d.conta.findText("Itaú") if d.conta.findText("Itaú") >= 0 else 0)
    conta = d.conta.currentData()
    d.categoria.setCurrentIndex(d.categoria.findText("Mercado"))
    _preencher(d)
    d.data.setDate(QDate(2026, 10, 3))
    assert d.salvar() and d.result() == d.DialogCode.Accepted
    (l,) = _todos(conn)
    assert (l["valor_centavos"], l["descricao"], l["metodo"], l["conta_id"], l["data"], l["origem"]) == (
        -3250, "Almoço", "pix", conta, "2026-10-03", "manual")
    assert l["categoria_id"] == id_cat(conn, "Mercado")


def test_entrada_usa_categorias_de_receita(app, conn):
    d = _dialogo(conn)
    d.tipo.clicar("Entrada")
    nomes = [d.categoria.itemText(i) for i in range(d.categoria.count())]
    assert nomes == [c["nome"] for c in lanc.listar_categorias(conn, "receita")]
    _preencher(d, "1.500", "Salário")
    assert d.salvar()
    assert _todos(conn)[0]["valor_centavos"] == 150000


def test_metodos_mudam_campos(app, conn):
    d = _dialogo(conn)
    assert d._pilha_extra.currentWidget() is d._pilha_extra.widget(0)
    d.metodo.clicar("Cartão")
    assert d._pilha_extra.currentIndex() == 1
    assert d.info_destino.text() == "Vai pro cartão de crédito, na data da compra"
    d.tipo.clicar("Entrada")  # entrada no cartão não parcela
    assert d._pilha_extra.currentIndex() == 2
    d.tipo.clicar("Saída")
    d.metodo.clicar("Dinheiro")
    assert d.info_destino.text() == "Sai da Carteira" and d._pilha_extra.currentIndex() == 2
    d.metodo.clicar("Boleto")
    assert d._pilha_extra.currentIndex() == 0 and d.info_destino.text() == ""


def test_dinheiro_grava_na_carteira(app, conn):
    d = _dialogo(conn)
    d.metodo.clicar("Dinheiro")
    _preencher(d)
    assert d.salvar()
    l = _todos(conn)[0]
    assert l["metodo"] == "dinheiro" and l["conta_id"] == id_conta(conn, "Carteira")


def test_cartao_parcelado(app, conn):
    d = _dialogo(conn)
    d.metodo.clicar("Cartão")
    _preencher(d, "600", "Tênis")
    d.parcelas.setCurrentIndex(5)
    assert d.parcelas.currentText() == "6x de R$ 100,00" and d.parcelas.itemText(0) == "1x"
    d.valor.setText("100")
    assert d.parcelas.currentText() == "6x de R$ 16,66"  # ao vivo (a sobra vai na 1ª parcela)
    d.valor.setText("600")
    assert d.salvar()
    ls = _todos(conn)
    assert len(ls) == 6 and {l["conta_id"] for l in ls} == {id_conta(conn, "Cartão de crédito")}
    assert [l["valor_centavos"] for l in ls] == [-10000] * 6
    assert [l["data"] for l in ls][:2] == ["2026-10-20", "2026-11-20"]
    assert ls[2]["parcela_atual"] == 3 and ls[2]["parcela_total"] == 6


def test_categoria_sugerida_pelo_historico(app, conn):
    _lanc(conn, descricao="Almoço no RU", categoria_id=id_cat(conn, "Alimentação fora"))
    d = _dialogo(conn)
    d.descricao.setText("almoço no ru")
    d.descricao.editingFinished.emit()
    assert d.categoria.currentText() == "Alimentação fora"
    assert d.dica_categoria.text() == "sugerida pelo seu histórico" and d.dica_categoria.isVisible()
    d.categoria.setCurrentIndex(d.categoria.findText("Mercado"))  # usuário troca
    d.categoria.activated.emit(d.categoria.currentIndex())
    assert not d.dica_categoria.isVisible()
    d.valor.setText("20")
    assert d.salvar()
    assert _todos(conn)[-1]["categoria_id"] == id_cat(conn, "Mercado")


def test_salvar_sem_sair_do_campo_usa_sugestao(app, conn):
    _lanc(conn, descricao="Padaria", categoria_id=id_cat(conn, "Mercado"))
    d = _dialogo(conn)
    _preencher(d, "8", "Padaria")
    assert d.salvar()
    assert _todos(conn)[-1]["categoria_id"] == id_cat(conn, "Mercado")


def test_completar_por_conteudo_do_tipo(app, conn):
    _lanc(conn, descricao="Almoço no RU")
    _lanc(conn, entrada=True, descricao="Salário", categoria_id=id_cat(conn, "Salário"))
    d = _dialogo(conn)
    d.completar.setCompletionPrefix("moço")
    assert d.completar.completionCount() == 1 and d.completar.currentCompletion() == "Almoço no RU"
    d.completar.setCompletionPrefix("sal")
    assert d.completar.completionCount() == 0  # "Salário" é de entrada
    d.tipo.clicar("Entrada")
    d.completar.setCompletionPrefix("SAL")
    assert d.completar.completionCount() == 1


# --- Erros inline ----------------------------------------------------------------------


@pytest.mark.parametrize("valor,trecho", [("abc", "inválido"), ("", "Digite o valor"), ("0", "maior que zero"), ("-5", "maior que zero")])
def test_erro_de_valor_nao_grava(app, conn, monkeypatch, valor, trecho):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))
    d = _dialogo(conn)
    _preencher(d, valor)
    assert not d.salvar()
    assert trecho in d.erro_valor.text() and not d.erro_descricao.text()
    assert d.valor.hasFocus() and d.isVisible()
    assert _todos(conn) == []
    d.valor.setText("10")  # corrigir limpa o erro
    assert d.erro_valor.text() == ""


def test_erro_de_descricao_vai_pro_campo(app, conn):
    d = _dialogo(conn)
    _preencher(d, "10", "   ")
    assert not d.salvar()
    assert d.erro_descricao.text().endswith("Diga o que foi.") and d.descricao.hasFocus()
    assert _todos(conn) == []


def test_dois_erros_foco_no_primeiro(app, conn):
    d = _dialogo(conn)
    assert not d.salvar()
    assert d.erro_valor.text() and d.erro_descricao.text() and d.valor.hasFocus()


# --- Salvar e lançar outro ------------------------------------------------------------------


def test_salvar_e_lancar_outro(app, conn):
    d = _dialogo(conn)
    d.tipo.clicar("Entrada")
    d.metodo.clicar("Boleto")
    d.conta.setCurrentIndex(d.conta.count() - 1)
    conta = d.conta.currentData()
    d.data.setDate(QDate(2026, 10, 2))
    d.categoria.setCurrentIndex(0)
    _preencher(d, "32,50", "Almoço")
    avisos = []
    d.gravado.connect(avisos.append)
    assert d.salvar_e_lancar_outro()
    assert d.isVisible() and avisos == ["Salvo: Almoço (R$ 32,50)"]
    assert d.aviso_salvo.text() == "✓ Salvo: Almoço (R$ 32,50)"
    assert d.valor.text() == "" and d.descricao.text() == "" and d.categoria.currentIndex() == -1
    assert d.valor.hasFocus()
    assert (d.tipo.atual(), d.metodo.atual(), d.conta.currentData(), d.data.date()) == (
        "Entrada", "Boleto", conta, QDate(2026, 10, 2))
    assert len(_todos(conn)) == 1
    _preencher(d, "10", "Outro")
    assert d.salvar_e_lancar_outro() and len(_todos(conn)) == 2


def test_ctrl_enter_salva_e_lanca_outro(app, conn):
    d = _dialogo(conn)
    _preencher(d)
    QTest.keyClick(d, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(_todos(conn)) == 1 and d.isVisible()


def test_enter_salva_e_esc_cancela(app, conn):
    d = _dialogo(conn)
    _preencher(d)
    QTest.keyClick(d.valor, Qt.Key.Key_Return)
    assert len(_todos(conn)) == 1 and not d.isVisible()
    d = _dialogo(conn)
    _preencher(d)
    QTest.keyClick(d, Qt.Key.Key_Escape)
    assert len(_todos(conn)) == 1 and not d.isVisible()


def test_enter_no_completar_escolhe_e_nao_salva(app, conn):
    _lanc(conn, descricao="Almoço no RU")
    d = _dialogo(conn)
    d.valor.setText("10")
    d.descricao.setFocus()
    QTest.keyClicks(d.descricao, "mo")
    d.completar.complete()
    QApplication.processEvents()
    assert d.completar.popup().isVisible()
    QTest.keyClick(d.completar.popup(), Qt.Key.Key_Down)
    QTest.keyClick(d.completar.popup(), Qt.Key.Key_Return)
    assert d.descricao.text() == "Almoço no RU"
    assert len(_todos(conn)) == 1 and d.isVisible()  # só o lançamento do teste: nada foi salvo


# --- Editar ----------------------------------------------------------------------------------


def test_editar_carrega_e_grava(app, conn):
    (i,) = _lanc(conn, valor_centavos=123456)
    d = _dialogo(conn, i)
    assert d.windowTitle() == "Lançamento" and d.botao_salvar.text() == "Salvar alterações"
    assert d.valor.text() == "1234,56" and d.descricao.text() == "Almoço"
    assert d.categoria.currentText() == "Alimentação fora" and d.metodo.atual() == "Pix"
    assert d.data.date() == QDate(2026, 10, 5) and not d.dica_categoria.isVisible()
    assert "Inter · Pix" in d.detalhes.text() and "lançado em" in d.detalhes.text()
    assert d.botao_excluir.isVisible() and not d.botao_outro.isVisible()
    assert not d._pilha_extra.currentIndex() == 1  # sem campo de parcelas
    d.valor.setText("99,90")
    d.descricao.setText("Jantar")
    d.data.setDate(QDate(2026, 10, 6))
    d.categoria.setCurrentIndex(d.categoria.findText("Mercado"))
    avisos = []
    d.gravado.connect(avisos.append)
    assert d.salvar()
    l = lanc.obter(conn, i)
    assert (l["valor_centavos"], l["descricao"], l["data"], l["categoria"]) == (-9990, "Jantar", "2026-10-06", "Mercado")
    assert avisos == ["Salvo: Jantar (R$ 99,90)"]


def test_editar_nao_sobrescreve_categoria_ao_sair_da_descricao(app, conn):
    (i,) = _lanc(conn, descricao="Padaria", categoria_id=id_cat(conn, "Mercado"))
    _lanc(conn, descricao="Jantar", categoria_id=id_cat(conn, "Alimentação fora"), data=date(2026, 10, 9))
    d = _dialogo(conn, i)
    d.descricao.setText("Jantar")
    d.descricao.editingFinished.emit()
    assert d.categoria.currentText() == "Mercado"


def test_editar_troca_saida_por_entrada_e_metodo(app, conn):
    (i,) = _lanc(conn)
    d = _dialogo(conn, i)
    d.tipo.clicar("Entrada")
    d.categoria.setCurrentIndex(d.categoria.findText("Salário"))
    d.metodo.clicar("Dinheiro")
    assert d.salvar()
    l = lanc.obter(conn, i)
    assert l["valor_centavos"] == 3250 and l["metodo"] == "dinheiro" and l["conta"] == "Carteira"
    assert l["categoria"] == "Salário"
    d = _dialogo(conn, i)  # reabre: o formulário reflete o banco
    assert d.tipo.atual() == "Entrada" and d.metodo.atual() == "Dinheiro"
    d.metodo.clicar("Pix")
    assert d.salvar()
    assert lanc.obter(conn, i)["conta"] == "Inter"


def test_editar_erro_inline_nao_grava(app, conn):
    (i,) = _lanc(conn)
    d = _dialogo(conn, i)
    d.valor.setText("x")
    assert not d.salvar() and d.erro_valor.text()
    assert lanc.obter(conn, i)["valor_centavos"] == -3250


def test_editar_parcela_so_altera_a_linha(app, conn):
    ids = _lanc(conn, metodo="cartao", valor_centavos=60000, parcelas=6, descricao="Tênis",
                conta_id=id_conta(conn, "Cartão de crédito"))
    d = _dialogo(conn, ids[2])
    assert "Parcela 3 de 6" in d.detalhes.text()
    assert not d.tipo.isEnabled() and not d.metodo.isEnabled()
    assert d._pilha_extra.currentIndex() != 1  # parcelas não mudam de quantidade
    d.valor.setText("120")
    assert d.salvar()
    valores = [lanc.obter(conn, i)["valor_centavos"] for i in ids]
    assert valores == [-10000, -10000, -12000, -10000, -10000, -10000]


# --- Excluir --------------------------------------------------------------------------------


def test_excluir_simples_pede_confirmacao(app, conn):
    (i,) = _lanc(conn)
    d = _dialogo(conn, i)
    avisos = []
    d.gravado.connect(avisos.append)
    d.botao_excluir.click()
    assert d._barra.currentIndex() == 1 and lanc.obter(conn, i)  # ainda não apagou
    d.botao_nao.click()
    assert d._barra.currentIndex() == 0 and lanc.obter(conn, i)
    d.botao_excluir.click()
    QTest.keyClick(d, Qt.Key.Key_Escape)  # Esc volta, não fecha
    assert d.isVisible() and d._barra.currentIndex() == 0
    d.botao_excluir.click()
    d.botao_sim.click()
    assert lanc.obter(conn, i) is None and not d.isVisible()
    assert avisos == ["Excluído: Almoço (R$ 32,50)"]


def _parcelas(conn):
    ids = _lanc(conn, metodo="cartao", valor_centavos=60000, parcelas=6, descricao="Tênis",
                conta_id=id_conta(conn, "Cartão de crédito"))
    (outro,) = _lanc(conn, descricao="Outro")
    return ids, outro


def test_excluir_so_esta_parcela(app, conn):
    ids, outro = _parcelas(conn)
    d = _dialogo(conn, ids[2])
    d.botao_excluir.click()
    assert d._barra.currentIndex() == 2 and d.botao_todas.text() == "Todas as 6 parcelas"
    d.botao_so_esta.click()
    restam = [i for i in ids if lanc.obter(conn, i)]
    assert restam == ids[:2] + ids[3:] and lanc.obter(conn, outro)


def test_excluir_todas_as_parcelas(app, conn):
    ids, outro = _parcelas(conn)
    d = _dialogo(conn, ids[2])
    avisos = []
    d.gravado.connect(avisos.append)
    d.botao_excluir.click()
    d.botao_todas.click()
    assert all(lanc.obter(conn, i) is None for i in ids) and lanc.obter(conn, outro)
    assert avisos == ["Excluído: Tênis (6 parcelas)"]


# --- Integração com a janela -------------------------------------------------------------------


@pytest.fixture
def janela(app, conn):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(1500, 900)
    j.show()
    QApplication.processEvents()
    yield j
    if j.dialogo is not None:
        j.dialogo.close()
    j.close()


def _itens(j):
    return [j._lista.itemAt(i).widget() for i in range(j._lista.count())]


def test_botao_e_atalho_abrem_o_dialogo(janela):
    j = janela
    j.botao_novo.click()
    assert isinstance(j.dialogo, DialogoLancamento) and j.dialogo.isVisible() and not j.dialogo.editando
    j.dialogo.reject()
    assert j.dialogo is None  # fechou
    assert j._atalho_novo.key().toString() == "Ctrl+N"
    j._atalho_novo.activated.emit()  # o offscreen não tem janela ativa pra receber a tecla
    assert j.dialogo is not None and j.dialogo.isVisible()
    j.dialogo.reject()


def test_janela_atualiza_depois_de_salvar_e_excluir(janela):
    j = janela
    assert _itens(j) == []
    j.novo_lancamento()
    _preencher(j.dialogo)
    j.dialogo.salvar()
    assert j.statusBar().currentMessage() == "Salvo: Almoço (R$ 32,50)"
    (item,) = _itens(j)
    assert item.descricao.texto() == "Almoço"
    assert j._vazio_recentes.isHidden()

    item.clicado.emit(item.id_lancamento)  # clique no item abre o modo editar
    assert j.dialogo.editando and j.dialogo.id_lancamento == item.id_lancamento
    j.dialogo.valor.setText("50")
    j.dialogo.salvar()
    assert "R$ 50,00" in _itens(j)[0].valor.text()

    j._abrir_lancamento(item.id_lancamento)
    j.dialogo.pedir_exclusao()
    j.dialogo.excluir_este()
    assert j.statusBar().currentMessage() == "Excluído: Almoço (R$ 50,00)"
    assert _itens(j) == [] and j._vazio_recentes.isVisibleTo(j)


def test_janela_atualiza_com_salvar_e_lancar_outro(janela):
    j = janela
    j.novo_lancamento()
    _preencher(j.dialogo)
    j.dialogo.salvar_e_lancar_outro()
    assert len(_itens(j)) == 1 and j.dialogo.isVisible()
    j.dialogo.reject()


def test_lancamento_inexistente_nao_abre(janela):
    j = janela
    assert j._abrir_lancamento(999) is None
