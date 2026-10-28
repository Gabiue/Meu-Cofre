"""Aba "Assinaturas/fixos" (docs/design/telas.md): tudo que pinga todo mês, agrupado por onde cobra.

Topo: filtro (Assinaturas | Fixos | Ambos) e os números (saídas por mês, por ano, entradas, este mês).
Lista: um grupo por cartão ou conta, com o subtotal mensal; cada linha tem situação no mês, histórico e
as ações Editar, Pausar/Retomar e Encerrar (com confirmação na própria linha).
`alterado(mensagem)` avisa a janela principal a cada mudança gravada.
"""

import sqlite3
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import recorrentes
from meu_cofre.lancamentos import METODOS, formatar_brl, formatar_brl_inteiro
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    Cartao,
    NumeroDestaque,
    Pilula,
    RotuloElidido,
    Segmentado,
    _espacar_letras,
)
from meu_cofre.ui.dialogo_recorrente import DialogoRecorrente
from meu_cofre.ui.graficos import MESES_CURTOS, EstadoVazio

OPCOES_FILTRO = {"Assinaturas": "assinaturas", "Fixos": "fixos", "Ambos": "ambos"}
LARGURA_QUATRO_NUMEROS = 1000  # abaixo disso, os 4 números viram 2 × 2
LARGURA_LINHA_COMPACTA = 760  # abaixo disso, as ações descem pra baixo do texto
_PAG_ACOES, _PAG_CONFIRMAR = 0, 1
VAZIO = {
    "ambos": ("Nenhuma assinatura ou fixo ainda", "Ao lançar, marque Repetir todo mês"),
    "assinaturas": ("Nenhuma assinatura por aqui", "Ao lançar, marque Repetir todo mês e É assinatura"),
    "fixos": ("Nenhum gasto fixo por aqui", "Ao lançar, marque Repetir todo mês"),
}


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _acao(texto: str, perigo: bool = False) -> QPushButton:
    """Ação discreta da linha: texto com contorno só ao passar o mouse (o perigo fica vermelho)."""
    botao = QPushButton(texto)
    botao.setObjectName("BotaoAcao")
    botao.setProperty("perigo", perigo)
    botao.setCursor(Qt.CursorShape.PointingHandCursor)
    botao.setAutoDefault(False)
    botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    botao.setMinimumWidth(76)
    return botao


def _valor_com_sinal(centavos: int) -> str:
    return f"{'+' if centavos > 0 else '−'}{formatar_brl(abs(centavos))}"


def _mes_ano(dia: date) -> str:
    return f"{MESES_CURTOS[dia.month - 1]}/{dia.year}"


def texto_situacao(situacao: str, dias: int | None) -> tuple[str, str]:
    """(texto, variante da pílula) da situação no mês."""
    if situacao == "cobrada":
        return "cobrada", "feito"
    if situacao == "prevista":
        return ("cobra amanhã" if dias == 1 else f"cobra em {dias} dias"), "neutra"
    if situacao == "pausada":
        return "pausada", "atencao"
    if situacao == "encerrada":
        return "encerrada", "neutra"
    return "sem cobrança este mês", "neutra"


