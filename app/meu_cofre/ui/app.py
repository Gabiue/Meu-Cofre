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
    """MEU_COFRE_DIALOGO=novo|cartao|repetir|contas|editar:<id>|excluir:<id>|recorrente:<id>|pagar: abre o diálogo
    já preparado (screenshots). `pagar` não é diálogo: abre a confirmação de pagamento da aba Cartões."""
    nome, _, id_texto = modo.partition(":")
    if nome == "pagar":
        janela.aba_cartoes.pedir_pagamento_da_proxima()
        for _ in range(3):  # a lista de faturas é refeita: dá tempo do layout assentar
            app.processEvents()
        return None
    if nome == "contas":
        dialogo = janela.abrir_contas()
        dialogo.mostrar_desativados(True)
        for _ in range(3):  # a janela se ajusta à lista em etapas (estilo, depois tamanho)
            app.processEvents()
        dialogo.resize(dialogo.width(), janela.height() - 60)  # a tela offscreen é baixa: dá espaço pra mostrar tudo
        app.processEvents()
        return dialogo
    if nome == "recorrente":
        dialogo = janela.aba_recorrentes.editar(int(id_texto))
        app.processEvents()
        return dialogo
    dialogo = janela.novo_lancamento() if nome in ("novo", "cartao", "repetir") else janela._abrir_lancamento(int(id_texto))
    if dialogo is None:
        return None
    if nome == "cartao":
        dialogo.metodo.clicar("Cartão")
        dialogo.valor.setText("600")
        dialogo.descricao.setText("Tênis")
        dialogo.parcelas.setCurrentIndex(5)
        dialogo._sugerir_categoria()
    elif nome == "repetir":
        dialogo.valor.setText("39,90")
        dialogo.descricao.setText("Streaming de vídeo")
        dialogo.metodo.clicar("Cartão")
        dialogo._sugerir_categoria()
        dialogo.repetir.setChecked(True)
        dialogo.assinatura.setChecked(True)
        dialogo.resize(dialogo.width(), janela.height() - 60)  # a tela offscreen é baixa: dá espaço pra mostrar tudo
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
        janela.mostrar_aba(os.environ.get("MEU_COFRE_ABA", "inicio"))
        painel = {"fluxo": "Fluxo", "rosca": "Para onde foi", "proximos": "Próximos meses"}.get(
            os.environ.get("MEU_COFRE_PAINEL", "")
        )
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
