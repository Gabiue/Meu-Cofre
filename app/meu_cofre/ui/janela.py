"""Janela principal (docs/design/telas.md): abas "Início", "Assinaturas/fixos" e "Cartões" com dados reais.

Início: painel (gráficos do mês escolhido, ou os próximos meses), resumo e recentes (sempre "agora"), em 2 colunas ou 1.
`atualizar()` gera as recorrentes do mês e recarrega tudo; os diálogos chamam depois de salvar ou excluir.
"""

import sqlite3
from datetime import date

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import lancamentos, recorrentes, relatorios
from meu_cofre.lancamentos import formatar_brl, formatar_brl_inteiro, nome_com_final
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    Cartao,
    ItemLancamento,
    GradeResponsiva,
    NumeroDestaque,
    Pilula,
    Segmentado,
)
from meu_cofre.ui.aba_cartoes import AbaCartoes
from meu_cofre.ui.aba_recorrentes import AbaRecorrentes
from meu_cofre.ui.dialogo_contas import DialogoContas
from meu_cofre.ui.dialogo_lancamento import DialogoLancamento
from meu_cofre.ui.graficos import (
    MESES_CURTOS,
    OPCAO_FLUXO,
    OPCAO_PROXIMOS,
    OPCAO_ROSCA,
    EstadoVazio,
    PainelGraficos,
    nome_do_mes,
)

LARGURA_DUAS_COLUNAS = 1100  # abaixo disso, RECENTES desce para baixo do RESUMO
ABA_INICIO, ABA_RECORRENTES, ABA_CARTOES = "Início", "Assinaturas/fixos", "Cartões"
ABAS = {"inicio": ABA_INICIO, "recorrentes": ABA_RECORRENTES, "cartoes": ABA_CARTOES}
OPCOES_PAINEL = [OPCAO_FLUXO, OPCAO_ROSCA, OPCAO_PROXIMOS]
MESES_PROXIMOS = 6
OPCOES_RECENTES = {"Todos": "todos", "Entradas": "entradas", "Saídas": "saidas"}
VAZIO_RECENTES = {
    "todos": "Nenhum lançamento ainda",
    "entradas": "Nenhuma entrada ainda",
    "saidas": "Nenhuma saída ainda",
}


def _data_curta(dia: date, hoje: date) -> str:
    """"hoje", "ontem" ou "27 out"."""
    if dia == hoje:
        return "hoje"
    if (hoje - dia).days == 1:
        return "ontem"
    return f"{dia.day} {MESES_CURTOS[dia.month - 1]}"


def _sem_quebra(texto: str) -> str:
    """Troca o espaço de "R$ 1.267" por um que não quebra a linha (o valor não se parte no meio)."""
    return texto.replace(" ", "\u00a0")


