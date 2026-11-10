"""Tema visual: tokens da direção visual (docs/design/direcao-visual.md) em um lugar só.

Tudo que é cor, tamanho de texto ou raio de canto mora aqui. Os componentes e a janela
só referenciam estas constantes (ou as propriedades do QSS global gerado por `gerar_qss`).
"""

from functools import cache
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# --- Superfícies e texto (paleta "gelo e ardósia", 10/11/2026) ---------------------
FUNDO = "#eef2f4"
CARTAO = "#ffffff"
CARTAO_ELEVADO = "#f4f7f8"  # hover, diálogos
BORDA = "#dce3e7"
TEXTO = "#1f3038"
TEXTO_SECUNDARIO = "#5d6b72"
TEXTO_APAGADO = "#667479"  # >= 4.5:1 no branco
TEXTO_DESABILITADO = "#a9b4ba"

# --- Destaques de interface (os nomes VERDE* ficaram: é o destaque único, verde-petróleo) --
VERDE = "#0d7480"
VERDE_HOVER = "#0b6570"
VERDE_PRESSIONADO = "#095862"
VERDE_SUAVE = "rgba(13, 116, 128, 0.10)"  # fundo de pílulas e seleção
VERDE_SUAVE_BORDA = "rgba(13, 116, 128, 0.35)"
DOURADO = "#a16207"  # atenção e previsão
DOURADO_SUAVE = "rgba(161, 98, 7, 0.10)"
DOURADO_SUAVE_BORDA = "rgba(161, 98, 7, 0.40)"
VERMELHO = "#b93a2b"  # status "ruim"
VERMELHO_HOVER = "#a33224"
VERMELHO_SUAVE = "rgba(185, 58, 43, 0.10)"
VERMELHO_SUAVE_BORDA = "rgba(185, 58, 43, 0.40)"
TEXTO_SOBRE_VERDE = "#ffffff"  # texto em cima do botão primário (e do de perigo)

# --- Cores de gráfico com várias séries (ordem fixa; "Outros" sempre por último) --
# validadas no validador do dataviz sobre o branco (faixa de luz, croma, daltonismo, contraste)
SERIES = ["#008ca3", "#d0501f", "#3a5fd9", "#c27c00", "#c2407a"]
SERIE_OUTROS = "#8a979d"

# --- Forma e ritmo (grade de 8px) ----------------------------------------------------
ESPACO = 8
RAIO_CARTAO = 8
PADDING_CARTAO = 20

# --- Tipografia (px) -----------------------------------------------------------------
FAMILIAS_PREFERIDAS = ("IBM Plex Sans", "Inter", "Noto Sans", "Segoe UI")
PX_TEXTO = 14
PX_TITULO_CARTAO = 11
PX_RODAPE = 12
PX_NUMERO = 26
PX_NUMERO_HEROI = 40  # o "Livre até o fim do mês", a resposta da tela inicial
PX_MARCA = 22
PX_TITULO_DIALOGO = 18
PX_VALOR = 22


# um arquivo por peso: do arquivo variável o Qt só enxerga o Regular, e negrito/seminegrito somem (OFL)
FONTES_EMBUTIDAS = [
    Path(__file__).with_name("fontes") / f"IBMPlexSans-{peso}.ttf" for peso in ("Regular", "Medium", "SemiBold", "Bold")
]


@cache
def _fonte_embutida_carregada() -> bool:
    return all(QFontDatabase.addApplicationFont(str(f)) != -1 for f in FONTES_EMBUTIDAS)  # uma vez por processo


def familia_da_fonte() -> str:
    """A fonte embutida (a mesma cara e as mesmas medidas em Linux e Windows); sem ela, a primeira preferida instalada."""
    if _fonte_embutida_carregada():
        return "IBM Plex Sans"
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
#Cabecalho {{ background: {CARTAO}; border-bottom: 1px solid {BORDA}; }}
#Marca {{ font-size: {PX_MARCA}px; font-weight: 700; }}

