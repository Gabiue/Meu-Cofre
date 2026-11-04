"""Aba "Cartões" (docs/design/telas.md, F6): o cartão é uma "caixa" por fatura.

Topo: seletor de cartão (se houver mais de um), a fatura fechada a pagar e a fatura aberta.
Lista: as faturas do cartão em cartões expansíveis (aberta, fechada, próximas e, recolhidas, as pagas), cada
uma com as suas compras e a parcela destacada ("2/6").
"Marcar como paga" abre a confirmação aqui mesmo (valor, conta de origem, data); pagar menos que o total deixa
a fatura "parcial" (mostra o restante e deixa pagar de novo). Logo após pagar, o destaque oferece "Desfazer";
a fatura paga também tem "Desfazer pagamento" (o último), com confirmação na linha. `alterado(mensagem)` avisa a janela principal.
"""

import sqlite3
from collections.abc import Callable
from datetime import date

import pandas as pd
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
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

from meu_cofre import cadastros, faturas, lancamentos
from meu_cofre.faturas import Fatura
from meu_cofre.lancamentos import formatar_brl, nome_com_final, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    CampoData,
    CampoLista,
    Cartao,
    GradeResponsiva,
    NumeroDestaque,
    Pilula,
    RotuloElidido,
    Segmentado,
    _espacar_letras,
    _repolir,
)
from meu_cofre.ui.dialogo_contas import detalhe_cartao
from meu_cofre.ui.graficos import MESES, MESES_CURTOS, EstadoVazio, PainelRosca