class LinhaRecorrente(QFrame):
    """Uma assinatura ou fixo. Emite os pedidos de ação com o id; o "Encerrar" pede confirmação aqui mesmo."""

    editar = Signal(int)
    alternar_pausa = Signal(int)
    encerrar = Signal(int)

    def __init__(self, r, parent: QWidget | None = None):
        super().__init__(parent)
        self.id_recorrente = int(r.id)
        self.setObjectName("LinhaRecorrente")
        apagada = bool(r.pausada) or not r.ativa

        self.nome = RotuloElidido(r.descricao, "RecorrenteNome")
        self.nome.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # a pílula fica colada no nome
        self.nome.setProperty("apagada", apagada)
        self.tipo = Pilula("assinatura" if r.assinatura else "fixo")
        titulo = QHBoxLayout()
        titulo.setSpacing(tema.ESPACO)
        titulo.addWidget(self.nome)
        titulo.addWidget(self.tipo)
        titulo.addStretch(1)

        partes = [f"todo dia {int(r.dia_do_mes)}"]
        if r.conta_tipo != "cartao" and r.metodo in METODOS:  # no grupo do cartão, "Cartão" seria repetição
            partes.append(METODOS[r.metodo])
        if isinstance(r.categoria, str):  # sem categoria vem NaN do pandas
            partes.append(r.categoria)
        self.quando = RotuloElidido(" · ".join(partes), "ItemDetalhe")
        self.historico = RotuloElidido(self._texto_historico(r), "ItemDetalhe")
        textos = QVBoxLayout()
        textos.setSpacing(2)
        textos.addLayout(titulo)
        textos.addWidget(self.quando)
        textos.addWidget(self.historico)

        self.valor = QLabel(_valor_com_sinal(int(r.valor_centavos)))
        self.valor.setObjectName("RecorrenteValor")
        self.valor.setProperty("apagada", apagada)
        texto, variante = texto_situacao(r.situacao_mes, None if r.dias_para_cobrar is None else int(r.dias_para_cobrar))
        self.situacao = Pilula(texto, variante)
        self.situacao.setToolTip(self._dica_situacao(r))
        direita = QVBoxLayout()
        direita.setSpacing(6)
        direita.addWidget(self.valor, 0, Qt.AlignmentFlag.AlignRight)
        direita.addWidget(self.situacao, 0, Qt.AlignmentFlag.AlignRight)

        principal = QWidget()
        linha_principal = QHBoxLayout(principal)
        linha_principal.setContentsMargins(0, 0, 0, 0)
        linha_principal.setSpacing(2 * tema.ESPACO)
        linha_principal.addLayout(textos, 1)
        linha_principal.addLayout(direita)

        self._acoes = self._montar_acoes(r)
        self._corpo = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._corpo.setContentsMargins(tema.ESPACO, tema.ESPACO + 2, tema.ESPACO, tema.ESPACO + 2)
        self._corpo.setSpacing(2 * tema.ESPACO)
        self._corpo.addWidget(principal, 1)
        self._corpo.addWidget(self._acoes, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _texto_historico(r) -> str:
        if r.total_ate_hoje > 0:
            return f"desde {_mes_ano(r.desde)} · {formatar_brl_inteiro(int(r.total_ate_hoje))} até hoje"
        return f"desde {_mes_ano(r.inicio)} · nada cobrado ainda"

    @staticmethod
    def _dica_situacao(r) -> str:
        if r.situacao_mes == "pausada":
            return "Pausada: não cobra até você retomar"
        if r.situacao_mes == "sem_cobranca":
            return "Nenhum lançamento deste item neste mês (talvez você o tenha apagado)"
        return ""

    def _montar_acoes(self, r) -> QStackedWidget:
        pilha = QStackedWidget()
        pilha.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        normal = QWidget()
        linha = QHBoxLayout(normal)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(tema.ESPACO)
        self.botao_editar = _acao("Editar")
        self.botao_editar.clicked.connect(lambda: self.editar.emit(self.id_recorrente))
        self.botao_pausa = _acao("Retomar" if r.pausada else "Pausar")
        self.botao_pausa.clicked.connect(lambda: self.alternar_pausa.emit(self.id_recorrente))
        self.botao_encerrar = _acao("Encerrar", perigo=True)
        self.botao_encerrar.clicked.connect(self.pedir_encerramento)
        for botao in (self.botao_editar, self.botao_pausa, self.botao_encerrar):
            linha.addWidget(botao)
        pilha.addWidget(normal)

        confirmar = QWidget()
        linha = QHBoxLayout(confirmar)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(tema.ESPACO)
        self.texto_confirmar = _rotulo("Encerrar mesmo?", "ConfirmaTexto")
        self.botao_nao = _acao("Não")
        self.botao_nao.clicked.connect(self.cancelar_encerramento)
        self.botao_sim = QPushButton("Sim, encerrar")
        self.botao_sim.setObjectName("BotaoPerigo")
        self.botao_sim.setProperty("compacto", True)
        self.botao_sim.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_sim.setAutoDefault(False)
        self.botao_sim.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_sim.clicked.connect(lambda: self.encerrar.emit(self.id_recorrente))
        for w in (self.texto_confirmar, self.botao_nao, self.botao_sim):
            linha.addWidget(w)
        pilha.addWidget(confirmar)

        pilha.setVisible(bool(r.ativa))
        return pilha

    def pedir_encerramento(self) -> None:
        self._acoes.setCurrentIndex(_PAG_CONFIRMAR)

    def cancelar_encerramento(self) -> None:
        self._acoes.setCurrentIndex(_PAG_ACOES)

    @property
    def confirmando(self) -> bool:
        return self._acoes.currentIndex() == _PAG_CONFIRMAR

    def resizeEvent(self, evento) -> None:
        compacto = evento.size().width() < LARGURA_LINHA_COMPACTA
        direcao = QBoxLayout.Direction.TopToBottom if compacto else QBoxLayout.Direction.LeftToRight
        if self._corpo.direction() != direcao:
            self._corpo.setDirection(direcao)
        super().resizeEvent(evento)


class AbaRecorrentes(QScrollArea):
    """Conteúdo da aba. `hoje` é uma função (a janela principal pode fixar a data nos testes)."""

    alterado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date] = date.today, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje
        self._mostrar_encerradas = False
        self.dialogo: DialogoRecorrente | None = None
        self._colunas_numeros: int | None = None

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        conteudo = QWidget()
        conteudo.setObjectName("Conteudo")
        coluna = QVBoxLayout(conteudo)
        m = 3 * tema.ESPACO
        coluna.setContentsMargins(m, tema.ESPACO, m, m)
        coluna.setSpacing(m)
        coluna.addWidget(self._montar_resumo())
        coluna.addWidget(self._montar_lista())
        coluna.addStretch(1)
        self.setWidget(conteudo)
        self._aplicar_colunas(4)
        self.recarregar()

    # --- Montagem --------------------------------------------------------------------
    def _montar_resumo(self) -> Cartao:
        self.filtro = Segmentado(list(OPCOES_FILTRO), "Ambos")
        self.filtro.escolhido.connect(lambda _o: self.recarregar())
        cartao = Cartao("Assinaturas e fixos", "O que se repete todo mês", acao=self.filtro)
        self._saidas = NumeroDestaque("Saídas fixas por mês", "", "")
        self._ano = NumeroDestaque("Por ano", "", "", destaque=True)
        self._entradas = NumeroDestaque("Entradas fixas por mês", "", "")
        self._este_mes = NumeroDestaque("Este mês", "", "")
        self._grade_numeros = QGridLayout()
        self._grade_numeros.setHorizontalSpacing(3 * tema.ESPACO)
        self._grade_numeros.setVerticalSpacing(3 * tema.ESPACO)
        cartao.corpo.addLayout(self._grade_numeros)
        return cartao

    def _montar_lista(self) -> Cartao:
        self._cartao_lista = Cartao("Cobranças", "Agrupadas por onde cobram")
        self._lista = QVBoxLayout()
        self._lista.setSpacing(0)
        self._cartao_lista.corpo.addLayout(self._lista)
        self._vazio = EstadoVazio("lista")
        self._vazio.setMinimumHeight(200)
        self._cartao_lista.corpo.addWidget(self._vazio)
        self.botao_encerradas = QPushButton()
        self.botao_encerradas.setObjectName("BotaoLink")
        self.botao_encerradas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_encerradas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_encerradas.clicked.connect(lambda: self.mostrar_encerradas(not self._mostrar_encerradas))
        self._cartao_lista.corpo.addWidget(self.botao_encerradas, 0, Qt.AlignmentFlag.AlignLeft)
        return self._cartao_lista

    def _aplicar_colunas(self, colunas: int) -> None:
        if colunas == self._colunas_numeros:
            return
        self._colunas_numeros = colunas
        numeros = (self._saidas, self._ano, self._entradas, self._este_mes)
        for w in numeros:
            self._grade_numeros.removeWidget(w)
        for c in range(4):
            self._grade_numeros.setColumnStretch(c, 1 if c < colunas else 0)
        for i, w in enumerate(numeros):
            self._grade_numeros.addWidget(w, i // colunas, i % colunas)

    def resizeEvent(self, evento) -> None:
        self._aplicar_colunas(4 if evento.size().width() >= LARGURA_QUATRO_NUMEROS else 2)
        super().resizeEvent(evento)

    # --- Dados ------------------------------------------------------------------------
    @property
    def filtro_atual(self) -> str:
        return OPCOES_FILTRO[self.filtro.atual()]

    def recarregar(self) -> None:
        hoje = self._hoje()
        filtro = self.filtro_atual
        self._carregar_numeros(hoje, filtro)
        todas = recorrentes.listar(self.conn, hoje, filtro, incluir_encerradas=True)
        ativas = todas[todas["ativa"]]
        encerradas = todas[~todas["ativa"]]

        self._esvaziar()
        for _conta_id, grupo in ativas.groupby("conta_id", sort=False):
            self._lista.addWidget(self._montar_grupo(grupo))
        if self._mostrar_encerradas and not encerradas.empty:
            self._lista.addWidget(self._montar_grupo(encerradas, "Encerradas"))

        vazio = ativas.empty and not (self._mostrar_encerradas and not encerradas.empty)
        if vazio:
            existe_alguma = not recorrentes.listar(self.conn, hoje, "ambos").empty
            self._vazio.definir(*(VAZIO[filtro] if existe_alguma else VAZIO["ambos"]))
        self._vazio.setVisible(vazio)
        self.botao_encerradas.setVisible(not encerradas.empty)
        self.botao_encerradas.setText(
            "Esconder encerradas" if self._mostrar_encerradas else f"Mostrar encerradas ({len(encerradas)})"
        )
        self._cartao_lista.subtitulo.setText(
            "Agrupadas por onde cobram" if not ativas.empty else "Nada ativo por enquanto"
        )

    def _carregar_numeros(self, hoje: date, filtro: str) -> None:
        r = recorrentes.resumo(self.conn, hoje, filtro)
        df = recorrentes.listar(self.conn, hoje, filtro)
        df = df[~df["pausada"]]
        n_saidas = int((df["valor_centavos"] < 0).sum())
        n_entradas = int((df["valor_centavos"] > 0).sum())
        self._saidas.definir(formatar_brl(r["saidas_por_mes"]), _contagem(n_saidas, "cobrança ativa", "cobranças ativas"))
        self._ano.definir(formatar_brl(r["saidas_por_ano"]), "12 meses disso")
        self._entradas.definir(formatar_brl(r["entradas_por_mes"]), _contagem(n_entradas, "entrada fixa", "entradas fixas"))
        n_cobradas, v_cobradas = r["este_mes_cobradas"]
        n_a_cobrar, v_a_cobrar = r["este_mes_a_cobrar"]
        self._este_mes.definir(
            formatar_brl(v_cobradas + v_a_cobrar),
            f"{_contagem(n_cobradas, 'cobrada', 'cobradas')} · {formatar_brl(v_cobradas)}\n{n_a_cobrar} a cobrar · {formatar_brl(v_a_cobrar)}",
        )

    def _esvaziar(self) -> None:
        while self._lista.count():
            widget = self._lista.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _montar_grupo(self, grupo, nome: str | None = None) -> QWidget:
        """Cabeçalho (onde cobra + subtotal mensal) e as linhas dele."""
        quadro = QWidget()
        coluna = QVBoxLayout(quadro)
        coluna.setContentsMargins(0, 0, 0, tema.ESPACO)
        coluna.setSpacing(2)

        cabecalho = QHBoxLayout()
        cabecalho.setContentsMargins(tema.ESPACO, tema.ESPACO, tema.ESPACO, 4)
        titulo = _rotulo((nome or grupo.iloc[0]["conta"]).upper(), "GrupoNome")
        _espacar_letras(titulo, 1.0)
        cabecalho.addWidget(titulo)
        cabecalho.addStretch(1)
        if nome is None:
            subtotal = _rotulo(_subtotal(grupo), "GrupoSubtotal")
            cabecalho.addWidget(subtotal)
        coluna.addLayout(cabecalho)
        separador = QFrame()
        separador.setObjectName("Separador")
        separador.setFixedHeight(1)
        coluna.addWidget(separador)

        for r in grupo.itertuples():
            linha = LinhaRecorrente(r)
            linha.editar.connect(self.editar)
            linha.alternar_pausa.connect(self.alternar_pausa)
            linha.encerrar.connect(self.encerrar)
            coluna.addWidget(linha)
        return quadro

    # --- Ações ------------------------------------------------------------------------------
    def mostrar_encerradas(self, mostrar: bool) -> None:
        self._mostrar_encerradas = mostrar
        self.recarregar()

    def _nome(self, recorrente_id: int) -> str:
        return recorrentes.obter(self.conn, recorrente_id)["descricao"]

    def editar(self, recorrente_id: int) -> DialogoRecorrente | None:
        """Abre o diálogo de edição; ao salvar, a janela principal recarrega tudo."""
        try:
            dialogo = DialogoRecorrente(self.conn, recorrente_id, parent=self, hoje=self._hoje())
        except ValueError:
            self.recarregar()
            return None
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._avisar)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo", None))
        self.dialogo = dialogo
        dialogo.open()
        return dialogo

    def alternar_pausa(self, recorrente_id: int) -> None:
        hoje = self._hoje()
        pausada = recorrentes.obter(self.conn, recorrente_id)["pausada"]
        nome = self._nome(recorrente_id)
        if pausada:
            recorrentes.retomar(self.conn, recorrente_id, hoje)
            recorrentes.gerar_pendentes(self.conn, hoje)
            self._avisar(f"Retomado: {nome}")
        else:
            recorrentes.pausar(self.conn, recorrente_id, hoje)
            self._avisar(f"Pausado: {nome}")

    def encerrar(self, recorrente_id: int) -> None:
        nome = self._nome(recorrente_id)
        recorrentes.encerrar(self.conn, recorrente_id, self._hoje())
        self._avisar(f"Encerrado: {nome}. O histórico fica.")

    def _avisar(self, mensagem: str) -> None:
        self.recarregar()
        self.alterado.emit(mensagem)

    def linhas(self) -> list[LinhaRecorrente]:
        return self.findChildren(LinhaRecorrente)


def _contagem(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _subtotal(grupo) -> str:
    """"−R$ 1.500,00 /mês" e, se o grupo tem entradas, "+R$ 5.200,00 /mês" (pausadas não entram)."""
    ativos = grupo[~grupo["pausada"]]
    saidas = int(-ativos.loc[ativos["valor_centavos"] < 0, "valor_centavos"].sum())
    entradas = int(ativos.loc[ativos["valor_centavos"] > 0, "valor_centavos"].sum())
    partes = []
    if saidas:
        partes.append(f"−{formatar_brl(saidas)} /mês")
    if entradas:
        partes.append(f"+{formatar_brl(entradas)} /mês")
    return " · ".join(partes) if partes else "pausado"
