"""Gráficos da tela inicial (QtCharts) no visual da direção visual.

- `PainelGraficos`: troca entre o fluxo (área, gasto por dia) e a rosca (gasto por categoria).
- Os gráficos recebem DataFrames prontos (de `relatorios`) e não conhecem o banco.
- Fundo transparente: quem dá a cor é o `Cartao` por trás.
"""

import calendar
import math
import os

import pandas as pd
from PySide6.QtCharts import (
    QAreaSeries,
    QCategoryAxis,
    QChart,
    QChartView,
    QLineSeries,
    QPieSeries,
    QScatterSeries,
)
from PySide6.QtCore import QMargins, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QGraphicsSimpleTextItem,
    QGridLayout,
    QLabel,
    QStackedLayout,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from meu_cofre.lancamentos import formatar_brl, formatar_brl_inteiro
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import RotuloElidido

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

OPCAO_FLUXO = "Fluxo"
OPCAO_ROSCA = "Para onde foi"

ROSCA_FURO = 0.62  # fração do diâmetro que é buraco
ROSCA_TAMANHO = 0.9  # fração da área do gráfico ocupada pela rosca


# --- Formatação e escalas ------------------------------------------------------------


def nome_do_mes(ano: int, mes: int) -> str:
    """(2026, 10) -> "Outubro de 2026"."""
    return f"{MESES[mes - 1].capitalize()} de {ano}"


def brl_curto(centavos: float) -> str:
    """150000 -> "R$ 1,5 mil"; 8500 -> "R$ 85"; 123456789 -> "R$ 1,2 mi"."""
    reais, sinal = abs(centavos) / 100, "-" if centavos < 0 else ""

    def n(x: float) -> str:
        return f"{x:.1f}".rstrip("0").rstrip(".").replace(".", ",")

    if reais >= 1_000_000:
        return f"{sinal}R$ {n(reais / 1_000_000)} mi"
    if reais >= 1000:
        return f"{sinal}R$ {n(reais / 1000)} mil"
    return f"{sinal}R$ {reais:.0f}"


def ticks_bonitos(minimo: float, maximo: float, alvo: int = 5) -> list[float]:
    """Valores "redondos" (1, 2, 2,5, 5 x 10^n) que cobrem [minimo, maximo]."""
    if maximo <= minimo:
        maximo = minimo + 100
    passo_bruto = (maximo - minimo) / alvo
    ordem = 10 ** math.floor(math.log10(passo_bruto))
    passo = next(m * ordem for m in (1, 2, 2.5, 5, 10) if m * ordem >= passo_bruto)
    inicio = math.floor(minimo / passo) * passo
    fim = math.ceil(maximo / passo) * passo
    return [inicio + i * passo for i in range(round((fim - inicio) / passo) + 1)]


def _animar() -> bool:
    """Sem animação no modo screenshot (o PNG sairia no meio do movimento)."""
    return not os.environ.get("MEU_COFRE_SCREENSHOT")


def _fonte(px: int) -> QFont:
    fonte = QFont(QApplication.font())
    fonte.setPixelSize(px)
    return fonte


def _cor(hex_: str, alfa: float = 1.0) -> QColor:
    cor = QColor(hex_)
    cor.setAlphaF(alfa)
    return cor


def _dica(ativo: bool, texto: str, vista: QWidget) -> None:
    if ativo:
        QToolTip.showText(QCursor.pos(), texto, vista)
    else:
        QToolTip.hideText()


# --- Peças comuns -----------------------------------------------------------------------


def _novo_grafico(margens: QMargins) -> QChart:
    grafico = QChart()
    grafico.setTheme(QChart.ChartTheme.ChartThemeDark)
    # Depois do tema: ele reativa o fundo.
    grafico.setBackgroundVisible(False)
    grafico.setPlotAreaBackgroundVisible(False)
    grafico.setBackgroundRoundness(0)
    grafico.setMargins(margens)
    grafico.layout().setContentsMargins(0, 0, 0, 0)
    grafico.legend().setVisible(False)
    if _animar():
        grafico.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        grafico.setAnimationDuration(450)
    return grafico


