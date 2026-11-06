"""Janela "Relatório": o texto do mês (`relatorio_mensal.gerar`) numa prévia editável, pra copiar e colar numa IA.

Abre no mês anterior ao de hoje (fechado). Trocar de mês refaz a prévia; "Atualizar prévia" também (por exemplo
depois de mexer no "Sobre mim", que fica salvo na preferência `contexto_relatorio`). Refazer descarta as edições
feitas na prévia. "Copiar relatório" copia o que está na prévia, com as edições. O app não envia nada.
"""

import sqlite3
from datetime import date

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from meu_cofre import db, relatorio_mensal
from meu_cofre.relatorio_automatico import PREF_CONTEXTO
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario

LARGURA = 760


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    rotulo.setWordWrap(True)
    return rotulo


class DialogoRelatorio(QDialog):
    def __init__(self, conn: sqlite3.Connection, hoje: date, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.hoje = hoje
        # abre no mês fechado mais recente
        self.mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Relatório")
        self.setMinimumWidth(LARGURA)
        self._montar()
        self.atualizar_previa()

    def _montar(self) -> None:
        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(m, m, m, m)
        coluna.setSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Relatório mensal", "DialogoTitulo"))
        coluna.addWidget(_rotulo(
            "Um texto com os números do mês pra você colar na IA que preferir. O app não envia nada: confira a prévia, "
            "apague o que não quiser mostrar e copie.", "DialogoDetalhe"))

        navegacao = QHBoxLayout()
        self.botao_anterior = self._botao_mes("‹", "Mês anterior")
        self.botao_seguinte = self._botao_mes("›", "Próximo mês")
        self.rotulo_mes = QLabel()
        self.rotulo_mes.setObjectName("MesRotulo")
        self.rotulo_mes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        navegacao.addStretch(1)
        for w in (self.botao_anterior, self.rotulo_mes, self.botao_seguinte):
            navegacao.addWidget(w)
        navegacao.addStretch(1)
        coluna.addLayout(navegacao)
        self.botao_anterior.clicked.connect(lambda: self._mudar_mes(-1))
        self.botao_seguinte.clicked.connect(lambda: self._mudar_mes(+1))

        coluna.addWidget(_rotulo("Sobre mim (vai junto no relatório)", "CampoRotulo"))
        self.contexto = QPlainTextEdit(db.ler_preferencia(self.conn, PREF_CONTEXTO, ""))
        self.contexto.setPlaceholderText("Opcional: renda, moradia, objetivos... o que ajuda a IA a entender seus números.")
        self.contexto.setFixedHeight(72)
        self.contexto.textChanged.connect(self._contexto_mudou)
        coluna.addWidget(self.contexto)
        self.aviso_contexto = _rotulo("O \"Sobre mim\" mudou: clique em Atualizar prévia pra incluí-lo.", "CampoInfo")
        self.aviso_contexto.hide()
        coluna.addWidget(self.aviso_contexto)

        coluna.addWidget(_rotulo("Prévia (editável: apague o que não quiser enviar)", "CampoRotulo"))
        self.previa = QPlainTextEdit()
        self.previa.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.previa.setMinimumHeight(300)
        coluna.addWidget(self.previa, 1)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO + 4)
        self.botao_fechar = BotaoSecundario("Fechar")
        self.botao_atualizar = BotaoSecundario("Atualizar prévia")
        self.botao_atualizar.setToolTip("Refaz o texto e descarta as edições feitas na prévia")
        self.botao_copiar = BotaoPrimario("Copiar relatório")
        for b in (self.botao_fechar, self.botao_atualizar, self.botao_copiar):
            b.setAutoDefault(False)
        self.copiado = _rotulo("", "AvisoSalvo")
        self._limpar_aviso = QTimer(self, singleShot=True, interval=3000)
        self._limpar_aviso.timeout.connect(lambda: self.copiado.setText(""))
        botoes.addWidget(self.botao_atualizar)
        botoes.addStretch(1)
        botoes.addWidget(self.copiado)
        botoes.addWidget(self.botao_fechar)
        botoes.addWidget(self.botao_copiar)
        coluna.addLayout(botoes)
        self.botao_fechar.clicked.connect(self.reject)
        self.botao_atualizar.clicked.connect(self.atualizar_previa)
        self.botao_copiar.clicked.connect(self.copiar)

    @staticmethod
    def _botao_mes(simbolo: str, dica: str) -> QPushButton:
        botao = QPushButton(simbolo)
        botao.setObjectName("BotaoMes")
        botao.setToolTip(dica)
        botao.setCursor(Qt.CursorShape.PointingHandCursor)
        botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return botao

    def _mudar_mes(self, passo: int) -> None:
        """Navega o mês; nunca passa do mês atual (que sai parcial)."""
        indice = self.mes[0] * 12 + (self.mes[1] - 1) + passo
        novo = (indice // 12, indice % 12 + 1)
        if novo > (self.hoje.year, self.hoje.month):
            return
        self.mes = novo
        self.atualizar_previa()

    def _contexto_mudou(self) -> None:
        db.gravar_preferencia(self.conn, PREF_CONTEXTO, self.contexto.toPlainText().strip() or None)
        self.aviso_contexto.show()

    def atualizar_previa(self) -> None:
        """Refaz o texto do mês escolhido (descarta as edições da prévia)."""
        ano, mes = self.mes
        parcial = (ano, mes) == (self.hoje.year, self.hoje.month)
        self.rotulo_mes.setText(relatorio_mensal.nome_do_mes(ano, mes) + (" (parcial)" if parcial else ""))
        self.botao_seguinte.setEnabled(not parcial)
        self.previa.setPlainText(relatorio_mensal.gerar(self.conn, ano, mes, self.hoje, self.contexto.toPlainText()))
        self.aviso_contexto.hide()
        self.copiado.setText("")

    def copiar(self) -> None:
        """Copia a prévia, como está agora (com as edições), pra área de transferência."""
        QGuiApplication.clipboard().setText(self.previa.toPlainText())
        self.copiado.setText("Copiado ✓")
        self._limpar_aviso.start()
