"""Mini-formulário de regra de categorização (padrão, contém/regex, categoria, prioridade), usado na janela
Categorias e na fila "A categorizar". Grava na hora; erros ficam no próprio formulário."""

import sqlite3

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from meu_cofre import cadastros
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, _repolir
from meu_cofre.ui.dialogo_contas import _compacto, _legenda, _rotulo


class FormularioRegra(QFrame):
    """Cria (ou edita, se `regra_id`) uma regra. Emite `salvo(regra_id)` depois de gravar e `cancelado()`."""

    salvo = Signal(int)
    cancelado = Signal()

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.regra_id: int | None = None
        self.setObjectName("FormCartao")
        coluna = QVBoxLayout(self)
        p = 2 * tema.ESPACO
        coluna.setContentsMargins(p, p, p, p)
        coluna.setSpacing(4)
        self.titulo = _legenda("Nova regra")
        coluna.addWidget(self.titulo)
        linha = QHBoxLayout()
        linha.setSpacing(tema.ESPACO + 4)
        self.tipo = CampoLista()
        for codigo, rotulo in cadastros.TIPOS_MATCH.items():
            self.tipo.addItem(rotulo.capitalize(), codigo)
        self.tipo.setFixedWidth(110)
        self.padrao = QLineEdit()
        self.padrao.setPlaceholderText("Trecho da descrição, ex.: uber")
        linha.addWidget(self.tipo)
        linha.addWidget(self.padrao, 1)
        coluna.addLayout(linha)
        linha2 = QHBoxLayout()
        linha2.setSpacing(tema.ESPACO + 4)
        self.categoria = CampoLista()
        for tipo in ("despesa", "receita"):
            for c in cadastros.categorias_por_tipo(conn, tipo, incluir_inativas=False):
                if not cadastros.categoria_do_sistema(c):
                    self.categoria.addItem(c["nome"], c["id"])
        self.prioridade = QLineEdit("100")
        self.prioridade.setFixedWidth(70)
        self.prioridade.setToolTip("Prioridade: menor número vence quando duas regras casam")
        linha2.addWidget(_legenda("Categoria"))
        linha2.addWidget(self.categoria, 1)
        linha2.addWidget(_legenda("Prioridade"))
        linha2.addWidget(self.prioridade)
        coluna.addLayout(linha2)
        self.erro = _rotulo("", "CampoErro")
        self.erro.setFixedHeight(22)
        coluna.addWidget(self.erro)
        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        cancelar = _compacto(BotaoSecundario("Cancelar"))
        cancelar.clicked.connect(self.cancelado.emit)
        self.botao_salvar = BotaoPrimario("Salvar regra")
        self.botao_salvar.setProperty("compacto", True)
        self.botao_salvar.setAutoDefault(False)
        self.botao_salvar.clicked.connect(self.salvar)
        botoes.addWidget(cancelar)
        botoes.addWidget(self.botao_salvar)
        coluna.addLayout(botoes)
        self.padrao.textChanged.connect(lambda _t: self._limpar_erro())
        self.padrao.returnPressed.connect(self.salvar)

    def abrir(self, regra: sqlite3.Row | None = None, padrao: str = "", categoria_id: int | None = None) -> None:
        """Prepara o formulário: vazio, pré-preenchido (`padrao`, `categoria_id`) ou com os dados de uma regra."""
        self.regra_id = regra["id"] if regra else None
        self.titulo.setText("EDITAR REGRA" if regra else "NOVA REGRA")
        self.padrao.setText(regra["padrao"] if regra else padrao)
        self.tipo.setCurrentIndex(self.tipo.findData(regra["tipo_match"] if regra else "contem"))
        self.prioridade.setText(str(regra["prioridade"]) if regra else "100")
        alvo = regra["categoria_id"] if regra else categoria_id
        if alvo is not None and self.categoria.findData(alvo) >= 0:
            self.categoria.setCurrentIndex(self.categoria.findData(alvo))
        self._limpar_erro()
        self.padrao.setFocus()

    def salvar(self) -> bool:
        try:
            prioridade = int(self.prioridade.text().strip())
        except ValueError:
            return self._falhar("A prioridade é um número inteiro, como 100.", self.prioridade)
        dados = (self.padrao.text(), self.categoria.currentData(), self.tipo.currentData(), prioridade)
        try:
            if self.regra_id is None:
                id_ = cadastros.criar_regra(self.conn, *dados)
            else:
                cadastros.editar_regra(self.conn, self.regra_id, *dados)
                id_ = self.regra_id
        except ValueError as erro:
            return self._falhar(str(erro), self.padrao)
        self.salvo.emit(id_)
        return True

    def _falhar(self, texto: str, campo: QLineEdit) -> bool:
        self.erro.setText(f"!  {texto}")
        campo.setProperty("erro", True)
        _repolir(campo)
        campo.setFocus()
        return False

    def _limpar_erro(self) -> None:
        self.erro.setText("")
        for campo in (self.padrao, self.prioridade):
            campo.setProperty("erro", False)
            _repolir(campo)