class _Vista(QChartView):
    """QChartView transparente, sem moldura."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(Qt.GlobalColor.transparent))
        self.viewport().setAutoFillBackground(False)


def _estilizar_eixo(eixo: QCategoryAxis, grade: bool) -> None:
    eixo.setLabelsBrush(QBrush(QColor(tema.TEXTO_APAGADO)))
    eixo.setLabelsFont(_fonte(11))
    eixo.setLinePen(QPen(Qt.PenStyle.NoPen))
    eixo.setGridLineVisible(grade)
    if grade:
        eixo.setGridLinePen(QPen(_cor(tema.TEXTO, 0.07), 1))
    eixo.setMinorGridLineVisible(False)
    eixo.setShadesVisible(False)


class EstadoVazio(QWidget):
    """Ícone discreto + título + dica, centralizados. `icone`: "linha", "rosca" ou "lista"."""

    def __init__(self, icone: str, titulo: str = "", dica: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._icone = icone
        self._desenho = QWidget()
        self._desenho.setFixedSize(56, 56)
        self._desenho.paintEvent = self._pintar_icone  # type: ignore[method-assign]
        self.titulo = QLabel(titulo)
        self.titulo.setObjectName("VazioTitulo")
        self.dica = QLabel(dica)
        self.dica.setObjectName("VazioDica")
        for rotulo in (self.titulo, self.dica):
            rotulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rotulo.setWordWrap(True)
        coluna = QVBoxLayout(self)
        coluna.setSpacing(6)
        coluna.addStretch(1)
        coluna.addWidget(self._desenho, 0, Qt.AlignmentFlag.AlignHCenter)
        coluna.addSpacing(6)
        coluna.addWidget(self.titulo)
        coluna.addWidget(self.dica)
        coluna.addStretch(1)
        self.dica.setVisible(bool(dica))

    def definir(self, titulo: str, dica: str = "") -> None:
        self.titulo.setText(titulo)
        self.dica.setText(dica)
        self.dica.setVisible(bool(dica))

    def _pintar_icone(self, _evento) -> None:
        p = QPainter(self._desenho)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        caneta = QPen(_cor(tema.TEXTO_APAGADO, 0.8), 2.5)
        caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
        caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(caneta)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._icone == "rosca":
            p.drawEllipse(QRectF(8, 8, 40, 40))
            p.drawEllipse(QRectF(20, 20, 16, 16))
        elif self._icone == "lista":
            for y in (16, 28, 40):
                p.drawLine(12, y, 12, y)
                p.drawLine(22, y, 44, y)
        else:  # linha: eixos + uma linha subindo e descendo
            p.drawLine(10, 10, 10, 46)
            p.drawLine(10, 46, 48, 46)
            p.drawPolyline([QPointF(16, 38), QPointF(26, 26), QPointF(34, 32), QPointF(44, 16)])
        p.end()


# --- Fluxo: gasto por dia ---------------------------------------------------------------


class PainelFluxo(QWidget):
    """Área verde com o gasto de cada dia, pontos nos dias com gasto, média tracejada e tooltip."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._vista = _Vista()
        self._vista.setMinimumHeight(240)
        self._vazio = EstadoVazio("linha")
        self._pilha = QStackedLayout(self)
        self._pilha.setContentsMargins(0, 0, 0, 0)
        self._pilha.addWidget(self._vista)
        self._pilha.addWidget(self._vazio)
        self._rotulo_media: QGraphicsSimpleTextItem | None = None
        self._serie_media: QLineSeries | None = None
        self._media_pos: tuple[int, float] | None = None
        self._valores_por_dia: list[int] = []  # centavos, do dia 1 em diante
        self._refs: tuple = ()

    def definir(self, dias: pd.DataFrame, ano: int, mes: int, mes_atual: bool) -> None:
        """`dias`: saída de `relatorios.gastos_por_dia` (data, centavos)."""
        if dias.empty or int(dias["centavos"].sum()) == 0:
            sufixo = " ainda" if mes_atual else ""
            self._vazio.definir(f"Nenhum gasto em {MESES[mes - 1]}{sufixo}", "Ctrl+N pra lançar")
            self._pilha.setCurrentWidget(self._vazio)
            return

        n_dias = calendar.monthrange(ano, mes)[1]
        numeros = dias["data"].dt.day.tolist()
        valores = [int(c) for c in dias["centavos"]]
        media = sum(valores) / len(valores)  # por dia decorrido

        grafico = _novo_grafico(QMargins(4, 8, 14, 0))

        topo = QLineSeries()
        base = QLineSeries()
        for dia, centavos in zip(numeros, valores):
            topo.append(dia, centavos / 100)
            base.append(dia, 0)
        area = QAreaSeries(topo, base)
        area.setPen(QPen(Qt.PenStyle.NoPen))
        degrade = QLinearGradient(0, 0, 0, 1)
        degrade.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
        degrade.setColorAt(0, _cor(tema.VERDE, 0.38))
        degrade.setColorAt(1, _cor(tema.VERDE, 0.0))
        area.setBrush(QBrush(degrade))

        linha = QLineSeries()
        linha.setPen(QPen(QColor(tema.VERDE), 2))
        for dia, centavos in zip(numeros, valores):
            linha.append(dia, centavos / 100)

        pontos = QScatterSeries()
        pontos.setMarkerShape(QScatterSeries.MarkerShape.MarkerShapeCircle)
        pontos.setMarkerSize(10)
        pontos.setColor(QColor(tema.VERDE))
        pontos.setBorderColor(QColor(tema.CARTAO))
        pontos.setPen(QPen(QColor(tema.CARTAO), 2))
        dados_do_ponto: dict[int, int] = {}
        for dia, centavos in zip(numeros, valores):
            if centavos > 0:
                pontos.append(dia, centavos / 100)
                dados_do_ponto[dia] = centavos
        pontos.hovered.connect(lambda p, ativo: self._ao_passar(p, ativo, dados_do_ponto, mes))

        serie_media = QLineSeries()
        caneta = QPen(_cor(tema.DOURADO, 0.75), 1.5)
        caneta.setStyle(Qt.PenStyle.DashLine)
        serie_media.setPen(caneta)
        serie_media.append(1, media / 100)
        serie_media.append(n_dias, media / 100)

        for serie in (area, linha, serie_media, pontos):
            grafico.addSeries(serie)

        # Eixo X: dias do mês, rótulos esparsos.
        eixo_x = QCategoryAxis()
        eixo_x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        eixo_x.setRange(1, n_dias)
        for d in [d for d in (1, 5, 10, 15, 20, 25) if d < n_dias - 2] + [n_dias]:
            eixo_x.append(str(d), d)
        eixo_x.setStartValue(1)
        _estilizar_eixo(eixo_x, grade=False)
        grafico.addAxis(eixo_x, Qt.AlignmentFlag.AlignBottom)

        # Eixo Y: reais curtos com ticks redondos.
        ticks = ticks_bonitos(0, max(valores) / 100 * 1.12)
        eixo_y = QCategoryAxis()
        eixo_y.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        eixo_y.setRange(ticks[0], ticks[-1])
        for t in ticks:
            eixo_y.append(brl_curto(t * 100), t)
        eixo_y.setStartValue(ticks[0])
        _estilizar_eixo(eixo_y, grade=True)
        grafico.addAxis(eixo_y, Qt.AlignmentFlag.AlignLeft)

        for serie in (area, linha, serie_media, pontos):
            serie.attachAxis(eixo_x)
            serie.attachAxis(eixo_y)

        # Rótulo da média (QtCharts não rotula linhas): item gráfico filho do gráfico.
        self._serie_media = serie_media
        self._media_pos = (n_dias, media / 100)
        self._valores_por_dia = valores
        self._rotulo_media = QGraphicsSimpleTextItem(f"média {formatar_brl_inteiro(round(media))}/dia", grafico)
        self._rotulo_media.setBrush(QBrush(QColor(tema.DOURADO)))
        self._rotulo_media.setFont(_fonte(11))
        self._rotulo_media.setZValue(20)
        grafico.plotAreaChanged.connect(self._posicionar_media)

        # Os wrappers Python das séries precisam viver tanto quanto o gráfico (senão o Qt cai).
        # Penduradas no próprio gráfico, elas só morrem junto com ele: trocar o gráfico ao atualizar
        # (ex.: depois de editar um lançamento) derrubava o app quando `self._refs` soltava as antigas antes.
        self._refs = (topo, base, area, linha, pontos, serie_media, eixo_x, eixo_y)
        grafico._refs = self._refs
        self._vista.setChart(grafico)
        self._pilha.setCurrentWidget(self._vista)
        QTimer.singleShot(0, self._posicionar_media)

    def _ao_passar(self, ponto: QPointF, ativo: bool, dados: dict[int, int], mes: int) -> None:
        dia = round(ponto.x())
        if ativo and dia in dados:
            _dica(True, f"{dia} {MESES_CURTOS[mes - 1]} · {formatar_brl(dados[dia])}", self._vista)
        else:
            _dica(False, "", self._vista)

    def _posicionar_media(self, *_args) -> None:
        """Põe "média R$ X/dia" sobre a linha tracejada, no canto (direito ou esquerdo) menos ocupado."""
        grafico = self._vista.chart()
        if grafico is None or self._rotulo_media is None or self._serie_media is None or self._media_pos is None:
            return
        n_dias, media = self._media_pos
        caixa = self._rotulo_media.boundingRect()
        fim = grafico.mapToPosition(QPointF(n_dias, media), self._serie_media)
        inicio = grafico.mapToPosition(QPointF(1, media), self._serie_media)
        # Quantos dias o rótulo cobre em cada ponta; escolhe a ponta onde os gastos ficam mais baixos.
        largura_do_dia = (fim.x() - inicio.x()) / max(n_dias - 1, 1)
        cobertos = max(1, math.ceil((caixa.width() + 16) / largura_do_dia)) if largura_do_dia > 0 else 1
        valores = self._valores_por_dia
        pico_direita = max(valores[n_dias - cobertos:], default=0)  # vazio se a série acaba antes
        pico_esquerda = max(valores[:cobertos], default=0)
        y = fim.y() - caixa.height() - 1
        if pico_direita <= pico_esquerda:
            x, pico = fim.x() - caixa.width() - 4, pico_direita
        else:
            x, pico = inicio.x() + 6, pico_esquerda
        topo_do_ponto = grafico.mapToPosition(QPointF(1, pico / 100), self._serie_media).y() - 6
        if topo_do_ponto < y + caixa.height() + 2:  # um ponto encostaria no rótulo: sobe acima dele
            y = topo_do_ponto - caixa.height() - 2
        self._rotulo_media.setPos(x, y)

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        QTimer.singleShot(0, self._posicionar_media)


