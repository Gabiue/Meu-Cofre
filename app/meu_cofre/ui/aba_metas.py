"""Aba "Metas" (docs/design/telas.md, F7): juntar dinheiro pra algo, com a conta de quanto cabe no mês.

Topo: quatro blocos (guardado, sobra média real, quanto as metas pedem por mês e quanto sobra pra novas metas).
Depois, a sugestão de reserva de emergência (enquanto não houver uma) e, lado a lado (empilhados em janela
estreita), a lista de metas em ordem de prioridade e o detalhe da escolhida: anel de progresso, quanto falta e
até quando, se cabe na sobra, o ritmo real dos últimos meses, Guardar e Retirar, o menu "⋯" (Editar, subir/descer,
Concluir, Arquivar) e a evolução mês a mês contra a linha ideal. Por último, o simulador "Comprar agora ou parcelar?".
Guardar e retirar abrem um formulário na própria meta. `alterado(mensagem)` avisa a janela principal.
"""

import sqlite3
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QDate, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout,
    QMenu,
    QToolButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import cadastros, metas
from meu_cofre.lancamentos import formatar_brl, para_centavos
from meu_cofre.metas import Meta
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BarraProgresso,
    BotaoPrimario,
    BotaoSecundario,
    CampoData,
    CampoLista,
    Cartao,
    GradeResponsiva,
    NumeroDestaque,
    Pilula,
    RotuloElidido,
    _repolir,
)
from meu_cofre.ui.dialogo_meta import (
    DialogoMeta,
    explicacao_viabilidade,
    mes_ano,
    pilula_viabilidade,
    plural_meses,
    reais,
    texto_atrasos,
)
from meu_cofre.ui.graficos import MESES_CURTOS, EstadoVazio

LARGURA_MINIMA_NUMERO = 220  # cada número num bloco próprio: abaixo disso, 2 × 2
LARGURA_MINIMA_BLOCO = 300
LARGURA_LADO_A_LADO = 1100  # abaixo disso, o detalhe da meta desce pra baixo da lista
PARCELAS_PADRAO = 10


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _acao(texto: str, perigo: bool = False, largura: int = 76) -> QPushButton:
    """Ação discreta da linha: texto com contorno só ao passar o mouse."""
    botao = QPushButton(texto)
    botao.setObjectName("BotaoAcao")
    botao.setProperty("perigo", perigo)
    botao.setCursor(Qt.CursorShape.PointingHandCursor)
    botao.setAutoDefault(False)
    botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    botao.setMinimumWidth(largura)
    return botao


def _compacto(botao: QPushButton) -> QPushButton:
    botao.setProperty("compacto", True)
    botao.setAutoDefault(False)
    botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return botao


def texto_do_prazo(m: Meta, hoje: date) -> str:
    """Quanto falta e até quando, pro item da lista: "faltam 12 dias · meta para 23/11/2026", "pronta em jul/2027"."""
    v = m.viabilidade
    if m.estado != "ativa":
        return texto_do_plano(m, hoje)
    if v.situacao == "cumprida":
        return "valor atingido"
    if m.modo != "prazo":
        return f"pronta em {mes_ano(v.pedida)}"
    dias = (m.prazo - hoje).days
    if dias < 0:
        return f"prazo venceu em {m.prazo:%d/%m/%Y}"
    falta = f"faltam {dias} dias" if dias <= 60 else f"faltam {plural_meses(metas.meses_de_diferenca(hoje, m.prazo))}"
    return f"{falta} · meta para {m.prazo:%d/%m/%Y}"


def texto_do_ritmo(m: Meta, ritmo: int | None) -> tuple[str, str] | None:
    """(texto, variante da pílula) comparando o que entrou de fato por mês com o que o plano pede; None sem dado."""
    v = m.viabilidade
    if ritmo is None or v is None or v.situacao == "cumprida" or not v.necessario:
        return None
    texto = f"Você guardou em média {reais(ritmo)}/mês · o plano pede {reais(v.necessario)}"
    return texto, "bom" if ritmo >= v.necessario else "atencao"


class AnelProgresso(QLabel):
    """O percentual no centro de um anel: o arco cheio é o progresso (o texto é o próprio QLabel)."""

    LADO = 96

    def __init__(self, fracao: float, apagada: bool = False, parent: QWidget | None = None):
        super().__init__(f"{int(fracao * 100)}%", parent)
        self._fracao, self._apagada = fracao, apagada
        self.setObjectName("MetaAnel")
        self.setFixedSize(self.LADO, self.LADO)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, evento) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        espessura = 9
        r = QRectF(self.rect()).adjusted(espessura, espessura, -espessura, -espessura)
        p.setPen(QPen(QColor(tema.BORDA), espessura))
        p.drawEllipse(r)
        if self._fracao > 0:
            caneta = QPen(QColor(tema.TEXTO_APAGADO if self._apagada else tema.VERDE), espessura)
            caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(caneta)
            p.drawArc(r, 90 * 16, -int(self._fracao * 360 * 16))  # do topo, no sentido do relógio
        p.end()
        super().paintEvent(evento)


