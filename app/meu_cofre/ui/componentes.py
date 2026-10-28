"""Componentes reutilizáveis da interface. A aparência vem do QSS global (ver `tema.py`)."""

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meu_cofre.lancamentos import formatar_brl
from meu_cofre.ui import tema


def _repolir(widget: QWidget) -> None:
    """Reaplica o QSS depois de mudar uma propriedade dinâmica."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class RotuloElidido(QLabel):
    """Rótulo de uma linha só: texto que não cabe vira "..." em vez de alargar o layout."""

    def __init__(self, texto: str = "", nome: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        if nome:
            self.setObjectName(nome)
        self._texto = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTexto(texto)

    def setTexto(self, texto: str) -> None:
        self._texto = texto
        self.setToolTip(texto)
        self.updateGeometry()
        self.update()

    def texto(self) -> str:
        return self._texto

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self._texto), self.fontMetrics().height() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 2)

    def paintEvent(self, _evento) -> None:
        pintor = QPainter(self)
        pintor.setPen(self.palette().color(self.foregroundRole()))
        pintor.setFont(self.font())
        elidido = self.fontMetrics().elidedText(self._texto, Qt.TextElideMode.ElideRight, self.width())
        pintor.drawText(self.rect(), int(self.alignment()) | int(Qt.AlignmentFlag.AlignVCenter), elidido)


class Cartao(QFrame):
    """Superfície com borda fina e cantos arredondados.

    Título (CAIXA ALTA, espaçado) e subtítulo são opcionais; `acao` é um widget no canto
    direito do cabeçalho (ex.: um `Segmentado`). O conteúdo entra em `self.corpo`.
    """

    def __init__(
        self,
        titulo: str = "",
        subtitulo: str = "",
        acao: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("Cartao")
        p = tema.PADDING_CARTAO
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(p, p, p, p)
        raiz.setSpacing(2 * tema.ESPACO)

        self.titulo = None
        self.subtitulo = None
        if titulo or subtitulo or acao:
            cab = QHBoxLayout()
            cab.setSpacing(tema.ESPACO)
            textos = QVBoxLayout()
            textos.setSpacing(2)
            if titulo:
                self.titulo = QLabel(titulo.upper())
                self.titulo.setObjectName("CartaoTitulo")
                self.titulo.setWordWrap(True)
                _espacar_letras(self.titulo, 1.2)
                textos.addWidget(self.titulo)
            if subtitulo:
                self.subtitulo = QLabel(subtitulo)
                self.subtitulo.setObjectName("CartaoSubtitulo")
                self.subtitulo.setWordWrap(True)
                textos.addWidget(self.subtitulo)
            cab.addLayout(textos, 1)
            if acao:
                cab.addWidget(acao, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            raiz.addLayout(cab)

        self.corpo = QVBoxLayout()
        self.corpo.setContentsMargins(0, 0, 0, 0)
        self.corpo.setSpacing(tema.ESPACO)
        raiz.addLayout(self.corpo, 1)


def _espacar_letras(rotulo: QLabel, px: float) -> None:
    fonte = rotulo.font()
    fonte.setLetterSpacing(fonte.SpacingType.AbsoluteSpacing, px)
    rotulo.setFont(fonte)


class Pilula(QLabel):
    """Texto curto em pílula. Variantes: neutra, bom, atencao, ruim.

    Cor nunca vai sozinha: as variantes de status ganham um ícone (▲, ! ou ▼) antes do texto.
    """

    ICONES = {"neutra": "", "bom": "▲ ", "atencao": "! ", "ruim": "▼ "}

    def __init__(self, texto: str, variante: str = "neutra", parent: QWidget | None = None):
        super().__init__(parent)
        if variante not in self.ICONES:
            raise ValueError(f"variante desconhecida: {variante!r}")
        self.setObjectName("Pilula")
        self.setFixedHeight(24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.definir(texto, variante)

    def definir(self, texto: str, variante: str = "neutra") -> None:
        """Troca texto e variante (reaplica o estilo)."""
        if variante not in self.ICONES:
            raise ValueError(f"variante desconhecida: {variante!r}")
        self.setProperty("variante", variante)
        self.setText(self.ICONES[variante] + texto)
        _repolir(self)


class NumeroDestaque(QWidget):
    """Rótulo pequeno + número grande em negrito + linha de apoio opcional.

    `destaque=True` usa o dourado no número (o "Livre"). `adicionar(widget)` põe algo
    embaixo, como uma `Pilula`.
    """

    def __init__(
        self,
        rotulo: str,
        valor: str,
        apoio: str = "",
        destaque: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

        self.rotulo = QLabel(rotulo.upper())
        self.rotulo.setObjectName("NumeroRotulo")
        self.rotulo.setWordWrap(True)
        _espacar_letras(self.rotulo, 1.0)

        self.valor = QLabel(valor)
        self.valor.setObjectName("NumeroValor")
        self.valor.setProperty("destaque", destaque)

        self.apoio = QLabel(apoio)
        self.apoio.setObjectName("NumeroApoio")
        self.apoio.setWordWrap(True)
        self.apoio.setVisible(bool(apoio))

        for w in (self.rotulo, self.valor, self.apoio):
            self._layout.addWidget(w)
        self._layout.addStretch(1)

    def definir(self, valor: str, apoio: str | None = None) -> None:
        """Atualiza o número; a linha de apoio só muda se `apoio` for dado."""
        self.valor.setText(valor)
        if apoio is not None:
            self.apoio.setText(apoio)
            self.apoio.setVisible(bool(apoio))

    def adicionar(self, widget: QWidget) -> None:
        """Acrescenta um widget (ex.: `Pilula`) logo abaixo do número, alinhado à esquerda."""
        self._layout.insertWidget(self._layout.count() - 1, widget, 0, Qt.AlignmentFlag.AlignLeft)


class LinhaResponsiva(QWidget):
    """Itens lado a lado; se não couberem (largura mínima de cada um), empilha em coluna."""

    def __init__(self, largura_minima_item: int = 200, espaco: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self._minimo = largura_minima_item
        self._itens: list[QWidget] = []
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(espaco)

    def adicionar(self, widget: QWidget) -> None:
        self._itens.append(widget)
        self._layout.addWidget(widget, 1)

    def resizeEvent(self, evento) -> None:
        lado_a_lado = evento.size().width() >= self._minimo * len(self._itens) + self._layout.spacing() * (len(self._itens) - 1)
        direcao = QBoxLayout.Direction.LeftToRight if lado_a_lado else QBoxLayout.Direction.TopToBottom
        if self._layout.direction() != direcao:
            self._layout.setDirection(direcao)
        super().resizeEvent(evento)


class BotaoPrimario(QPushButton):
    """Botão verde de ação principal."""

    def __init__(self, texto: str, parent: QWidget | None = None):
        super().__init__(texto, parent)
        self.setObjectName("BotaoPrimario")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class BotaoSecundario(QPushButton):
    """Botão só com contorno."""

    def __init__(self, texto: str, parent: QWidget | None = None):
        super().__init__(texto, parent)
        self.setObjectName("BotaoSecundario")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class Segmentado(QFrame):
    """Grupo de botões exclusivos (tipo abas). Emite `escolhido(opcao)` quando o usuário troca."""

    escolhido = Signal(str)

    def __init__(self, opcoes: list[str], atual: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        if not opcoes:
            raise ValueError("Segmentado precisa de ao menos uma opção")
        self.setObjectName("Segmentado")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._grupo = QButtonGroup(self)
        self._grupo.setExclusive(True)
        self._botoes: dict[str, QPushButton] = {}

        linha = QHBoxLayout(self)
        linha.setContentsMargins(3, 3, 3, 3)
        linha.setSpacing(2)
        for opcao in opcoes:
            b = QPushButton(opcao)
            b.setObjectName("SegmentadoOpcao")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._grupo.addButton(b)
            linha.addWidget(b)
            self._botoes[opcao] = b
        self._botoes[atual if atual in self._botoes else opcoes[0]].setChecked(True)
        self._grupo.buttonClicked.connect(lambda b: self.escolhido.emit(b.text()))

    def atual(self) -> str:
        return self._grupo.checkedButton().text()

    def definir(self, opcao: str) -> None:
        """Seleciona uma opção por código, sem emitir o sinal."""
        self._botoes[opcao].setChecked(True)

    def habilitar(self, opcao: str, habilitada: bool, dica: str = "") -> None:
        """Liga ou desliga uma opção; desligada, mostra `dica` ao passar o mouse."""
        self._botoes[opcao].setEnabled(habilitada)
        self._botoes[opcao].setToolTip("" if habilitada else dica)

    def clicar(self, opcao: str) -> None:
        """Simula o clique do usuário (emite o sinal). Útil em testes."""
        self._botoes[opcao].click()


class ItemLancamento(QFrame):
    """Linha da lista de recentes. Emite `clicado(id)` ao clicar.

    `valor_centavos` é positivo para entrada e negativo para saída. O número fica sempre na
    cor do texto: quem diferencia entrada de saída é o marcador e o sinal (+ / −).
    `parcela` (ex.: "3/6") aparece apagada logo depois da descrição, que é quem encurta.
    """

    clicado = Signal(int)

    def __init__(
        self,
        id_lancamento: int,
        descricao: str,
        categoria: str,
        conta: str,
        data: str,
        valor_centavos: int,
        parcela: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.id_lancamento = id_lancamento
        self.setObjectName("ItemLancamento")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(60)

        entrada = valor_centavos > 0
        marcador = QLabel("▲" if entrada else "▼")
        marcador.setObjectName("ItemMarcador")
        marcador.setProperty("tipo", "entrada" if entrada else "saida")
        marcador.setFixedSize(32, 32)
        marcador.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.descricao = RotuloElidido(descricao, "ItemDescricao")
        self.parcela = QLabel(f"({parcela})" if parcela else "")
        self.parcela.setObjectName("ItemDetalhe")
        self.parcela.setVisible(bool(parcela))
        titulo = QHBoxLayout()
        titulo.setSpacing(6)
        titulo.addWidget(self.descricao, 1)
        titulo.addWidget(self.parcela)
        self.detalhe = RotuloElidido(f"{categoria} · {conta}", "ItemDetalhe")
        textos = QVBoxLayout()
        textos.setSpacing(2)
        textos.addStretch(1)
        textos.addLayout(titulo)
        textos.addWidget(self.detalhe)
        textos.addStretch(1)

        sinal = "+" if entrada else "−"
        self.valor = QLabel(f"{sinal}{formatar_brl(abs(valor_centavos))}")
        self.valor.setObjectName("ItemValor")
        self.data = QLabel(data)
        self.data.setObjectName("ItemData")
        direita = QVBoxLayout()
        direita.setSpacing(2)
        direita.addStretch(1)
        direita.addWidget(self.valor, 0, Qt.AlignmentFlag.AlignRight)
        direita.addWidget(self.data, 0, Qt.AlignmentFlag.AlignRight)
        direita.addStretch(1)

        linha = QHBoxLayout(self)
        linha.setContentsMargins(8, 0, 12, 0)
        linha.setSpacing(12)
        linha.addWidget(marcador)
        linha.addLayout(textos, 1)
        linha.addLayout(direita)

    def mouseReleaseEvent(self, evento) -> None:
        if evento.button() == Qt.MouseButton.LeftButton and self.rect().contains(evento.position().toPoint()):
            self.clicado.emit(self.id_lancamento)
        super().mouseReleaseEvent(evento)


def _desenhar_seta(widget: QWidget) -> None:
    """Chevron "v" no canto direito de um campo de lista (o QSS esconde a seta nativa)."""
    pintor = QPainter(widget)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    caneta = QPen(QColor(tema.TEXTO_APAGADO), 1.6)
    caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
    caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pintor.setPen(caneta)
    x, y = widget.width() - 20, widget.height() / 2
    pintor.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])


class CampoLista(QComboBox):
    """QComboBox com a seta desenhada à mão (combina com o tema)."""

    def paintEvent(self, evento) -> None:
        super().paintEvent(evento)
        _desenhar_seta(self)


class CampoData(QDateEdit):
    """QDateEdit com calendário em popup e a mesma seta do `CampoLista`."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd/MM/yyyy")
        calendario = self.calendarWidget()  # fim de semana sem o vermelho padrão (vermelho aqui é status "ruim")
        formato = QTextCharFormat()
        formato.setForeground(QColor(tema.TEXTO_SECUNDARIO))
        calendario.setHeaderTextFormat(formato)
        for dia in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            calendario.setWeekdayTextFormat(dia, formato)

    def paintEvent(self, evento) -> None:
        super().paintEvent(evento)
        _desenhar_seta(self)