LARGURA_MINIMA_BLOCO = 300
LARGURA_ROSCA_LADO_A_LADO = 960  # largura do painel: janela de ~1100 px pra cima fica lado a lado
_PAG_ACOES, _PAG_CONFIRMAR = 0, 1


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _dia_mes(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def _dias(n: int) -> str:
    return f"{n} dia" if n == 1 else f"{n} dias"


def mes_da_referencia(referencia: str, hoje: date) -> str:
    """"novembro" (com o ano, se não for o de hoje: "janeiro de 2027")."""
    ano, mes = int(referencia[:4]), int(referencia[5:])
    return MESES[mes - 1] + (f" de {ano}" if ano != hoje.year else "")


def titulo_da_fatura(f: Fatura, hoje: date) -> str:
    """"Fatura de novembro": o mês do vencimento."""
    return f"Fatura de {mes_da_referencia(f.referencia, hoje)}"


def situacao_da_fatura(f: Fatura, hoje: date) -> tuple[str, str]:
    """(texto, variante da pílula) da situação."""
    if f.situacao == "paga":
        return "paga", "feito"
    if f.situacao == "parcial":
        return "parcial", "atencao"
    if f.situacao == "aberta":
        return "em aberto", "neutra"
    if f.situacao == "futura":
        return "próxima", "neutra"
    dias = (f.vencimento - hoje).days
    if dias < 0:
        return f"venceu há {_dias(-dias)}", "ruim"
    if dias == 0:
        return "vence hoje", "atencao"
    if dias == 1:
        return "vence amanhã", "atencao"
    if dias <= 7:
        return f"vence em {dias} dias", "atencao"
    return "a pagar", "atencao"


def detalhe_da_fatura(f: Fatura) -> str:
    """Linha de apoio: quando fecha/fechou e vence, ou quando foi paga."""
    if f.situacao == "paga":
        pago = f"pagou {formatar_brl(f.pago_centavos)}" if f.pago_centavos != f.total_centavos else "paga"
        if f.a_mais_centavos:
            pago += f" (pago {formatar_brl(f.a_mais_centavos)} a mais)"
        return f"{pago} em {_dia_mes(f.data_pagamento)} · vencia {_dia_mes(f.vencimento)}"
    if f.situacao == "parcial":
        return f"Pago {formatar_brl(f.pago_centavos)} · falta {formatar_brl(f.restante_centavos)} · vence {_dia_mes(f.vencimento)}"
    verbo = "fechou" if f.situacao == "fechada" else "fecha"
    return f"{verbo} {_dia_mes(f.fechamento)} · vence {_dia_mes(f.vencimento)}"


# --- Confirmação de pagamento ----------------------------------------------------------------


class FormPagamento(QFrame):
    """"Marcar como paga": valor (editável), conta de origem (corrente; Inter por padrão) e data."""

    confirmado = Signal(int, int, date)  # valor em centavos, id da conta, data
    cancelado = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FormPagamento")
        grade = QGridLayout(self)
        p = 2 * tema.ESPACO
        grade.setContentsMargins(p, p, p, p)
        grade.setHorizontalSpacing(2 * tema.ESPACO)
        grade.setVerticalSpacing(4)
        grade.setColumnStretch(0, 1)
        grade.setColumnStretch(1, 1)
        grade.setColumnStretch(2, 1)

        self.titulo = _rotulo("", "NumeroRotulo")
        _espacar_letras(self.titulo, 1.0)
        grade.addWidget(self.titulo, 0, 0, 1, 3)
        grade.setRowMinimumHeight(0, 22)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        self.conta = CampoLista()
        self.data = CampoData()
        for coluna, (texto, campo) in enumerate(
            (("Valor pago (R$)", self.valor), ("Pago com", self.conta), ("Data do pagamento", self.data))
        ):
            grade.addWidget(_rotulo(texto, "CampoRotulo"), 1, coluna)
            grade.addWidget(campo, 2, coluna)
        self.erro = _rotulo("", "CampoErro")
        self.erro.setWordWrap(True)
        self.erro.setFixedHeight(22)
        grade.addWidget(self.erro, 3, 0, 1, 3)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        self.botao_cancelar = BotaoSecundario("Cancelar")
        self.botao_cancelar.setProperty("compacto", True)
        self.botao_cancelar.setAutoDefault(False)
        self.botao_cancelar.clicked.connect(self.cancelado.emit)
        self.botao_confirmar = BotaoPrimario("Confirmar pagamento")
        self.botao_confirmar.setProperty("compacto", True)
        self.botao_confirmar.setAutoDefault(False)
        self.botao_confirmar.clicked.connect(self.confirmar)
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_confirmar)
        grade.addLayout(botoes, 4, 0, 1, 3)

        self.valor.returnPressed.connect(self.confirmar)
        self.valor.textChanged.connect(lambda _t: self._limpar_erro())

    def preparar(self, titulo: str, total_centavos: int, hoje: date, contas: list[tuple[int, str]]) -> None:
        """Preenche com o total da fatura, a conta Inter (ou a primeira corrente) e a data de hoje."""
        self.titulo.setText(titulo.upper())
        self.valor.setText(formatar_brl(total_centavos).removeprefix("R$ "))
        self.conta.clear()
        for id_, nome in contas:
            self.conta.addItem(nome, id_)
        self.conta.setCurrentIndex(max(self.conta.findText("Inter"), 0))
        self.data.setDate(QDate(hoje.year, hoje.month, hoje.day))
        self._limpar_erro()

    def confirmar(self) -> bool:
        try:
            centavos = para_centavos(self.valor.text())
        except ValueError:
            self._mostrar_erro("Valor inválido. Use só números, como 1.234,56.")
            return False
        if centavos <= 0:
            self._mostrar_erro("O valor pago precisa ser maior que zero.")
            return False
        if self.conta.currentData() is None:
            self._mostrar_erro("Escolha de qual conta saiu o dinheiro.")
            return False
        self.confirmado.emit(centavos, int(self.conta.currentData()), self.data.date().toPython())
        return True

    def mostrar_erro(self, texto: str) -> None:
        self._mostrar_erro(texto)

    def _mostrar_erro(self, texto: str) -> None:
        self.erro.setText(f"!  {texto}")
        self.valor.setProperty("erro", True)
        _repolir(self.valor)

    def _limpar_erro(self) -> None:
        if self.erro.text():
            self.erro.setText("")
            self.valor.setProperty("erro", False)
            _repolir(self.valor)


# --- Fatura expansível -----------------------------------------------------------------------


class _Cabecalho(QFrame):
    """Cabeçalho clicável da fatura."""

    clicado = Signal()

    def mouseReleaseEvent(self, evento) -> None:
        if evento.button() == Qt.MouseButton.LeftButton and self.rect().contains(evento.position().toPoint()):
            self.clicado.emit()
        super().mouseReleaseEvent(evento)