class JanelaPrincipal(QMainWindow):
    """Tela inicial: painel (gráficos), resumo e recentes, com layout de 2 colunas ou 1 coluna."""

    def __init__(self, conn: sqlite3.Connection, hoje: date | None = None):
        """`hoje` fixa a data (testes e screenshots); sem ele, vale a data do computador."""
        super().__init__()
        self.conn = conn
        self._hoje_fixo = hoje
        self.setWindowTitle("meu-cofre")
        self.setMinimumSize(560, 400)
        self.resize(1400, 900)

        self._duas_colunas: bool | None = None
        self.dialogo: DialogoLancamento | None = None  # diálogo aberto (se houver)
        self.dialogo_contas: DialogoContas | None = None
        hoje = self._hoje()
        self._mes = (hoje.year, hoje.month)  # mês mostrado no painel
        self._montar()
        self._atalho_novo = QShortcut(QKeySequence("Ctrl+N"), self, activated=self.novo_lancamento)
        self._aplicar_layout(self.width() >= LARGURA_DUAS_COLUNAS)
        self.atualizar()

    def _hoje(self) -> date:
        return self._hoje_fixo or date.today()

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

        self.aba_recorrentes = AbaRecorrentes(self.conn, self._hoje)
        self.aba_recorrentes.alterado.connect(self._lancamento_gravado)
        self.aba_cartoes = AbaCartoes(self.conn, self._hoje)
        self.aba_cartoes.alterado.connect(self._lancamento_gravado)
        self._paginas = QStackedWidget()
        self._paginas.addWidget(rolagem)
        self._paginas.addWidget(self.aba_recorrentes)
        self._paginas.addWidget(self.aba_cartoes)
        coluna.addWidget(self._paginas, 1)

    def _montar_cabecalho(self) -> QWidget:
        faixa = QWidget()
        linha = QHBoxLayout(faixa)
        m = 3 * tema.ESPACO
        linha.setContentsMargins(m, 2 * tema.ESPACO, m, tema.ESPACO)
        linha.setSpacing(2 * tema.ESPACO)

        marca = QLabel("meu-cofre")
        marca.setObjectName("Marca")
        linha.addWidget(marca, 0, Qt.AlignmentFlag.AlignVCenter)

        self.abas = Segmentado([ABA_INICIO, ABA_RECORRENTES, ABA_CARTOES])
        self.abas.escolhido.connect(self._trocar_aba)
        linha.addSpacing(tema.ESPACO)
        linha.addWidget(self.abas, 0, Qt.AlignmentFlag.AlignVCenter)

        self.botao_mes_anterior = self._botao_mes("‹", "Mês anterior")
        self.botao_mes_anterior.clicked.connect(lambda: self._mudar_mes(-1))
        self.botao_mes_seguinte = self._botao_mes("›", "Próximo mês")
        self.botao_mes_seguinte.clicked.connect(lambda: self._mudar_mes(+1))
        self.rotulo_mes = QLabel()
        self.rotulo_mes.setObjectName("MesRotulo")
        self.rotulo_mes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._navegacao_mes = (self.botao_mes_anterior, self.rotulo_mes, self.botao_mes_seguinte)
        linha.addSpacing(tema.ESPACO)
        for w in self._navegacao_mes:
            linha.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        linha.addStretch(1)

        self.botao_contas = BotaoSecundario("Contas")
        self.botao_contas.setToolTip("Contas e cartões")
        self.botao_contas.clicked.connect(self.abrir_contas)
        linha.addWidget(self.botao_contas)
        linha.addSpacing(tema.ESPACO)

        dica = QLabel("Ctrl+N")
        dica.setObjectName("DicaAtalho")
        self.botao_novo = BotaoPrimario("+ Novo lançamento")
        self.botao_novo.setToolTip("Novo lançamento (Ctrl+N)")
        self.botao_novo.clicked.connect(self.novo_lancamento)
        linha.addWidget(dica, 0, Qt.AlignmentFlag.AlignVCenter)
        linha.addWidget(self.botao_novo)
        return faixa

    @staticmethod
    def _botao_mes(simbolo: str, dica: str) -> QPushButton:
        botao = QPushButton(simbolo)
        botao.setObjectName("BotaoMes")
        botao.setToolTip(dica)
        botao.setCursor(Qt.CursorShape.PointingHandCursor)
        botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return botao

    def _montar_painel(self) -> Cartao:
        self.seletor_painel = Segmentado(OPCOES_PAINEL)
        self.seletor_painel.escolhido.connect(self._trocar_painel)
        cartao = Cartao("Painel", nome_do_mes(*self._mes), acao=self.seletor_painel)
        cartao.setMinimumHeight(380)
        self._painel_grafico = PainelGraficos()
        cartao.corpo.addWidget(self._painel_grafico, 1)
        return cartao

    def _montar_resumo(self) -> Cartao:
        cartao = Cartao("Resumo")
        self._em_conta = NumeroDestaque("Em conta", "", "Saldo de hoje nas contas e na carteira")
        self._devo = NumeroDestaque("Devo", "", "Cartão em aberto, com parcelas futuras")
        self._gastei = NumeroDestaque("Gastei no mês", "", "")
        self._livre = NumeroDestaque("Livre até o fim do mês", "", "Depois do cartão e do que ainda vai sair", destaque=True)
        self._pilula_livre = Pilula("", "neutra")
        self._livre.adicionar(self._pilula_livre)
        grade = GradeResponsiva(largura_minima_item=185, espaco=3 * tema.ESPACO)
        for numero in (self._em_conta, self._devo, self._gastei, self._livre):
            grade.adicionar(numero)
        cartao.corpo.addWidget(grade)
        return cartao

    def _montar_recentes(self) -> Cartao:
        self.seletor_recentes = Segmentado(list(OPCOES_RECENTES))
        self.seletor_recentes.escolhido.connect(lambda _opcao: self._carregar_recentes())
        cartao = Cartao("Recentes", "Últimos 10 lançamentos", acao=self.seletor_recentes)
        cartao.setMinimumWidth(360)
        self._lista = QVBoxLayout()
        self._lista.setSpacing(2)
        cartao.corpo.addLayout(self._lista)
        self._vazio_recentes = EstadoVazio("lista", dica="Ctrl+N pra lançar")
        self._vazio_recentes.setMinimumHeight(200)
        cartao.corpo.addWidget(self._vazio_recentes)
        cartao.corpo.addStretch(1)
        return cartao

    # --- Dados -----------------------------------------------------------------------
    def atualizar(self) -> None:
        """Gera o que as recorrentes devem até hoje e recarrega tudo do banco (os diálogos chamam depois de salvar)."""
        recorrentes.gerar_pendentes(self.conn, self._hoje())
        self._carregar_resumo()
        self._carregar_recentes()
        self._carregar_painel()
        self.aba_recorrentes.recarregar()
        self.aba_cartoes.recarregar()

    def _carregar_resumo(self) -> None:
        hoje = self._hoje()
        em_conta = relatorios.em_conta(self.conn, hoje)
        livre = relatorios.livre_ate_fim_do_mes(self.conn, hoje)
        self._em_conta.definir(formatar_brl(em_conta))
        self._devo.definir(formatar_brl(relatorios.devo(self.conn)))
        self._livre.definir(formatar_brl(livre))
        total, da_conta, no_cartao = relatorios.gastei_no_mes(self.conn, hoje.year, hoje.month, ate=hoje)
        self._gastei.definir(
            formatar_brl(total),
            f"{_sem_quebra(formatar_brl_inteiro(da_conta))} já saiu da conta · {_sem_quebra(formatar_brl_inteiro(no_cartao))} no cartão",
        )
        if livre < 0:
            self._pilula_livre.definir(f"{formatar_brl_inteiro(-livre)} acima do disponível", "atencao")
        else:
            por_dia = relatorios.por_dia_ate_fim_do_mes(self.conn, hoje)
            self._pilula_livre.definir(f"≈ {formatar_brl_inteiro(por_dia)} por dia", "neutra")

    def _carregar_recentes(self) -> None:
        """Refaz a lista conforme o filtro Todos / Entradas / Saídas."""
        while self._lista.count():
            item = self._lista.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        filtro = OPCOES_RECENTES[self.seletor_recentes.atual()]
        hoje = self._hoje()
        df = lancamentos.recentes(self.conn, hoje, 10, filtro)
        for linha in df.itertuples():
            parcela = ""
            if not pd.isna(linha.parcela_total):
                parcela = f"{int(linha.parcela_atual)}/{int(linha.parcela_total)}"
            item = ItemLancamento(
                int(linha.id),
                linha.descricao,
                "A categorizar" if pd.isna(linha.categoria) else linha.categoria,  # NaN do pandas é truthy
                nome_com_final(linha.conta, linha.conta_final),
                _data_curta(linha.data.date(), hoje),
                int(linha.valor_centavos),
                parcela,
                recorrente=not pd.isna(linha.recorrente_id),
            )
            item.clicado.connect(self._abrir_lancamento)
            self._lista.addWidget(item)
        self._vazio_recentes.definir(VAZIO_RECENTES[filtro], "Ctrl+N pra lançar")
        self._vazio_recentes.setVisible(df.empty)

    def _carregar_painel(self) -> None:
        ano, mes = self._mes
        hoje = self._hoje()
        mes_atual = (ano, mes) == (hoje.year, hoje.month)
        gastos_dia = relatorios.gastos_por_dia(self.conn, ano, mes, hoje if mes_atual else None)
        categorias = relatorios.gastos_top_categorias(self.conn, ano, mes)
        comprometido = relatorios.comprometido_proximos_meses(self.conn, hoje, MESES_PROXIMOS)
        self._painel_grafico.atualizar(ano, mes, mes_atual, gastos_dia, categorias, comprometido)
        self.rotulo_mes.setText(nome_do_mes(ano, mes))
        self._atualizar_subtitulo_painel()
        self.botao_mes_seguinte.setEnabled(not mes_atual)

    def _atualizar_subtitulo_painel(self) -> None:
        if self.seletor_painel.atual() == OPCAO_PROXIMOS:
            self._cartao_painel.subtitulo.setText(f"Próximos {MESES_PROXIMOS} meses · o que já está garantido")
        else:
            self._cartao_painel.subtitulo.setText(nome_do_mes(*self._mes))

    def _atualizar_navegacao_mes(self) -> None:
        """Os botões de mês são do painel da tela inicial, e só valem pros gráficos do mês."""
        visivel = self.aba_atual == "inicio" and self.seletor_painel.atual() != OPCAO_PROXIMOS
        for w in self._navegacao_mes:
            w.setVisible(visivel)

    # --- Comportamento -----------------------------------------------------------
    def _mudar_mes(self, passo: int) -> None:
        """Navega no mês do painel; nunca passa do mês atual."""
        hoje = self._hoje()
        ano, mes = self._mes
        indice = ano * 12 + (mes - 1) + passo
        novo = (indice // 12, indice % 12 + 1)
        if novo > (hoje.year, hoje.month):
            return
        self._mes = novo
        self._carregar_painel()

    def _trocar_aba(self, rotulo: str) -> None:
        self._paginas.setCurrentIndex(list(ABAS.values()).index(rotulo))
        self._atualizar_navegacao_mes()

    def mostrar_aba(self, nome: str) -> None:
        """Troca de aba por código ("inicio", "recorrentes" ou "cartoes"), como se clicasse nela."""
        rotulo = ABAS[nome]
        self.abas.definir(rotulo)
        self._trocar_aba(rotulo)

    @property
    def aba_atual(self) -> str:
        return list(ABAS)[self._paginas.currentIndex()]

    def _trocar_painel(self, opcao: str) -> None:
        self._painel_grafico.mostrar(opcao)
        self._atualizar_subtitulo_painel()
        self._atualizar_navegacao_mes()

    def mostrar_painel(self, opcao: str) -> None:
        """Escolhe o gráfico do painel por código (usado no modo screenshot)."""
        self.seletor_painel.definir(opcao)
        self._trocar_painel(opcao)

    def _abrir_lancamento(self, id_lancamento: int) -> DialogoLancamento | None:
        """Clique num item dos Recentes: abre o lançamento pra ver, editar ou excluir."""
        return self._abrir_dialogo(id_lancamento)

    def novo_lancamento(self) -> DialogoLancamento | None:
        """Ctrl+N / botão: abre o diálogo de novo lançamento."""
        return self._abrir_dialogo(None)

    def _abrir_dialogo(self, id_lancamento: int | None) -> DialogoLancamento | None:
        try:
            dialogo = DialogoLancamento(self.conn, id_lancamento, parent=self, hoje=self._hoje())
        except ValueError:  # o lançamento sumiu (ex.: excluído em outro lugar)
            self.atualizar()
            return None
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._lancamento_gravado)
        dialogo.gerenciar_recorrente.connect(lambda _id: self.mostrar_aba("recorrentes"))
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo", None))
        self.dialogo = dialogo
        dialogo.open()
        return dialogo

    def abrir_contas(self) -> DialogoContas:
        """Botão "Contas": abre a janela de contas e cartões; cada mudança recarrega esta tela."""
        dialogo = DialogoContas(self.conn, parent=self, hoje=self._hoje())
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.alterado.connect(self._lancamento_gravado)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo_contas", None))
        self.dialogo_contas = dialogo
        dialogo.open()
        return dialogo

    def _lancamento_gravado(self, mensagem: str) -> None:
        """Depois de salvar, excluir ou mudar contas: recarrega a tela e avisa na barra de status."""
        self.atualizar()
        self.statusBar().showMessage(mensagem, 6000)

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