# --- Rosca: para onde foi ---------------------------------------------------------------


class _CentroRosca(QWidget):
    """Total no meio da rosca (número grande + "gasto no mês"), desenhado por cima do gráfico."""

    def __init__(self, vista: _Vista, parent: QWidget | None = None):
        super().__init__(parent)
        self._vista = vista
        self._total = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def definir(self, total_centavos: int) -> None:
        self._total = formatar_brl(total_centavos)
        self.update()

    def paintEvent(self, _evento) -> None:
        grafico = self._vista.chart()
        if grafico is None or not self._total:
            return
        area = grafico.plotArea()
        diametro_furo = min(area.width(), area.height()) * ROSCA_TAMANHO * ROSCA_FURO
        centro = area.center()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Maior fonte (até 28 px) em que o total cabe dentro do furo.
        fonte = _fonte(28)
        fonte.setWeight(QFont.Weight.Bold)
        while fonte.pixelSize() > 12 and QFontMetrics(fonte).horizontalAdvance(self._total) > diametro_furo * 0.84:
            fonte.setPixelSize(fonte.pixelSize() - 1)
        legenda = _fonte(12)

        p.setFont(fonte)
        p.setPen(QColor(tema.TEXTO))
        altura_total = p.fontMetrics().height()
        p.drawText(
            QRectF(centro.x() - diametro_furo / 2, centro.y() - altura_total + 2, diametro_furo, altura_total),
            Qt.AlignmentFlag.AlignCenter,
            self._total,
        )
        p.setFont(legenda)
        p.setPen(QColor(tema.TEXTO_APAGADO))
        p.drawText(
            QRectF(centro.x() - diametro_furo / 2, centro.y() + 2, diametro_furo, p.fontMetrics().height() + 2),
            Qt.AlignmentFlag.AlignCenter,
            "gasto no mês",
        )
        p.end()


