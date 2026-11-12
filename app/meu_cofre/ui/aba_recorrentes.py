"""Aba "Assinaturas/fixos" (docs/design/telas.md): tudo que pinga todo mês, agrupado por onde cobra.

Topo: quatro números (gasto mensal, gasto anual, próximo pagamento, ativas). Embaixo, a lista (com o filtro
Assinaturas | Fixos | Ambos) e, ao lado (ou abaixo, em janela estreita), os próximos pagamentos numa linha do tempo
e a rosca das saídas por categoria (com subcategoria, "Assinaturas" se divide em streaming, música etc.).
Lista: um grupo por cartão ou conta, com o subtotal mensal; cada linha tem a situação no mês e o menu "⋯" com
Editar, Pausar/Retomar e Encerrar (com confirmação na própria linha). O histórico fica na dica da linha.
`alterado(mensagem)` avisa a janela principal a cada mudança gravada.
"""

import calendar
import sqlite3
from collections.abc import Callable
from datetime import date

import pandas as pd
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import recorrentes
from meu_cofre.relatorios import top_com_outros
from meu_cofre.lancamentos import METODOS, formatar_brl, formatar_brl_inteiro
from meu_cofre.ui import logos, tema
from meu_cofre.ui.componentes import (
    BotaoMenu,
    BotaoPrimario,
    BotaoSecundario,
    Marcador,
    Cartao,
    GradeResponsiva,
    NumeroDestaque,
    Pilula,
    RotuloElidido,
    Segmentado,
)
from meu_cofre.ui.dialogo_recorrente import DialogoRecorrente
from meu_cofre.ui.graficos import MESES_CURTOS, EstadoVazio, PainelRosca

OPCOES_FILTRO = {"Assinaturas": "assinaturas", "Fixos": "fixos", "Ambos": "ambos"}
LARGURA_LADO_A_LADO = 1100  # abaixo disso, os próximos pagamentos descem pra baixo da lista
LARGURA_ROSCA_LADO_A_LADO = 640  # na coluna lateral a legenda fica embaixo; abaixo da lista, ao lado
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


