"""Janela principal: esqueleto da tela inicial (docs/design/telas.md).

F1 só monta a estrutura com dados falsos; F2 liga os dados reais e os gráficos.
"""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    Cartao,
    ItemLancamento,
    LinhaResponsiva,
    NumeroDestaque,
    Pilula,
    Segmentado,
)

LARGURA_DUAS_COLUNAS = 1100  # abaixo disso, RECENTES desce para baixo do RESUMO
OPCOES_PAINEL = ["Fluxo", "Para onde foi"]
OPCOES_RECENTES = ["Todos", "Entradas", "Saídas"]

# F2: trocar por dados reais
MES_ATUAL = "Outubro de 2026"
# (id, descrição, categoria, conta, data curta, valor em centavos)
RECENTES_FALSOS = [
    (10, "Salário outubro", "Salário", "Inter", "27 out", 520000),
    (9, "Supermercado Dia", "Mercado", "Cartão", "27 out", -23490),
    (8, "Assinatura de streaming com nome bem longo para testar o corte", "Lazer", "Cartão", "26 out", -3990),
    (7, "Uber", "Transporte", "Carteira", "26 out", -1850),
    (6, "Aluguel", "Moradia", "Inter", "25 out", -140000),
    (5, "Freela de design", "Renda extra", "Caixa", "24 out", 85000),
    (4, "Padaria", "Mercado", "Carteira", "24 out", -2120),
    (3, "Farmácia", "Saúde", "Cartão", "23 out", -6780),
    (2, "Conta de luz", "Moradia", "Inter", "22 out", -18934),
    (1, "Almoço com a equipe", "Restaurantes", "Cartão", "21 out", -4500),
]
RESUMO_FALSO = {
    "em_conta": ("R$ 8.432,10", "Inter + Caixa + Carteira"),
    "devo": ("R$ 2.315,40", "Fatura aberta + parcelas futuras"),
    "livre": ("R$ 1.870,25", "Depois de fatura e fixos do mês"),
    "livre_pilula": "12% vs setembro",
}
GRAFICOS_FALSOS = {
    "Fluxo": ("Quanto saiu por dia", "Gráfico de linha do mês entra aqui"),
    "Para onde foi": ("Gastos por categoria", "Rosca com as 5 maiores categorias entra aqui"),
}


