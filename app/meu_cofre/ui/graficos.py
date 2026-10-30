"""Gráficos da tela inicial e da aba Investimentos no visual da direção visual.

- `PainelGraficos`: troca entre o fluxo (área, gasto por dia), a rosca (gasto por categoria) e os próximos
  meses (barras empilhadas do que já está comprometido). Fluxo e rosca usam QtCharts; as barras são
  desenhadas à mão (QtCharts não arredonda os cantos das barras).
- `PainelPatrimonio`: o patrimônio no ano (linha sólida até hoje, tracejada até 31/12), também desenhado à mão:
  linha sólida e tracejada na mesma curva, rótulo do 31/12 e tooltip, sem depender do ciclo de vida das séries do QtCharts.
- Os gráficos recebem DataFrames prontos (de `relatorios`) e não conhecem o banco.
- Fundo transparente: quem dá a cor é o `Cartao` por trás.
"""

import calendar
import math
import os
from datetime import date

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
from PySide6.QtCore import QMargins, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QGraphicsSimpleTextItem,
    QGridLayout,
    QHBoxLayout,
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
OPCAO_PROXIMOS = "Próximos meses"

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

    def sizeHint(self) -> QSize:
        # O padrão do QGraphicsView é o tamanho da cena, que acompanha o gráfico: a cada vez que a janela
        # crescia o "tamanho ideal" crescia junto e nunca mais voltava, empurrando o resumo pra fora da tela.
        return QSize(480, 280)


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
    """Ícone discreto + título + dica, centralizados. `icone`: "linha", "rosca", "barras" ou "lista"."""

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
        elif self._icone == "barras":
            p.drawLine(10, 46, 48, 46)
            for x, altura in ((14, 14), (26, 26), (38, 20)):
                p.drawRoundedRect(QRectF(x, 46 - altura, 8, altura), 2, 2)
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


