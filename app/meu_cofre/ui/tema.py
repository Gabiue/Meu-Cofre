"""Tema visual: tokens da direção visual (docs/design/direcao-visual.md) em um lugar só.

Tudo que é cor, tamanho de texto ou raio de canto mora aqui. Os componentes e a janela
só referenciam estas constantes (ou as propriedades do QSS global gerado por `gerar_qss`).
"""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# --- Superfícies e texto ----------------------------------------------------------
FUNDO = "#0d1a13"
CARTAO = "#13241b"
CARTAO_ELEVADO = "#183022"
BORDA = "#1f3a2b"
TEXTO = "#e8f0ea"
TEXTO_SECUNDARIO = "#9fb5a8"
TEXTO_APAGADO = "#6f8a7b"

# --- Destaques de interface -------------------------------------------------------
VERDE = "#3fb37f"
VERDE_HOVER = "#4fc590"
VERDE_SUAVE = "rgba(63, 179, 127, 0.18)"  # fundo de pílulas e seleção
VERDE_SUAVE_BORDA = "rgba(63, 179, 127, 0.45)"
DOURADO = "#e0a030"
DOURADO_SUAVE = "rgba(224, 160, 48, 0.16)"
DOURADO_SUAVE_BORDA = "rgba(224, 160, 48, 0.45)"
VERMELHO = "#e36a5a"  # status "ruim"
VERMELHO_SUAVE = "rgba(227, 106, 90, 0.16)"
VERMELHO_SUAVE_BORDA = "rgba(227, 106, 90, 0.45)"
TEXTO_SOBRE_VERDE = "#0d1a13"  # texto em cima do botão primário

# --- Cores de gráfico com várias séries (ordem fixa; "Outros" sempre por último) --
SERIES = ["#26a06a", "#9085e9", "#c98500", "#3987e5", "#d95926"]
SERIE_OUTROS = "#6f8a7b"

# --- Forma e ritmo (grade de 8px) ----------------------------------------------------
ESPACO = 8
RAIO_CARTAO = 12
PADDING_CARTAO = 20

# --- Tipografia (px) -----------------------------------------------------------------
FAMILIAS_PREFERIDAS = ("Inter", "Adwaita Sans", "Noto Sans")
PX_TEXTO = 14
PX_TITULO_CARTAO = 11
PX_RODAPE = 12
PX_NUMERO = 30
PX_MARCA = 22


def familia_da_fonte() -> str:
    """Primeira família preferida que existe no sistema (senão, a padrão do Qt)."""
    instaladas = set(QFontDatabase.families())
    for familia in FAMILIAS_PREFERIDAS:
        if familia in instaladas:
            return familia
    return QApplication.font().family()


def fonte_base(familia: str) -> QFont:
    """Fonte do app: tamanho em px e algarismos tabulares quando o Qt suporta."""
    fonte = QFont(familia)
    fonte.setPixelSize(PX_TEXTO)
    if hasattr(QFont, "Tag"):  # Qt 6.7+
        fonte.setFeature(QFont.Tag("tnum"), 1)
    return fonte