class AreaGrafico(QFrame):
    """Espaço reservado ao gráfico, com texto que acompanha a opção escolhida (F2 troca por QChartView)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AreaGrafico")
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._titulo = QLabel()
        self._titulo.setObjectName("AreaGraficoTitulo")
        self._nota = QLabel()
        self._nota.setObjectName("AreaGraficoNota")
        for rotulo in (self._titulo, self._nota):
            rotulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rotulo.setWordWrap(True)
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(tema.PADDING_CARTAO, tema.PADDING_CARTAO, tema.PADDING_CARTAO, tema.PADDING_CARTAO)
        coluna.setSpacing(4)
        coluna.addStretch(1)
        coluna.addWidget(self._titulo)
        coluna.addWidget(self._nota)
        coluna.addStretch(1)

    def mostrar(self, opcao: str) -> None:
        titulo, nota = GRAFICOS_FALSOS[opcao]
        self._titulo.setText(titulo)
        self._nota.setText(nota)


class JanelaPrincipal(QMainWindow):
    """Tela inicial: painel (gráfico), resumo e recentes, com layout de 2 colunas ou 1 coluna."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        super().__init__()
        self.conn = conn  # F1 ainda não lê dados; F2 usa.
        self.setWindowTitle("meu-cofre")
        self.setMinimumSize(560, 400)
        self.resize(1400, 900)

        self._duas_colunas: bool | None = None
        self._montar()
        self._atalho_novo = QShortcut(QKeySequence("Ctrl+N"), self, activated=self.novo_lancamento)
        self._mostrar_recentes("Todos")
        self._painel_grafico.mostrar(OPCOES_PAINEL[0])
        self._aplicar_layout(self.width() >= LARGURA_DUAS_COLUNAS)

    # --- Montagem ----------------------------------------------------------------
    def _montar(self) -> None:
        raiz = QWidget()
        self.setCentralWidget(raiz)
        coluna = QVBoxLayout(raiz)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(0)
        coluna.addWidget(self._montar_cabecalho())

        self._conteudo = QWidget()
        self._conteudo.setObjectName("Conteudo")
        self._grade = QGridLayout(self._conteudo)
        m = 3 * tema.ESPACO
        self._grade.setContentsMargins(m, tema.ESPACO, m, m)
        self._grade.setSpacing(m)

        self._cartao_painel = self._montar_painel()
        self._cartao_resumo = self._montar_resumo()
        self._cartao_recentes = self._montar_recentes()

        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        rolagem.setWidget(self._conteudo)
        coluna.addWidget(rolagem, 1)

    def _montar_cabecalho(self) -> QWidget:
        faixa = QWidget()
        linha = QHBoxLayout(faixa)
        m = 3 * tema.ESPACO
        linha.setContentsMargins(m, 2 * tema.ESPACO, m, tema.ESPACO)
        linha.setSpacing(2 * tema.ESPACO)

        marca = QLabel("meu-cofre")
        marca.setObjectName("Marca")
        mes = QLabel(MES_ATUAL)  # F2: trocar por dados reais
        mes.setObjectName("Mes")
        linha.addWidget(marca, 0, Qt.AlignmentFlag.AlignBaseline)
        linha.addWidget(mes, 0, Qt.AlignmentFlag.AlignBaseline)
        linha.addStretch(1)

        dica = QLabel("Ctrl+N")
        dica.setObjectName("DicaAtalho")
        self.botao_novo = BotaoPrimario("+ Novo lançamento")
        self.botao_novo.setToolTip("Novo lançamento (Ctrl+N)")
        self.botao_novo.clicked.connect(self.novo_lancamento)
        linha.addWidget(dica, 0, Qt.AlignmentFlag.AlignVCenter)
        linha.addWidget(self.botao_novo)
        return faixa

    def _montar_painel(self) -> Cartao:
        self.seletor_painel = Segmentado(OPCOES_PAINEL)
        self.seletor_painel.escolhido.connect(self._trocar_painel)
        cartao = Cartao("Painel", "Outubro de 2026", acao=self.seletor_painel)
        cartao.setMinimumHeight(360)
        self._painel_grafico = AreaGrafico()
        cartao.corpo.addWidget(self._painel_grafico, 1)
        return cartao

    def _montar_resumo(self) -> Cartao:
        cartao = Cartao("Resumo")
        f = RESUMO_FALSO  # F2: trocar por dados reais
        em_conta = NumeroDestaque("Em conta", f["em_conta"][0], f["em_conta"][1])
        devo = NumeroDestaque("Devo", f["devo"][0], f["devo"][1])
        livre = NumeroDestaque("Livre até o fim do mês", f["livre"][0], f["livre"][1], destaque=True)
        livre.adicionar(Pilula(f["livre_pilula"], "bom"))
        linha = LinhaResponsiva(largura_minima_item=220, espaco=3 * tema.ESPACO)
        for numero in (em_conta, devo, livre):
            linha.adicionar(numero)
        cartao.corpo.addWidget(linha)
        return cartao

    def _montar_recentes(self) -> Cartao:
        self.seletor_recentes = Segmentado(OPCOES_RECENTES)
        self.seletor_recentes.escolhido.connect(self._mostrar_recentes)
        cartao = Cartao("Recentes", "Últimos 10 lançamentos", acao=self.seletor_recentes)
        cartao.setMinimumWidth(360)
        self._lista = QVBoxLayout()
        self._lista.setSpacing(2)
        cartao.corpo.addLayout(self._lista)
        cartao.corpo.addStretch(1)
        return cartao

    # --- Comportamento -----------------------------------------------------------
    def _trocar_painel(self, opcao: str) -> None:
        self._painel_grafico.mostrar(opcao)

    def _mostrar_recentes(self, filtro: str) -> None:
        """Refaz a lista de recentes conforme o filtro Todos / Entradas / Saídas."""
        while self._lista.count():
            item = self._lista.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for dados in RECENTES_FALSOS:  # F2: trocar por dados reais
            valor = dados[5]
            if (filtro == "Entradas" and valor < 0) or (filtro == "Saídas" and valor > 0):
                continue
            item = ItemLancamento(*dados)
            item.clicado.connect(self._abrir_lancamento)
            self._lista.addWidget(item)

    def _abrir_lancamento(self, id_lancamento: int) -> None:
        self.statusBar().showMessage(f"Detalhe do lançamento {id_lancamento}: chega na F3", 4000)

    def novo_lancamento(self) -> None:
        """Ctrl+N / botão. O diálogo de lançamento é a F3; por ora só avisa."""
        print("Novo lançamento (diálogo chega na F3)")
        self.statusBar().showMessage("Novo lançamento: o diálogo chega na F3", 4000)

    # --- Layout responsivo ---------------------------------------------------------
    def _aplicar_layout(self, duas_colunas: bool) -> None:
        """2 colunas (painel+resumo | recentes) ou 1 coluna (painel, resumo, recentes)."""
        if duas_colunas == self._duas_colunas:
            return
        self._duas_colunas = duas_colunas
        for w in (self._cartao_painel, self._cartao_resumo, self._cartao_recentes):
            self._grade.removeWidget(w)
        for i in range(3):
            self._grade.setRowStretch(i, 0)
            self._grade.setColumnStretch(i, 0)

        if duas_colunas:
            self._grade.addWidget(self._cartao_painel, 0, 0)
            self._grade.addWidget(self._cartao_resumo, 1, 0)
            self._grade.addWidget(self._cartao_recentes, 0, 1, 2, 1)
            self._grade.setColumnStretch(0, 2)
            self._grade.setColumnStretch(1, 1)
            self._grade.setRowStretch(0, 1)  # painel absorve a sobra; resumo fica no tamanho natural
        else:
            self._grade.addWidget(self._cartao_painel, 0, 0)
            self._grade.addWidget(self._cartao_resumo, 1, 0)
            self._grade.addWidget(self._cartao_recentes, 2, 0)
            self._grade.setColumnStretch(0, 1)
            self._grade.setRowStretch(0, 0)

    def resizeEvent(self, evento) -> None:
        self._aplicar_layout(evento.size().width() >= LARGURA_DUAS_COLUNAS)
        super().resizeEvent(evento)
