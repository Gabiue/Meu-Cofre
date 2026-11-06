"""Diálogo do relatório mensal: mês anterior, seletor, "Sobre mim" salvo, prévia editável e cópia. Offscreen."""

from datetime import date

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from meu_cofre import db
from meu_cofre.ui import tema
from meu_cofre.ui.dialogo_relatorio import PREF_CONTEXTO, DialogoRelatorio
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _dialogo(conn, hoje=HOJE):
    d = DialogoRelatorio(conn, hoje)
    d.show()
    QApplication.processEvents()
    return d


def test_abre_no_mes_anterior(app, conn):
    d = _dialogo(conn)
    assert d.mes == (2026, 9) and d.rotulo_mes.text() == "Setembro de 2026"
    assert d.previa.toPlainText().startswith("# Relatório financeiro: Setembro de 2026")
    assert d.botao_seguinte.isEnabled()
    assert _dialogo(conn, date(2026, 1, 3)).mes == (2025, 12)


def test_seletor_troca_o_mes_e_marca_o_atual_como_parcial(app, conn):
    d = _dialogo(conn)
    d.botao_seguinte.click()
    assert d.mes == (2026, 10) and d.rotulo_mes.text() == "Outubro de 2026 (parcial)"
    assert "(parcial, até 20/10)" in d.previa.toPlainText()
    assert not d.botao_seguinte.isEnabled()
    d._mudar_mes(+1)  # não passa do mês atual
    assert d.mes == (2026, 10)
    d.botao_anterior.click()
    d.botao_anterior.click()
    assert d.mes == (2026, 8) and "Agosto de 2026" in d.previa.toPlainText()


def test_sobre_mim_fica_salvo_e_entra_no_relatorio(app, conn):
    d = _dialogo(conn)
    d.contexto.setPlainText("Moro com a família.")
    assert db.ler_preferencia(conn, PREF_CONTEXTO) == "Moro com a família."
    assert not d.aviso_contexto.isHidden() and "Moro com a família." not in d.previa.toPlainText()
    d.botao_atualizar.click()
    assert d.aviso_contexto.isHidden() and "## Sobre mim\nMoro com a família." in d.previa.toPlainText()
    assert _dialogo(conn).contexto.toPlainText() == "Moro com a família."  # reabre com o que ficou salvo
    d.contexto.setPlainText("")
    assert db.ler_preferencia(conn, PREF_CONTEXTO) is None


def test_copiar_leva_a_previa_com_as_edicoes(app, conn):
    d = _dialogo(conn)
    d.previa.setPlainText(d.previa.toPlainText().replace("## Pedido", "## Pedido (editado)"))
    d.botao_copiar.click()
    copiado = QGuiApplication.clipboard().text()
    assert copiado == d.previa.toPlainText() and "## Pedido (editado)" in copiado
    assert d.copiado.text() == "Copiado ✓"


def test_atualizar_descarta_as_edicoes(app, conn):
    d = _dialogo(conn)
    d.previa.setPlainText("só isto")
    d.botao_atualizar.click()
    assert d.previa.toPlainText().startswith("# Relatório financeiro")


def test_botao_e_atalho_na_janela_principal(app, conn):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.show()
    assert j.botao_relatorio.text() == "Relatório do mês…" and "Ctrl+R" in j.botao_relatorio.toolTip()
    assert j._atalho_relatorio.key().toString() == "Ctrl+R"
    dialogo = j.abrir_relatorio()
    assert isinstance(dialogo, DialogoRelatorio) and dialogo.isModal() and dialogo.mes == (2026, 9)
    dialogo.reject()
