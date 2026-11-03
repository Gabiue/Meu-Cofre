"""Fila "A categorizar" (#3): lançamentos sem categoria, com uma categoria por linha e "criar regra" em cada uma.

"Salvar" grava as categorias escolhidas de uma vez e avisa a janela principal (`gravado`). Criar uma regra aqui já
categoriza, na hora, todos os pendentes que ela casa (inclusive esta linha). Linhas que ficarem sem escolha continuam na fila.
"""

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from meu_cofre import cadastros, lancamentos
from meu_cofre.lancamentos import formatar_brl
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista
from meu_cofre.ui.dialogo_contas import _compacto, _esvaziar, _rotulo, _separador
from meu_cofre.ui.formulario_regra import FormularioRegra

LARGURA = 760


class DialogoACategorizar(QDialog):
    gravado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.combos: dict[int, CampoLista] = {}  # id do lançamento -> escolha de categoria
        self._escolhas: dict[int, int] = {}  # sobrevive à recarga da lista (ao criar uma regra)
        self._regra_aberta: int | None = None
        self.formulario: FormularioRegra | None = None
        self.setObjectName("Dialogo")
        self.setProperty("tela", "contas")
        self.setModal(True)
        self.setWindowTitle("A categorizar")
        self.resize(LARGURA, 560)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        corpo = QWidget()
        corpo.setObjectName("DialogoCorpo")
        rolagem.setWidget(corpo)
        raiz.addWidget(rolagem, 1)
        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(corpo)
        coluna.setContentsMargins(m, m, m, tema.ESPACO)
        coluna.setSpacing(2 * tema.ESPACO)
        coluna.addWidget(_rotulo("A categorizar", "DialogoTitulo"))
        self.resumo = _rotulo("", "CampoInfo")
        coluna.addWidget(self.resumo)
        self._lista = QVBoxLayout()
        self._lista.setSpacing(0)
        coluna.addLayout(self._lista)
        coluna.addStretch(1)

        barra = QHBoxLayout()
        barra.setContentsMargins(m, tema.ESPACO, m, m)
        barra.addStretch(1)
        fechar = BotaoSecundario("Fechar")
        fechar.setAutoDefault(False)
        fechar.clicked.connect(self.reject)
        self.botao_salvar = BotaoPrimario("Salvar")
        self.botao_salvar.setAutoDefault(False)
        self.botao_salvar.clicked.connect(self.salvar)
        barra.addWidget(fechar)
        barra.addWidget(self.botao_salvar)
        raiz.addLayout(barra)
        self._carregar()

    def _carregar(self) -> None:
        self._escolhas.update({i: c.currentData() for i, c in self.combos.items() if c.currentData() is not None})
        _esvaziar(self._lista)
        self.combos.clear()
        self.formulario = None
        pend = lancamentos.pendentes(self.conn)
        self._escolhas = {i: c for i, c in self._escolhas.items() if i in set(pend["id"])}
        self.resumo.setText(f"{len(pend)} lançamento{'s' if len(pend) != 1 else ''} sem categoria" if len(pend)
                            else "Tudo categorizado.")
        self.botao_salvar.setEnabled(len(pend) > 0)
        for i, p in enumerate(pend.itertuples()):
            if i:
                self._lista.addWidget(_separador())
            self._lista.addWidget(self._linha(p))

    def _linha(self, p) -> QWidget:
        quadro = QWidget()
        coluna = QVBoxLayout(quadro)
        coluna.setContentsMargins(0, tema.ESPACO, 0, tema.ESPACO)
        coluna.setSpacing(tema.ESPACO)
        linha = QHBoxLayout()
        linha.setSpacing(2 * tema.ESPACO)
        texto = QVBoxLayout()
        texto.setSpacing(0)
        texto.addWidget(_rotulo(p.descricao, "ContaNome"))
        texto.addWidget(_rotulo(f"{p.data:%d/%m/%Y} · {p.conta} · {formatar_brl(int(p.valor_centavos))}", "CampoInfo"))
        linha.addLayout(texto, 1)
        combo = CampoLista()
        combo.setPlaceholderText("Escolha uma categoria")
        for c in cadastros.categorias_por_tipo(self.conn, p.tipo, incluir_inativas=False):
            if not cadastros.categoria_do_sistema(c):
                combo.addItem(c["nome"], c["id"])
        combo.setCurrentIndex(combo.findData(self._escolhas.get(int(p.id))))
        combo.setFixedWidth(210)
        self.combos[int(p.id)] = combo
        regra = _compacto(BotaoSecundario("Criar regra"))
        regra.clicked.connect(lambda _c=False, i=int(p.id), d=p.descricao, q=quadro: self.criar_regra(i, d, q))
        linha.addWidget(combo)
        linha.addWidget(regra)
        coluna.addLayout(linha)
        return quadro

    def criar_regra(self, lancamento_id: int, descricao: str, quadro: QWidget) -> FormularioRegra:
        """Abre o formulário abaixo da linha, com o trecho e a categoria já escolhidos."""
        if self.formulario is not None:
            self.formulario.setParent(None)
            self.formulario.deleteLater()
        self.formulario = FormularioRegra(self.conn)
        self.formulario.abrir(None, lancamentos.padrao_sugerido(descricao), self.combos[lancamento_id].currentData())
        self.formulario.salvo.connect(self._regra_salva)
        self.formulario.cancelado.connect(self._fechar_formulario)
        quadro.layout().addWidget(self.formulario)
        return self.formulario

    def _fechar_formulario(self) -> None:
        if self.formulario is not None:
            self.formulario.setParent(None)
            self.formulario.deleteLater()
            self.formulario = None

    def _regra_salva(self, regra_id: int) -> None:
        n = lancamentos.aplicar_regras_aos_pendentes(self.conn, regra_id)
        self._carregar()
        self.gravado.emit(f"Regra criada · {n} lançamento{'s' if n != 1 else ''} categorizado{'s' if n != 1 else ''}")

    def salvar(self) -> bool:
        """Grava as categorias escolhidas; as linhas sem escolha ficam na fila."""
        escolhas = {i: c.currentData() for i, c in self.combos.items() if c.currentData() is not None}
        if not escolhas:
            self.resumo.setText("Escolha ao menos uma categoria.")
            return False
        for id_, categoria_id in escolhas.items():
            lancamentos.atualizar(self.conn, id_, categoria_id=categoria_id)
        self._escolhas.clear()
        self._carregar()
        self.gravado.emit(f"{len(escolhas)} lançamento{'s' if len(escolhas) != 1 else ''} categorizado{'s' if len(escolhas) != 1 else ''}")
        return True
