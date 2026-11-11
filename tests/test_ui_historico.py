"""Histórico de operações por ativo (aba Investimentos) com Editar e Excluir inline, e as contas de investimento na
janela de contas. Offscreen."""

from datetime import date

import pytest
from conftest import id_conta
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from meu_cofre import cadastros
from meu_cofre import investimentos as inv
from meu_cofre import metas
from meu_cofre import renda_variavel as rv
from meu_cofre.ui import tema
from meu_cofre.ui.aba_investimentos import AbaInvestimentos
from meu_cofre.ui.dialogo_contas import DialogoContas

HOJE = date(2026, 10, 30)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _textos(widget):
    return [l.toolTip() or l.text() for l in widget.findChildren(QLabel)]  # o elidido guarda o texto no tooltip


def _aba(conn):
    a = AbaInvestimentos(conn, lambda: HOJE)
    a.resize(1400, 1400)
    a.show()
    QApplication.processEvents()
    return a


def test_historico_de_acao_edita_e_atualiza_a_aba(app, conn):
    ativo = inv.criar(conn, HOJE, nome="Petrobras", tipo="acao", ticker="PETR4")
    inter = id_conta(conn, "Inter")
    rv.comprar(conn, ativo, date(2026, 3, 2), 100, 3000, conta_id=inter)
    rv.registrar_provento(conn, ativo, "dividendo", date(2026, 4, 1), 500)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    cartao = aba.cartao_da_acao(ativo)
    assert cartao.lista_historico.isHidden()
    cartao.botao_historico.trigger()
    assert not cartao.lista_historico.isHidden()
    textos = _textos(cartao.lista_historico)
    assert any(t.startswith("Dividendo") for t in textos) and any("Compra · 100" in t for t in textos)

    cartao.lista_historico.abrir_edicao(conn.execute("SELECT id FROM operacoes_rv WHERE tipo = 'compra'").fetchone()[0])
    form = cartao.lista_historico.form
    form.quantidade.setText("50")
    assert form.confirmar()
    assert avisos == ["Operação corrigida"]
    assert rv.posicao(conn, ativo, HOJE)["quantidade"] == 50
    cartao = aba.cartao_da_acao(ativo)  # a aba recarregou e o histórico continua aberto
    assert not cartao.lista_historico.isHidden()
    assert any("Compra · 50" in t for t in _textos(cartao.lista_historico))
    cartao.botao_historico.trigger()
    assert aba.cartao_da_acao(ativo).lista_historico.isHidden()


def test_excluir_pede_confirmacao_e_apaga_os_dois_lados(app, conn):
    ativo = inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="pre", taxa=10.0)
    inv.aportar(conn, ativo, 100_000, id_conta(conn, "Inter"), date(2026, 9, 1))
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.cartao_do_ativo(ativo).botao_historico.trigger()
    hist = aba.cartao_do_ativo(ativo).lista_historico
    hist.pedir_exclusao(hist._itens[0]["id"])
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE transferencia_id IS NOT NULL").fetchone()[0] == 2  # ainda não apagou
    hist.botao_nao.click()
    assert hist._excluindo is None
    hist.pedir_exclusao(hist._itens[0]["id"])
    hist.botao_sim.click()
    assert avisos == ["Operação excluída"]
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE transferencia_id IS NOT NULL").fetchone()[0] == 0
    assert aba.cartao_do_ativo(ativo).lista_historico._itens == []


def test_erro_de_regra_aparece_no_formulario(app, conn):
    ativo = inv.criar(conn, HOJE, nome="Vale", tipo="acao", ticker="VALE3")
    compra = rv.comprar(conn, ativo, date(2026, 3, 2), 100, 3000)
    rv.vender(conn, ativo, date(2026, 5, 2), 80, 4000)
    aba = _aba(conn)
    aba.cartao_da_acao(ativo).botao_historico.trigger()
    hist = aba.cartao_da_acao(ativo).lista_historico
    hist.abrir_edicao(compra)
    hist.form.quantidade.setText("10")
    hist.form.confirmar()
    assert "vendendo mais cotas" in hist.form.erro.text()
    assert rv.posicao(conn, ativo, HOJE)["quantidade"] == 20
    hist.form.quantidade.setText("")
    assert not hist.form.confirmar() and "quantidade" in hist.form.erro.text()
    hist.pedir_exclusao(compra)
    hist.botao_sim.click()
    assert "vendendo mais cotas" in hist.erro.text()


def test_caixinhas_nao_tem_historico(app, conn):
    metas.conta_caixinhas(conn)
    conn.execute("INSERT INTO ativos (nome, tipo, indexador, taxa, caixinhas) VALUES ('Caixinhas (metas)', 'renda_fixa', 'cdi', 100, 1)")
    conn.commit()
    aba = _aba(conn)
    cartao = next(c for c in aba.cartoes() if c.posicao.ativo.caixinhas)
    assert not cartao.botao_historico.isVisible()


def test_janela_de_contas_mostra_caixinhas_e_investimentos_so_leitura(app, conn):
    inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="pre", taxa=10.0)
    ativo = inv.obter(conn, conn.execute("SELECT id FROM ativos").fetchone()[0]).id
    inv.aportar(conn, ativo, 250_000, id_conta(conn, "Inter"), date(2026, 9, 1))
    d = DialogoContas(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    textos = [l.text() for l in d._cartao_investimentos.findChildren(QLabel)]
    assert "Investimentos" in textos and "R$ 2.500,00" in textos
    assert not d._cartao_investimentos.findChildren(QPushButton)
    assert cadastros.saldo_da_conta(conn, id_conta(conn, "Investimentos"), HOJE) == 250_000