class _Legenda(QWidget):
    """Legenda própria da rosca: quadradinho da cor + nome + valor + %."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._grade = QGridLayout(self)
        self._grade.setContentsMargins(0, 0, 0, 0)
        self._grade.setHorizontalSpacing(10)
        self._grade.setVerticalSpacing(12)
        self._grade.setColumnStretch(1, 1)

    def definir(self, linhas: list[tuple[str, str, int, float]]) -> None:
        """linhas: (cor, nome, centavos, percentual 0-100)."""
        while self._grade.count():
            item = self._grade.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (cor, nome, centavos, percentual) in enumerate(linhas):
            quadro = QFrame()
            quadro.setFixedSize(10, 10)
            quadro.setStyleSheet(f"background: {cor}; border-radius: 3px; border: none;")
            valor = QLabel(formatar_brl(centavos))
            valor.setObjectName("LegendaValor")
            pct = QLabel("<1%" if percentual < 1 else f"{round(percentual)}%")
            pct.setObjectName("LegendaPct")
            pct.setMinimumWidth(34)
            pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grade.addWidget(quadro, i, 0, Qt.AlignmentFlag.AlignVCenter)
            self._grade.addWidget(RotuloElidido(nome, "LegendaNome"), i, 1)
            self._grade.addWidget(valor, i, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grade.addWidget(pct, i, 3)


class PainelRosca(QWidget):
    """Rosca das maiores categorias (cores em ordem fixa), total no centro e legenda ao lado."""

    LARGURA_LADO_A_LADO = 560

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._vista = _Vista()
        self._vista.setMinimumSize(220, 220)
        self._centro = _CentroRosca(self._vista)
        self._legenda = _Legenda()
        self._vazio = EstadoVazio("rosca")

        area_rosca = QWidget()
        sobreposto = QStackedLayout(area_rosca)
        sobreposto.setStackingMode(QStackedLayout.StackingMode.StackAll)
        sobreposto.setContentsMargins(0, 0, 0, 0)
        sobreposto.addWidget(self._vista)
        sobreposto.addWidget(self._centro)

        conteudo = QWidget()
        self._linha = QBoxLayout(QBoxLayout.Direction.LeftToRight, conteudo)
        self._linha.setContentsMargins(0, 0, 0, 0)
        self._linha.setSpacing(3 * tema.ESPACO)
        area_rosca.setMaximumWidth(440)
        self._legenda.setFixedWidth(300)
        self._linha.addStretch(1)
        self._linha.addWidget(area_rosca, 3)
        self._linha.addWidget(self._legenda, 0, Qt.AlignmentFlag.AlignVCenter)
        self._linha.addStretch(1)

        self._pilha = QStackedLayout(self)
        self._pilha.setContentsMargins(0, 0, 0, 0)
        self._pilha.addWidget(conteudo)
        self._pilha.addWidget(self._vazio)
        self._conteudo = conteudo

    def definir(self, categorias: pd.DataFrame, ano: int, mes: int, mes_atual: bool) -> None:
        """`categorias`: saída de `relatorios.gastos_top_categorias` (categoria, centavos, percentual)."""
        if categorias.empty:
            sufixo = " ainda" if mes_atual else ""
            self._vazio.definir(f"Nenhum gasto em {MESES[mes - 1]}{sufixo}", "Ctrl+N pra lançar")
            self._pilha.setCurrentWidget(self._vazio)
            return

        grafico = _novo_grafico(QMargins(0, 0, 0, 0))
        serie = QPieSeries()
        serie.setHoleSize(ROSCA_FURO)
        serie.setPieSize(ROSCA_TAMANHO)
        legenda = []
        for i, linha in enumerate(categorias.itertuples()):
            cor = tema.SERIE_OUTROS if linha.categoria == "Outros" else tema.SERIES[i % len(tema.SERIES)]
            fatia = serie.append(linha.categoria, linha.centavos / 100)
            fatia.setColor(QColor(cor))
            fatia.setBorderColor(QColor(tema.CARTAO))
            fatia.setBorderWidth(3)  # ~2px visíveis de espaço entre as fatias, na cor do cartão
            fatia.setLabelVisible(False)
            texto = f"{linha.categoria} · {formatar_brl(int(linha.centavos))} · {linha.percentual:.0f}%"
            fatia.hovered.connect(lambda ativo, f=fatia, t=texto: self._ao_passar(f, ativo, t))
            legenda.append((cor, linha.categoria, int(linha.centavos), float(linha.percentual)))
        serie.setPieSize(ROSCA_TAMANHO)
        grafico.addSeries(serie)
        self._centro.definir(int(categorias["centavos"].sum()))
        self._legenda.definir(legenda)
        self._pilha.setCurrentWidget(self._conteudo)

    def _ao_passar(self, fatia, ativo: bool, texto: str) -> None:
        fatia.setExploded(ativo)
        _dica(ativo, texto, self._vista)

    def resizeEvent(self, evento) -> None:
        lado_a_lado = evento.size().width() >= self.LARGURA_LADO_A_LADO
        direcao = QBoxLayout.Direction.LeftToRight if lado_a_lado else QBoxLayout.Direction.TopToBottom
        if self._linha.direction() != direcao:
            self._linha.setDirection(direcao)
        super().resizeEvent(evento)


# --- Painel ---------------------------------------------------------------------------------


class PainelGraficos(QStackedWidget):
    """Mostra o gráfico escolhido no seletor do painel. Atualiza os dois de uma vez."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fluxo = PainelFluxo()
        self.rosca = PainelRosca()
        self.addWidget(self.fluxo)
        self.addWidget(self.rosca)

    def mostrar(self, opcao: str) -> None:
        self.setCurrentWidget(self.rosca if opcao == OPCAO_ROSCA else self.fluxo)

    def atualizar(
        self,
        ano: int,
        mes: int,
        mes_atual: bool,
        gastos_dia: pd.DataFrame,
        categorias: pd.DataFrame,
    ) -> None:
        self.fluxo.definir(gastos_dia, ano, mes, mes_atual)
        self.rosca.definir(categorias, ano, mes, mes_atual)