def proximas_cobrancas(df: pd.DataFrame, hoje: date, n: int = 3) -> list[tuple[date, str, int]]:
    """As `n` próximas saídas (data, descrição, centavos positivos) das ativas e não pausadas: a deste mês se
    ainda vai cobrar; se já cobrou (ou não cobra este mês), a do mês que vem."""
    seguinte = date(hoje.year + hoje.month // 12, hoje.month % 12 + 1, 1)
    itens = []
    for r in df[(df["valor_centavos"] < 0) & ~df["pausada"]].itertuples():
        ano, mes = (hoje.year, hoje.month) if r.situacao_mes == "prevista" else (seguinte.year, seguinte.month)
        dia = min(int(r.dia_do_mes), calendar.monthrange(ano, mes)[1])
        itens.append((date(ano, mes, dia), r.descricao, -int(r.valor_centavos)))
    return sorted(itens)[:n]


class ProximosPagamentos(QWidget):
    """Linha do tempo vertical: um ponto por pagamento, ligados por um trilho; prazo e nome à esquerda, valor à direita."""

    RAIO = 6
    RECUO = 28  # espaço do trilho à esquerda dos textos

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._linhas = QVBoxLayout(self)
        self._linhas.setContentsMargins(self.RECUO, 0, 0, 0)
        self._linhas.setSpacing(2 * tema.ESPACO)

    def definir(self, itens: list[tuple[date, str, int]], hoje: date) -> None:
        while self._linhas.count():
            w = self._linhas.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for quando, nome, centavos in itens:
            dias = (quando - hoje).days
            prazo = "Hoje" if dias == 0 else "Amanhã" if dias == 1 else f"Em {dias} dias"
            dia = f"{quando.day} {MESES_CURTOS[quando.month - 1]}"
            linha = QWidget()
            grade = QHBoxLayout(linha)
            grade.setContentsMargins(0, 0, 0, 0)
            textos = QVBoxLayout()
            textos.setSpacing(2)
            textos.addWidget(_rotulo(f"{prazo} · {dia}", "ItemDescricao"))
            textos.addWidget(RotuloElidido(nome, "ItemDetalhe"))
            grade.addLayout(textos, 1)
            grade.addWidget(_rotulo(formatar_brl(centavos), "ItemValor"), 0, Qt.AlignmentFlag.AlignTop)
            self._linhas.addWidget(linha)
        self.update()

    def paintEvent(self, evento) -> None:
        linhas = [self._linhas.itemAt(i).widget() for i in range(self._linhas.count())]
        if not linhas:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = self.RECUO / 2 - 2
        centros = [w.y() + 10 for w in linhas]  # na altura da primeira linha de texto
        p.setPen(QPen(QColor(tema.BORDA), 2))
        p.drawLine(QPointF(x, centros[0]), QPointF(x, centros[-1]))
        for i, y in enumerate(centros):
            p.setPen(QPen(QColor(tema.CARTAO), 2))
            p.setBrush(QColor(tema.DOURADO if i == 0 else tema.VERDE))  # o mais próximo em destaque
            p.drawEllipse(QPointF(x, y), self.RAIO, self.RAIO)


class LinhaRecorrente(QFrame):
    """Uma assinatura ou fixo. Emite os pedidos de ação com o id; o "Encerrar" pede confirmação aqui mesmo.

    Na simulação "E se eu cancelasse?", saída ativa e não pausada ganha uma caixinha (`simular`); marcar emite `marcada`.
    """

    marcada = Signal(int, bool)
    editar = Signal(int)
    alternar_pausa = Signal(int)
    encerrar = Signal(int)
    reativar = Signal(int)

    def __init__(self, r, simulando: bool = False, marcada: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.id_recorrente = int(r.id)
        self.setObjectName("LinhaRecorrente")
        apagada = bool(r.pausada) or not r.ativa

        self.marcador = marcador = QLabel(r.descricao[:1].upper())
        marcador.setObjectName("ItemMarcador")
        marcador.setProperty("tipo", "entrada" if r.valor_centavos > 0 else "saida")
        marcador.setFixedSize(32, 32)
        marcador.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if (icone := logos.icone_para(r.descricao)) is not None:  # logo da marca ou ícone da conta; sem par, a inicial
            marcador.setPixmap(logos.pixmap_do_icone(*icone, 18))
            marcador.setProperty("tipo", "logo")
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
        self.setToolTip(self._texto_historico(r))  # o histórico não precisa de uma linha só pra ele
        textos = QVBoxLayout()
        textos.setSpacing(2)
        textos.addLayout(titulo)
        textos.addWidget(self.quando)

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

        self._acoes = self._montar_acoes(r)
        self._corpo = QHBoxLayout(self)
        self._corpo.setContentsMargins(tema.ESPACO, tema.ESPACO, tema.ESPACO, tema.ESPACO)
        self._corpo.setSpacing(12)
        self.simular = Marcador("")
        self.simular.setAccessibleName(f"Simular o cancelamento de {r.descricao}")
        self.simular.setChecked(marcada)
        self.simular.toggled.connect(lambda v: self.marcada.emit(self.id_recorrente, v))
        self.simular.setVisible(simulando and r.ativa and not r.pausada and r.valor_centavos < 0)
        self._corpo.addWidget(self.simular, 0, Qt.AlignmentFlag.AlignVCenter)
        self._corpo.addWidget(marcador, 0, Qt.AlignmentFlag.AlignVCenter)
        self._corpo.addLayout(textos, 1)
        self._corpo.addLayout(direita)
        self._corpo.addWidget(self._acoes, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.botao_reativar = _acao("Reativar")
        self.botao_reativar.clicked.connect(lambda: self.reativar.emit(self.id_recorrente))
        self.botao_reativar.setVisible(not r.ativa)
        self._corpo.addWidget(self.botao_reativar, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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

    def _montar_acoes(self, r) -> QWidget:
        """O "⋯" ou, ao pedir pra encerrar, a confirmação no lugar dele (só um visível, pra não reservar largura)."""
        acoes = QWidget()
        acoes.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        caixa = QHBoxLayout(acoes)
        caixa.setContentsMargins(0, 0, 0, 0)
        # as três ações num menu: 3 botões por linha amontoavam a lista
        self.botao_menu = BotaoMenu(f"Ações de {r.descricao}", "Editar, pausar ou encerrar")
        self.botao_editar = self.botao_menu.acao("Editar", lambda: self.editar.emit(self.id_recorrente))
        self.botao_pausa = self.botao_menu.acao(
            "Retomar" if r.pausada else "Pausar", lambda: self.alternar_pausa.emit(self.id_recorrente)
        )
        self.botao_menu.separador()
        self.botao_encerrar = self.botao_menu.acao("Encerrar…", self.pedir_encerramento)
        caixa.addWidget(self.botao_menu)

        self._confirmar = confirmar = QWidget()
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
        confirmar.setVisible(False)
        caixa.addWidget(confirmar)

        acoes.setVisible(bool(r.ativa))
        return acoes

    def pedir_encerramento(self) -> None:
        self.botao_menu.setVisible(False)
        self._confirmar.setVisible(True)

    def cancelar_encerramento(self) -> None:
        self._confirmar.setVisible(False)
        self.botao_menu.setVisible(True)

    @property
    def confirmando(self) -> bool:
        return not self._confirmar.isHidden()


class AbaRecorrentes(QScrollArea):
    """Conteúdo da aba. `hoje` é uma função (a janela principal pode fixar a data nos testes)."""

    alterado = Signal(str)
    criar_meta = Signal(str, int, int)  # nome, valor (o ano de economia), mensal: a simulação vira meta na aba Metas

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date] = date.today, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje
        self._mostrar_encerradas = False
        self._simulando = False
        self._cancelar: set[int] = set()  # o que está marcado na simulação
        self._economia: dict = {"nomes": []}
        self.dialogo: DialogoRecorrente | None = None

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        conteudo = QWidget()
        conteudo.setObjectName("Conteudo")
        coluna = QVBoxLayout(conteudo)
        m = 3 * tema.ESPACO
        coluna.setContentsMargins(m, m, m, m)
        coluna.setSpacing(m)
        coluna.addWidget(self._montar_numeros())
        self._corpo = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._corpo.setSpacing(m)
        self._corpo.addWidget(self._montar_lista(), 2, Qt.AlignmentFlag.AlignTop)  # não estica com a coluna ao lado
        lateral = QWidget()
        coluna_lateral = QVBoxLayout(lateral)
        coluna_lateral.setContentsMargins(0, 0, 0, 0)
        coluna_lateral.setSpacing(m)
        coluna_lateral.addWidget(self._montar_proximos())
        coluna_lateral.addWidget(self._montar_categorias())
        self._corpo.addWidget(lateral, 1, Qt.AlignmentFlag.AlignTop)
        coluna.addLayout(self._corpo)
        coluna.addStretch(1)
        self.setWidget(conteudo)
        self.recarregar()

    # --- Montagem --------------------------------------------------------------------
    def _montar_numeros(self) -> GradeResponsiva:
        self._mensal = NumeroDestaque("Gasto mensal", "", "", destaque=True)
        self._anual = NumeroDestaque("Gasto anual", "", "")
        self.botao_simular = QPushButton("E se eu cancelasse?")
        self.botao_simular.setObjectName("BotaoLink")
        self.botao_simular.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_simular.setToolTip("Marque na lista o que cancelaria e veja quanto sobra por mês e por ano")
        self.botao_simular.clicked.connect(lambda: self.simular(not self._simulando))
        self._anual.adicionar(self.botao_simular)
        self._proximo = NumeroDestaque("Próximo pagamento", "", "")
        self._ativas = NumeroDestaque("Ativas", "", "")
        grade = GradeResponsiva(largura_minima_item=220, espaco=3 * tema.ESPACO, colunas_permitidas=(4, 2, 1))
        for numero in (self._mensal, self._anual, self._proximo, self._ativas):
            cartao = Cartao()
            cartao.corpo.addWidget(numero)
            grade.adicionar(cartao)
        return grade

    def _montar_proximos(self) -> Cartao:
        self._cartao_proximos = Cartao("Próximos pagamentos")
        self.proximos = ProximosPagamentos()
        self._cartao_proximos.corpo.addWidget(self.proximos)
        return self._cartao_proximos

    def _montar_categorias(self) -> Cartao:
        self._cartao_categorias = Cartao("Por categoria", "Saídas por mês")
        self.rosca = PainelRosca("por mês", largura_lado_a_lado=LARGURA_ROSCA_LADO_A_LADO)
        self.rosca.setMinimumHeight(260)
        self._cartao_categorias.corpo.addWidget(self.rosca)
        return self._cartao_categorias

    def _montar_lista(self) -> Cartao:
        self.filtro = Segmentado(list(OPCOES_FILTRO), "Ambos")
        self.filtro.escolhido.connect(lambda _o: self.recarregar())
        self._cartao_lista = Cartao("Assinaturas e fixos", "Agrupadas por onde cobram", acao=self.filtro)
        self._lista = QVBoxLayout()
        self._lista.setSpacing(0)
        self._cartao_lista.corpo.addWidget(self._montar_simulacao())
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

    def _montar_simulacao(self) -> QFrame:
        """A barra da simulação: quanto sobraria com o que está marcado, e o botão que transforma isso numa meta."""
        self.barra_simulacao = QFrame()
        self.barra_simulacao.setObjectName("FormPagamento")
        linha = QHBoxLayout(self.barra_simulacao)
        linha.setContentsMargins(2 * tema.ESPACO, tema.ESPACO + 4, 2 * tema.ESPACO, tema.ESPACO + 4)
        linha.setSpacing(2 * tema.ESPACO)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        # linhas que não quebram (quebrada, a linha era cortada dentro do layout): quem sai, e quanto sobra
        self.texto_simulacao = RotuloElidido("", "ConfirmaTexto")
        self.valor_simulacao = RotuloElidido("", "PreviaDestaque")
        self.apoio_simulacao = RotuloElidido("", "ItemDetalhe")
        for w in (self.texto_simulacao, self.valor_simulacao, self.apoio_simulacao):
            textos.addWidget(w)
        linha.addLayout(textos, 1)
        self.botao_meta = BotaoPrimario("Guardar numa meta")
        self.botao_meta.setProperty("compacto", True)
        self.botao_meta.setToolTip("Abre uma meta nova na aba Metas com essa economia por mês")
        self.botao_meta.clicked.connect(self._guardar_economia)
        self.botao_sair_simulacao = BotaoSecundario("Sair da simulação")
        self.botao_sair_simulacao.setProperty("compacto", True)
        self.botao_sair_simulacao.clicked.connect(lambda: self.simular(False))
        linha.addWidget(self.botao_meta, 0, Qt.AlignmentFlag.AlignVCenter)
        linha.addWidget(self.botao_sair_simulacao, 0, Qt.AlignmentFlag.AlignVCenter)
        self.barra_simulacao.setVisible(False)
        return self.barra_simulacao

    # --- Simulação "E se eu cancelasse?" ---------------------------------------------------
    def simular(self, ligar: bool) -> None:
        """Liga/desliga a simulação. Desligar limpa as marcas: simular nunca muda os dados."""
        self._simulando = ligar
        self._cancelar.clear()
        self.recarregar()

    def _marcar(self, recorrente_id: int, marcada: bool) -> None:
        (self._cancelar.add if marcada else self._cancelar.discard)(recorrente_id)
        self._carregar_simulacao()

    def _carregar_simulacao(self) -> None:
        self.barra_simulacao.setVisible(self._simulando)
        self.botao_simular.setVisible(not self._simulando)  # na simulação, quem sai dela é o botão da barra
        if not self._simulando:
            return
        e = recorrentes.economia(self.conn, self._hoje(), self._cancelar)
        self._economia = e
        if not e["nomes"]:
            self.texto_simulacao.setTexto("Marque o que você cancelaria")
            self.valor_simulacao.setTexto("pra ver quanto sobra")
        else:
            self.texto_simulacao.setTexto(f"Cancelando {_juntar(e['nomes'])}")
            self.valor_simulacao.setTexto(f"sobram {formatar_brl(e['mensal'])}/mês · {formatar_brl(e['anual'])}/ano")
        pct = e["pct_entradas"]
        self.apoio_simulacao.setTexto(
            (f"{pct:.1f}% das entradas fixas · ".replace(".", ",") if pct is not None and e["nomes"] else "")
            + "simulação: nada é cancelado de verdade"
        )
        self.botao_meta.setEnabled(bool(e["nomes"]))

    def _guardar_economia(self) -> None:
        e = self._economia
        if e["nomes"]:
            self.criar_meta.emit(f"Economia: {_juntar(e['nomes'], 2)}", e["anual"], e["mensal"])

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
        self._carregar_simulacao()
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
        saidas = df[df["valor_centavos"] < 0]
        n_cobradas, _v = r["este_mes_cobradas"]
        n_ativas = int((~saidas["pausada"]).sum())
        self._mensal.definir(formatar_brl(r["saidas_por_mes"]), f"{_contagem(n_cobradas, 'já cobrada', 'já cobradas')} este mês")
        entradas = recorrentes.resumo(self.conn, hoje, "ambos")["entradas_por_mes"]  # a renda fixa não depende do filtro
        self._anual.definir(
            formatar_brl(r["saidas_por_ano"]),
            f"{r['saidas_por_mes'] / entradas * 100:.0f}% das entradas fixas" if entradas else "12 meses disso",
        )
        proximas = proximas_cobrancas(df, hoje, 5)
        if proximas:
            quando, nome, centavos = proximas[0]
            dias = (quando - hoje).days
            self._proximo.definir("Hoje" if dias == 0 else "Amanhã" if dias == 1 else f"Em {dias} dias",
                                  f"{nome} · {formatar_brl(centavos)}")
        else:
            self._proximo.definir("—", "Nada a pagar")
        pausadas = len(saidas) - n_ativas
        self._ativas.definir(f"{n_ativas} de {len(saidas)}",
                             _contagem(pausadas, "pausada", "pausadas") if pausadas else "nenhuma pausada")
        self.proximos.definir(proximas, hoje)
        self._cartao_proximos.setVisible(bool(proximas))
        ativas = saidas[~saidas["pausada"]]
        por_categoria = pd.DataFrame({
            "categoria": ativas["categoria"].fillna("A categorizar"), "centavos": -ativas["valor_centavos"],
        }).groupby("categoria", as_index=False)["centavos"].sum()
        self.rosca.definir(top_com_outros(por_categoria), hoje.year, hoje.month, True)
        self._cartao_categorias.setVisible(not ativas.empty)

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
        titulo = _rotulo(nome or grupo.iloc[0]["conta"], "GrupoNome")
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
            linha = LinhaRecorrente(r, self._simulando, int(r.id) in self._cancelar)
            linha.marcada.connect(self._marcar)
            linha.editar.connect(self.editar)
            linha.alternar_pausa.connect(self.alternar_pausa)
            linha.encerrar.connect(self.encerrar)
            linha.reativar.connect(self.reativar)
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

    def reativar(self, recorrente_id: int) -> None:
        nome = self._nome(recorrente_id)
        recorrentes.reativar(self.conn, recorrente_id, self._hoje())
        self._avisar(f"Reativado: {nome}. Volta a cobrar a partir deste mês.")

    def _avisar(self, mensagem: str) -> None:
        self.recarregar()
        self.alterado.emit(mensagem)

    def linhas(self) -> list[LinhaRecorrente]:
        return self.findChildren(LinhaRecorrente)

    def resizeEvent(self, evento) -> None:
        estreita = evento.size().width() < LARGURA_LADO_A_LADO
        direcao = QBoxLayout.Direction.TopToBottom if estreita else QBoxLayout.Direction.LeftToRight
        if self._corpo.direction() != direcao:
            self._corpo.setDirection(direcao)
        super().resizeEvent(evento)


def _juntar(nomes: list[str], maximo: int = 3) -> str:
    """"Netflix", "Netflix e Spotify", "Netflix, Spotify e Max"; passando de `maximo`, "... e mais 2"."""
    if len(nomes) > maximo:
        return f"{', '.join(nomes[:maximo])} e mais {len(nomes) - maximo}"
    return nomes[0] if len(nomes) == 1 else f"{', '.join(nomes[:-1])} e {nomes[-1]}"


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