class LinhaCompra(QFrame):
    """Uma compra da fatura: data, descrição com a parcela em destaque, categoria e valor.

    `valor_centavos` vem como no lançamento (compra < 0); na fatura a compra aparece positiva e o estorno
    com "−". Em fatura paga, um ✓ discreto antes do valor.
    """

    def __init__(self, dia: date, descricao: str, categoria: str, valor_centavos: int, parcela: str, paga: bool,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LinhaCompra")
        self.setFixedHeight(40)
        linha = QHBoxLayout(self)
        linha.setContentsMargins(tema.ESPACO, 0, tema.ESPACO, 0)
        linha.setSpacing(2 * tema.ESPACO)

        self.data = _rotulo(f"{dia.day} {MESES_CURTOS[dia.month - 1]}", "CompraData")
        self.data.setFixedWidth(46)
        self.descricao = RotuloElidido(descricao, "CompraDescricao")
        self.descricao.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # a parcela fica colada nele
        self.descricao.setProperty("paga", paga)
        self.parcela = _rotulo(parcela, "ParcelaTag")
        self.parcela.setVisible(bool(parcela))
        self.categoria = RotuloElidido(categoria, "ItemDetalhe")
        self.categoria.setFixedWidth(170)
        self.categoria.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)  # o elidido ignora o tamanho; aqui vale o fixo
        self.pago = _rotulo("✓" if paga else "", "CompraPago")
        self.pago.setFixedWidth(14)
        valor = -valor_centavos
        self.valor = _rotulo(("−" if valor < 0 else "") + formatar_brl(abs(valor)), "CompraValor")
        self.valor.setProperty("paga", paga)
        self.valor.setMinimumWidth(100)
        self.valor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        linha.addWidget(self.data)
        linha.addWidget(self.descricao)
        linha.addWidget(self.parcela, 0, Qt.AlignmentFlag.AlignVCenter)
        linha.addStretch(1)
        linha.addWidget(self.categoria)
        linha.addWidget(self.pago)
        linha.addWidget(self.valor)


