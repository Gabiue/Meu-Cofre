"""Janela "Cotações…" da aba Investimentos: o token grátis da brapi (brapi.dev), que traz histórico de preços e proventos.

Salvar ou remover grava a preferência e emite `alterado`; quem abre força uma nova busca das cotações.
Se o token vem da variável de ambiente, a janela só avisa (não dá pra editar por aqui).
"""

import sqlite3

from PySide6.QtCore import QObject, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from meu_cofre import cotacoes, db
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario

LARGURA = 520


class _Sinais(QObject):
    pronto = Signal(object)  # None = token ok, ou a mensagem de erro


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    rotulo.setWordWrap(True)
    return rotulo


class DialogoCotacoes(QDialog):
    alterado = Signal()

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._do_ambiente = bool(cotacoes.token_brapi_do_ambiente())
        self._sinais = _Sinais(self)
        self._sinais.pronto.connect(self._teste_terminou)
        # pool da própria janela, como no atualizador: ao ser apagada ela espera o teste, e os sinais nunca morrem antes
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Cotações")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()

    def _montar(self) -> None:
        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(m, m, m, m)
        coluna.setSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Cotações", "DialogoTitulo"))
        coluna.addWidget(_rotulo(
            "O token gratuito da brapi (brapi.dev) traz o histórico de preços e os proventos de ações e FIIs. "
            "Só o ticker sai do seu computador, nunca quantidades ou valores.", "DialogoDetalhe"))
        coluna.addSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Token da brapi", "CampoRotulo"))
        linha = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Cole o token aqui")
        self.botao_mostrar = QPushButton("Mostrar")
        self.botao_mostrar.setObjectName("BotaoLink")
        self.botao_mostrar.setCheckable(True)
        self.botao_mostrar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_mostrar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_mostrar.toggled.connect(
            lambda ligado: self.token.setEchoMode(QLineEdit.EchoMode.Normal if ligado else QLineEdit.EchoMode.Password)
        )
        linha.addWidget(self.token, 1)
        linha.addWidget(self.botao_mostrar)
        coluna.addLayout(linha)
        self.resultado = _rotulo("", "CampoDica")
        coluna.addWidget(self.resultado)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO + 4)
        self.botao_testar = BotaoSecundario("Testar")
        self.botao_remover = BotaoSecundario("Remover token")
        self.botao_fechar = BotaoSecundario("Fechar")
        self.botao_salvar = BotaoPrimario("Salvar")
        for b in (self.botao_testar, self.botao_remover, self.botao_fechar, self.botao_salvar):
            b.setAutoDefault(False)
        botoes.addWidget(self.botao_testar)
        botoes.addWidget(self.botao_remover)
        botoes.addStretch(1)
        botoes.addWidget(self.botao_fechar)
        botoes.addWidget(self.botao_salvar)
        coluna.addSpacing(tema.ESPACO)
        coluna.addLayout(botoes)
        self.botao_testar.clicked.connect(self.testar)
        self.botao_remover.clicked.connect(self.remover)
        self.botao_salvar.clicked.connect(self.salvar)
        self.botao_fechar.clicked.connect(self.reject)

        salvo = db.ler_preferencia(self.conn, cotacoes.PREF_TOKEN) or ""
        if self._do_ambiente:
            self.token.setText(cotacoes.token_brapi(self.conn))
            for w in (self.token, self.botao_remover, self.botao_salvar):
                w.setEnabled(False)
            self.resultado.setText(f"O token vem da variável de ambiente {cotacoes.VAR_BRAPI}; por aqui não dá pra mudar.")
        else:
            self.token.setText(salvo)
            self.botao_remover.setEnabled(bool(salvo))
            self.token.setFocus()

    def testar(self) -> None:
        token = self.token.text().strip()
        if not token:
            self.resultado.setText("Cole o token pra testar.")
            return
        self.botao_testar.setEnabled(False)
        self.resultado.setText("testando…")
        # a rede fica fora da thread principal; o resultado volta pelo sinal
        self._pool.start(lambda: self._sinais.pronto.emit(cotacoes.testar_token(token)))

    def _teste_terminou(self, erro: str | None) -> None:
        self.botao_testar.setEnabled(True)
        self.resultado.setText("Token funcionando." if erro is None else erro)

    def salvar(self) -> None:
        token = self.token.text().strip()
        if not token:
            self.resultado.setText("Cole o token ou use Remover token.")
            return
        db.gravar_preferencia(self.conn, cotacoes.PREF_TOKEN, token)
        self.alterado.emit()
        self.accept()

    def remover(self) -> None:
        db.gravar_preferencia(self.conn, cotacoes.PREF_TOKEN, None)
        self.alterado.emit()
        self.accept()