/* Cartão */
#Cartao {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: {RAIO_CARTAO}px; }}
#CartaoTitulo {{ font-size: {PX_TEXTO + 2}px; font-weight: 600; color: {TEXTO}; }}
#CartaoSubtitulo {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Número em destaque */
#NumeroRotulo {{ font-size: {PX_RODAPE + 1}px; font-weight: 500; color: {TEXTO_SECUNDARIO}; }}
#NumeroValor {{ font-size: {PX_NUMERO}px; font-weight: 600; color: {TEXTO}; }}
#NumeroValor[destaque="true"] {{ color: {VERDE}; }}
#NumeroValor[heroi="true"] {{ font-size: {PX_NUMERO_HEROI}px; }}
#NumeroApoio {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

/* Pílula */
#Pilula {{ font-size: {PX_RODAPE}px; font-weight: 600; padding: 0 10px; border-radius: 12px;
          background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {TEXTO}; }}
#Pilula[variante="neutra"] {{ background: transparent; border: 1px solid {BORDA}; color: {TEXTO_SECUNDARIO}; }}
#Pilula[variante="bom"] {{ background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {VERDE}; }}
#Pilula[variante="feito"] {{ background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {VERDE}; }}
#Pilula[variante="atencao"] {{ background: {DOURADO_SUAVE}; border: 1px solid {DOURADO_SUAVE_BORDA}; color: {DOURADO}; }}
#Pilula[variante="ruim"] {{ background: {VERMELHO_SUAVE}; border: 1px solid {VERMELHO_SUAVE_BORDA}; color: {VERMELHO}; }}

/* Botões */
QPushButton#BotaoPrimario {{ background: {VERDE}; color: {TEXTO_SOBRE_VERDE}; border: 1px solid {VERDE};
    border-radius: 6px; padding: 0 20px; min-height: 40px; font-size: {PX_TEXTO}px; font-weight: 700; }}
QPushButton#BotaoPrimario:hover {{ background: {VERDE_HOVER}; border-color: {VERDE_HOVER}; }}
QPushButton#BotaoPrimario:pressed {{ background: {VERDE_PRESSIONADO}; }}
QPushButton#BotaoSecundario {{ background: transparent; color: {TEXTO}; border: 1px solid {BORDA};
    border-radius: 6px; padding: 0 20px; min-height: 40px; font-size: {PX_TEXTO}px; font-weight: 600; }}
QPushButton#BotaoSecundario:hover {{ background: {CARTAO_ELEVADO}; border-color: {TEXTO_APAGADO}; }}
QPushButton#BotaoSecundario:disabled {{ color: {TEXTO_DESABILITADO}; border-color: {BORDA}; background: transparent; }}
QPushButton:focus {{ outline: none; }}

/* Segmentado */
#Segmentado {{ background: {FUNDO}; border: 1px solid {BORDA}; border-radius: 8px; }}
#Segmentado:focus {{ border: 1px solid {VERDE}; }}
#SegmentadoOpcao {{ background: transparent; color: {TEXTO_SECUNDARIO}; border: none; border-radius: 6px;
    padding: 0 14px; min-height: 26px; font-size: {PX_RODAPE + 1}px; font-weight: 600; }}
#SegmentadoOpcao:hover {{ color: {TEXTO}; }}
#SegmentadoOpcao:checked {{ background: {CARTAO}; color: {TEXTO}; border: 1px solid {BORDA}; }}
#SegmentadoOpcao:disabled {{ color: {TEXTO_DESABILITADO}; }}
#SegmentadoOpcao:checked:disabled {{ background: {VERDE_SUAVE}; color: {TEXTO_DESABILITADO}; }}

/* Item de lançamento */
#ItemLancamento {{ background: transparent; border: none; border-radius: 6px; }}
#ItemLancamento:hover {{ background: {CARTAO_ELEVADO}; }}
#ItemMarcador {{ border-radius: 6px; font-size: 13px; font-weight: 600; }}
#ItemMarcador[tipo="entrada"] {{ background: {VERDE_SUAVE}; color: {VERDE}; }}
#ItemMarcador[tipo="saida"] {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; color: {TEXTO_SECUNDARIO}; }}
#ItemMarcador[tipo="meta"] {{ background: {BORDA}; color: {DOURADO}; font-size: 14px; }}
#ItemMarcador[tipo="transferencia"] {{ background: {BORDA}; color: {TEXTO_SECUNDARIO}; font-size: 15px; }}
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

