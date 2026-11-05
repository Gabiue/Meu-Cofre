"""Navegação por teclado (#11): seletor segmentado, Ctrl+1..5, lançamento sem mouse. Offscreen."""

from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import Segmentado
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.janela import ABAS, JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _janela(conn):
    for w in QApplication.topLevelWidgets():  # janelas de outros testes roubam a ativação (e os atalhos)
        w.close()
    janela = JanelaPrincipal(conn, hoje=HOJE)
    janela.resize(1500, 900)
    janela.show()
    janela.activateWindow()  # atalhos só valem em janela ativa; outros testes deixam janelas abertas
    QTest.qWaitForWindowActive(janela)
    return janela


def _dialogo(conn):
    d = DialogoLancamento(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def _tab(n=1):
    for _ in range(n):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)


def test_ctrl_numero_troca_de_aba(app, conn):
    janela = _janela(conn)
    for i, nome in enumerate(ABAS, 1):
        QTest.keyClick(janela, getattr(Qt.Key, f"Key_{i}"), Qt.KeyboardModifier.ControlModifier)
        assert janela.aba_atual == nome and janela.abas.atual() == ABAS[nome]
    janela.close()


def test_segmentado_uma_parada_de_tab_e_setas(app):
    w = QWidget()
    antes, seg, depois = Segmentado(["A", "B", "C"]), Segmentado(["X", "Y"]), Segmentado(["Z"])
    linha = QHBoxLayout(w)
    for s in (antes, seg, depois):
        linha.addWidget(s)
    w.show()
    antes.setFocus()
    QApplication.processEvents()
    escolhas = []
    antes.escolhido.connect(escolhas.append)
    QTest.keyClick(antes, Qt.Key.Key_Right)
    QTest.keyClick(antes, Qt.Key.Key_Down)
    assert antes.atual() == "C" and escolhas == ["B", "C"]
    QTest.keyClick(antes, Qt.Key.Key_Right)  # no limite não gira
    assert antes.atual() == "C" and escolhas == ["B", "C"]
    QTest.keyClick(antes, Qt.Key.Key_Left)
    assert antes.atual() == "B"
    _tab()  # uma Tab sai do grupo inteiro
    assert seg.hasFocus()
    _tab()
    assert depois.hasFocus()
    w.close()


def test_segmentado_pula_opcao_desligada(app):
    seg = Segmentado(["A", "B", "C"])
    seg.habilitar("B", False)
    seg.show()
    seg.setFocus()
    QTest.keyClick(seg, Qt.Key.Key_Right)
    assert seg.atual() == "C"


def test_filtro_dos_recentes_so_pelo_teclado(app, conn):
    for entrada, valor in ((True, 100000), (False, 3250)):
        lanc.registrar(
            conn, entrada=entrada, valor_centavos=valor, descricao="x", categoria_id=id_cat(conn, "Mercado"),
            metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5),
        )
    janela = _janela(conn)
    janela.seletor_recentes.setFocus()
    QTest.keyClick(janela.seletor_recentes, Qt.Key.Key_Right)
    assert janela.seletor_recentes.atual() == "Entradas" and janela._lista.count() == 1
    QTest.keyClick(janela.seletor_recentes, Qt.Key.Key_Right)
    assert janela.seletor_recentes.atual() == "Saídas" and janela._lista.count() == 1
    janela.close()


def test_gastei_no_mes_nao_parte_o_valor(app, conn):
    lanc.registrar(
        conn, entrada=False, valor_centavos=123456, descricao="x", categoria_id=id_cat(conn, "Mercado"),
        metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5),
    )
    janela = _janela(conn)
    apoio = janela._gastei.apoio.text()
    assert "R$ " in apoio and "R$ " not in apoio
    janela.close()


def test_ordem_de_tab_do_lancamento_e_visual(app, conn):
    d = _dialogo(conn)
    visitados = []
    for _ in range(12):
        visitados.append(QApplication.focusWidget())
        _tab()
    esperado = [d.valor, d.descricao, d.categoria, d.metodo, d.data, d.conta, d.repetir]
    assert visitados[:7] == esperado
    d.close()


def test_lancar_so_pelo_teclado(app, conn):
    janela = _janela(conn)
    QTest.keyClick(janela, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    d = janela.dialogo
    assert d is not None and d.valor.hasFocus()
    QTest.keyClicks(d.valor, "32,50")
    _tab()
    assert d.descricao.hasFocus()
    QTest.keyClicks(d.descricao, "Almoco")
    d.categoria.setCurrentIndex(1)
    d.metodo.setFocus()
    QTest.keyClick(d.metodo, Qt.Key.Key_Right)  # Pix -> próximo método
    assert d.metodo.atual() != "Pix"
    QTest.keyClick(d.metodo, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert janela.dialogo is None
    linhas = conn.execute("SELECT valor_centavos, descricao FROM lancamentos").fetchall()
    assert [(r[0], r[1]) for r in linhas] == [(-3250, "Almoco")]
    janela.close()


def test_enter_de_qualquer_campo_salva_e_validacao_continua(app, conn):
    d = _dialogo(conn)
    QTest.keyClick(d.valor, Qt.Key.Key_Return)  # vazio: erro, não fecha
    assert d.isVisible() and d.erro_valor.text() and conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    d.close()
    d = _dialogo(conn)
    d.valor.setText("10,00")
    d.descricao.setText("Café")
    d.categoria.setCurrentIndex(1)
    d.categoria.setFocus()
    QTest.keyClick(d.categoria, Qt.Key.Key_Return)  # combo fechado
    assert not d.isVisible()
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1


def test_enter_no_autocompletar_so_escolhe(app, conn):
    lanc.registrar(
        conn, entrada=False, valor_centavos=500, descricao="Padaria Central", categoria_id=id_cat(conn, "Mercado"),
        metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5),
    )
    d = _dialogo(conn)
    d.valor.setText("10,00")
    d.descricao.setFocus()
    QTest.keyClicks(d.descricao, "Padar")
    d.completar.complete()
    QApplication.processEvents()
    assert d.completar.popup().isVisible()
    QTest.keyClick(d.completar.popup(), Qt.Key.Key_Down)
    QTest.keyClick(d.completar.popup(), Qt.Key.Key_Return)
    assert d.descricao.text() == "Padaria Central"
    assert d.isVisible() and conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1
    d.close()


def test_esc_fecha_sem_gravar(app, conn):
    d = _dialogo(conn)
    d.valor.setText("10,00")
    d.descricao.setText("Café")
    QTest.keyClick(d.descricao, Qt.Key.Key_Escape)
    assert not d.isVisible()
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