class _VistaRosca(_Vista):
    """Vista da rosca que desenha o total no furo, no primeiro plano da própria cena.

    Antes era um QWidget sobreposto; numa janela real (Wayland) ele ganhava fundo opaco e
    escondia a rosca inteira (offscreen não mostrava o problema).
    """

    def __init__(self, rotulo: str = "gasto no mês", parent: QWidget | None = None):
        super().__init__(parent)
        self._total = ""
        self._rotulo = rotulo

    def definir_total(self, total_centavos: int) -> None:
        self._total = formatar_brl(total_centavos)
        self.viewport().update()

    def drawForeground(self, p: QPainter, rect) -> None:
        super().drawForeground(p, rect)
        grafico = self.chart()
        if grafico is None or not self._total:
            return
        area = grafico.plotArea()
        diametro_furo = min(area.width(), area.height()) * ROSCA_TAMANHO * ROSCA_FURO
        centro = area.center()
        p.save()
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Maior fonte (até 28 px) em que o total cabe dentro do furo.
        fonte = _fonte(28)
        fonte.setWeight(QFont.Weight.Bold)
        while fonte.pixelSize() > 12 and QFontMetrics(fonte).horizontalAdvance(self._total) > diametro_furo * 0.84:
            fonte.setPixelSize(fonte.pixelSize() - 1)

        p.setFont(fonte)
        p.setPen(QColor(tema.TEXTO))
        altura_total = p.fontMetrics().height()
        p.drawText(
            QRectF(centro.x() - diametro_furo / 2, centro.y() - altura_total + 2, diametro_furo, altura_total),
            Qt.AlignmentFlag.AlignCenter,
            self._total,
        )
        p.setFont(_fonte(12))
        p.setPen(QColor(tema.TEXTO_APAGADO))
        p.drawText(
            QRectF(centro.x() - diametro_furo / 2, centro.y() + 2, diametro_furo, p.fontMetrics().height() + 2),
            Qt.AlignmentFlag.AlignCenter,
            self._rotulo,
        )
        p.restore()


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
            antigo = item.widget()
            if antigo:
                antigo.setParent(None)  # sai já da tela; só o deleteLater deixava o antigo sobreposto
                antigo.deleteLater()
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

    def __init__(
        self, rotulo_centro: str = "gasto no mês", largura_lado_a_lado: int = LARGURA_LADO_A_LADO,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._largura_lado_a_lado = largura_lado_a_lado
        self._vista = _VistaRosca(rotulo_centro)
        self._vista.setMinimumSize(220, 220)
        self._legenda = _Legenda()
        self._vazio = EstadoVazio("rosca")

        area_rosca = self._vista

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

    def definir(
        self, categorias: pd.DataFrame, ano: int, mes: int, mes_atual: bool, vazio: tuple[str, str] | None = None
    ) -> None:
        """`categorias`: saída de `relatorios.gastos_top_categorias` (categoria, centavos, percentual).

        `vazio` (título, dica) troca o texto do estado vazio (ex.: o da fatura do cartão).
        """
        if categorias.empty:
            sufixo = " ainda" if mes_atual else ""
            self._vazio.definir(*(vazio or (f"Nenhum gasto em {MESES[mes - 1]}{sufixo}", "Ctrl+N pra lançar")))
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
        grafico._refs = (serie,)  # a série precisa viver tanto quanto o gráfico (senão segfault)
        self._vista.setChart(grafico)
        self._vista.definir_total(int(categorias["centavos"].sum()))
        self._legenda.definir(legenda)
        self._pilha.setCurrentWidget(self._conteudo)

    def _ao_passar(self, fatia, ativo: bool, texto: str) -> None:
        fatia.setExploded(ativo)
        _dica(ativo, texto, self._vista)

    def resizeEvent(self, evento) -> None:
        lado_a_lado = evento.size().width() >= self._largura_lado_a_lado
        direcao = QBoxLayout.Direction.LeftToRight if lado_a_lado else QBoxLayout.Direction.TopToBottom
        if self._linha.direction() != direcao:
            self._linha.setDirection(direcao)
            h, v = Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignVCenter
            self._linha.setAlignment(self._vista, Qt.AlignmentFlag(0) if lado_a_lado else h)  # empilhada: centrada
            self._linha.setAlignment(self._legenda, v if lado_a_lado else h)
        super().resizeEvent(evento)


# --- Próximos meses: o que já está comprometido ------------------------------------------------


def _caminho_barra(retangulo: QRectF, raio_topo: float, raio_base: float) -> QPainterPath:
    """Retângulo com raio de canto diferente em cima e embaixo (0 = canto reto)."""
    x0, y0, x1, y1 = retangulo.left(), retangulo.top(), retangulo.right(), retangulo.bottom()
    t, b = raio_topo, raio_base
    caminho = QPainterPath()
    caminho.moveTo(x0, y0 + t)
    if t:
        caminho.arcTo(QRectF(x0, y0, 2 * t, 2 * t), 180, -90)
    caminho.lineTo(x1 - t, y0)
    if t:
        caminho.arcTo(QRectF(x1 - 2 * t, y0, 2 * t, 2 * t), 90, -90)
    caminho.lineTo(x1, y1 - b)
    if b:
        caminho.arcTo(QRectF(x1 - 2 * b, y1 - 2 * b, 2 * b, 2 * b), 0, -90)
    caminho.lineTo(x0 + b, y1)
    if b:
        caminho.arcTo(QRectF(x0, y1 - 2 * b, 2 * b, 2 * b), 270, -90)
    caminho.closeSubpath()
    return caminho


class _Barras(QWidget):
    """Barras empilhadas (cartão embaixo, contas em cima) por mês, desenhadas à mão.

    2px de espaço entre os segmentos, cantos de fora arredondados, total acima de cada barra, eixo em BRL
    curto e tooltip ao passar o mouse.
    """

    MARGEM = QMargins(66, 22, 8, 26)
    GAP = 2
    COR_CARTAO, COR_CONTAS = tema.SERIES[0], tema.SERIES[1]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self._meses: list[dict] = []
        self._ticks: list[float] = [0, 100]
        self._sobre: int | None = None

    def definir(self, comprometido: pd.DataFrame) -> None:
        """`comprometido`: saída de `relatorios.comprometido_proximos_meses`."""
        self._meses = []
        for linha in comprometido.itertuples():
            ano, mes = int(linha.mes[:4]), int(linha.mes[5:])
            self._meses.append(
                {
                    "curto": MESES_CURTOS[mes - 1].capitalize(),
                    "longo": f"{MESES_CURTOS[mes - 1].capitalize()}/{ano}",
                    "cartao": int(linha.cartao),
                    "contas": int(linha.contas),
                }
            )
        maior = max((m["cartao"] + m["contas"] for m in self._meses), default=0)
        self._ticks = ticks_bonitos(0, max(maior / 100, 1), 4)
        self._sobre = None
        self.update()

    def dica_do_mes(self, indice: int) -> str:
        m = self._meses[indice]
        total = m["cartao"] + m["contas"]
        return (
            f"{m['longo']} · {formatar_brl_inteiro(total)} "
            f"(cartão {formatar_brl_inteiro(m['cartao'])} · contas {formatar_brl_inteiro(m['contas'])})"
        )

    def _plano(self) -> QRectF:
        return QRectF(self.rect().adjusted(self.MARGEM.left(), self.MARGEM.top(), -self.MARGEM.right(), -self.MARGEM.bottom()))

    def _faixa(self, indice: int) -> QRectF:
        plano = self._plano()
        largura = plano.width() / len(self._meses)
        return QRectF(plano.left() + indice * largura, plano.top(), largura, plano.height())

    def _indice_em(self, x: float, y: float) -> int | None:
        plano = self._plano()
        if not self._meses or not plano.contains(x, y):
            return None
        return min(int((x - plano.left()) / (plano.width() / len(self._meses))), len(self._meses) - 1)

    def mouseMoveEvent(self, evento) -> None:
        ponto = evento.position()
        indice = self._indice_em(ponto.x(), ponto.y())
        if indice != self._sobre:
            self._sobre = indice
            self.update()
        if indice is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(evento.globalPosition().toPoint(), self.dica_do_mes(indice), self)

    def leaveEvent(self, evento) -> None:
        self._sobre = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(evento)

    def paintEvent(self, _evento) -> None:
        if not self._meses:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plano = self._plano()
        topo_escala = self._ticks[-1] * 100  # centavos
        fonte = _fonte(11)
        p.setFont(fonte)

        for tick in self._ticks:  # grade quase invisível + rótulos do eixo
            y = plano.bottom() - plano.height() * (tick * 100 / topo_escala)
            p.setPen(QPen(_cor(tema.TEXTO, 0.07), 1))
            p.drawLine(QPointF(plano.left(), y), QPointF(plano.right(), y))
            p.setPen(QColor(tema.TEXTO_APAGADO))
            p.drawText(QRectF(0, y - 8, self.MARGEM.left() - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, brl_curto(tick * 100))

        for i, m in enumerate(self._meses):
            faixa = self._faixa(i)
            if i == self._sobre:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_cor(tema.TEXTO, 0.05))
                p.drawRoundedRect(faixa.adjusted(4, 0, -4, 0), 8, 8)
            largura = min(52.0, faixa.width() * 0.5)
            x = faixa.center().x() - largura / 2
            y_base = plano.bottom()
            segmentos = [(m["cartao"], self.COR_CARTAO), (m["contas"], self.COR_CONTAS)]
            segmentos = [(v, c) for v, c in segmentos if v > 0]
            for n, (valor, cor) in enumerate(segmentos):
                altura = max(3.0, plano.height() * valor / topo_escala)
                ultimo = n == len(segmentos) - 1
                topo = y_base - altura
                # 2px entre segmentos: cada um cede 1px na borda que encosta no vizinho
                borda_de_cima = topo + (0 if ultimo else self.GAP / 2)
                borda_de_baixo = y_base - (self.GAP / 2 if n else 0)
                retangulo = QRectF(x, borda_de_cima, largura, borda_de_baixo - borda_de_cima)
                raio_topo = min(6.0, largura / 2, retangulo.height() / 2) if ultimo else 2.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(cor))
                p.drawPath(_caminho_barra(retangulo, raio_topo, 0 if n == 0 else 2.0))
                y_base = topo
            if segmentos:
                p.setPen(QColor(tema.TEXTO_SECUNDARIO))
                p.drawText(QRectF(faixa.left(), y_base - 20, faixa.width(), 16), Qt.AlignmentFlag.AlignCenter, brl_curto(m["cartao"] + m["contas"]))
            p.setPen(QColor(tema.TEXTO_APAGADO))
            p.drawText(QRectF(faixa.left(), plano.bottom() + 6, faixa.width(), 16), Qt.AlignmentFlag.AlignCenter, m["curto"])
        p.end()