class GraficoEvolucao(QWidget):
    """Guardado no fim de cada mês (linha cheia) e onde deveria estar pelo plano (tracejada), de 0 ao alvo.

    A linha ideal: no modo mensal, o mensal acumulado desde o primeiro mês; no modo prazo, uma reta de zero até o
    alvo no mês do prazo.
    """

    ALTURA = 150

    def __init__(
        self, pontos: list[tuple[date, int]], alvo: int, prazo: date | None = None, mensal: int | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._pontos, self._alvo, self._prazo, self._mensal = pontos, alvo, prazo, mensal
        self.setFixedHeight(self.ALTURA)

    def ideal(self, i: int) -> int | None:
        """Quanto o plano pedia guardado no fim do i-ésimo mês (sem plano: None)."""
        if self._mensal:
            return min(self._alvo, self._mensal * (i + 1))
        if self._prazo is None:
            return None
        total = metas.meses_ate_prazo(self._pontos[0][0], self._prazo)  # a mesma conta do plano mensal
        return min(self._alvo, round(self._alvo * (i + 1) / total))

    def paintEvent(self, evento) -> None:
        if not self._pontos:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        esq, dir_, topo, base = 44.0, self.width() - 12.0, 8.0, self.height() - 22.0
        n = len(self._pontos)
        fonte = QFont(self.font())
        fonte.setPixelSize(tema.PX_RODAPE)
        p.setFont(fonte)

        def x(i: int) -> float:
            return esq + (dir_ - esq) * (i / (n - 1) if n > 1 else 0.5)

        def y(c: int) -> float:
            return base - (base - topo) * min(max(c / self._alvo, 0), 1)

        p.setPen(QColor(tema.TEXTO_APAGADO))
        for frac, rotulo in ((0, "0%"), (0.5, "50%"), (1, "100%")):
            yy = base - (base - topo) * frac
            p.drawText(QRectF(0, yy - 8, esq - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, rotulo)
            p.setPen(QPen(QColor(tema.BORDA), 1))
            p.drawLine(QPointF(esq, yy), QPointF(dir_, yy))
            p.setPen(QColor(tema.TEXTO_APAGADO))
        for i in sorted({0, n // 2, n - 1}):
            mes = self._pontos[i][0]
            p.drawText(QRectF(x(i) - 30, base + 4, 60, 16), Qt.AlignmentFlag.AlignCenter, f"{MESES_CURTOS[mes.month - 1]}")
        ideais = [self.ideal(i) for i in range(n)]
        if ideais[0] is not None:
            caneta = QPen(QColor(tema.DOURADO), 1.5, Qt.PenStyle.DashLine)
            p.setPen(caneta)
            p.drawPolyline([QPointF(x(i), y(c)) for i, c in enumerate(ideais)])
        p.setPen(QPen(QColor(tema.VERDE), 2))
        reais_ = [QPointF(x(i), y(c)) for i, (_m, c) in enumerate(self._pontos)]
        p.drawPolyline(reais_)
        p.setBrush(QColor(tema.VERDE))
        p.setPen(QPen(QColor(tema.CARTAO), 2))
        p.drawEllipse(reais_[-1], 5, 5)


class ItemMeta(QFrame):
    """Uma meta na lista: inicial, nome, percentual, barra, quanto já tem e quanto falta. Clicar escolhe a meta."""

    clicado = Signal(int)

    def __init__(self, m: Meta, hoje: date, selecionada: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.id_meta = m.id
        encerrada = m.estado != "ativa"
        self.setObjectName("MetaItem")
        self.setProperty("selecionada", selecionada)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        marcador = QLabel(m.nome[:1].upper())
        marcador.setObjectName("ItemMarcador")
        marcador.setProperty("tipo", "saida")
        marcador.setFixedSize(40, 40)
        marcador.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nome = RotuloElidido(m.nome, "MetaNome")
        self.nome.setProperty("encerrada", encerrada)
        self.percentual = _rotulo(f"{int(m.progresso * 100)}%", "MetaPct")
        self.percentual.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        topo = QHBoxLayout()
        topo.addWidget(self.nome, 1)
        topo.addWidget(self.percentual)
        self.barra = BarraProgresso(m.progresso, apagada=encerrada)
        self.valores = RotuloElidido(f"{reais(m.guardado)} de {reais(m.valor_alvo_centavos)} · {texto_do_prazo(m, hoje)}", "ItemDetalhe")
        textos = QVBoxLayout()
        textos.setSpacing(6)
        textos.addLayout(topo)
        textos.addWidget(self.barra)
        textos.addWidget(self.valores)
        linha = QHBoxLayout(self)
        linha.setContentsMargins(12, 12, 12, 12)
        linha.setSpacing(12)
        linha.addWidget(marcador, 0, Qt.AlignmentFlag.AlignTop)
        linha.addLayout(textos, 1)

    def mouseReleaseEvent(self, evento) -> None:
        if evento.button() == Qt.MouseButton.LeftButton:
            self.clicado.emit(self.id_meta)
        super().mouseReleaseEvent(evento)


def texto_do_plano(m: Meta, hoje: date) -> str:
    """"R$ 250/mês até mar/2027" (ou o estado, se a meta já foi encerrada ou cumprida)."""
    v = m.viabilidade
    if m.estado == "arquivada":
        return "arquivada"
    if m.estado == "concluida":
        return f"concluída em {m.concluida_em:%d/%m/%Y}"
    if v.situacao == "cumprida":
        return "valor atingido: já dá pra concluir"
    if m.modo == "prazo":
        if (m.prazo.year, m.prazo.month) < (hoje.year, hoje.month):
            return f"prazo venceu em {mes_ano(m.prazo)} · faltam {reais(m.restante)}"
        return f"{reais(v.necessario)}/mês até {mes_ano(m.prazo)}"
    return f"{reais(m.mensal_centavos)}/mês · pronta em {mes_ano(v.pedida)}"


# --- Guardar e retirar: formulário na meta ---------------------------------------------------------


class FormMovimento(QFrame):
    """"Guardar" ou "Retirar": valor, conta e data. Emite `confirmado(centavos, id da conta, data)`."""

    confirmado = Signal(int, int, date)
    cancelado = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FormMeta")
        grade = QGridLayout(self)
        p = 2 * tema.ESPACO
        grade.setContentsMargins(p, p, p, p)
        grade.setHorizontalSpacing(2 * tema.ESPACO)
        grade.setVerticalSpacing(4)
        for c in range(3):
            grade.setColumnStretch(c, 1)

        self.titulo = _rotulo("", "NumeroRotulo")
        self.titulo.setWordWrap(True)
        grade.addWidget(self.titulo, 0, 0, 1, 3)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        self.conta = CampoLista()
        self.data = CampoData()
        self.rotulo_conta = _rotulo("", "CampoRotulo")
        for coluna, (rotulo, campo) in enumerate(
            ((_rotulo("Valor (R$)", "CampoRotulo"), self.valor), (self.rotulo_conta, self.conta), (_rotulo("Data", "CampoRotulo"), self.data))
        ):
            grade.addWidget(rotulo, 1, coluna)
            grade.addWidget(campo, 2, coluna)
        self.erro = _rotulo("", "CampoErro")
        self.erro.setWordWrap(True)
        self.erro.setFixedHeight(22)
        grade.addWidget(self.erro, 3, 0, 1, 3)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        self.botao_cancelar = _compacto(BotaoSecundario("Cancelar"))
        self.botao_cancelar.clicked.connect(self.cancelado.emit)
        self.botao_confirmar = _compacto(BotaoPrimario("Guardar"))
        self.botao_confirmar.clicked.connect(self.confirmar)
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_confirmar)
        grade.addLayout(botoes, 4, 0, 1, 3)

        self.valor.returnPressed.connect(self.confirmar)
        self.valor.textChanged.connect(lambda _t: self._limpar_erro())

    def preparar(
        self, titulo: str, rotulo_conta: str, botao: str, valor_centavos: int | None, hoje: date, contas: list[tuple[int, str]]
    ) -> None:
        self.titulo.setText(titulo)
        self.rotulo_conta.setText(rotulo_conta)
        self.botao_confirmar.setText(botao)
        self.valor.setText(formatar_brl(valor_centavos).removeprefix("R$ ") if valor_centavos else "")
        self.conta.clear()
        for id_, nome in contas:
            self.conta.addItem(nome, id_)
        self.conta.setCurrentIndex(max(self.conta.findText("Inter"), 0))
        self.data.setDate(QDate(hoje.year, hoje.month, hoje.day))
        self._limpar_erro()

    def confirmar(self) -> bool:
        try:
            centavos = para_centavos(self.valor.text()) if self.valor.text().strip() else 0
        except ValueError:
            self.mostrar_erro("Valor inválido. Use só números, como 150,00.")
            return False
        if centavos <= 0:
            self.mostrar_erro("Digite um valor maior que zero.")
            return False
        if self.conta.currentData() is None:
            self.mostrar_erro("Escolha a conta.")
            return False
        self.confirmado.emit(centavos, int(self.conta.currentData()), self.data.date().toPython())
        return True

    def mostrar_erro(self, texto: str) -> None:
        self.erro.setText(f"!  {texto}")
        self.valor.setProperty("erro", True)
        _repolir(self.valor)

    def _limpar_erro(self) -> None:
        if self.erro.text():
            self.erro.setText("")
            self.valor.setProperty("erro", False)
            _repolir(self.valor)


# --- Uma meta ----------------------------------------------------------------------------------


class CartaoMeta(QFrame):
    """O detalhe da meta escolhida: anel, quanto tem e quanto falta, plano, se cabe na sobra, ritmo, ações e evolução."""

    guardar = Signal(int)
    retirar = Signal(int)
    editar = Signal(int)
    mover = Signal(int, int)  # id, passo (-1 sobe, +1 desce)
    concluir = Signal(int)
    arquivar = Signal(int)
    reativar = Signal(int)
    movimento = Signal(int, str, int, int, date)  # id, "guardar" | "retirar", centavos, conta, data

    def __init__(
        self, m: Meta, hoje: date, sobra: int, posicao: int = 0, total: int = 1,
        evolucao: list[tuple[date, int]] | None = None, ritmo: int | None = None, parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.meta = m
        self.id_meta = m.id
        self._tipo_form: str | None = None
        self.setObjectName("MetaCard")
        encerrada = m.estado != "ativa"
        v = m.viabilidade

        self.nome = RotuloElidido(m.nome, "MetaNome")
        self.nome.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # a pílula fica colada no nome
        self.nome.setProperty("encerrada", encerrada)
        self.pilula_reserva = Pilula("reserva")
        self.pilula_reserva.setVisible(m.reserva)
        if encerrada:
            texto, variante = ("concluída", "feito") if m.estado == "concluida" else ("arquivada", "neutra")
        else:
            texto, variante = pilula_viabilidade(v)
        self.pilula = Pilula(texto, variante)
        if v is not None and v.situacao in ("parcial", "nao_cabe"):
            self.pilula.setToolTip(explicacao_viabilidade(v, sobra))
        topo = QHBoxLayout()
        topo.setSpacing(tema.ESPACO)
        topo.addWidget(self.nome)
        topo.addWidget(self.pilula_reserva)
        topo.addStretch(1)
        topo.addWidget(self.pilula)
        topo.addWidget(self._montar_menu(posicao, total, encerrada))

        self.percentual = AnelProgresso(m.progresso, apagada=encerrada)
        self.guardado = _rotulo(f"{formatar_brl(m.guardado)} de {formatar_brl(m.valor_alvo_centavos)}", "MetaGuardado")
        self.barra = BarraProgresso(m.progresso, apagada=encerrada)
        self.plano = RotuloElidido(texto_do_plano(m, hoje), "MetaInfo")
        self.prazo = RotuloElidido(texto_do_prazo(m, hoje), "MetaInfo")
        # no modo mensal o plano já diz "pronta em ..."; o prazo só acrescenta no modo prazo
        self.prazo.setVisible(not encerrada and m.modo == "prazo" and v is not None and v.situacao != "cumprida")
        numeros = QVBoxLayout()
        numeros.setSpacing(6)
        numeros.addStretch(1)
        numeros.addWidget(self.guardado)
        numeros.addWidget(self.barra)
        numeros.addWidget(self.plano)
        numeros.addWidget(self.prazo)
        numeros.addStretch(1)
        valores = QHBoxLayout()
        valores.setSpacing(3 * tema.ESPACO)
        valores.addWidget(self.percentual)
        valores.addLayout(numeros, 1)
        self.pilula_ritmo = Pilula("")
        ritmo_txt = texto_do_ritmo(m, ritmo)
        self.ritmo = _rotulo(ritmo_txt[0] if ritmo_txt else "", "MetaInfo")
        self.ritmo.setWordWrap(True)
        if ritmo_txt:
            self.pilula_ritmo.definir("no ritmo" if ritmo_txt[1] == "bom" else "abaixo do plano", ritmo_txt[1])
        linha_ritmo = QHBoxLayout()
        linha_ritmo.setSpacing(tema.ESPACO)
        linha_ritmo.addWidget(self.pilula_ritmo)
        linha_ritmo.addWidget(self.ritmo, 1)
        self.pilula_ritmo.setVisible(bool(ritmo_txt))
        self.ritmo.setVisible(bool(ritmo_txt))
        self.grafico = GraficoEvolucao(
            evolucao or [], m.valor_alvo_centavos,
            prazo=m.prazo if m.modo == "prazo" else None, mensal=m.mensal_centavos if m.modo != "prazo" else None,
        )
        self.titulo_grafico = _rotulo("Evolução", "SimTitulo")
        tem_historia = len(evolucao or []) > 1  # um mês só não faz linha
        self.grafico.setVisible(tem_historia)
        self.titulo_grafico.setVisible(tem_historia)

        self.detalhe = _rotulo("", "MetaDetalhe")
        self.detalhe.setWordWrap(True)
        if v is not None and v.situacao in ("parcial", "nao_cabe"):
            self.detalhe.setText(explicacao_viabilidade(v, sobra))
        self.detalhe.setVisible(bool(self.detalhe.text()))

        self.form = FormMovimento()
        self.form.setVisible(False)
        self.form.cancelado.connect(self.fechar_form)
        self.form.confirmado.connect(self._confirmado)

        self._acoes = self._montar_acoes(m, encerrada, v)

        p = 2 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(p, p, p, p)
        coluna.setSpacing(tema.ESPACO)
        coluna.addLayout(topo)
        coluna.addLayout(valores)
        coluna.addWidget(self.detalhe)
        coluna.addLayout(linha_ritmo)
        coluna.addWidget(self.form)
        coluna.addLayout(self._acoes)
        coluna.addSpacing(tema.ESPACO)
        coluna.addWidget(self.titulo_grafico)
        coluna.addWidget(self.grafico)

    def _montar_menu(self, posicao: int, total: int, encerrada: bool) -> QToolButton:
        """Editar, prioridade, Concluir e Arquivar num menu "⋯": à vista ficam só Guardar e Retirar."""
        self.botao_menu = QToolButton()
        self.botao_menu.setObjectName("BotaoMenu")
        self.botao_menu.setText("⋯")
        self.botao_menu.setToolTip("Editar, mudar a prioridade, concluir ou arquivar")
        self.botao_menu.setAccessibleName(f"Ações de {self.meta.nome}")
        self.botao_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.botao_menu)
        self.botao_editar = menu.addAction("Editar")
        self.botao_editar.triggered.connect(lambda: self.editar.emit(self.id_meta))
        self.botao_sobe = menu.addAction("Mais prioridade")
        self.botao_sobe.setEnabled(posicao > 0)
        self.botao_sobe.triggered.connect(lambda: self.mover.emit(self.id_meta, -1))
        self.botao_desce = menu.addAction("Menos prioridade")
        self.botao_desce.setEnabled(posicao < total - 1)
        self.botao_desce.triggered.connect(lambda: self.mover.emit(self.id_meta, +1))
        menu.addSeparator()
        self.acao_concluir = menu.addAction("Concluir")
        self.acao_concluir.triggered.connect(lambda: self.concluir.emit(self.id_meta))
        self.botao_arquivar = menu.addAction("Arquivar")
        self.botao_arquivar.triggered.connect(lambda: self.arquivar.emit(self.id_meta))
        self.botao_menu.setMenu(menu)
        self.botao_menu.setVisible(not encerrada)
        return self.botao_menu

    def _montar_acoes(self, m: Meta, encerrada: bool, v) -> QHBoxLayout:
        linha = QHBoxLayout()
        linha.setSpacing(tema.ESPACO)
        cumprida = v is not None and v.situacao == "cumprida"

        self.botao_guardar = _compacto(BotaoSecundario("Guardar") if cumprida else BotaoPrimario("Guardar"))
        self.botao_guardar.clicked.connect(lambda: self.guardar.emit(self.id_meta))
        self.botao_retirar = _compacto(BotaoSecundario("Retirar"))
        self.botao_retirar.setEnabled(m.guardado > 0)
        self.botao_retirar.setToolTip("" if m.guardado > 0 else "Não há nada guardado nessa meta")
        self.botao_retirar.clicked.connect(lambda: self.retirar.emit(self.id_meta))
        # cumprida: concluir vira a ação principal, à vista (o menu também tem)
        self.botao_concluir = _compacto(BotaoPrimario("Concluir"))
        self.botao_concluir.clicked.connect(lambda: self.concluir.emit(self.id_meta))
        self.botao_reativar = _acao("Reativar")
        self.botao_reativar.clicked.connect(lambda: self.reativar.emit(self.id_meta))

        if encerrada:
            linha.addWidget(self.botao_retirar)
            linha.addStretch(1)
            linha.addWidget(self.botao_reativar)
            for w in (self.botao_guardar, self.botao_concluir):
                w.setVisible(False)
                linha.addWidget(w)
            self.botao_retirar.setVisible(m.guardado > 0)
        else:
            self.botao_reativar.setVisible(False)
            linha.addWidget(self.botao_guardar)
            linha.addWidget(self.botao_retirar)
            linha.addWidget(self.botao_concluir)
            self.botao_concluir.setVisible(cumprida)
            linha.addStretch(1)
        return linha

    # --- Formulário de guardar/retirar -------------------------------------------------------
    def abrir_form(self, tipo: str, hoje: date, contas: list[tuple[int, str]]) -> None:
        """`tipo`: "guardar" ou "retirar"."""
        self._tipo_form = tipo
        if tipo == "guardar":
            v = self.meta.viabilidade
            sugerido = v.necessario if v is not None and v.necessario else None
            self.form.preparar(f"Guardar em {self.meta.nome}", "Tirar de", "Guardar", sugerido, hoje, contas)
        else:
            self.form.preparar(
                f"Retirar de {self.meta.nome} · {formatar_brl(self.meta.guardado)} guardados", "Devolver pra", "Retirar", None, hoje, contas
            )
        self.form.setVisible(True)
        self.form.valor.setFocus()
        self.form.valor.selectAll()

    def fechar_form(self) -> None:
        self._tipo_form = None
        self.form.setVisible(False)

    @property
    def tipo_form(self) -> str | None:
        return self._tipo_form

    def _confirmado(self, centavos: int, conta_id: int, data: date) -> None:
        self.movimento.emit(self.id_meta, self._tipo_form, centavos, conta_id, data)


# --- Comprar agora ou parcelar? --------------------------------------------------------------------


class _BlocoSimulacao(QFrame):
    """Um dos dois lados da comparação: título, frase principal, linhas de apoio e uma pílula."""

    def __init__(self, titulo: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SimBloco")
        p = 2 * tema.ESPACO
        self.coluna = QVBoxLayout(self)
        self.coluna.setContentsMargins(p, p, p, p)
        self.coluna.setSpacing(6)
        self.titulo = _rotulo(titulo, "SimTitulo")
        self.destaque = _rotulo("", "SimDestaque")
        self.destaque.setWordWrap(True)
        self.pilula = Pilula("", "neutra")
        self.linhas = QVBoxLayout()
        self.linhas.setSpacing(4)
        self.coluna.addWidget(self.titulo)
        self.coluna.addWidget(self.destaque)
        self.coluna.addWidget(self.pilula, 0, Qt.AlignmentFlag.AlignLeft)
        self.coluna.addLayout(self.linhas)
        self.coluna.addStretch(1)

    def definir(self, destaque: str, linhas: list[str], pilula: tuple[str, str] | None = None) -> None:
        self.destaque.setText(destaque)
        if pilula:
            self.pilula.definir(*pilula)
        self.pilula.setVisible(pilula is not None)
        while self.linhas.count():
            widget = self.linhas.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for texto in linhas:
            rotulo = _rotulo(texto, "SimDetalhe")
            rotulo.setWordWrap(True)
            self.linhas.addWidget(rotulo)


class Simulador(Cartao):
    """"Comprar agora ou parcelar?": valor, parcelas e acréscimo; mostra À vista × Parcelado lado a lado."""

    criar_meta = Signal(int, int)  # valor da compra, sugestão de mensal (0 se não há sobra livre)

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date], parent: QWidget | None = None):
        super().__init__("Comprar agora ou parcelar?", "Compare antes de decidir", parent=parent)
        self.conn = conn
        self._hoje = hoje
        self._valor_atual: int | None = None
        self._mensal_sugerido = 0

        entrada = QFrame()
        entrada.setObjectName("FormMeta")
        grade = QGridLayout(entrada)
        p = 2 * tema.ESPACO
        grade.setContentsMargins(p, p, p, p)
        grade.setHorizontalSpacing(2 * tema.ESPACO)
        grade.setVerticalSpacing(4)
        for c in range(3):
            grade.setColumnStretch(c, 1)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        self.parcelas = CampoLista()
        for n in range(1, metas.MAX_PARCELAS + 1):
            self.parcelas.addItem(f"{n}x", n)
        self.parcelas.setCurrentIndex(self.parcelas.findData(PARCELAS_PADRAO))
        self.acrescimo = QLineEdit()
        self.acrescimo.setPlaceholderText("0,00")
        self.acrescimo.setToolTip("Quanto o parcelado custa a mais no total (juros, taxas). Deixe vazio se não tem.")
        for coluna, (texto, campo) in enumerate(
            (("Valor da compra (R$)", self.valor), ("Parcelas", self.parcelas), ("Acréscimo do parcelado (R$)", self.acrescimo))
        ):
            grade.addWidget(_rotulo(texto, "CampoRotulo"), 0, coluna)
            grade.addWidget(campo, 1, coluna)
        self.erro = _rotulo("", "CampoErro")
        self.erro.setWordWrap(True)
        self.erro.setVisible(False)
        grade.addWidget(self.erro, 2, 0, 1, 3)
        self.corpo.addWidget(entrada)

        self.dica = _rotulo("Digite o valor da compra pra comparar.", "CartaoSubtitulo")
        self.corpo.addWidget(self.dica)

        self.a_vista = _BlocoSimulacao("À vista")
        self.botao_criar = _compacto(BotaoSecundario("Criar como meta"))
        self.botao_criar.setToolTip("Junta o dinheiro antes de comprar: cria uma meta com esse valor")
        self.botao_criar.clicked.connect(self._pedir_meta)
        self.a_vista.coluna.insertWidget(self.a_vista.coluna.count() - 1, self.botao_criar, 0, Qt.AlignmentFlag.AlignLeft)
        self.parcelado = _BlocoSimulacao("Parcelado")
        self.blocos = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_BLOCO, espaco=3 * tema.ESPACO)
        self.blocos.adicionar(self.a_vista)
        self.blocos.adicionar(self.parcelado)
        self.corpo.addWidget(self.blocos)
        self.nota = _rotulo("", "CampoDica")
        self.nota.setWordWrap(True)
        self.corpo.addWidget(self.nota)

        self.valor.textChanged.connect(lambda _t: self.simular())
        self.acrescimo.textChanged.connect(lambda _t: self.simular())
        self.parcelas.currentIndexChanged.connect(lambda _i: self.simular())
        self.simular()

    def _centavos(self, campo: QLineEdit, rotulo: str) -> int | None:
        """Centavos do campo (vazio = 0); None e mensagem de erro se não é número."""
        texto = campo.text().strip()
        if not texto:
            return 0
        try:
            return para_centavos(texto)
        except ValueError:
            self.erro.setText(f"!  {rotulo}: use só números, como 1.500,00.")
            return None

    def simular(self) -> None:
        """Recalcula tudo a cada mudança nos campos; sem um valor válido, mostra só a dica."""
        self.erro.setText("")
        valor = self._centavos(self.valor, "Valor da compra")
        acrescimo = self._centavos(self.acrescimo, "Acréscimo")
        pronto = bool(valor) and valor > 0 and acrescimo is not None and acrescimo >= 0
        if valor is not None and valor < 0 or acrescimo is not None and acrescimo < 0:
            self.erro.setText("!  Os valores não podem ser negativos.")
        self.erro.setVisible(bool(self.erro.text()))
        self.dica.setVisible(not pronto)
        self.blocos.setVisible(pronto)
        self.nota.setVisible(False)
        self._valor_atual = valor if pronto else None
        if not pronto:
            return
        s = metas.simular_compra(self.conn, self._hoje(), valor, self.parcelas.currentData(), acrescimo)
        self._mostrar_a_vista(s["a_vista"])
        self._mostrar_parcelado(s["parcelado"], s["parcelas"], valor)
        self.nota.setText("Ainda sem um mês completo de lançamentos: a sobra aparece depois dele e os prazos podem mudar." if s["estimativa"] else "")
        self.nota.setVisible(s["estimativa"])

    def _mostrar_a_vista(self, a: dict) -> None:
        self._mensal_sugerido = a["guardando_por_mes"]
        if a["cabe_agora"]:
            self.a_vista.definir(
                "Cabe no Livre agora", [f"Livre até o fim do mês: {formatar_brl(a['livre_agora'])}"], ("dá pra pagar já", "bom")
            )
        elif a["meses"] is not None:
            self.a_vista.definir(
                f"≈ {plural_meses(a['meses'])} guardando",
                [f"{reais(a['guardando_por_mes'])} por mês da sobra livre · pronto em {mes_ano(a['mes_previsto'])}"],
                ("juntando antes", "neutra"),
            )
        else:
            self.a_vista.definir(
                "Sem sobra livre pra juntar",
                ["A sobra média está toda comprometida com metas, ou não é positiva."],
                ("não cabe agora", "ruim"),
            )

    def _mostrar_parcelado(self, p: dict, parcelas: int, valor: int) -> None:
        total = f"total {formatar_brl(p['total_centavos'])}"
        if p["acrescimo_centavos"]:
            total += f" (+{reais(p['acrescimo_centavos'])})"
        linhas = [
            f"até {mes_ano(p['mes_final'])} · {total}",
            f"Sobra média: {reais(p['sobra_antes'])} → {reais(p['sobra_depois'])} por mês até lá",
        ]
        if p["sobra_depois"] < 0:
            pilula = ("a parcela passa da sua sobra", "ruim")
        elif p["atrasos"]:
            pilula = ("atrasa suas metas", "atencao")
            linhas.append(f"Atrasa: {texto_atrasos(p['atrasos'])}")
        else:
            pilula = ("não atrasa nenhuma meta", "bom")
        if p["sobra_depois"] < 0 and p["atrasos"]:
            linhas.append(f"Atrasa: {texto_atrasos(p['atrasos'])}")
        self.parcelado.definir(f"{parcelas}x de {formatar_brl(p['parcela_centavos'])}", linhas, pilula)

    def _pedir_meta(self) -> None:
        if self._valor_atual:
            self.criar_meta.emit(self._valor_atual, self._mensal_sugerido)

    def limpar(self) -> None:
        self.valor.clear()
        self.acrescimo.clear()


# --- A aba --------------------------------------------------------------------------------------


class AbaMetas(QScrollArea):
    """Conteúdo da aba. `hoje` é uma função (a janela principal pode fixar a data nos testes)."""

    alterado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date] = date.today, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje
        self._mostrar_encerradas = False
        self._sobra = 0
        self._selecionada: int | None = None
        self._detalhe: CartaoMeta | None = None
        self.dialogo: DialogoMeta | None = None

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        conteudo = QWidget()
        conteudo.setObjectName("Conteudo")
        coluna = QVBoxLayout(conteudo)
        m = 3 * tema.ESPACO
        coluna.setContentsMargins(m, m, m, m)
        coluna.setSpacing(m)
        coluna.addWidget(self._montar_resumo())
        self._cartao_reserva = self._montar_reserva()
        coluna.addWidget(self._cartao_reserva)
        self._corpo = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._corpo.setSpacing(m)
        self._corpo.addWidget(self._montar_lista(), 2, Qt.AlignmentFlag.AlignTop)
        self._lado_detalhe = QVBoxLayout()
        self._corpo.addLayout(self._lado_detalhe, 3)
        coluna.addLayout(self._corpo)
        self.simulador = Simulador(conn, hoje)
        self.simulador.criar_meta.connect(self._criar_do_simulador)
        coluna.addWidget(self.simulador)
        coluna.addStretch(1)
        self.setWidget(conteudo)
        self.recarregar()

    # --- Montagem --------------------------------------------------------------------
    def _montar_resumo(self) -> GradeResponsiva:
        self._guardado = NumeroDestaque("Guardado", "", "", destaque=True)
        self._sobra_media = NumeroDestaque("Sobra média por mês", "", "")
        self._comprometido = NumeroDestaque("Comprometido com metas", "", "")
        self._livre = NumeroDestaque("Livre pra novas metas", "", "")
        grade = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_NUMERO, espaco=3 * tema.ESPACO, colunas_permitidas=(4, 2, 1))
        for numero in (self._guardado, self._sobra_media, self._comprometido, self._livre):
            bloco = Cartao()
            bloco.corpo.addWidget(numero)
            grade.adicionar(bloco)
        return grade

    def _montar_reserva(self) -> Cartao:
        cartao = Cartao("Sugestão")
        linha = QHBoxLayout()
        linha.setSpacing(3 * tema.ESPACO)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        self.reserva_texto = _rotulo("", "ReservaTexto")
        self.reserva_texto.setWordWrap(True)
        self.reserva_apoio = _rotulo("", "ReservaApoio")
        self.reserva_apoio.setWordWrap(True)
        textos.addWidget(self.reserva_texto)
        textos.addWidget(self.reserva_apoio)
        linha.addLayout(textos, 1)
        self.botao_reserva = BotaoPrimario("Criar meta")
        self.botao_reserva.clicked.connect(self._criar_reserva)
        linha.addWidget(self.botao_reserva, 0, Qt.AlignmentFlag.AlignVCenter)
        cartao.corpo.addLayout(linha)
        self._reserva_alvo = 0
        return cartao

    def _montar_lista(self) -> Cartao:
        self._cartao_lista = Cartao("Suas metas", "Em ordem de prioridade: a de cima recebe a sobra primeiro")
        self._lista = QVBoxLayout()
        self._lista.setSpacing(tema.ESPACO)
        self._cartao_lista.corpo.addLayout(self._lista)
        self.botao_nova = QPushButton("+ Nova meta")
        self.botao_nova.setObjectName("NovaMeta")
        self.botao_nova.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_nova.setToolTip("Criar uma meta")
        self.botao_nova.clicked.connect(lambda: self.nova_meta())
        self._cartao_lista.corpo.addWidget(self.botao_nova)
        self._vazio = EstadoVazio("lista", "Nenhuma meta ainda", "Crie uma pra começar: reserva, viagem, cadeira nova...")
        self._vazio.setMinimumHeight(200)
        self._cartao_lista.corpo.addWidget(self._vazio)
        self.botao_encerradas = QPushButton()
        self.botao_encerradas.setObjectName("BotaoLink")
        self.botao_encerradas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_encerradas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_encerradas.clicked.connect(lambda: self.mostrar_encerradas(not self._mostrar_encerradas))
        self._cartao_lista.corpo.addWidget(self.botao_encerradas, 0, Qt.AlignmentFlag.AlignLeft)
        return self._cartao_lista

    # --- Dados ------------------------------------------------------------------------
    def recarregar(self) -> None:
        hoje = self._hoje()
        r = metas.resumo(self.conn, hoje)
        self._sobra = r.sobra
        lista = metas.listar(self.conn, hoje, incluir_arquivadas=True)
        ativas = [m for m in lista if m.estado == "ativa"]
        encerradas = [m for m in lista if m.estado != "ativa"]

        self._carregar_numeros(r, len(ativas))
        self._carregar_reserva(hoje)

        self._esvaziar()
        visiveis = ativas + (encerradas if self._mostrar_encerradas else [])
        if self._selecionada not in {m.id for m in visiveis}:
            self._selecionada = visiveis[0].id if visiveis else None
        for m in ativas:
            self._lista.addWidget(self._montar_item(m, hoje))
        if self._mostrar_encerradas and encerradas:
            self._lista.addWidget(_rotulo("Concluídas e arquivadas", "GrupoNome"))
            for m in encerradas:
                self._lista.addWidget(self._montar_item(m, hoje))
        self._mostrar_detalhe(hoje, ativas, visiveis)
        self._vazio.setVisible(not lista)
        self.botao_encerradas.setVisible(bool(encerradas))
        self.botao_encerradas.setText(
            "Esconder concluídas e arquivadas" if self._mostrar_encerradas else f"Mostrar concluídas e arquivadas ({len(encerradas)})"
        )
        self._cartao_lista.subtitulo.setText(
            "Em ordem de prioridade: a de cima recebe a sobra primeiro" if ativas else "Nenhuma meta ativa por enquanto"
        )
        self.simulador.simular()

    def _carregar_numeros(self, r: metas.ResumoMetas, n_ativas: int) -> None:
        self._guardado.definir(formatar_brl(r.guardado), "nas caixinhas, fora do Em conta")
        # sem mês completo a sobra é 0 e `estimativa` vale True (as duas coisas andam juntas)
        apoio = "aparece depois do primeiro mês completo" if r.meses_usados == 0 else f"média dos últimos {plural_meses(r.meses_usados)}"
        self._sobra_media.definir(formatar_brl(r.sobra), apoio)
        self._comprometido.definir(
            formatar_brl(r.comprometido), "por mês · nenhuma meta ativa" if not n_ativas else f"por mês · {n_ativas} {'meta ativa' if n_ativas == 1 else 'metas ativas'}"
        )
        self._livre.definir(formatar_brl(r.livre), "por mês, depois das metas")

    def _carregar_reserva(self, hoje: date) -> None:
        alvo, gasto, meses = metas.reserva_sugerida(self.conn, hoje)
        self._reserva_alvo = alvo
        visivel = not metas.tem_reserva(self.conn) and alvo > 0
        self._cartao_reserva.setVisible(visivel)
        if visivel:
            self.reserva_texto.setText(f"Reserva de emergência: {formatar_brl(alvo)}")
            self.reserva_apoio.setText(
                f"{metas.MESES_DA_RESERVA} meses dos seus gastos essenciais ({reais(gasto)}/mês), "
                f"com base em {plural_meses(meses)} de lançamentos"
            )

    def _esvaziar(self) -> None:
        while self._lista.count():
            widget = self._lista.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _montar_item(self, m: Meta, hoje: date) -> ItemMeta:
        item = ItemMeta(m, hoje, m.id == self._selecionada)
        item.clicado.connect(self.selecionar)
        return item

    def _mostrar_detalhe(self, hoje: date, ativas: list[Meta], visiveis: list[Meta]) -> None:
        """Refaz o painel da meta escolhida (só um existe por vez: o formulário aberto fecha ao trocar de meta)."""
        if self._detalhe is not None:
            self._detalhe.setParent(None)
            self._detalhe.deleteLater()
            self._detalhe = None
        m = next((x for x in visiveis if x.id == self._selecionada), None)
        if m is None:
            return
        posicao = next((i for i, x in enumerate(ativas) if x.id == m.id), 0)
        self._detalhe = self._montar_cartao(m, hoje, posicao, len(ativas))
        self._lado_detalhe.addWidget(self._detalhe, 0, Qt.AlignmentFlag.AlignTop)

    def selecionar(self, meta_id: int) -> None:
        if meta_id != self._selecionada:
            self._selecionada = meta_id
            self.recarregar()

    def _montar_cartao(self, m: Meta, hoje: date, posicao: int = 0, total: int = 1) -> CartaoMeta:
        cartao = CartaoMeta(
            m, hoje, self._sobra, posicao, total,
            evolucao=metas.evolucao(self.conn, m.id, hoje), ritmo=metas.ritmo_medio(self.conn, m.id, hoje),
        )
        cartao.guardar.connect(lambda i: self.abrir_form(i, "guardar"))
        cartao.retirar.connect(lambda i: self.abrir_form(i, "retirar"))
        cartao.editar.connect(self.editar_meta)
        cartao.mover.connect(self.mover)
        cartao.concluir.connect(self.concluir)
        cartao.arquivar.connect(self.arquivar)
        cartao.reativar.connect(self.reativar)
        cartao.movimento.connect(self._movimento)
        return cartao

    def itens(self) -> list[ItemMeta]:
        """As metas da lista, na ordem em que aparecem."""
        return [w for i in range(self._lista.count()) if isinstance(w := self._lista.itemAt(i).widget(), ItemMeta)]

    def cartao_da_meta(self, meta_id: int) -> CartaoMeta | None:
        """Escolhe a meta (como clicar nela) e devolve o detalhe dela."""
        if meta_id not in {i.id_meta for i in self.itens()}:
            return None
        self.selecionar(meta_id)
        return self._detalhe

    # --- Ações ------------------------------------------------------------------------------
    def mostrar_encerradas(self, mostrar: bool) -> None:
        self._mostrar_encerradas = mostrar
        self.recarregar()

    def _contas_do_formulario(self) -> list[tuple[int, str]]:
        return [(c["id"], c["nome"]) for c in cadastros.contas_por_tipo(self.conn, ("corrente", "dinheiro"))]

    def abrir_form(self, meta_id: int, tipo: str) -> None:
        """Escolhe a meta e abre o formulário de guardar/retirar no detalhe dela."""
        cartao = self.cartao_da_meta(meta_id)
        if cartao is not None:
            cartao.abrir_form(tipo, self._hoje(), self._contas_do_formulario())

    def _movimento(self, meta_id: int, tipo: str, centavos: int, conta_id: int, data: date) -> None:
        cartao = self._detalhe
        try:
            nome = metas.obter(self.conn, meta_id).nome
            if tipo == "guardar":
                metas.guardar(self.conn, meta_id, centavos, conta_id, data)
                mensagem = f"Guardado {formatar_brl(centavos)} em {nome}"
            else:
                metas.retirar(self.conn, meta_id, centavos, conta_id, data)
                mensagem = f"Retirado {formatar_brl(centavos)} de {nome}"
        except ValueError as erro:
            if cartao is not None:
                cartao.form.mostrar_erro(str(erro))
            return
        self._avisar(mensagem)

    def mover(self, meta_id: int, passo: int) -> None:
        metas.mover_prioridade(self.conn, meta_id, passo)
        self._avisar(f"Prioridade alterada: {metas.obter(self.conn, meta_id).nome}")

    def concluir(self, meta_id: int) -> None:
        meta = metas.obter(self.conn, meta_id)
        metas.concluir(self.conn, self._hoje(), meta_id)
        resto = f" Ainda há {formatar_brl(meta.guardado)} guardados nela: use Retirar." if meta.guardado else ""
        self._avisar(f"Meta concluída: {meta.nome}.{resto}")

    def arquivar(self, meta_id: int) -> None:
        meta = metas.obter(self.conn, meta_id)
        metas.arquivar(self.conn, meta_id)
        resto = f" Ainda há {formatar_brl(meta.guardado)} guardados nela." if meta.guardado else ""
        self._avisar(f"Meta arquivada: {meta.nome}.{resto}")

    def reativar(self, meta_id: int) -> None:
        try:
            metas.reativar(self.conn, meta_id)
        except ValueError as erro:
            self.alterado.emit(str(erro))
            return
        self._avisar(f"Meta reativada: {metas.obter(self.conn, meta_id).nome}")

    def _avisar(self, mensagem: str) -> None:
        self.recarregar()
        self.alterado.emit(mensagem)

    # --- Diálogos ---------------------------------------------------------------------------
    def _abrir_dialogo(self, meta_id: int | None, **sugestao) -> DialogoMeta | None:
        try:
            dialogo = DialogoMeta(self.conn, meta_id, parent=self, hoje=self._hoje(), **sugestao)
        except ValueError:  # a meta sumiu
            self.recarregar()
            return None
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._avisar)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo", None))
        self.dialogo = dialogo
        dialogo.open()
        return dialogo

    def nova_meta(self, **sugestao) -> DialogoMeta | None:
        """"+ Nova meta". `sugestao`: nome, valor_centavos, mensal_centavos, reserva (pré-preenchem o diálogo)."""
        return self._abrir_dialogo(None, **sugestao)

    def editar_meta(self, meta_id: int) -> DialogoMeta | None:
        return self._abrir_dialogo(meta_id)

    def _criar_reserva(self) -> None:
        self.nova_meta(nome="Reserva de emergência", valor_centavos=self._reserva_alvo, reserva=True)

    def _criar_do_simulador(self, valor_centavos: int, mensal_centavos: int) -> None:
        self.nova_meta(valor_centavos=valor_centavos, mensal_centavos=mensal_centavos or None)

    def resizeEvent(self, evento) -> None:
        estreita = evento.size().width() < LARGURA_LADO_A_LADO
        direcao = QBoxLayout.Direction.TopToBottom if estreita else QBoxLayout.Direction.LeftToRight
        if self._corpo.direction() != direcao:
            self._corpo.setDirection(direcao)
        super().resizeEvent(evento)
