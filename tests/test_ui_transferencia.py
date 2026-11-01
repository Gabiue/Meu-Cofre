"""Modo Transferência do diálogo de lançamento e a linha nos recentes (issue #1). Offscreen."""

from datetime import date

import pytest
from conftest import id_conta
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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


def _transferencia(conn, valor=10_000, descricao="Saque"):
    tid = lanc.transferir(
        conn, origem_id=id_conta(conn, "Inter"), destino_id=id_conta(conn, "Carteira"),
        valor_centavos=valor, data=date(2026, 10, 10), descricao=descricao,
    )
    return conn.execute(
        "SELECT id FROM lancamentos WHERE transferencia_id = ? AND valor_centavos < 0", (tid,)
    ).fetchone()["id"]


def test_modo_transferencia_mostra_so_os_campos_dela(app, conn):
    d = _dialogo(conn)
    assert [d.tipo._botoes[o].text() for o in d.tipo._botoes] == ["Saída", "Entrada", "Transferência"]
    d.tipo.clicar("Transferência")
    assert d.transferencia
    assert d._bloco_transferencia.isVisibleTo(d) and d.data.isVisibleTo(d) and d.descricao.isVisibleTo(d)
    assert not any(w.isVisibleTo(d) for w in (d._bloco_categoria, d._bloco_metodo, d._bloco_repetir))
    assert (d.de.currentText(), d.para.currentText()) == ("Inter", "Carteira")
    assert d.descricao.placeholderText() == "Transferência"
    d.tipo.clicar("Saída")  # volta ao normal
    assert d._bloco_categoria.isVisibleTo(d) and d._bloco_metodo.isVisibleTo(d) and not d._bloco_transferencia.isVisibleTo(d)


def test_mesma_conta_da_erro_inline_e_nao_grava(app, conn):
    d = _dialogo(conn)
    d.tipo.clicar("Transferência")
    d.valor.setText("50")
    d.para.setCurrentIndex(d.de.currentIndex())
    assert d.salvar() is False
    assert "diferente" in d.erro_para.text()
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    d.para.setCurrentIndex(1 - d.de.currentIndex())
    assert d.erro_para.text() == ""


def test_salvar_cria_os_dois_lados_com_descricao_padrao(app, conn):
    d = _dialogo(conn)
    d.tipo.clicar("Transferência")
    d.valor.setText("120,50")
    mensagens = []
    d.gravado.connect(mensagens.append)
    assert d.salvar() is True
    assert mensagens == ["Transferido: Inter → Carteira (R$ 120,50)"]
    lados = conn.execute("SELECT * FROM lancamentos ORDER BY valor_centavos").fetchall()
    assert [(l["conta_id"], l["valor_centavos"], l["descricao"], l["data"]) for l in lados] == [
        (id_conta(conn, "Inter"), -12_050, "Transferência", "2026-10-20"),
        (id_conta(conn, "Carteira"), 12_050, "Transferência", "2026-10-20"),
    ]


def test_valor_vazio_da_erro_e_enter_e_ctrl_enter_valem(app, conn):
    d = _dialogo(conn)
    d.tipo.clicar("Transferência")
    QTest.keyClick(d.valor, Qt.Key.Key_Return)  # Enter sem valor: erro, não fecha
    assert d.erro_valor.text() and d.isVisible()
    d.valor.setText("10")
    QTest.keyClick(d, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # salvar e lançar outro
    assert d.isVisible() and d.valor.text() == "" and d.aviso_salvo.text().startswith("✓ Transferido")
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2
    QTest.keyClick(d, Qt.Key.Key_Escape)
    assert not d.isVisible()


def test_editar_atualiza_os_dois_lados(app, conn):
    lado = _transferencia(conn)
    d = _dialogo(conn, lado)
    assert d.editando and d.transferencia and not d.tipo.isEnabled()
    assert (d.valor.text(), d.descricao.text(), d.de.currentText(), d.para.currentText()) == ("100,00", "Saque", "Inter", "Carteira")
    d.valor.setText("80")
    d.descricao.setText("Saque menor")
    assert d.salvar()
    valores = [(r["valor_centavos"], r["descricao"]) for r in conn.execute("SELECT * FROM lancamentos ORDER BY valor_centavos")]
    assert valores == [(-8_000, "Saque menor"), (8_000, "Saque menor")]


def test_abrir_pelo_lado_da_entrada_tambem_edita_a_transferencia(app, conn):
    _transferencia(conn)
    entrada = conn.execute("SELECT id FROM lancamentos WHERE valor_centavos > 0").fetchone()["id"]
    d = _dialogo(conn, entrada)
    assert d.transferencia and (d.de.currentText(), d.para.currentText()) == ("Inter", "Carteira")


def test_excluir_apaga_os_dois_lados(app, conn):
    d = _dialogo(conn, _transferencia(conn))
    d.pedir_exclusao()
    d.excluir_este()
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0


def test_lancamento_comum_nao_vira_transferencia(app, conn):
    lanc.registrar(
        conn, entrada=False, valor_centavos=500, descricao="Pão", categoria_id=None, metodo="pix",
        conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 1),
    )
    d = _dialogo(conn, conn.execute("SELECT id FROM lancamentos").fetchone()["id"])
    assert not d.tipo._botoes["Transferência"].isEnabled() and not d.transferencia


def test_recentes_mostram_a_transferencia_uma_vez(app, conn):
    _transferencia(conn, 25_000)
    j = JanelaPrincipal(conn, hoje=HOJE)
    itens = [j._lista.itemAt(i).widget() for i in range(j._lista.count())]
    assert len(itens) == 1
    (item,) = itens
    assert item.detalhe.texto() == "Inter → Carteira" and item.valor.text() == "R$ 250,00"
    assert item.findChild(type(item.valor), "ItemMarcador").text() == "⇄"
    j.seletor_recentes.clicar("Saídas")
    assert j._lista.count() == 0
    j.seletor_recentes.clicar("Entradas")
    assert j._lista.count() == 0
