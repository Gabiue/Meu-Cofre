"""Janela de lançamento (docs/design/telas.md): diálogo para lançar, ver, editar e excluir.

Modo novo (`id_lancamento=None`): jornada em 4 passos (entrada/saída, valor, o que foi, método).
Modo ver/editar: o mesmo formulário preenchido, com os detalhes do lançamento e o botão Excluir.
O tipo "Transferência" (de uma conta própria pra outra) troca categoria, método, parcelas e repetir por De / Para;
grava e apaga os dois lados juntos.
Todo erro aparece embaixo do campo; nada de caixa de mensagem. Quem abre o diálogo escuta
`gravado(mensagem)` para atualizar a tela e mostrar a mensagem na barra de status.
No modo novo, "Repetir todo mês" (com "É assinatura") faz do lançamento o primeiro de uma recorrente;
no modo editar, um lançamento que veio de uma recorrente mostra "Repete todo dia N" e um atalho
`gerenciar_recorrente(id)` pra aba Assinaturas/fixos.
"""

import sqlite3
from datetime import date, datetime

from PySide6.QtCore import QDate, QStringListModel, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import cadastros, lancamentos, recorrentes
from meu_cofre.lancamentos import METODOS, formatar_brl, nome_com_final, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    CampoData,
    CampoLista,
    Marcador,
    Segmentado,
    _repolir,
)

LARGURA = 520
SAIDA, ENTRADA, TRANSFERENCIA = "Saída", "Entrada", "Transferência"
METODO_POR_ROTULO = {rotulo: codigo for codigo, rotulo in METODOS.items()}
PARCELAS_MAX = 24
ORIGENS = {"manual": "lançamento manual", "importacao": "importado do extrato", "recorrente": "recorrente"}

# Páginas do bloco "conta / parcelas" (à direita da data)
_PAG_CONTA, _PAG_PARCELAS, _PAG_VAZIA = 0, 1, 2
# Páginas da barra de botões
_BARRA_NORMAL, _BARRA_EXCLUIR, _BARRA_EXCLUIR_PARCELA = 0, 1, 2


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _botao_neutro(botao: QPushButton) -> QPushButton:
    """Só o botão principal responde ao Enter."""
    botao.setAutoDefault(False)
    botao.setDefault(False)
    return botao


