"""Janela "Meta de aporte…" da aba Investimentos: quanto você quer aportar por mês.

Salvar ou remover grava a meta (`investimentos.definir_meta_aporte`) e emite `alterado`.
"""

import sqlite3

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from meu_cofre import investimentos
from meu_cofre.lancamentos import formatar_brl, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario

LARGURA = 480


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    rotulo.setWordWrap(True)
    return rotulo


class DialogoMetaAporte(QDialog):
    alterado = Signal()

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Meta de aporte")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()

    def _montar(self) -> None:
        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(m, m, m, m)
        coluna.setSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Meta de aporte mensal", "DialogoTitulo"))
        margem = formatar_brl(investimentos.MARGEM_META).removesuffix(",00")
        coluna.addWidget(_rotulo(
            f"Quanto você quer aportar por mês. Cada aporte do mês abate da meta, o resgate desconta, e chegar a "
            f"até {margem} dela (pra mais ou pra menos) conta como meta batida.", "DialogoDetalhe"))
        coluna.addSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Valor por mês (R$)", "CampoRotulo"))
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        atual = investimentos.meta_aporte(self.conn)
        if atual:
            self.valor.setText(formatar_brl(atual).removeprefix("R$ ").replace(".", ""))
        coluna.addWidget(self.valor)
        self.erro = _rotulo("", "CampoErro")
        self.erro.setFixedHeight(22)
        coluna.addWidget(self.erro)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO + 4)
        self.botao_remover = BotaoSecundario("Remover meta")
        self.botao_fechar = BotaoSecundario("Fechar")
        self.botao_salvar = BotaoPrimario("Salvar")
        for b in (self.botao_remover, self.botao_fechar, self.botao_salvar):
            b.setAutoDefault(False)
        self.botao_remover.setEnabled(bool(atual))
        botoes.addWidget(self.botao_remover)
        botoes.addStretch(1)
        botoes.addWidget(self.botao_fechar)
        botoes.addWidget(self.botao_salvar)
        coluna.addLayout(botoes)
        self.botao_remover.clicked.connect(self.remover)
        self.botao_fechar.clicked.connect(self.reject)
        self.botao_salvar.clicked.connect(self.salvar)
        self.valor.returnPressed.connect(self.salvar)
        self.valor.textChanged.connect(lambda _t: self.erro.setText(""))
        self.valor.setFocus()

    def salvar(self) -> None:
        try:
            centavos = para_centavos(self.valor.text()) if self.valor.text().strip() else 0
            if centavos <= 0:
                raise ValueError("Digite um valor maior que zero ou use Remover meta.")
            investimentos.definir_meta_aporte(self.conn, centavos)
        except ValueError as erro:
            self.erro.setText(str(erro))
            return
        self.alterado.emit()
        self.accept()

    def remover(self) -> None:
        investimentos.definir_meta_aporte(self.conn, None)
        self.alterado.emit()
        self.accept()
