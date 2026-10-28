"""Ponto de entrada: `uv run meu-cofre`."""

import os
import sys

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from meu_cofre import db
from meu_cofre.ui import tema
from meu_cofre.ui.janela import JanelaPrincipal


def _tamanho_screenshot() -> tuple[int, int]:
    """Lê MEU_COFRE_TAMANHO=LxA (padrão 1400x900)."""
    try:
        largura, altura = os.environ.get("MEU_COFRE_TAMANHO", "1400x900").lower().split("x")
        return int(largura), int(altura)
    except ValueError:
        return 1400, 900


def _abrir_dialogo_de_revisao(janela: JanelaPrincipal, app: QApplication, modo: str):
    """MEU_COFRE_DIALOGO=novo|cartao|editar:<id>|excluir:<id>: abre o diálogo já preparado (screenshots)."""
    nome, _, id_texto = modo.partition(":")
    dialogo = janela.novo_lancamento() if nome in ("novo", "cartao") else janela._abrir_lancamento(int(id_texto))
    if dialogo is None:
        return None
    if nome == "cartao":
        dialogo.metodo.clicar("Cartão")
        dialogo.valor.setText("600")
        dialogo.descricao.setText("Tênis")
        dialogo.parcelas.setCurrentIndex(5)
        dialogo._sugerir_categoria()
    elif nome == "excluir":
        dialogo.pedir_exclusao()
    app.processEvents()
    return dialogo


def _compor_screenshot(janela: JanelaPrincipal, dialogo) -> QPixmap:
    """Janela principal ao fundo, escurecida, com o diálogo centralizado por cima."""
    fundo = janela.grab()
    if dialogo is None:
        return fundo
    pintor = QPainter(fundo)
    pintor.fillRect(fundo.rect(), QColor(0, 0, 0, 120))
    frente = dialogo.grab()
    pintor.drawPixmap(QPoint((fundo.width() - frente.width()) // 2, (fundo.height() - frente.height()) // 2), frente)
    pintor.end()
    return fundo


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("meu-cofre")
    tema.aplicar_tema(app)

    conn = db.conectar()  # respeita MEU_COFRE_DB
    janela = JanelaPrincipal(conn)

    destino = os.environ.get("MEU_COFRE_SCREENSHOT")
    if destino:  # modo revisão: salva um PNG e sai
        largura, altura = _tamanho_screenshot()
        janela.resize(largura, altura)
        painel = {"fluxo": "Fluxo", "rosca": "Para onde foi"}.get(os.environ.get("MEU_COFRE_PAINEL", ""))
        if painel:
            janela.mostrar_painel(painel)
        janela.show()
        app.processEvents()
        modo_dialogo = os.environ.get("MEU_COFRE_DIALOGO")
        dialogo = _abrir_dialogo_de_revisao(janela, app, modo_dialogo) if modo_dialogo else None
        _compor_screenshot(janela, dialogo).save(destino)
        conn.close()
        return 0

    janela.show()
    codigo = app.exec()
    conn.close()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