/* Diálogo de lançamento */
#Dialogo {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; }}
#Dialogo QScrollArea, #DialogoCorpo {{ background: transparent; }}
#DialogoTitulo {{ font-size: {PX_TITULO_DIALOGO}px; font-weight: 700; }}
#DialogoDetalhe {{ font-size: {PX_RODAPE}px; color: {TEXTO_SECUNDARIO}; }}
#CampoRotulo {{ font-size: {PX_RODAPE}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#CampoDica {{ font-size: {PX_RODAPE}px; color: {VERDE}; }}
#CampoInfo {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}
#CampoErro {{ font-size: {PX_RODAPE}px; font-weight: 600; color: {VERMELHO}; }}
#AvisoSalvo {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {VERDE}; }}
#ConfirmaTexto {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#Dialogo QLineEdit, #Dialogo QComboBox, #Dialogo QDateEdit, #FormPagamento QLineEdit, #FormPagamento QComboBox, #FormPagamento QDateEdit, #FormMeta QLineEdit, #FormMeta QComboBox, #FormMeta QDateEdit {{
    background: {FUNDO}; color: {TEXTO}; border: 1px solid {BORDA}; border-radius: 8px;
    padding: 0 12px; min-height: 38px; selection-background-color: {VERDE}; selection-color: {TEXTO_SOBRE_VERDE}; }}
#Dialogo QLineEdit:hover, #Dialogo QComboBox:hover, #Dialogo QDateEdit:hover, #FormPagamento QLineEdit:hover, #FormPagamento QComboBox:hover, #FormPagamento QDateEdit:hover, #FormMeta QLineEdit:hover, #FormMeta QComboBox:hover, #FormMeta QDateEdit:hover {{ border-color: {TEXTO_APAGADO}; }}
#Dialogo QLineEdit:focus, #Dialogo QComboBox:focus, #Dialogo QDateEdit:focus, #FormPagamento QLineEdit:focus, #FormPagamento QComboBox:focus, #FormPagamento QDateEdit:focus, #FormMeta QLineEdit:focus, #FormMeta QComboBox:focus, #FormMeta QDateEdit:focus {{ border-color: {VERDE}; }}
#Dialogo QLineEdit[erro="true"], #Dialogo QComboBox[erro="true"], #FormPagamento QLineEdit[erro="true"], #FormMeta QLineEdit[erro="true"] {{ border-color: {VERMELHO}; }}
#Dialogo QPlainTextEdit {{ background: {FUNDO}; color: {TEXTO}; border: 1px solid {BORDA}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {VERDE}; selection-color: {TEXTO_SOBRE_VERDE}; }}
#Dialogo QPlainTextEdit:focus {{ border-color: {VERDE}; }}
#Dialogo QLineEdit#CampoValor, #FormPagamento QLineEdit#CampoValor, #FormMeta QLineEdit#CampoValor {{ font-size: {PX_VALOR}px; font-weight: 700; min-height: 44px; }}
#Dialogo QComboBox::drop-down, #Dialogo QDateEdit::drop-down, #FormPagamento QComboBox::drop-down, #FormPagamento QDateEdit::drop-down, #FormMeta QComboBox::drop-down, #FormMeta QDateEdit::drop-down {{ width: 30px; border: none; background: transparent; }}
#Dialogo QComboBox::down-arrow, #Dialogo QDateEdit::down-arrow, #FormPagamento QComboBox::down-arrow, #FormPagamento QDateEdit::down-arrow, #FormMeta QComboBox::down-arrow, #FormMeta QDateEdit::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView, QListView#CompletarPopup {{
    background: {CARTAO_ELEVADO}; color: {TEXTO}; border: 1px solid {BORDA}; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: {VERDE_SUAVE}; selection-color: {VERDE}; }}
QComboBox QAbstractItemView::item, QListView#CompletarPopup::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected, QListView#CompletarPopup::item:selected {{
    background: {VERDE_SUAVE}; color: {VERDE}; }}