class FaturaCard(QFrame):
    """Cartão expansível de uma fatura. Cabeçalho: situação e total; corpo: as compras e as ações."""

    alternar = Signal(str)  # referência: abrir/fechar
    pagar = Signal(str)
    desfazer = Signal(str)  # pedido já confirmado

    def __init__(self, f: Fatura, hoje: date, expandida: bool, confirmando_desfazer: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.fatura = f
        self.referencia = f.referencia
        self.expandida = expandida
        self.setObjectName("FaturaCard")
        paga = f.paga

        self.cabecalho = _Cabecalho()
        self.cabecalho.setObjectName("FaturaCabecalho")
        self.cabecalho.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cabecalho.clicado.connect(lambda: self.alternar.emit(f.referencia))
        self.seta = _rotulo("▾" if expandida else "▸", "FaturaSeta")
        self.seta.setFixedWidth(14)
        self.nome = RotuloElidido(titulo_da_fatura(f, hoje), "FaturaNome")
        self.nome.setProperty("paga", paga)
        texto, variante = situacao_da_fatura(f, hoje)
        self.situacao = Pilula(texto, variante)
        quando = QHBoxLayout()
        quando.setSpacing(tema.ESPACO)
        quando.addWidget(self.nome, 1)
        quando.addWidget(self.situacao)
        n = len(f.lancamentos)
        self.detalhe = RotuloElidido(f"{detalhe_da_fatura(f)} · {n} compra{'' if n == 1 else 's'}" if n else detalhe_da_fatura(f), "ItemDetalhe")
        textos = QVBoxLayout()
        textos.setSpacing(2)
        textos.addLayout(quando)
        textos.addWidget(self.detalhe)
        self.total = _rotulo(formatar_brl(f.total_centavos), "FaturaTotal")
        self.total.setProperty("paga", paga)
        topo = QHBoxLayout(self.cabecalho)
        topo.setContentsMargins(tema.ESPACO + 4, tema.ESPACO + 2, tema.ESPACO + 4, tema.ESPACO + 2)
        topo.setSpacing(2 * tema.ESPACO)
        topo.addWidget(self.seta)
        topo.addLayout(textos, 1)
        topo.addWidget(self.total, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.corpo = QWidget()
        coluna = QVBoxLayout(self.corpo)
        coluna.setContentsMargins(tema.ESPACO, 0, tema.ESPACO, tema.ESPACO)
        coluna.setSpacing(0)
        separador = QFrame()
        separador.setObjectName("Separador")
        separador.setFixedHeight(1)
        coluna.addWidget(separador)
        periodo = f"Compras de {_dia_mes(f.inicio)} a {_dia_mes(date.fromordinal(f.fim_exclusivo.toordinal() - 1))}"
        cab_corpo = _rotulo(periodo, "ItemDetalhe")
        cab_corpo.setContentsMargins(tema.ESPACO, tema.ESPACO, 0, 4)
        coluna.addWidget(cab_corpo)
        self.linhas: list[LinhaCompra] = []
        for c in f.lancamentos.itertuples():
            parcela = ""
            if not pd.isna(c.parcela_total):
                parcela = f"{int(c.parcela_atual)}/{int(c.parcela_total)}"
            linha = LinhaCompra(
                c.data.date(), c.descricao, c.categoria if isinstance(c.categoria, str) else "A categorizar",
                int(c.valor_centavos), parcela, paga,
            )
            self.linhas.append(linha)
            coluna.addWidget(linha)
        if not self.linhas:
            vazio = _rotulo("Nenhuma compra nesta fatura ainda", "ItemDetalhe")
            vazio.setContentsMargins(tema.ESPACO, 4, 0, tema.ESPACO)
            coluna.addWidget(vazio)
        self._acoes = self._montar_acoes(f, confirmando_desfazer)
        if self._acoes is not None:
            coluna.addWidget(self._acoes)
        self.corpo.setVisible(expandida)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)
        raiz.addWidget(self.cabecalho)
        raiz.addWidget(self.corpo)

    def _montar_acoes(self, f: Fatura, confirmando: bool) -> QWidget | None:
        self.botao_pagar = self.botao_desfazer = self.botao_nao = self.botao_sim = self._pilha_acoes = None
        partes = []
        if f.situacao in ("fechada", "parcial") and f.total_centavos > 0:
            self.botao_pagar = BotaoSecundario("Marcar como paga")
            self.botao_pagar.setProperty("compacto", True)
            self.botao_pagar.clicked.connect(lambda: self.pagar.emit(f.referencia))
            barra = QWidget()
            linha = QHBoxLayout(barra)
            linha.setContentsMargins(tema.ESPACO, tema.ESPACO, tema.ESPACO, 0)
            linha.addStretch(1)
            linha.addWidget(self.botao_pagar)
            partes.append(barra)
        if f.pagamentos:
            partes.append(self._montar_desfazer(f, confirmando))
        if not partes:
            return None
        if len(partes) == 1:
            return partes[0]
        junto = QWidget()
        coluna = QVBoxLayout(junto)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(0)
        for parte in partes:
            coluna.addWidget(parte)
        return junto

    def _montar_desfazer(self, f: Fatura, confirmando: bool) -> QWidget:
        pilha = QStackedWidget()
        normal = QWidget()
        linha = QHBoxLayout(normal)
        linha.setContentsMargins(tema.ESPACO, tema.ESPACO, tema.ESPACO, 0)
        linha.addStretch(1)
        self.botao_desfazer = QPushButton("Desfazer pagamento" if len(f.pagamentos) < 2 else "Desfazer último pagamento")
        self.botao_desfazer.setObjectName("BotaoAcao")
        self.botao_desfazer.setProperty("perigo", True)
        self.botao_desfazer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_desfazer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_desfazer.clicked.connect(lambda: self.pedir_desfazer())
        linha.addWidget(self.botao_desfazer)
        pilha.addWidget(normal)

        confirma = QWidget()
        linha = QHBoxLayout(confirma)
        linha.setContentsMargins(tema.ESPACO, tema.ESPACO, tema.ESPACO, 0)
        linha.setSpacing(tema.ESPACO)
        ultimo = f.pagamentos[-1]
        quando = f"o último pagamento ({formatar_brl(ultimo['valor_centavos'])} em {_dia_mes(ultimo['data'])})" if len(f.pagamentos) > 1 else "o pagamento"
        depois = "." if len(f.pagamentos) > 1 else " e a fatura volta a ficar em aberto."
        texto = _rotulo(f"Desfazer {quando}? O dinheiro volta pra conta{depois}", "ConfirmaTexto")
        texto.setWordWrap(True)
        linha.addWidget(texto, 1)
        self.botao_nao = BotaoSecundario("Não")
        self.botao_nao.setProperty("compacto", True)
        self.botao_nao.clicked.connect(self.cancelar_desfazer)
        self.botao_sim = QPushButton("Sim, desfazer")
        self.botao_sim.setObjectName("BotaoPerigo")
        self.botao_sim.setProperty("compacto", True)
        self.botao_sim.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_sim.clicked.connect(lambda: self.desfazer.emit(f.referencia))
        linha.addWidget(self.botao_nao)
        linha.addWidget(self.botao_sim)
        pilha.addWidget(confirma)
        pilha.setCurrentIndex(_PAG_CONFIRMAR if confirmando else _PAG_ACOES)
        self._pilha_acoes = pilha
        return pilha

    def pedir_desfazer(self) -> None:
        self._pilha_acoes.setCurrentIndex(_PAG_CONFIRMAR)

    def cancelar_desfazer(self) -> None:
        self._pilha_acoes.setCurrentIndex(_PAG_ACOES)

    @property
    def confirmando_desfazer(self) -> bool:
        return self.botao_sim is not None and self._pilha_acoes.currentIndex() == _PAG_CONFIRMAR


# --- A aba -------------------------------------------------------------------------------------


class AbaCartoes(QScrollArea):
    """Conteúdo da aba. `hoje` é uma função (a janela principal pode fixar a data nos testes)."""

    alterado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date] = date.today, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje
        self._cartao_id: int | None = None
        self._cartoes: list[tuple[int, str]] = []
        self._estado: dict[tuple[int, str], bool] = {}  # (cartão, referência) -> aberta pelo usuário?
        self._mostrar_pagas = False
        self._pagando: str | None = None
        self._pago_agora: tuple[int, str, int] | None = None  # (cartão, referência, id do pagamento) recém-feito
        self._desfazendo: str | None = None
        self._seletor: Segmentado | None = None
        self._seletor_nomes: list[str] = []
        self._visao = "Aberta"  # qual fatura a rosca mostra: "Aberta" ou "Fechada"
        self._ref_rosca: str | None = None
        self._seg_fatura: Segmentado | None = None
        self._seg_opcoes: tuple[str, ...] = ()

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        conteudo = QWidget()
        conteudo.setObjectName("Conteudo")
        coluna = QVBoxLayout(conteudo)
        m = 3 * tema.ESPACO
        coluna.setContentsMargins(m, tema.ESPACO, m, m)
        coluna.setSpacing(m)
        self._barra = QHBoxLayout()
        coluna.addLayout(self._barra)
        coluna.addWidget(self._montar_destaque())
        coluna.addWidget(self._montar_rosca())
        coluna.addWidget(self._montar_lista())
        self._vazio = EstadoVazio("lista", "Nenhum cartão", "Cadastre um cartão em Contas pra acompanhar as faturas")
        self._vazio.setMinimumHeight(260)
        coluna.addWidget(self._vazio)
        coluna.addStretch(1)
        self.setWidget(conteudo)
        self.recarregar()

    # --- Montagem --------------------------------------------------------------------
    def _montar_destaque(self) -> Cartao:
        self._cartao_destaque = Cartao("Cartão", "Cartão")
        self._fechada = NumeroDestaque("Fatura fechada", "", "")
        self.pilula_fechada = Pilula("")
        self.botao_pagar = BotaoPrimario("Marcar como paga")
        self.botao_pagar.clicked.connect(self.pedir_pagamento_da_proxima)
        self.link_pagas = QPushButton("Ver faturas pagas")
        self.link_pagas.setObjectName("BotaoLink")
        self.link_pagas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_pagas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.link_pagas.clicked.connect(lambda: self.mostrar_pagas(True))
        for w in (self.pilula_fechada, self.botao_pagar, self.link_pagas):
            self._fechada.adicionar(w)
        self._ref_destaque: str | None = None
        self._aberta = NumeroDestaque("Fatura aberta", "", "")
        self.pilula_aberta = Pilula("")
        self._aberta.adicionar(self.pilula_aberta)
        grade = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_BLOCO, espaco=3 * tema.ESPACO)
        grade.adicionar(self._fechada)
        grade.adicionar(self._aberta)
        self._cartao_destaque.corpo.addWidget(grade)
        self.aviso_pago = QFrame()  # "Fatura de X paga ✓" + Desfazer, logo após pagar
        self.aviso_pago.setObjectName("FormPagamento")
        linha_aviso = QHBoxLayout(self.aviso_pago)
        p = 2 * tema.ESPACO
        linha_aviso.setContentsMargins(p, tema.ESPACO, p, tema.ESPACO)
        self.texto_aviso_pago = _rotulo("", "ConfirmaTexto")
        self.texto_aviso_pago.setWordWrap(True)
        self.botao_desfazer_agora = BotaoSecundario("Desfazer")
        self.botao_desfazer_agora.setProperty("compacto", True)
        self.botao_desfazer_agora.setAutoDefault(False)
        self.botao_desfazer_agora.clicked.connect(self._desfazer_agora)
        linha_aviso.addWidget(self.texto_aviso_pago, 1)
        linha_aviso.addWidget(self.botao_desfazer_agora)
        self.aviso_pago.setVisible(False)
        self._cartao_destaque.corpo.addWidget(self.aviso_pago)
        self.form = FormPagamento()
        self.form.cancelado.connect(self.cancelar_pagamento)
        self.form.confirmado.connect(self._pagar)
        self.form.setVisible(False)
        self._cartao_destaque.corpo.addWidget(self.form)
        return self._cartao_destaque

    def _montar_rosca(self) -> Cartao:
        self._acao_rosca = QWidget()
        self._acao_rosca_linha = QHBoxLayout(self._acao_rosca)
        self._acao_rosca_linha.setContentsMargins(0, 0, 0, 0)
        self._cartao_rosca = Cartao("Para onde foi", "Fatura", acao=self._acao_rosca)
        self.rosca = PainelRosca("gasto na fatura", largura_lado_a_lado=LARGURA_ROSCA_LADO_A_LADO)
        self.rosca.setMinimumHeight(260)
        self._cartao_rosca.corpo.addWidget(self.rosca)
        return self._cartao_rosca

    def _montar_lista(self) -> Cartao:
        self._cartao_lista = Cartao("Faturas", "Toque numa fatura pra ver as compras")
        self._lista = QVBoxLayout()
        self._lista.setSpacing(tema.ESPACO)
        self._cartao_lista.corpo.addLayout(self._lista)
        self.botao_pagas = QPushButton()
        self.botao_pagas.setObjectName("BotaoLink")
        self.botao_pagas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_pagas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_pagas.clicked.connect(lambda: self.mostrar_pagas(not self._mostrar_pagas))
        self._cartao_lista.corpo.addWidget(self.botao_pagas, 0, Qt.AlignmentFlag.AlignLeft)
        return self._cartao_lista

    # --- Dados ------------------------------------------------------------------------
    def _cartoes_da_aba(self) -> list[tuple[int, str]]:
        """Cartões ativos e os desativados que ainda têm fatura em aberto (cartão cancelado ainda se paga)."""
        lista = []
        for c in cadastros.contas_por_tipo(self.conn, ("cartao",), incluir_inativas=True):
            if c["ativa"]:
                lista.append((c["id"], nome_com_final(c["nome"], c["final"])))
            elif cadastros.divida_do_cartao(self.conn, c["id"]) > 0:
                lista.append((c["id"], nome_com_final(c["nome"], c["final"]) + " (desativado)"))
        return lista

    @property
    def cartao_atual(self) -> int | None:
        return self._cartao_id

    def selecionar_cartao(self, cartao_id: int) -> None:
        if cartao_id == self._cartao_id:
            return
        self._cartao_id = cartao_id
        self._pagando = self._desfazendo = self._pago_agora = None
        self._mostrar_pagas = False
        self.recarregar()

    def _atualizar_seletor(self) -> None:
        nomes = [nome for _id, nome in self._cartoes]
        if self._seletor is not None and self._seletor_nomes == nomes:
            self._seletor.definir(dict(self._cartoes)[self._cartao_id])
            return
        while self._barra.count():
            item = self._barra.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
        self._seletor = None
        if len(self._cartoes) > 1:
            self._seletor = Segmentado(nomes, dict(self._cartoes)[self._cartao_id])
            self._seletor.escolhido.connect(self._escolheu_cartao)
            self._barra.addWidget(self._seletor)
            self._barra.addStretch(1)
        self._seletor_nomes = nomes if self._seletor is not None else []

    def _escolheu_cartao(self, nome: str) -> None:
        for id_, n in self._cartoes:
            if n == nome:
                self.selecionar_cartao(id_)

    def recarregar(self) -> None:
        hoje = self._hoje()
        self._cartoes = self._cartoes_da_aba()
        existe = bool(self._cartoes)
        self._vazio.setVisible(not existe)
        self._cartao_destaque.setVisible(existe)
        self._cartao_rosca.setVisible(existe)
        self._cartao_lista.setVisible(existe)
        if not existe:
            self._cartao_id = None
            self._atualizar_seletor()
            return
        if self._cartao_id not in dict(self._cartoes):
            self._cartao_id = self._cartoes[0][0]
            self._pagando = self._desfazendo = None
        self._atualizar_seletor()

        cartao = cadastros._conta(self.conn, self._cartao_id)
        lista = faturas.faturas_do_cartao(self.conn, self._cartao_id, hoje)
        nome = nome_com_final(cartao["nome"], cartao["final"])
        self._cartao_destaque.titulo.setText(nome.upper())
        self._cartao_destaque.subtitulo.setText(detalhe_cartao(cartao) + ("" if cartao["dia_fechamento"] else " · fatura = mês do calendário"))
        self._carregar_destaque(lista, hoje, nome)
        self._carregar_rosca(lista, hoje, nome)
        self._carregar_lista(lista, hoje)

    def _carregar_destaque(self, lista: list[Fatura], hoje: date, nome_cartao: str) -> None:
        a_pagar = sorted((f for f in lista if f.situacao in ("fechada", "parcial") and f.total_centavos > 0), key=lambda f: f.vencimento)
        pagas = [f for f in lista if f.situacao == "paga"]
        escolhida = next((f for f in a_pagar if f.referencia == self._pagando), None)
        if self._pagando and escolhida is None:
            self._pagando = None  # a fatura mudou (paga ou sumiu) enquanto a confirmação estava aberta
        fechada = escolhida or (a_pagar[0] if a_pagar else None)

        self._ref_destaque = fechada.referencia if fechada else None
        if fechada:
            texto, variante = situacao_da_fatura(fechada, hoje)
            self._fechada.rotulo.setText(titulo_da_fatura(fechada, hoje).upper())
            apoio = detalhe_da_fatura(fechada)
            if len(a_pagar) > 1:
                apoio += f"\n+ {len(a_pagar) - 1} fatura{'s' if len(a_pagar) > 2 else ''} fechada{'s' if len(a_pagar) > 2 else ''} a pagar"
            self._fechada.definir(formatar_brl(fechada.restante_centavos), apoio)  # parcial: o que falta
            self.pilula_fechada.definir(texto, variante)
        elif pagas:
            ultima = max(pagas, key=lambda f: f.vencimento)
            self._fechada.rotulo.setText(titulo_da_fatura(ultima, hoje).upper())
            self._fechada.definir(formatar_brl(ultima.total_centavos), detalhe_da_fatura(ultima))
            self.pilula_fechada.definir("paga", "feito")
        else:
            self._fechada.rotulo.setText("FATURA FECHADA")
            self._fechada.definir("—", "Nenhuma fatura fechada pra pagar")
        self.pilula_fechada.setVisible(fechada is not None or bool(pagas))
        self.botao_pagar.setVisible(fechada is not None and self._pagando is None)
        self.link_pagas.setVisible(fechada is None and bool(pagas) and not self._mostrar_pagas)

        aberta = next((f for f in lista if f.situacao == "aberta"), None)
        if aberta:
            dias = (aberta.fechamento - hoje).days
            self._aberta.rotulo.setText(f"FATURA ABERTA · {mes_da_referencia(aberta.referencia, hoje).upper()}")
            self._aberta.definir(formatar_brl(aberta.total_centavos), detalhe_da_fatura(aberta))
            self.pilula_aberta.definir(f"fecha em {_dias(dias)}" if dias > 0 else "fecha hoje", "neutra")

        self._carregar_aviso_pago(lista, hoje)
        if fechada and self._pagando == fechada.referencia:
            correntes = [(c["id"], c["nome"]) for c in lancamentos.listar_contas(self.conn) if c["tipo"] == "corrente"]
            self.form.preparar(
                f"Marcar como paga · {titulo_da_fatura(fechada, hoje)} · {nome_cartao}", fechada.restante_centavos, hoje, correntes
            )
            self.form.setVisible(True)
        else:
            self.form.setVisible(False)

    def _carregar_rosca(self, lista: list[Fatura], hoje: date, nome_cartao: str) -> None:
        """Rosca de categorias da fatura aberta ou da fechada (a de vencimento mais próximo); só futuras: a próxima."""
        por_vencimento = sorted(lista, key=lambda f: f.vencimento)
        opcoes = {
            "Aberta": next((f for f in por_vencimento if f.situacao == "aberta"), None),
            "Fechada": next((f for f in por_vencimento if f.situacao in ("fechada", "parcial")), None),
        }
        opcoes = {nome: f for nome, f in opcoes.items() if f is not None}
        if not opcoes:
            proxima = next((f for f in por_vencimento if f.situacao == "futura"), None)
            if proxima is None:
                self._cartao_rosca.setVisible(False)
                return
            escolhida = proxima
        else:
            if self._visao not in opcoes:
                self._visao = next(iter(opcoes))
            escolhida = opcoes[self._visao]
        self._atualizar_seletor_fatura(tuple(opcoes) if len(opcoes) > 1 else ())

        self._ref_rosca = escolhida.referencia
        self._cartao_rosca.subtitulo.setText(f"{titulo_da_fatura(escolhida, hoje)} · {nome_cartao}")
        categorias = faturas.gastos_por_categoria(self.conn, self._cartao_id, escolhida.referencia)
        self.rosca.definir(
            categorias, int(escolhida.referencia[:4]), int(escolhida.referencia[5:]), False,
            vazio=("Nenhuma compra nessa fatura ainda", ""),
        )

    def _atualizar_seletor_fatura(self, opcoes: tuple[str, ...]) -> None:
        """Aberta | Fechada: só aparece quando há as duas."""
        if opcoes != self._seg_opcoes:
            if self._seg_fatura is not None:
                self._acao_rosca_linha.removeWidget(self._seg_fatura)
                self._seg_fatura.setParent(None)
                self._seg_fatura.deleteLater()
                self._seg_fatura = None
            if opcoes:
                self._seg_fatura = Segmentado(list(opcoes), self._visao)
                self._seg_fatura.escolhido.connect(self._escolheu_fatura)
                self._acao_rosca_linha.addWidget(self._seg_fatura)
            self._seg_opcoes = opcoes
        elif self._seg_fatura is not None:
            self._seg_fatura.definir(self._visao)

    def _carregar_aviso_pago(self, lista: list[Fatura], hoje: date) -> None:
        """Mostra o "Desfazer" do pagamento recém-feito, enquanto ele ainda existir (some na próxima ação)."""
        f = pagamento = None
        if self._pago_agora and self._pago_agora[0] == self._cartao_id:
            f = next((x for x in lista if x.referencia == self._pago_agora[1]), None)
            pagamento = next((p for p in f.pagamentos if p["id"] == self._pago_agora[2]), None) if f else None
        if pagamento is None:
            self._pago_agora = None
        elif f.paga:
            self.texto_aviso_pago.setText(f"{titulo_da_fatura(f, hoje)} paga ✓")
        else:
            self.texto_aviso_pago.setText(
                f"Pagamento de {formatar_brl(pagamento['valor_centavos'])} registrado · falta {formatar_brl(f.restante_centavos)}"
            )
        self.aviso_pago.setVisible(pagamento is not None)

    def _desfazer_agora(self) -> None:
        if self._pago_agora:
            self.desfazer_pagamento(self._pago_agora[1], self._pago_agora[2])

    def _escolheu_fatura(self, nome: str) -> None:
        self._visao = nome
        self.recarregar()

    def _carregar_lista(self, lista: list[Fatura], hoje: date) -> None:
        while self._lista.count():
            widget = self._lista.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        abertas = [f for f in lista if f.situacao == "aberta"]
        fechadas = sorted((f for f in lista if f.situacao in ("fechada", "parcial")), key=lambda f: f.vencimento)
        futuras = [f for f in lista if f.situacao == "futura"]
        pagas = sorted((f for f in lista if f.situacao == "paga"), key=lambda f: f.vencimento, reverse=True)
        for f in abertas + fechadas + futuras + (pagas if self._mostrar_pagas else []):
            card = FaturaCard(
                f, hoje, self._expandida(f), confirmando_desfazer=self._desfazendo == f.referencia
            )
            card.alternar.connect(self._alternar)
            card.pagar.connect(self.pedir_pagamento)
            card.desfazer.connect(self.desfazer_pagamento)
            self._lista.addWidget(card)
        self.botao_pagas.setVisible(bool(pagas))
        self.botao_pagas.setText("Esconder faturas pagas" if self._mostrar_pagas else f"Mostrar faturas pagas ({len(pagas)})")

    def _expandida(self, f: Fatura) -> bool:
        return self._estado.get((f.cartao_id, f.referencia), f.referencia == self._desfazendo)

    # --- Ações ------------------------------------------------------------------------------
    def _alternar(self, referencia: str) -> None:
        card = self.card(referencia)
        self._pago_agora = None
        self._estado[(self._cartao_id, referencia)] = not (card.expandida if card else False)
        self.recarregar()

    def mostrar_pagas(self, mostrar: bool) -> None:
        self._mostrar_pagas = mostrar
        self._pago_agora = None
        self.recarregar()

    def pedir_pagamento(self, referencia: str) -> None:
        """Abre a confirmação de pagamento (lá em cima) pra fatura `referencia`."""
        self._pagando = referencia
        self._pago_agora = None
        self.recarregar()
        self.verticalScrollBar().setValue(0)

    def pedir_pagamento_da_proxima(self) -> None:
        """Abre a confirmação pra fatura fechada em destaque (a de vencimento mais próximo)."""
        if self._ref_destaque:
            self.pedir_pagamento(self._ref_destaque)

    def cancelar_pagamento(self) -> None:
        self._pagando = None
        self.recarregar()

    def _pagar(self, valor_centavos: int, conta_id: int, dia: date) -> None:
        if self._pagando is None:
            return
        referencia, cartao_id = self._pagando, self._cartao_id
        try:
            pagamento_id = faturas.pagar_fatura(self.conn, cartao_id, referencia, valor_centavos, conta_id, dia)
        except ValueError as erro:
            self.form.mostrar_erro(str(erro))
            return
        self._pagando = None
        self._pago_agora = (cartao_id, referencia, pagamento_id)
        self._avisar(f"Fatura de {mes_da_referencia(referencia, self._hoje())} paga: {formatar_brl(valor_centavos)}")

    def desfazer_pagamento(self, referencia: str, pagamento_id: int | None = None) -> None:
        """Desfaz um pagamento da fatura: o `pagamento_id` ou, sem ele, o mais recente."""
        self._pago_agora = None
        try:
            faturas.desfazer_pagamento(self.conn, self._cartao_id, referencia, pagamento_id)
        except ValueError:
            self._desfazendo = None
            self.recarregar()
            return
        self._desfazendo = None
        self._avisar("Pagamento desfeito")

    def _avisar(self, mensagem: str) -> None:
        self.recarregar()
        self.alterado.emit(mensagem)

    # --- Para testes e screenshots ------------------------------------------------------------
    def cards(self) -> list[FaturaCard]:
        return [self._lista.itemAt(i).widget() for i in range(self._lista.count())]

    def card(self, referencia: str) -> FaturaCard | None:
        return next((c for c in self.cards() if c.referencia == referencia), None)
