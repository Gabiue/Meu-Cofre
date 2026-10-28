"""Ponto de entrada: `uv run meu-cofre`."""

import os
import sys

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
        janela.show()
        app.processEvents()
        janela.grab().save(destino)
        conn.close()
        return 0

    janela.show()
    codigo = app.exec()
    conn.close()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