QCalendarWidget QWidget {{ background: {CARTAO_ELEVADO}; color: {TEXTO}; }}
QCalendarWidget QToolButton {{ background: transparent; color: {TEXTO}; border: none; border-radius: 6px;
    padding: 4px 8px; font-weight: 600; }}
QCalendarWidget QToolButton:hover {{ background: {BORDA}; }}
QCalendarWidget QMenu {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; }}
QCalendarWidget QSpinBox {{ background: {FUNDO}; border: 1px solid {BORDA}; border-radius: 6px; }}
QCalendarWidget QAbstractItemView:enabled {{ background: {CARTAO_ELEVADO}; color: {TEXTO};
    selection-background-color: {VERDE}; selection-color: {TEXTO_SOBRE_VERDE}; outline: none; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {TEXTO_APAGADO}; }}
QPushButton#BotaoPerigo {{ background: {VERMELHO}; color: {TEXTO_SOBRE_VERDE}; border: 1px solid {VERMELHO};
    border-radius: 6px; padding: 0 20px; min-height: 40px; font-size: {PX_TEXTO}px; font-weight: 700; }}
QPushButton#BotaoPerigo:hover {{ background: {VERMELHO_HOVER}; border-color: {VERMELHO_HOVER}; }}
QPushButton#BotaoSecundario[perigo="true"] {{ color: {VERMELHO}; border-color: {VERMELHO_SUAVE_BORDA}; }}
QPushButton#BotaoSecundario[perigo="true"]:hover {{ background: {VERMELHO_SUAVE}; border-color: {VERMELHO}; }}

/* Janela "Contas e cartões" */
#Dialogo[tela="contas"] {{ background: {FUNDO}; }}
#ContaNome {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO}; }}
#ContaNome[inativo="true"] {{ color: {TEXTO_APAGADO}; }}
#ContaDetalhe {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}
#ContaValor {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#Separador {{ background: {BORDA}; border: none; }}
#FormCartao {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; border-radius: 10px; }}
QPushButton#BotaoLink {{ background: transparent; border: none; padding: 6px 0; color: {TEXTO_SECUNDARIO};
    font-size: {PX_RODAPE + 1}px; font-weight: 600; }}
QPushButton#BotaoLink:hover {{ color: {TEXTO}; }}
QPushButton#BotaoSecundario[compacto="true"], QPushButton#BotaoPrimario[compacto="true"],
QPushButton#BotaoPerigo[compacto="true"] {{ min-height: 32px; padding: 0 14px; font-size: {PX_RODAPE + 1}px; border-radius: 8px; }}

/* Marcador (caixinha de marcar) */
QCheckBox#Marcador {{ spacing: 10px; font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
QCheckBox#Marcador:disabled {{ color: {TEXTO_DESABILITADO}; }}
QCheckBox#Marcador::indicator {{ width: 20px; height: 20px; border: 1px solid {TEXTO_APAGADO}; border-radius: 6px; background: {FUNDO}; }}
QCheckBox#Marcador::indicator:hover {{ border-color: {TEXTO_SECUNDARIO}; }}
QCheckBox#Marcador::indicator:checked {{ background: {VERDE}; border-color: {VERDE}; }}
QCheckBox#Marcador::indicator:disabled {{ border-color: {BORDA}; background: transparent; }}
QCheckBox#Marcador::indicator:checked:disabled {{ background: {BORDA}; border-color: {BORDA}; }}

/* Aba "Assinaturas/fixos" */
#GrupoNome {{ font-size: {PX_TITULO_CARTAO}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#GrupoSubtotal {{ font-size: {PX_RODAPE + 1}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#LinhaRecorrente {{ background: transparent; border: none; border-radius: 10px; }}
#LinhaRecorrente:hover {{ background: {CARTAO_ELEVADO}; }}
QPushButton#BotaoAcao {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0 12px;
    min-height: 30px; font-size: {PX_RODAPE + 1}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