class PainelProximos(QWidget):
    """Quanto já está comprometido em cada um dos próximos meses: cartão × contas, com legenda."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._barras = _Barras()
        self._vazio = EstadoVazio("barras", "Nada comprometido nos próximos meses", "Parcelas no cartão e assinaturas aparecem aqui")
        conteudo = QWidget()
        coluna = QVBoxLayout(conteudo)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(tema.ESPACO)
        legenda = QHBoxLayout()
        legenda.setSpacing(tema.ESPACO)
        for cor, nome in ((_Barras.COR_CARTAO, "Cartão"), (_Barras.COR_CONTAS, "Contas")):
            quadro = QFrame()
            quadro.setFixedSize(10, 10)
            quadro.setStyleSheet(f"background: {cor}; border-radius: 3px; border: none;")
            legenda.addWidget(quadro, 0, Qt.AlignmentFlag.AlignVCenter)
            legenda.addWidget(QLabel(nome, objectName="LegendaNome"))
            legenda.addSpacing(tema.ESPACO)
        nota = QLabel("Parcelas, assinaturas e fixos já garantidos, no mês de cada data")
        nota.setObjectName("CartaoSubtitulo")
        legenda.addStretch(1)
        legenda.addWidget(nota)
        coluna.addLayout(legenda)
        coluna.addWidget(self._barras, 1)
        self._pilha = QStackedLayout(self)
        self._pilha.setContentsMargins(0, 0, 0, 0)
        self._pilha.addWidget(conteudo)
        self._pilha.addWidget(self._vazio)
        self._conteudo = conteudo

    def definir(self, comprometido: pd.DataFrame) -> None:
        if comprometido.empty or int(comprometido["total"].sum()) == 0:
            self._pilha.setCurrentWidget(self._vazio)
            return
        self._barras.definir(comprometido)
        self._pilha.setCurrentWidget(self._conteudo)


# --- Patrimônio no ano ---------------------------------------------------------------------


class _LinhaPatrimonio(QWidget):
    """Linha do patrimônio no ano: sólida (verde, 2px, com área suave) até hoje e tracejada até 31/12.

    O eixo X é o ano inteiro (1º de janeiro a 31 de dezembro); o Y vai em BRL curto com ticks redondos. O ponto
    de hoje é verde, o de 31/12 é dourado e leva o valor. Ao passar o mouse perto de um ponto, tooltip.
    """

    MARGEM = QMargins(66, 28, 24, 26)
    RAIO_HOVER = 18

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self._pontos: list[dict] = []  # data, reais, projetado
        self._ticks: list[float] = [0, 100]
        self._ano = date.today().year
        self._sobre: int | None = None

    def definir(self, serie: pd.DataFrame, ano: int) -> None:
        """`serie`: saída de `investimentos.serie_patrimonio_no_ano` (data, centavos, projetado)."""
        self._ano = ano
        self._pontos = [
            {"data": linha.data.date(), "reais": int(linha.centavos) / 100, "centavos": int(linha.centavos), "projetado": bool(linha.projetado)}
            for linha in serie.itertuples()
        ]
        valores = [p["reais"] for p in self._pontos]
        minimo, maximo = min(valores, default=0), max(valores, default=0)
        folga = max((maximo - minimo) * 0.1, 1.0)
        self._ticks = ticks_bonitos(minimo - folga if minimo < 0 else max(minimo - folga, 0), maximo + folga, 4)
        self._sobre = None
        self.update()

    def dica_do_ponto(self, indice: int) -> str:
        p = self._pontos[indice]
        data = f"{p['data'].day} {MESES_CURTOS[p['data'].month - 1]}"
        return f"{data} · previsão {formatar_brl(p['centavos'])}" if p["projetado"] else f"{data} · {formatar_brl(p['centavos'])}"

    def _plano(self) -> QRectF:
        return QRectF(self.rect().adjusted(self.MARGEM.left(), self.MARGEM.top(), -self.MARGEM.right(), -self.MARGEM.bottom()))

    def _dias_do_ano(self) -> int:
        return (date(self._ano, 12, 31) - date(self._ano, 1, 1)).days

    def _x(self, d: date) -> float:
        plano = self._plano()
        return plano.left() + plano.width() * (d - date(self._ano, 1, 1)).days / self._dias_do_ano()

    def _y(self, reais: float) -> float:
        plano = self._plano()
        minimo, maximo = self._ticks[0], self._ticks[-1]
        return plano.bottom() - plano.height() * (reais - minimo) / (maximo - minimo)

    def _posicao(self, p: dict) -> QPointF:
        return QPointF(self._x(p["data"]), self._y(p["reais"]))

    def _indice_perto(self, x: float, y: float) -> int | None:
        melhor, distancia = None, self.RAIO_HOVER
        for i, p in enumerate(self._pontos):
            d = math.hypot(self._posicao(p).x() - x, self._posicao(p).y() - y)
            if d <= distancia:
                melhor, distancia = i, d
        return melhor

    def mouseMoveEvent(self, evento) -> None:
        ponto = evento.position()
        indice = self._indice_perto(ponto.x(), ponto.y())
        if indice != self._sobre:
            self._sobre = indice
            self.update()
        if indice is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(evento.globalPosition().toPoint(), self.dica_do_ponto(indice), self)

    def leaveEvent(self, evento) -> None:
        self._sobre = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(evento)

    def paintEvent(self, _evento) -> None:
        if not self._pontos:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plano = self._plano()
        p.setFont(_fonte(11))

        for tick in self._ticks:  # grade quase invisível + rótulos do eixo
            y = self._y(tick)
            p.setPen(QPen(_cor(tema.TEXTO, 0.07), 1))
            p.drawLine(QPointF(plano.left(), y), QPointF(plano.right(), y))
            p.setPen(QColor(tema.TEXTO_APAGADO))
            p.drawText(QRectF(0, y - 8, self.MARGEM.left() - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, brl_curto(tick * 100))
        p.setPen(QColor(tema.TEXTO_APAGADO))  # meses no eixo X, no meio de cada um
        for mes in range(1, 13):
            inicio, fim = date(self._ano, mes, 1), date(self._ano, mes, calendar.monthrange(self._ano, mes)[1])
            p.drawText(QRectF(self._x(inicio), plano.bottom() + 6, self._x(fim) - self._x(inicio), 16), Qt.AlignmentFlag.AlignCenter, MESES_CURTOS[mes - 1])

        realizados = [self._posicao(q) for q in self._pontos if not q["projetado"]]
        hoje = realizados[-1] if realizados else None
        previstos = [self._posicao(q) for q in self._pontos if q["projetado"]]

        if len(realizados) > 1:  # área suave sob o realizado
            base = self._y(self._ticks[0])
            area = QPainterPath(QPointF(realizados[0].x(), base))
            for ponto in realizados:
                area.lineTo(ponto)
            area.lineTo(QPointF(realizados[-1].x(), base))
            area.closeSubpath()
            degrade = QLinearGradient(0, plano.top(), 0, base)
            degrade.setColorAt(0, _cor(tema.VERDE, 0.30))
            degrade.setColorAt(1, _cor(tema.VERDE, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(degrade))
            p.drawPath(area)

        def tracar(pontos: list[QPointF], estilo: Qt.PenStyle) -> None:
            if len(pontos) < 2:
                return
            caneta = QPen(QColor(tema.VERDE), 2)
            caneta.setStyle(estilo)
            caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
            caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            caminho = QPainterPath(pontos[0])
            for ponto in pontos[1:]:
                caminho.lineTo(ponto)
            p.setPen(caneta)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(caminho)

        tracar(realizados, Qt.PenStyle.SolidLine)
        if hoje is not None:
            tracar([hoje, *previstos], Qt.PenStyle.DashLine)

        def bolinha(centro: QPointF, cor: str, raio: float = 5.0) -> None:
            p.setPen(QPen(QColor(tema.CARTAO), 2))
            p.setBrush(QColor(cor))
            p.drawEllipse(centro, raio, raio)

        if hoje is not None:
            bolinha(hoje, tema.VERDE)
        if previstos:
            fim = previstos[-1]
            bolinha(fim, tema.DOURADO)
            texto = f"{formatar_brl_inteiro(self._pontos[-1]['centavos'])} em 31/12"
            p.setFont(_fonte(12))
            p.setPen(QColor(tema.DOURADO))
            caixa = QRectF(fim.x() - 190, fim.y() - 26, 188, 18)
            p.drawText(caixa, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, texto)
        if self._sobre is not None:
            q = self._pontos[self._sobre]
            bolinha(self._posicao(q), tema.DOURADO if q["projetado"] else tema.VERDE, 6.5)
        p.end()


class PainelPatrimonio(QWidget):
    """O patrimônio no ano: legenda curta em cima e a linha embaixo; sem dados, o estado vazio."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._linha = _LinhaPatrimonio()
        self._vazio = EstadoVazio("linha", "Sem dados no ano ainda", "Lance receitas e gastos pra ver o patrimônio crescer")
        conteudo = QWidget()
        coluna = QVBoxLayout(conteudo)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(tema.ESPACO)
        legenda = QHBoxLayout()
        legenda.setSpacing(tema.ESPACO)
        for tracejado, texto, cor in ((False, "realizado", tema.VERDE), (True, "previsão até 31/12", tema.DOURADO)):
            amostra = QHBoxLayout()
            amostra.setSpacing(3)
            for _ in range(2 if tracejado else 1):
                traco = QFrame()
                traco.setFixedSize(6 if tracejado else 16, 2)
                traco.setStyleSheet(f"background: {cor}; border: none;")
                amostra.addWidget(traco, 0, Qt.AlignmentFlag.AlignVCenter)
            legenda.addLayout(amostra)
            legenda.addWidget(QLabel(texto, objectName="LegendaNome"))
            legenda.addSpacing(2 * tema.ESPACO)
        legenda.addStretch(1)
        coluna.addLayout(legenda)
        coluna.addWidget(self._linha, 1)
        self._pilha = QStackedLayout(self)
        self._pilha.setContentsMargins(0, 0, 0, 0)
        self._pilha.addWidget(conteudo)
        self._pilha.addWidget(self._vazio)
        self._conteudo = conteudo

    def definir(self, serie: pd.DataFrame, ano: int) -> None:
        if serie.empty or int(serie["centavos"].abs().sum()) == 0:
            self._pilha.setCurrentWidget(self._vazio)
            return
        self._linha.definir(serie, ano)
        self._pilha.setCurrentWidget(self._conteudo)


# --- Painel ---------------------------------------------------------------------------------


class PainelGraficos(QStackedWidget):
    """Mostra o gráfico escolhido no seletor do painel. Atualiza todos de uma vez."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fluxo = PainelFluxo()
        self.rosca = PainelRosca()
        self.proximos = PainelProximos()
        self.addWidget(self.fluxo)
        self.addWidget(self.rosca)
        self.addWidget(self.proximos)

    def mostrar(self, opcao: str) -> None:
        self.setCurrentWidget({OPCAO_ROSCA: self.rosca, OPCAO_PROXIMOS: self.proximos}.get(opcao, self.fluxo))

    def atualizar(
        self,
        ano: int,
        mes: int,
        mes_atual: bool,
        gastos_dia: pd.DataFrame,
        categorias: pd.DataFrame,
        comprometido: pd.DataFrame | None = None,
    ) -> None:
        self.fluxo.definir(gastos_dia, ano, mes, mes_atual)
        self.rosca.definir(categorias, ano, mes, mes_atual)
        if comprometido is not None:
            self.proximos.definir(comprometido)
