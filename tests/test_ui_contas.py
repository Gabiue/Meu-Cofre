"""Janela "Contas e cartões" (F4) e o seletor "Qual cartão" no lançamento. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from meu_cofre import cadastros
from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import RotuloElidido
from meu_cofre.ui.dialogo_contas import DialogoContas
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _contas(conn):
    d = DialogoContas(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def _lancamento(conn, id_lancamento=None):
    d = DialogoLancamento(conn, id_lancamento, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def _compra(conn, conta_id, valor=10000):
    return lanc.registrar(conn, entrada=False, valor_centavos=valor, descricao="Tênis",
                          categoria_id=id_cat(conn, "Compras pessoais"), metodo="cartao",
                          conta_id=conta_id, data=date(2026, 10, 5))[0]


def _textos(widget):
    return [l.text() for l in widget.findChildren(QLabel)]


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


@pytest.fixture
def mudancas():
    return []


# --- Janela de contas ---------------------------------------------------------------


def test_lista_contas_com_saldo_de_hoje(app, conn):
    inter = id_conta(conn, "Inter")
    cadastros.definir_saldo_inicial(conn, inter, 100000)
    d = _contas(conn)
    assert set(d._saldos) == {inter, id_conta(conn, "Caixa"), id_conta(conn, "Carteira")}
    assert d._saldos[inter].text() == "1.000,00"
    assert d._saldos_hoje[inter].text() == "Saldo de hoje R$ 1.000,00"
    assert d.isModal() and not d.botao_concluir.isDefault()


def test_salvar_saldo_grava_e_avisa(app, conn, mudancas):
    inter = id_conta(conn, "Inter")
    d = _contas(conn)
    d.alterado.connect(mudancas.append)
    assert d.salvar_saldo(inter, "1.234,56")
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE id = ?", (inter,)).fetchone()[0] == 123456
    assert d._saldos[inter].text() == "1.234,56"
    assert d._saldos_hoje[inter].text() == "Saldo de hoje R$ 1.234,56"
    assert mudancas == ["Saldo inicial de Inter: R$ 1.234,56"]
    assert d.salvar_saldo(inter, "1.234,56") and len(mudancas) == 1  # igual: não grava nem avisa de novo
    assert d.salvar_saldo(inter, "") and d._saldos[inter].text() == "0,00"  # vazio vale zero


def test_saldo_invalido_mostra_erro_no_campo(app, conn, mudancas):
    inter = id_conta(conn, "Inter")
    d = _contas(conn)
    d.alterado.connect(mudancas.append)
    assert not d.salvar_saldo(inter, "abc")
    assert d._erros_saldo[inter].isVisible() and "inválido" in d._erros_saldo[inter].text()
    assert d._saldos[inter].property("erro") is True
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE id = ?", (inter,)).fetchone()[0] == 0
    d._saldos[inter].setText("10")  # digitar de novo limpa o erro
    assert not d._erros_saldo[inter].isVisible() and mudancas == []


def test_adicionar_cartao(app, conn, mudancas):
    d = _contas(conn)
    d.alterado.connect(mudancas.append)
    assert not d._form.isVisible()
    d.botao_adicionar.click()
    assert d._form.isVisible() and not d.botao_adicionar.isVisible() and d.novo_nome.hasFocus()
    d.novo_nome.setText("Caixa Visa")
    d.novo_banco.setText("Caixa")
    d.novo_fechamento.setCurrentIndex(d.novo_fechamento.findData(5))
    d.novo_vencimento.setCurrentIndex(d.novo_vencimento.findData(12))
    assert d.adicionar_cartao()
    c = conn.execute("SELECT * FROM contas WHERE nome = 'Caixa Visa'").fetchone()
    assert (c["tipo"], c["banco"], c["dia_fechamento"], c["dia_vencimento"]) == ("cartao", "Caixa", 5, 12)
    assert not d._form.isVisible() and d.botao_adicionar.isVisible()
    assert "Caixa · fecha dia 5 · vence dia 12" in _textos(d)
    assert mudancas == ["Cartão adicionado: Caixa Visa"]


def test_adicionar_cartao_erros_inline(app, conn, mudancas):
    d = _contas(conn)
    d.alterado.connect(mudancas.append)
    d.iniciar_adicao()
    assert not d.adicionar_cartao()
    assert "Diga o nome" in d.erro_novo.text() and d.novo_nome.property("erro") is True
    d.novo_nome.setText("cartão de crédito")
    assert not d.adicionar_cartao() and "Já existe" in d.erro_novo.text()
    d.novo_nome.setText("Outro")
    assert d.erro_novo.text() == "" and d.novo_nome.property("erro") is False
    d.cancelar_adicao()
    assert not d._form.isVisible() and mudancas == []
    assert conn.execute("SELECT COUNT(*) FROM contas WHERE tipo = 'cartao'").fetchone()[0] == 1


def test_renomear_cartao(app, conn, mudancas):
    cartao = id_conta(conn, "Cartão de crédito")
    d = _contas(conn)
    d.alterado.connect(mudancas.append)
    d.pedir_renomear(cartao)
    assert d.campo_renomear.text() == "Cartão de crédito"
    d.campo_renomear.setText("Inter")  # nome de outra conta
    assert not d.salvar_renome() and "Já existe" in d.erro_renomear.text()
    d.campo_renomear.setText("Caixa Elo")
    assert d.salvar_renome()
    assert conn.execute("SELECT nome FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == "Caixa Elo"
    assert "Caixa Elo" in _textos(d) and mudancas == ["Cartão renomeado: Caixa Elo"]


def test_desativar_com_confirmacao_inline_e_reativar(app, conn, mudancas):
    elo = id_conta(conn, "Cartão de crédito")
    cadastros.renomear_conta(conn, elo, "Caixa Elo")
    cadastros.criar_cartao(conn, "Caixa Visa")
    _compra(conn, elo, 5000)
    d = _contas(conn)
    d.alterado.connect(mudancas.append)

    d.pedir_desativar(elo)
    assert "Desativar Caixa Elo? Os lançamentos antigos continuam." in _textos(d)
    d.botao_nao.click()
    assert conn.execute("SELECT ativa FROM contas WHERE id = ?", (elo,)).fetchone()[0] == 1 and mudancas == []

    d.pedir_desativar(elo)
    d.botao_sim.click()
    assert conn.execute("SELECT ativa FROM contas WHERE id = ?", (elo,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1  # histórico fica
    assert mudancas == ["Cartão desativado: Caixa Elo"]

    # desativados ficam recolhidos até pedir
    assert d.botao_desativados.text() == "Mostrar cartões desativados (1)"
    assert "desativado" not in _textos(d)
    d.botao_desativados.click()
    assert "desativado" in _textos(d) and d.botao_desativados.text() == "Esconder cartões desativados"
    assert "R$ 50,00" in _textos(d)  # a fatura em aberto continua aparecendo
    d.reativar(elo)
    assert conn.execute("SELECT ativa FROM contas WHERE id = ?", (elo,)).fetchone()[0] == 1
    assert not d.botao_desativados.isVisible()
    assert mudancas[-1] == "Cartão reativado: Caixa Elo"


def test_sem_cartao_ativo_mostra_aviso(app, conn):
    cadastros.desativar_conta(conn, id_conta(conn, "Cartão de crédito"))
    d = _contas(conn)
    assert any("Nenhum cartão ativo" in t for t in _textos(d))


def test_botao_contas_na_janela_principal_recarrega_a_tela(app, conn):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.show()
    assert j.botao_contas.text() == "Contas"
    j.botao_contas.click()
    QApplication.processEvents()
    d = j.dialogo_contas
    assert isinstance(d, DialogoContas)
    assert j._em_conta.valor.text() == "R$ 0,00"
    d.salvar_saldo(id_conta(conn, "Inter"), "2.000,00")
    assert j._em_conta.valor.text() == "R$ 2.000,00"
    assert "Saldo inicial de Inter" in j.statusBar().currentMessage()
    d.accept()
    QApplication.processEvents()
    assert j.dialogo_contas is None


# --- Qual cartão no lançamento ---------------------------------------------------------


@pytest.fixture
def dois_cartoes(conn):
    elo = id_conta(conn, "Cartão de crédito")
    cadastros.renomear_conta(conn, elo, "Caixa Elo")
    return elo, cadastros.criar_cartao(conn, "Caixa Visa")


def test_um_so_cartao_mostra_o_nome_no_texto(app, conn):
    d = _lancamento(conn)
    d.metodo.clicar("Cartão")
    assert d._bloco_cartao.isHidden()
    assert d.info_destino.text() == "Vai pro Cartão de crédito, na data da compra"
    cadastros.renomear_conta(conn, id_conta(conn, "Cartão de crédito"), "Caixa Visa")
    d = _lancamento(conn)
    d.metodo.clicar("Cartão")
    assert d.info_destino.text() == "Vai pro Caixa Visa, na data da compra"


def test_varios_cartoes_mostram_qual_cartao_com_o_ultimo_usado(app, conn, dois_cartoes):
    elo, visa = dois_cartoes
    _compra(conn, visa)
    d = _lancamento(conn)
    assert d._bloco_cartao.isHidden()  # só aparece com método Cartão
    d.metodo.clicar("Cartão")
    assert d._bloco_cartao.isVisible()
    assert [d.cartao.itemText(i) for i in range(d.cartao.count())] == ["Caixa Elo", "Caixa Visa"]
    assert d.cartao.currentData() == visa  # padrão: o último usado
    d.metodo.clicar("Pix")
    assert d._bloco_cartao.isHidden()


def test_salvar_no_cartao_escolhido(app, conn, dois_cartoes):
    elo, visa = dois_cartoes
    d = _lancamento(conn)
    assert d.cartao.currentData() == elo  # nunca usou: o primeiro
    d.metodo.clicar("Cartão")
    d.valor.setText("90")
    d.descricao.setText("TV")
    d.cartao.setCurrentIndex(d.cartao.findData(visa))
    d.parcelas.setCurrentIndex(2)
    assert d.salvar()
    contas = {r[0] for r in conn.execute("SELECT conta_id FROM lancamentos")}
    assert contas == {visa}
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 3


def test_sem_cartao_ativo_o_metodo_cartao_fica_desabilitado(app, conn):
    cadastros.desativar_conta(conn, id_conta(conn, "Cartão de crédito"))
    d = _lancamento(conn)
    botao = d.metodo._botoes["Cartão"]
    assert not botao.isEnabled() and "Adicione um em Contas" in botao.toolTip()
    cadastros.criar_cartao(conn, "Novo")
    d = _lancamento(conn)
    assert d.metodo._botoes["Cartão"].isEnabled()


def test_editar_compra_mantem_ou_troca_o_cartao(app, conn, dois_cartoes):
    elo, visa = dois_cartoes
    id_ = _compra(conn, elo)
    d = _lancamento(conn, id_)
    assert d._bloco_cartao.isVisible() and d.cartao.currentData() == elo
    d.cartao.setCurrentIndex(d.cartao.findData(visa))
    assert d.salvar()
    assert lanc.obter(conn, id_)["conta_id"] == visa


def test_editar_compra_de_cartao_desativado_nao_move_a_compra(app, conn, dois_cartoes):
    elo, visa = dois_cartoes
    id_ = _compra(conn, elo)
    cadastros.desativar_conta(conn, elo)
    d = _lancamento(conn, id_)
    assert d.cartao.currentData() == elo and "desativado" in d.cartao.currentText()
    d.descricao.setText("Tênis novo")
    assert d.salvar()
    l = lanc.obter(conn, id_)
    assert (l["conta_id"], l["descricao"]) == (elo, "Tênis novo")


def test_recentes_mostram_o_nome_do_cartao(app, conn, dois_cartoes):
    _compra(conn, dois_cartoes[1])
    j = JanelaPrincipal(conn, hoje=HOJE)
    assert any(r.texto() == "Compras pessoais · Caixa Visa" for r in j.findChildren(RotuloElidido))