QPushButton#BotaoAcao:hover {{ background: {CARTAO_ELEVADO}; border-color: {BORDA}; color: {TEXTO}; }}
QPushButton#BotaoAcao:disabled {{ color: {TEXTO_DESABILITADO}; background: transparent; border-color: transparent; }}
QPushButton#BotaoAcao[perigo="true"]:hover {{ background: {VERMELHO_SUAVE}; border-color: {VERMELHO_SUAVE_BORDA}; color: {VERMELHO}; }}
#RecorrenteNome {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO}; }}
#RecorrenteNome[apagada="true"] {{ color: {TEXTO_APAGADO}; }}
#RecorrenteValor {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#RecorrenteValor[apagada="true"] {{ color: {TEXTO_APAGADO}; }}

/* Aba "Cartões" */
#FormPagamento {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; border-radius: 10px; }}
#FaturaCard {{ background: transparent; border: 1px solid {BORDA}; border-radius: 10px; }}
#FaturaCabecalho {{ background: transparent; border: none; border-radius: 10px; }}
#FaturaCabecalho:hover {{ background: {CARTAO_ELEVADO}; }}
#FaturaSeta {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_APAGADO}; }}
#FaturaNome {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO}; }}
#FaturaNome[paga="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#FaturaTotal {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#FaturaTotal[paga="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#CompraData {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}
#CompraDescricao {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#CompraDescricao[paga="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#CompraValor {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#CompraValor[paga="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#CompraPago {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}
#ParcelaTag {{ font-size: {PX_RODAPE}px; font-weight: 700; padding: 1px 8px; border-radius: 9px;
    background: {VERDE_SUAVE}; border: 1px solid {VERDE_SUAVE_BORDA}; color: {VERDE}; }}

/* Aba "Metas" */
#FormMeta {{ background: {CARTAO_ELEVADO}; border: 1px solid {BORDA}; border-radius: 10px; }}
#MetaCard {{ background: transparent; border: 1px solid {BORDA}; border-radius: 10px; }}
#MetaNome {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#MetaNome[encerrada="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#MetaPct {{ font-size: {PX_TEXTO}px; font-weight: 700; color: {TEXTO}; min-width: 40px; }}
#MetaGuardado {{ font-size: {PX_TEXTO}px; font-weight: 600; color: {TEXTO}; }}
#MetaInfo {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}
#MetaDetalhe {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}
#ReservaTexto {{ font-size: {PX_TEXTO + 1}px; font-weight: 600; color: {TEXTO}; }}
#ReservaApoio {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}
#SimBloco {{ background: transparent; border: 1px solid {BORDA}; border-radius: 10px; }}
#SimTitulo {{ font-size: {PX_TITULO_CARTAO}px; font-weight: 600; color: {TEXTO_SECUNDARIO}; }}
#SimDestaque {{ font-size: {PX_VALOR}px; font-weight: 700; color: {TEXTO}; }}
#SimDetalhe {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}
#PreviaMeta {{ background: {FUNDO}; border: 1px solid {BORDA}; border-radius: 10px; }}
#PreviaDestaque {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#PreviaDetalhe {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_SECUNDARIO}; }}

/* Aba "Investimentos" */
#AtivoCard {{ background: transparent; border: 1px solid {BORDA}; border-radius: 10px; }}
#AtivoNome {{ font-size: {PX_TEXTO + 2}px; font-weight: 700; color: {TEXTO}; }}
#AtivoNome[encerrado="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#AtivoInstituicao {{ font-size: {PX_RODAPE + 1}px; color: {TEXTO_APAGADO}; }}
#AtivoValor {{ font-size: {PX_TEXTO + 3}px; font-weight: 700; color: {TEXTO}; }}
#AtivoValor[tom="positivo"] {{ color: {VERDE}; }}
#AtivoValor[tom="negativo"] {{ color: {VERMELHO}; }}
#AtivoTicker {{ font-size: {PX_TEXTO + 5}px; font-weight: 800; color: {TEXTO}; letter-spacing: 1px; }}
#AtivoTicker[encerrado="true"] {{ color: {TEXTO_SECUNDARIO}; }}
#SeloTaxas {{ font-size: {PX_RODAPE}px; color: {TEXTO_APAGADO}; }}

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


def _paleta() -> QPalette:
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
    """Fusion + paleta + fonte + QSS global."""
    app.setStyle("Fusion")
    app.setPalette(_paleta())
    familia = familia_da_fonte()
    app.setFont(fonte_base(familia))
    app.setStyleSheet(gerar_qss(familia))