class DialogoLancamento(QDialog):
    """Diálogo modal de lançamento. Emite `gravado(mensagem)` depois de salvar ou excluir."""

    gravado = Signal(str)
    gerenciar_recorrente = Signal(int)  # pediu pra gerenciar a recorrência na aba (o diálogo já fechou)

    def __init__(
        self,
        conn: sqlite3.Connection,
        id_lancamento: int | None = None,
        parent: QWidget | None = None,
        hoje: date | None = None,
    ):
        super().__init__(parent)
        self.conn = conn
        self.id_lancamento = id_lancamento
        self._hoje = hoje or date.today()
        self._original: dict | None = None
        self._ultima_mensagem = ""
        self._assinatura_manual = False  # o dono mexeu em "É assinatura": a categoria deixa de sugerir
        if id_lancamento is not None:
            self._original = lancamentos.obter(conn, id_lancamento)
            if self._original is None:
                raise ValueError(f"Lançamento {id_lancamento} não existe.")
        # Transferência entre contas próprias (os dois lados editam juntos), ou None.
        self._transf = lancamentos.obter_transferencia(conn, id_lancamento) if id_lancamento is not None else None
        # Parcela ou lançamento sem método da lista (fatura, meta, aporte): só valor, descrição, categoria e data mudam.
        self._restrito = bool(
            self._original
            and not self._transf
            and (self._original["grupo_parcelas"] or self._original["metodo"] not in METODOS)
        )

        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Lançamento" if self.editando else "Novo lançamento")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)

        self._montar()
        self._ligar()
        self._carregar_listas()
        if self._original:
            self._preencher(self._original)
        self._atualizar_metodo()
        self._atualizar_modo()
        self._ajustado = False
        self.valor.setFocus()

    @property
    def editando(self) -> bool:
        return self._original is not None

    # --- Montagem ------------------------------------------------------------------
    def _montar(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        self._rolagem = QScrollArea()
        self._rolagem.setWidgetResizable(True)
        self._rolagem.setFrameShape(QFrame.Shape.NoFrame)
        self._rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._corpo = corpo = QWidget()
        corpo.setObjectName("DialogoCorpo")
        self._rolagem.setWidget(corpo)
        raiz.addWidget(self._rolagem, 1)

        m = 3 * tema.ESPACO
        form = QVBoxLayout(corpo)
        form.setContentsMargins(m, m, m, tema.ESPACO)
        form.setSpacing(0)

        titulo = _rotulo("Lançamento" if self.editando else "Novo lançamento", "DialogoTitulo")
        form.addWidget(titulo)
        if self.editando:
            self.detalhes = _rotulo(self._texto_detalhes(self._original, bool(self._transf)), "DialogoDetalhe")
            self.detalhes.setWordWrap(True)
            form.addSpacing(4)
            form.addWidget(self.detalhes)
        form.addSpacing(2 * tema.ESPACO)

        # 1. Saída | Entrada
        self.tipo = Segmentado([SAIDA, ENTRADA, TRANSFERENCIA])
        form.addWidget(self.tipo, 0, Qt.AlignmentFlag.AlignLeft)
        form.addSpacing(tema.ESPACO + 4)

        # 2. Valor
        form.addWidget(_rotulo("Valor (R$)", "CampoRotulo"))
        form.addSpacing(4)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        form.addWidget(self.valor)
        self.erro_valor = self._rotulo_erro()
        form.addWidget(self.erro_valor)

        # De / Para (só na transferência)
        self._bloco_transferencia = QWidget()
        coluna_transf = QVBoxLayout(self._bloco_transferencia)
        coluna_transf.setContentsMargins(0, 0, 0, 0)
        coluna_transf.setSpacing(0)
        linha_transf = QHBoxLayout()
        linha_transf.setSpacing(2 * tema.ESPACO)
        self.de, self.para = CampoLista(), CampoLista()
        for texto, campo in (("De", self.de), ("Para", self.para)):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(_rotulo(texto, "CampoRotulo"))
            col.addWidget(campo)
            linha_transf.addLayout(col, 1)
        coluna_transf.addLayout(linha_transf)
        self.erro_para = self._rotulo_erro()
        coluna_transf.addWidget(self.erro_para)
        form.addWidget(self._bloco_transferencia)

        # 3. O que foi + categoria
        self._rotulo_descricao = _rotulo("O que foi", "CampoRotulo")
        form.addWidget(self._rotulo_descricao)
        form.addSpacing(4)
        self.descricao = QLineEdit()
        self.descricao.setPlaceholderText("Digite ou escolha (ex.: Almoço)")
        self.completar = QCompleter([], self)
        self.completar.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completar.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completar.setMaxVisibleItems(6)
        self.completar.popup().setObjectName("CompletarPopup")
        self.descricao.setCompleter(self.completar)
        form.addWidget(self.descricao)
        self.erro_descricao = self._rotulo_erro()
        form.addWidget(self.erro_descricao)

        self._bloco_categoria = QWidget()
        coluna_categoria = QVBoxLayout(self._bloco_categoria)
        coluna_categoria.setContentsMargins(0, 0, 0, 0)
        coluna_categoria.setSpacing(0)
        cab_categoria = QHBoxLayout()
        cab_categoria.addWidget(_rotulo("Categoria", "CampoRotulo"))
        cab_categoria.addStretch(1)
        self.dica_categoria = _rotulo("sugerida pelo seu histórico", "CampoDica")
        self.dica_categoria.setVisible(False)
        cab_categoria.addWidget(self.dica_categoria)
        coluna_categoria.addLayout(cab_categoria)
        coluna_categoria.addSpacing(4)
        self.categoria = CampoLista()
        self.categoria.setPlaceholderText("Escolha uma categoria")
        coluna_categoria.addWidget(self.categoria)
        coluna_categoria.addSpacing(tema.ESPACO + 4)
        form.addWidget(self._bloco_categoria)

        # 4. Método
        self._bloco_metodo = QWidget()
        coluna_metodo = QVBoxLayout(self._bloco_metodo)
        coluna_metodo.setContentsMargins(0, 0, 0, 0)
        coluna_metodo.setSpacing(4)
        coluna_metodo.addWidget(_rotulo("Método", "CampoRotulo"))
        self.metodo = Segmentado(list(METODOS.values()), METODOS["pix"])
        coluna_metodo.addWidget(self.metodo, 0, Qt.AlignmentFlag.AlignLeft)
        # "Qual cartão": só aparece com método Cartão e mais de um cartão para escolher
        self._bloco_cartao = QWidget()
        coluna_cartao = QVBoxLayout(self._bloco_cartao)
        coluna_cartao.setContentsMargins(0, tema.ESPACO + 4, 0, 0)
        coluna_cartao.setSpacing(4)
        coluna_cartao.addWidget(_rotulo("Qual cartão", "CampoRotulo"))
        self.cartao = CampoLista()
        coluna_cartao.addWidget(self.cartao)
        coluna_metodo.addWidget(self._bloco_cartao)
        form.addWidget(self._bloco_metodo)
        form.addSpacing(tema.ESPACO + 4)

        # Data | conta ou parcelas
        linha = QHBoxLayout()
        linha.setSpacing(2 * tema.ESPACO)
        coluna_data = QVBoxLayout()
        coluna_data.setSpacing(4)
        coluna_data.addWidget(_rotulo("Data", "CampoRotulo"))
        self.data = CampoData()
        self.data.setDate(QDate(self._hoje.year, self._hoje.month, self._hoje.day))
        coluna_data.addWidget(self.data)
        linha.addLayout(coluna_data, 1)

        self._pilha_extra = QStackedWidget()
        self.conta = CampoLista()
        self.parcelas = CampoLista()
        self._rotulo_conta = _rotulo("Conta", "CampoRotulo")
        self._rotulo_parcelas = _rotulo("Parcelas", "CampoRotulo")
        for rotulo, campo in ((self._rotulo_conta, self.conta), (self._rotulo_parcelas, self.parcelas)):
            pagina = QWidget()
            col = QVBoxLayout(pagina)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)
            col.addWidget(rotulo)
            col.addWidget(campo)
            self._pilha_extra.addWidget(pagina)
        self._pilha_extra.addWidget(QWidget())  # _PAG_VAZIA
        linha.addWidget(self._pilha_extra, 1)
        form.addLayout(linha)

        self.info_destino = _rotulo("", "CampoInfo")
        self.info_destino.setWordWrap(True)
        self.info_destino.setMinimumHeight(24)
        self.info_destino.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        form.addSpacing(4)
        form.addWidget(self.info_destino)

        # Repetir todo mês (só no lançamento novo)
        form.addSpacing(tema.ESPACO)
        self._bloco_repetir = QWidget()
        coluna_repetir = QVBoxLayout(self._bloco_repetir)
        coluna_repetir.setContentsMargins(0, 0, 0, 0)
        coluna_repetir.setSpacing(tema.ESPACO)
        linha_repetir = QHBoxLayout()
        self.repetir = Marcador("Repetir todo mês")
        linha_repetir.addWidget(self.repetir)
        linha_repetir.addStretch(1)
        self.texto_repetir = _rotulo("", "CampoInfo")
        linha_repetir.addWidget(self.texto_repetir)
        coluna_repetir.addLayout(linha_repetir)
        self.assinatura = Marcador("É assinatura")
        self.assinatura.setToolTip("Assinaturas e gastos fixos ficam juntos na aba Assinaturas/fixos; este marca qual é qual")
        coluna_repetir.addWidget(self.assinatura)
        form.addWidget(self._bloco_repetir)
        self._bloco_repetir.setVisible(not self.editando)
        self.assinatura.setVisible(False)

        # Lançamento que veio de uma recorrente (só ao editar)
        self._bloco_recorrente = QWidget()
        linha_recorrente = QHBoxLayout(self._bloco_recorrente)
        linha_recorrente.setContentsMargins(0, 0, 0, 0)
        self.texto_recorrente = _rotulo("", "CampoInfo")
        linha_recorrente.addWidget(self.texto_recorrente)
        linha_recorrente.addStretch(1)
        self.botao_gerenciar = QPushButton("Gerenciar em Assinaturas/fixos")
        self.botao_gerenciar.setObjectName("BotaoLink")
        self.botao_gerenciar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_gerenciar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_gerenciar.setAutoDefault(False)
        linha_recorrente.addWidget(self.botao_gerenciar)
        form.addWidget(self._bloco_recorrente)
        self._bloco_recorrente.setVisible(False)

        # Confirmação discreta do "salvar e lançar outro"
        self.aviso_salvo = _rotulo("", "AvisoSalvo")
        self.aviso_salvo.setMinimumHeight(24)
        form.addWidget(self.aviso_salvo)
        # Oferta discreta (#3): depois de "salvar e lançar outro" com categoria diferente da sugerida
        self._oferta: tuple[int, str] | None = None  # (categoria_id, nome)
        self.oferta_regra = QWidget()
        linha_oferta = QHBoxLayout(self.oferta_regra)
        linha_oferta.setContentsMargins(0, 0, 0, 0)
        linha_oferta.setSpacing(tema.ESPACO)
        linha_oferta.addWidget(_rotulo("Sempre categorizar", "CampoInfo"))
        self.padrao_regra = QLineEdit()
        linha_oferta.addWidget(self.padrao_regra, 1)
        self.texto_oferta = _rotulo("", "CampoInfo")
        linha_oferta.addWidget(self.texto_oferta)
        self.botao_criar_regra = QPushButton("Criar regra")
        self.botao_criar_regra.setObjectName("BotaoLink")
        self.botao_criar_regra.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_criar_regra.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_criar_regra.setAutoDefault(False)
        self.botao_criar_regra.clicked.connect(self.criar_regra)
        linha_oferta.addWidget(self.botao_criar_regra)
        self.oferta_regra.setVisible(False)
        form.addWidget(self.oferta_regra)
        form.addStretch(1)

        raiz.addWidget(self._montar_barra())

    @staticmethod
    def _rotulo_erro() -> QLabel:
        """Linha de erro com altura reservada, para o formulário não pular quando o erro aparece."""
        rotulo = _rotulo("", "CampoErro")
        rotulo.setFixedHeight(22)
        rotulo.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        return rotulo

    def _montar_barra(self) -> QWidget:
        """Rodapé com as ações; uma pilha de 3 páginas: normal, confirmar exclusão, confirmar parcela."""
        self._barra = QStackedWidget()
        self._barra.setObjectName("DialogoBarra")
        m = 3 * tema.ESPACO

        # Página normal
        normal = QWidget()
        linha = QHBoxLayout(normal)
        linha.setContentsMargins(m, tema.ESPACO, m, m)
        linha.setSpacing(tema.ESPACO + 4)
        self.botao_excluir = _botao_neutro(BotaoSecundario("Excluir"))
        self.botao_excluir.setProperty("perigo", True)
        self.botao_excluir.setVisible(self.editando)
        self.botao_cancelar = _botao_neutro(BotaoSecundario("Cancelar"))
        self.botao_cancelar.setToolTip("Cancelar (Esc)")
        self.botao_outro = _botao_neutro(BotaoSecundario("Salvar e lançar outro"))
        self.botao_outro.setToolTip("Salvar e lançar outro (Ctrl+Enter)")
        self.botao_outro.setVisible(not self.editando)
        self.botao_salvar = BotaoPrimario("Salvar alterações" if self.editando else "Salvar")
        self.botao_salvar.setToolTip("Salvar (Enter)")
        self.botao_salvar.setAutoDefault(True)
        self.botao_salvar.setDefault(True)
        linha.addWidget(self.botao_excluir)
        linha.addStretch(1)
        for botao in (self.botao_cancelar, self.botao_outro, self.botao_salvar):
            linha.addWidget(botao)
        self._barra.addWidget(normal)

        # Confirmação de exclusão simples
        simples = QWidget()
        linha = QHBoxLayout(simples)
        linha.setContentsMargins(m, tema.ESPACO, m, m)
        linha.setSpacing(tema.ESPACO + 4)
        linha.addWidget(_rotulo("Excluir mesmo?", "ConfirmaTexto"))
        linha.addStretch(1)
        self.botao_nao = _botao_neutro(BotaoSecundario("Não"))
        self.botao_sim = _botao_neutro(self._botao_perigo("Sim, excluir"))
        linha.addWidget(self.botao_nao)
        linha.addWidget(self.botao_sim)
        self._barra.addWidget(simples)

        # Confirmação de exclusão de parcela: só esta ou todas
        parcela = QWidget()
        coluna = QVBoxLayout(parcela)
        coluna.setContentsMargins(m, tema.ESPACO, m, m)
        coluna.setSpacing(2 * tema.ESPACO)
        self.texto_parcelas = _rotulo("", "ConfirmaTexto")
        self.texto_parcelas.setWordWrap(True)
        coluna.addWidget(self.texto_parcelas)
        linha = QHBoxLayout()
        linha.setSpacing(tema.ESPACO + 4)
        linha.addStretch(1)
        self.botao_nao_parcela = _botao_neutro(BotaoSecundario("Não"))
        self.botao_so_esta = _botao_neutro(BotaoSecundario("Só esta parcela"))
        self.botao_so_esta.setProperty("perigo", True)
        self.botao_todas = _botao_neutro(self._botao_perigo("Todas as parcelas"))
        for botao in (self.botao_nao_parcela, self.botao_so_esta, self.botao_todas):
            linha.addWidget(botao)
        coluna.addLayout(linha)
        self._barra.addWidget(parcela)
        return self._barra

    @staticmethod
    def _botao_perigo(texto: str) -> QPushButton:
        botao = QPushButton(texto)
        botao.setObjectName("BotaoPerigo")
        botao.setCursor(Qt.CursorShape.PointingHandCursor)
        return botao

    def _ligar(self) -> None:
        self.tipo.escolhido.connect(lambda _t: self._trocou_tipo())
        self.metodo.escolhido.connect(lambda _m: self._atualizar_metodo())
        self.cartao.currentIndexChanged.connect(lambda _i: self._atualizar_destino())
        self.valor.textChanged.connect(self._valor_mudou)
        self.de.currentIndexChanged.connect(lambda _i: self._limpar_erro_para())
        self.para.currentIndexChanged.connect(lambda _i: self._limpar_erro_para())
        self.descricao.textChanged.connect(lambda _t: self._limpar_erro(self.erro_descricao, self.descricao))
        self.descricao.editingFinished.connect(self._sugerir_categoria)
        self.completar.activated.connect(lambda _t: self._sugerir_categoria())
        self.categoria.activated.connect(lambda _i: self.dica_categoria.setVisible(False))
        self.categoria.currentIndexChanged.connect(lambda _i: self._sugerir_assinatura())
        self.data.dateChanged.connect(lambda _d: self._atualizar_repetir())
        self.repetir.toggled.connect(lambda _m: self._repetir_mudou())
        self.assinatura.clicked.connect(lambda _m: setattr(self, "_assinatura_manual", True))
        self.parcelas.currentIndexChanged.connect(lambda _i: self._atualizar_repetir())
        self.botao_gerenciar.clicked.connect(self._gerenciar)

        self.botao_salvar.clicked.connect(self.salvar)
        self.botao_outro.clicked.connect(self.salvar_e_lancar_outro)
        self.botao_cancelar.clicked.connect(self.reject)
        self.botao_excluir.clicked.connect(self.pedir_exclusao)
        self.botao_nao.clicked.connect(self.cancelar_exclusao)
        self.botao_nao_parcela.clicked.connect(self.cancelar_exclusao)
        self.botao_sim.clicked.connect(self.excluir_este)
        self.botao_so_esta.clicked.connect(self.excluir_este)
        self.botao_todas.clicked.connect(self.excluir_todas_as_parcelas)
        if not self.editando:
            for sequencia in ("Ctrl+Return", "Ctrl+Enter"):
                QShortcut(QKeySequence(sequencia), self, activated=self.salvar_e_lancar_outro)

    # --- Dados dos combos ------------------------------------------------------------
    def _carregar_listas(self) -> None:
        correntes = [c for c in lancamentos.listar_contas(self.conn) if c["tipo"] == "corrente"]
        for c in correntes:
            self.conta.addItem(c["nome"], c["id"])
        self.conta.setCurrentIndex(max(self.conta.findText("Inter"), 0))

        # Cartões para escolher: os ativos (e, ao editar, o da compra, mesmo que já desativado).
        self._cartoes = [(c["id"], nome_com_final(c["nome"], c["final"])) for c in lancamentos.cartoes_ativos(self.conn)]
        original = self._original
        if original and original["metodo"] == "cartao" and original["conta_id"] not in [i for i, _ in self._cartoes]:
            nome = nome_com_final(original["conta"], original["conta_final"])
            self._cartoes.append((original["conta_id"], f"{nome} (desativado)"))
        for id_cartao, nome in self._cartoes:
            self.cartao.addItem(nome, id_cartao)
        self.cartao.setCurrentIndex(max(self.cartao.findData(lancamentos.ultimo_cartao_usado(self.conn)), 0))
        for c in lancamentos.listar_contas(self.conn):
            if c["tipo"] in lancamentos.TIPOS_TRANSFERENCIA:
                self.de.addItem(c["nome"], c["id"])
                self.para.addItem(c["nome"], c["id"])
        self.de.setCurrentIndex(max(self.de.findText("Inter"), 0))
        self.para.setCurrentIndex(max(self.para.findText("Carteira"), 0))
        if self.de.currentData() == self.para.currentData() and self.para.count() > 1:
            self.para.setCurrentIndex(1 if self.de.currentIndex() == 0 else 0)
        if self.editando and not self._transf:
            self.tipo.habilitar(TRANSFERENCIA, False, "Um lançamento que já existe não vira transferência.")
        self.metodo.habilitar(METODOS["cartao"], bool(self._cartoes), "Nenhum cartão ativo. Adicione um em Contas.")

        for n in range(1, PARCELAS_MAX + 1):
            self.parcelas.addItem(f"{n}x", n)

        self._recarregar_tipo()

    @property
    def entrada(self) -> bool:
        return self.tipo.atual() == ENTRADA

    @property
    def transferencia(self) -> bool:
        return self.tipo.atual() == TRANSFERENCIA

    @property
    def _tipo_categoria(self) -> str:
        return "receita" if self.entrada else "despesa"

    @property
    def codigo_metodo(self) -> str:
        return METODO_POR_ROTULO[self.metodo.atual()]

    def _recarregar_tipo(self) -> None:
        """Categorias e autocompletar do tipo atual (entrada ou saída)."""
        anterior = self.categoria.currentData()
        self.completar.setModel(QStringListModel(lancamentos.descricoes_usadas(self.conn, self._tipo_categoria), self.completar))
        self.categoria.clear()
        for c in lancamentos.listar_categorias(self.conn, self._tipo_categoria):
            self.categoria.addItem(c["nome"], c["id"])
        # A categoria de um lançamento antigo pode ser de outro tipo (ex.: transferência): mantém na lista.
        if self._original and self._original["categoria_id"] and self.categoria.findData(self._original["categoria_id"]) < 0:
            self.categoria.addItem(self._original["categoria"], self._original["categoria_id"])
        self._escolher_categoria(anterior)

    def _escolher_categoria(self, categoria_id: int | None) -> bool:
        indice = self.categoria.findData(categoria_id) if categoria_id else -1
        self.categoria.setCurrentIndex(indice)
        return indice >= 0

    # --- Preenchimento (modo editar) -----------------------------------------------------
    @staticmethod
    def _texto_detalhes(l: dict, transferencia: bool = False) -> str:
        conta = "Transferência entre contas" if transferencia else nome_com_final(l["conta"], l["conta_final"])
        if l["metodo"] in METODOS:
            conta += f" · {METODOS[l['metodo']]}"
        if l["grupo_parcelas"]:
            conta += f" · Parcela {l['parcela_atual']} de {l['parcela_total']}"
        criado = ""
        try:
            criado = " · lançado em " + datetime.fromisoformat(l["criado_em"]).strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            pass
        return f"{conta}\n{ORIGENS.get(l['origem'], l['origem']).capitalize()}{criado}"

    def _preencher(self, l: dict) -> None:
        if self._transf:
            t = self._transf
            self.tipo.definir(TRANSFERENCIA)
            self.tipo.setEnabled(False)
            self.valor.setText(formatar_brl(t["valor_centavos"]).removeprefix("R$ ").replace(".", ""))
            self.descricao.setText(t["descricao"])
            dia = date.fromisoformat(t["data"])
            self.data.setDate(QDate(dia.year, dia.month, dia.day))
            for campo, id_conta in ((self.de, t["origem_id"]), (self.para, t["destino_id"])):
                if campo.findData(id_conta) >= 0:
                    campo.setCurrentIndex(campo.findData(id_conta))
            return
        entrada = l["valor_centavos"] > 0
        self.tipo.definir(ENTRADA if entrada else SAIDA)
        self._recarregar_tipo()
        self._escolher_categoria(l["categoria_id"])
        self.valor.setText(formatar_brl(abs(l["valor_centavos"])).removeprefix("R$ ").replace(".", ""))
        self.descricao.setText(l["descricao"])
        dia = date.fromisoformat(l["data"])
        self.data.setDate(QDate(dia.year, dia.month, dia.day))
        if l["metodo"] in METODOS:
            self.metodo.definir(METODOS[l["metodo"]])
        if l["metodo"] == "cartao":
            self.cartao.setCurrentIndex(max(self.cartao.findData(l["conta_id"]), 0))
        else:
            indice = self.conta.findData(l["conta_id"])
            if indice < 0 and l["metodo"] in ("pix", "debito", "boleto"):  # conta já desativada: mantém a escolha
                self.conta.addItem(f"{l['conta']} (desativada)", l["conta_id"])
                indice = self.conta.count() - 1
            if indice >= 0:
                self.conta.setCurrentIndex(indice)
        if self._restrito:
            self.tipo.setEnabled(False)
            self.metodo.setEnabled(False)
            self._bloco_metodo.setVisible(l["metodo"] in METODOS)
        if l["recorrente_id"] and l["recorrente_dia"]:
            tipo = "assinatura" if l["recorrente_assinatura"] else "fixo"
            self.texto_recorrente.setText(f"Repete todo dia {l['recorrente_dia']} · {tipo}")
            self._bloco_recorrente.setVisible(True)

    # --- Comportamento dos campos ----------------------------------------------------------
    def _trocou_tipo(self) -> None:
        self._recarregar_tipo()
        self.dica_categoria.setVisible(False)
        self._atualizar_metodo()
        self._atualizar_modo()

    def _atualizar_modo(self) -> None:
        """Transferência troca categoria, método, parcelas e repetir por De / Para; a descrição vira opcional."""
        t = self.transferencia
        self._bloco_transferencia.setVisible(t)
        self._bloco_categoria.setVisible(not t)
        self._bloco_metodo.setVisible(not t and not (self._restrito and self._original["metodo"] not in METODOS))
        self._bloco_repetir.setVisible(not self.editando and not t)
        self._rotulo_descricao.setText("Descrição (opcional)" if t else "O que foi")
        self.descricao.setPlaceholderText("Transferência" if t else "Digite ou escolha (ex.: Almoço)")
        if t:
            self._limpar_erro(self.erro_descricao, self.descricao)

    def _atualizar_metodo(self) -> None:
        """Mostra conta, parcelas ou o texto de destino conforme o método e o tipo."""
        if self.transferencia:
            self._pilha_extra.setCurrentIndex(_PAG_VAZIA)
            self._bloco_cartao.setVisible(False)
            self.info_destino.setText("")
            return
        if self._restrito and self._original["metodo"] not in METODOS:
            self._pilha_extra.setCurrentIndex(_PAG_VAZIA)
            self.info_destino.setText("")
            return
        metodo = self.codigo_metodo
        parcelavel = metodo == "cartao" and not self.entrada and not self.editando
        escolhe_cartao = metodo == "cartao" and len(self._cartoes) > 1 and not self._restrito
        self._bloco_cartao.setVisible(escolhe_cartao)
        if metodo in ("pix", "debito", "boleto"):
            self._pilha_extra.setCurrentIndex(_PAG_CONTA)
        elif parcelavel:
            self._pilha_extra.setCurrentIndex(_PAG_PARCELAS)
        else:
            self._pilha_extra.setCurrentIndex(_PAG_VAZIA)
        self._atualizar_destino()
        self._atualizar_parcelas()
        self._atualizar_repetir()

    def _atualizar_destino(self) -> None:
        """Texto de apoio sobre onde o lançamento vai cair."""
        metodo = self.codigo_metodo
        if self._restrito and self._original["metodo"] not in METODOS:
            return
        if metodo == "cartao":
            if self._bloco_cartao.isHidden():
                self.info_destino.setText(f"Vai pro {self.cartao.currentText()}, na data da compra")
            else:
                self.info_destino.setText("Cai na fatura do cartão, na data da compra")
        elif metodo == "dinheiro":
            self.info_destino.setText("Entra na Carteira" if self.entrada else "Sai da Carteira")
        else:
            self.info_destino.setText("")

    def _atualizar_repetir(self) -> None:
        """"todo dia 5" ao vivo; Repetir e Parcelas não valem juntos (um desliga o outro, com dica)."""
        dia = self.data.date().day()
        self.texto_repetir.setText(f"todo dia {dia}" if self.repetir.isChecked() else "")
        parcelado = self._pilha_extra.currentIndex() == _PAG_PARCELAS and self.parcelas.currentData() > 1
        self.repetir.setEnabled(not parcelado)
        self.repetir.setToolTip("Compra parcelada não repete todo mês: são coisas diferentes" if parcelado else "")
        repete = self.repetir.isChecked()
        self.parcelas.setEnabled(not repete)
        self.parcelas.setToolTip("Não combina com Repetir todo mês" if repete else "")

    def _repetir_mudou(self) -> None:
        self.assinatura.setVisible(self.repetir.isChecked())
        self._sugerir_assinatura()
        self._atualizar_repetir()

    def _sugerir_assinatura(self) -> None:
        """"É assinatura" já vem marcada com a categoria Assinaturas, até o dono mexer nela."""
        if not self._assinatura_manual:
            self.assinatura.setChecked(self.categoria.currentText() == "Assinaturas")

    def _gerenciar(self) -> None:
        recorrente_id = self._original["recorrente_id"]
        self.reject()
        self.gerenciar_recorrente.emit(recorrente_id)

    def _valor_mudou(self, _texto: str) -> None:
        self._limpar_erro(self.erro_valor, self.valor)
        self.aviso_salvo.setText("")
        self.oferta_regra.setVisible(False)
        self._atualizar_parcelas()

    def _atualizar_parcelas(self) -> None:
        """Escreve "6x de R$ 100,00" nas opções, calculado ao vivo a partir do valor digitado."""
        try:
            total = para_centavos(self.valor.text())
        except ValueError:
            total = 0
        for n in range(1, PARCELAS_MAX + 1):
            texto = f"{n}x"
            if n > 1 and total > 0:
                texto += f" de {formatar_brl(lancamentos.dividir_em_parcelas(total, n)[-1])}"
            self.parcelas.setItemText(n - 1, texto)

    def _sugerir_categoria(self) -> None:
        """Preenche a categoria pelo histórico depois que a descrição é digitada ou escolhida."""
        if self.editando:
            return
        texto = self.descricao.text().strip()
        sugerida = lancamentos.sugerir_categoria(self.conn, texto, self._tipo_categoria) if texto else None
        if sugerida is not None and self._escolher_categoria(sugerida):
            self.dica_categoria.setVisible(True)

    # --- Erros inline -----------------------------------------------------------------------
    def _mostrar_erro(self, rotulo: QLabel, campo: QLineEdit, texto: str) -> None:
        rotulo.setText(f"!  {texto}")
        campo.setProperty("erro", True)
        _repolir(campo)

    def _limpar_erro_para(self) -> None:
        if self.erro_para.text():
            self.erro_para.setText("")
            self.para.setProperty("erro", False)
            _repolir(self.para)

    def _limpar_erro(self, rotulo: QLabel, campo: QLineEdit) -> None:
        if rotulo.text():
            rotulo.setText("")
            campo.setProperty("erro", False)
            _repolir(campo)

    def _validar(self) -> tuple[int, str] | None:
        """Devolve (centavos, descrição) ou mostra os erros e leva o foco ao primeiro campo ruim."""
        centavos, descricao = 0, self.descricao.text().strip()
        primeiro: QLineEdit | None = None

        texto = self.valor.text().strip()
        mensagem = ""
        if not texto:
            mensagem = "Digite o valor, como 32,50."
        else:
            try:
                centavos = para_centavos(texto)
            except ValueError:
                mensagem = "Valor inválido. Use só números, como 32,50."
            else:
                if centavos <= 0:
                    mensagem = "O valor precisa ser maior que zero."
        if mensagem:
            self._mostrar_erro(self.erro_valor, self.valor, mensagem)
            primeiro = self.valor

        if not descricao and not self.transferencia:
            self._mostrar_erro(self.erro_descricao, self.descricao, "Diga o que foi.")
            primeiro = primeiro or self.descricao

        if self.transferencia and self.de.currentData() == self.para.currentData():
            self.erro_para.setText("!  Escolha uma conta diferente da de origem.")
            self.para.setProperty("erro", True)
            _repolir(self.para)
            primeiro = primeiro or self.para

        if primeiro:
            primeiro.setFocus()
            if isinstance(primeiro, QLineEdit):
                primeiro.selectAll()
            return None
        return centavos, descricao

    def _conta_escolhida(self) -> int:
        """Cartão escolhido (método Cartão) ou conta corrente escolhida; 0 se não há."""
        campo = self.cartao if self.codigo_metodo == "cartao" else self.conta
        return campo.currentData() or 0

    # --- Gravar ---------------------------------------------------------------------------------
    def salvar(self) -> bool:
        """Grava e fecha. Devolve False (sem gravar) se houver erro."""
        if not self._gravar():
            return False
        self.accept()
        return True

    def salvar_e_lancar_outro(self) -> bool:
        """Grava e deixa o formulário pronto pro próximo (mantém tipo, método, conta e data)."""
        if self.editando or not self._gravar():
            return False
        self._limpar_para_o_proximo()
        return True

    def _gravar(self) -> bool:
        validos = self._validar()
        if validos is None:
            return False
        centavos, descricao = validos
        if self.transferencia:
            return self._gravar_transferencia(centavos, descricao)
        categoria_id = self.categoria.currentData()
        sugerida = None if self.editando else lancamentos.sugerir_categoria(self.conn, descricao, self._tipo_categoria)
        if not self.editando and categoria_id is None:  # salvou sem sair do campo: usa a sugestão mesmo assim
            categoria_id = sugerida
        self._oferta = None
        if not self.editando and categoria_id is not None and categoria_id != sugerida:
            self._oferta = (categoria_id, self.categoria.currentText() or cadastros._categoria(self.conn, categoria_id)["nome"])
        data = self.data.date().toPython()

        try:
            if self.editando:
                campos = dict(valor_centavos=centavos, descricao=descricao, categoria_id=categoria_id, data=data)
                if not self._restrito:
                    campos.update(metodo=self.codigo_metodo, conta_id=self._conta_escolhida() or self._original["conta_id"])
                lancamentos.atualizar(self.conn, self.id_lancamento, entrada=self.entrada, **campos)
                mensagem = f"Salvo: {descricao} ({formatar_brl(centavos)})"
            else:
                parcelas = self.parcelas.currentData() if self._pilha_extra.currentIndex() == _PAG_PARCELAS else 1
                ids = lancamentos.registrar(
                    self.conn,
                    entrada=self.entrada,
                    valor_centavos=centavos,
                    descricao=descricao,
                    categoria_id=categoria_id,
                    metodo=self.codigo_metodo,
                    conta_id=self._conta_escolhida(),
                    data=data,
                    parcelas=parcelas,
                )
                mensagem = f"Salvo: {descricao} ({formatar_brl(centavos)})" + (f" em {parcelas}x" if parcelas > 1 else "")
                if self.repetir.isChecked() and self.repetir.isEnabled():
                    recorrentes.criar_a_partir_de_lancamento(self.conn, ids[0], self.assinatura.isChecked())
                    mensagem += f" · repete todo dia {data.day}"
        except ValueError as erro:  # regra de negócio que a tela não previu (ex.: sem conta corrente)
            self._mostrar_erro(self.erro_valor, self.valor, str(erro))
            return False
        self._ultima_mensagem = mensagem
        self.gravado.emit(mensagem)
        return True

    def _gravar_transferencia(self, centavos: int, descricao: str) -> bool:
        dados = dict(
            origem_id=self.de.currentData(), destino_id=self.para.currentData(), valor_centavos=centavos,
            data=self.data.date().toPython(), descricao=descricao,
        )
        try:
            if self.editando:
                lancamentos.atualizar_transferencia(self.conn, self._transf["transferencia_id"], **dados)
            else:
                lancamentos.transferir(self.conn, **dados)
        except ValueError as erro:
            self._mostrar_erro(self.erro_valor, self.valor, str(erro))
            return False
        self._ultima_mensagem = f"Transferido: {self.de.currentText()} → {self.para.currentText()} ({formatar_brl(centavos)})"
        self.gravado.emit(self._ultima_mensagem)
        return True

    def _limpar_para_o_proximo(self) -> None:
        descricao = self.descricao.text().strip()
        self.valor.clear()  # (limpa também o aviso: ele é escrito depois)
        self.descricao.clear()
        self.categoria.setCurrentIndex(-1)
        self.dica_categoria.setVisible(False)
        self.parcelas.setCurrentIndex(0)
        self.repetir.setChecked(False)
        self._assinatura_manual = False
        self.completar.setModel(QStringListModel(lancamentos.descricoes_usadas(self.conn, self._tipo_categoria), self.completar))
        self.aviso_salvo.setText("✓ " + self._ultima_mensagem)
        self._oferecer_regra(descricao)
        self.valor.setFocus()

    def _oferecer_regra(self, descricao: str) -> None:
        """"Sempre categorizar '<trecho>' como <Categoria>?" com o trecho (descrição normalizada) editável."""
        if self._oferta is None:
            return
        self.padrao_regra.setText(lancamentos.padrao_sugerido(descricao))
        self.texto_oferta.setText(f"como {self._oferta[1]}")
        self.botao_criar_regra.setText("Criar regra")
        self.oferta_regra.setVisible(True)

    def criar_regra(self) -> bool:
        if self._oferta is None:
            return False
        try:
            cadastros.criar_regra(self.conn, self.padrao_regra.text(), self._oferta[0])
        except ValueError as erro:
            self.texto_oferta.setText(f"! {erro}")
            return False
        self.aviso_salvo.setText(f"✓ Regra criada: '{self.padrao_regra.text().strip()}' → {self._oferta[1]}")
        self._oferta = None
        self.oferta_regra.setVisible(False)
        return True

    # --- Excluir ------------------------------------------------------------------------------------
    @property
    def _tem_parcelas(self) -> bool:
        return bool(self._original and self._original["grupo_parcelas"])

    def pedir_exclusao(self) -> None:
        """Troca a barra de botões pela confirmação (sem abrir outra janela)."""
        if not self.editando:
            return
        if self._tem_parcelas:
            total = self._original["parcela_total"]
            self.texto_parcelas.setText(
                f"Esta é a parcela {self._original['parcela_atual']} de {total}. Excluir qual?"
            )
            self.botao_todas.setText(f"Todas as {total} parcelas")
            self._barra.setCurrentIndex(_BARRA_EXCLUIR_PARCELA)
            self.botao_nao_parcela.setFocus()
        else:
            self._barra.setCurrentIndex(_BARRA_EXCLUIR)
            self.botao_nao.setFocus()

    def cancelar_exclusao(self) -> None:
        self._barra.setCurrentIndex(_BARRA_NORMAL)
        self.botao_salvar.setFocus()

    def _descricao_e_valor(self) -> str:
        return f"{self._original['descricao']} ({formatar_brl(abs(self._original['valor_centavos']))})"

    def excluir_este(self) -> None:
        """Exclui só este lançamento (ou só esta parcela)."""
        if self._transf:
            lancamentos.excluir_transferencia(self.conn, self._transf["transferencia_id"])
        else:
            lancamentos.excluir(self.conn, [self.id_lancamento])
        self._concluir_exclusao(f"Excluído: {self._descricao_e_valor()}")

    def excluir_todas_as_parcelas(self) -> None:
        quantas = lancamentos.excluir_parcelas(self.conn, self._original["grupo_parcelas"])
        self._concluir_exclusao(f"Excluído: {self._original['descricao']} ({quantas} parcelas)")

    def _concluir_exclusao(self, mensagem: str) -> None:
        self.gravado.emit(mensagem)
        self.accept()

    def reject(self) -> None:
        """Esc: dentro da confirmação de exclusão, volta pro formulário; senão, fecha."""
        if self._barra.currentIndex() != _BARRA_NORMAL:
            self.cancelar_exclusao()
            return
        super().reject()

    # --- Tamanho --------------------------------------------------------------------------------------
    def showEvent(self, evento) -> None:
        if not self._ajustado:  # só depois do estilo aplicado os tamanhos dos campos são os de verdade
            self._ajustado = True
            self._ajustar_tamanho()
        super().showEvent(evento)

    def _ajustar_tamanho(self) -> None:
        """Altura pelo conteúdo, limitada à tela (se faltar espaço, o formulário rola)."""
        tela = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        # Mede com o "Qual cartão" aberto, pra janela não precisar crescer (nem rolar) ao escolher Cartão.
        reservar = len(self._cartoes) > 1 and not self._restrito and self._bloco_cartao.isHidden()
        reservar_assinatura = not self.editando and self.assinatura.isHidden()  # "É assinatura" aparece ao repetir
        if reservar:
            self._bloco_cartao.setVisible(True)
        if reservar_assinatura:
            self.assinatura.setVisible(True)
        self._corpo.layout().activate()
        natural = self._corpo.sizeHint().height() + self._barra.sizeHint().height() + 4
        if reservar:
            self._bloco_cartao.setVisible(False)
        if reservar_assinatura:
            self.assinatura.setVisible(False)
        self.setMinimumHeight(0)
        altura = min(natural, int(tela.height() * 0.9))
        self.resize(LARGURA, altura)