def gerar_qss(familia: str) -> str:
    """QSS global. Componentes se diferenciam por objectName e pela propriedade `variante`."""
    return f"""
* {{ font-family: "{familia}"; color: {TEXTO}; }}
QMainWindow, QScrollArea, #Conteudo {{ background: {FUNDO}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {CARTAO_ELEVADO}; color: {TEXTO}; border: 1px solid {BORDA}; padding: 4px 8px; }}

/* Cabeçalho da janela */
#Marca {{ font-size: {PX_MARCA}px; font-weight: 700; }}
#DicaAtalho {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Cartão */
#Cartao {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: {RAIO_CARTAO}px; }}
#CartaoTitulo {{ font-size: {PX_TITULO_CARTAO}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#CartaoSubtitulo {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Número em destaque */
#NumeroRotulo {{ font-size: {PX_TITULO_CARTAO}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#NumeroValor {{ font-size: {PX_NUMERO}px; font-weight: 700; color: {TEXTO}; }}
#NumeroValor[destaque="true"] {{ color: {DOURADO}; }}
#NumeroApoio {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Pílula */
#Pilula {{ font-size: {PX_RODAPE}px; font-weight: 600; padding: 0 10px; border-radius: 12px;
          background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {TEXTO}; }}
#Pilula[variante="neutra"] {{ background: transparent; border: 1px solid {BORDA}; color: {TEXTO_SECUNDARIO}; }}
#Pilula[variante="bom"] {{ background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {VERDE}; }}
#Pilula[variante="atencao"] {{ background: {DOURADO_SUAVE}; border: 1px solid {DOURADO_SUAVE_BORDA}; color: {DOURADO}; }}
#Pilula[variante="ruim"] {{ background: {VERMELHO_SUAVE}; border: 1px solid {VERMELHO_SUAVE_BORDA}; color: {VERMELHO}; }}

/* Botões */
QPushButton#BotaoPrimario {{ background: {VERDE}; color: {TEXTO_SOBRE_VERDE}; border: 1px solid {VERDE};
    border-radius: 10px; padding: 0 20px; min-height: 40px; font-size: {PX_TEXTO}px; font-weight: 700; }}
QPushButton#BotaoPrimario:hover {{ background: {VERDE_HOVER}; border-color: {VERDE_HOVER}; }}
QPushButton#BotaoPrimario:pressed {{ background: #35a072; }}
QPushButton#BotaoSecundario {{ background: transparent; color: {TEXTO}; border: 1px solid {BORDA};
    border-radius: 10px; padding: 0 20px; min-height: 40px; font-size: {PX_TEXTO}px; font-weight: 600; }}
QPushButton#BotaoSecundario:hover {{ background: {CARTAO_ELEVADO}; border-color: {TEXTO_APAGADO}; }}
QPushButton:focus {{ outline: none; }}

/* Segmentado */
#Segmentado {{ background: {FUNDO}; border: 1px solid {BORDA}; border-radius: 17px; }}
#SegmentadoOpcao {{ background: transparent; color: {TEXTO_SECUNDARIO}; border: none; border-radius: 13px;
    padding: 0 14px; min-height: 26px; font-size: {PX_RODAPE + 1}px; font-weight: 600; }}
#SegmentadoOpcao:hover {{ color: {TEXTO}; }}
#SegmentadoOpcao:checked {{ background: {VERDE_SUAVE}; color: {VERDE}; }}

/* Item de lançamento */
#ItemLancamento {{ background: transparent; border: none; border-radius: 10px; }}
#ItemLancamento:hover {{ background: {CARTAO_ELEVADO}; }}
#ItemMarcador {{ border-radius: 16px; font-size: 11px; font-weight: 700; }}
#ItemMarcador[tipo="entrada"] {{ background: {VERDE_SUAVE}; color: {VERDE}; }}
#ItemMarcador[tipo="saida"] {{ background: {BORDA}; color: {TEXTO_SECUNDARIO}; }}
#ItemDescricao {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#ItemDetalhe {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}
#ItemValor {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#ItemData {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Navegação de mês */
QPushButton#BotaoMes {{ background: transparent; border: 1px solid {BORDA}; border-radius: 14px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 16px; font-weight: 700;
    color: {TEXTO_SECUNDARIO}; padding: 0; }}
QPushButton#BotaoMes:hover {{ background: {CARTAO_ELEVADO}; color: {TEXTO}; }}
QPushButton#BotaoMes:disabled {{ color: {BORDA}; border-color: {BORDA}; background: transparent; }}
#MesRotulo {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO}; min-width: 150px; }}

/* Estado vazio (gráficos e lista) */
#VazioTitulo {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#VazioDica {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Legenda da rosca */
#LegendaNome {{ font-size: {PX_TEXTO}px; color: {TEXTO}; }}
#LegendaValor {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#LegendaPct {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Barra de rolagem fina */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDA}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXTO_APAGADO}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDA}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEXTO_APAGADO}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {FUNDO}; color: {TEXTO_APAGADO}; font-size: {PX_RODAPE}px; }}
QStatusBar::item {{ border: none; }}
"""


def _paleta_escura() -> QPalette:
    p = QPalette()
    for papel, cor in (
        (QPalette.ColorRole.Window, FUNDO),
        (QPalette.ColorRole.WindowText, TEXTO),
        (QPalette.ColorRole.Base, CARTAO),
        (QPalette.ColorRole.AlternateBase, CARTAO_ELEVADO),
        (QPalette.ColorRole.Text, TEXTO),
        (QPalette.ColorRole.Button, CARTAO),
        (QPalette.ColorRole.ButtonText, TEXTO),
        (QPalette.ColorRole.ToolTipBase, CARTAO_ELEVADO),
        (QPalette.ColorRole.ToolTipText, TEXTO),
        (QPalette.ColorRole.PlaceholderText, TEXTO_APAGADO),
        (QPalette.ColorRole.Highlight, VERDE),
        (QPalette.ColorRole.HighlightedText, TEXTO_SOBRE_VERDE),
        (QPalette.ColorRole.Link, VERDE),
    ):
        p.setColor(papel, QColor(cor))
    return p


def aplicar_tema(app: QApplication) -> None:
    """Fusion + paleta escura + fonte + QSS global."""
    app.setStyle("Fusion")
    app.setPalette(_paleta_escura())
    familia = familia_da_fonte()
    app.setFont(fonte_base(familia))
    app.setStyleSheet(gerar_qss(familia))
