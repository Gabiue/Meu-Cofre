"""Janela principal (docs/design/telas.md): abas "Início", "Assinaturas/fixos", "Cartões", "Metas" e "Investimentos".

Início: painel (gráficos do mês escolhido, ou os próximos meses), resumo e recentes (sempre "agora"), em 2 colunas ou 1.
`atualizar()` gera as recorrentes do mês, recarrega tudo e pede (em segundo plano) as taxas do Banco Central; os
diálogos chamam depois de salvar ou excluir.

O cabeçalho (marca, abas, mês, Contas, Categorias e Novo lançamento) se reorganiza pela largura: numa linha só; em duas (a marca
e os botões em cima, as abas e o mês embaixo); ou em três (o mês na terceira). Quem decide é a largura mínima que
cada arranjo realmente pede, medida uma vez, então nada é espremido nem cortado.
"""

import sqlite3
from datetime import date

import pandas as pd
from PySide6.QtCore import Qt, Signal
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
from meu_cofre.ui.aba_investimentos import AbaInvestimentos
from meu_cofre.ui.aba_metas import AbaMetas
from meu_cofre.ui.aba_recorrentes import AbaRecorrentes
from meu_cofre.ui.atualizador import AtualizadorIndicadores
from meu_cofre.ui.dialogo_a_categorizar import DialogoACategorizar
from meu_cofre.ui.dialogo_categorias import DialogoCategorias
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
ABA_INICIO, ABA_RECORRENTES, ABA_CARTOES, ABA_METAS, ABA_INVESTIMENTOS = (
    "Início", "Assinaturas/fixos", "Cartões", "Metas", "Investimentos",
)
ABAS = {
    "inicio": ABA_INICIO, "recorrentes": ABA_RECORRENTES, "cartoes": ABA_CARTOES, "metas": ABA_METAS,
    "investimentos": ABA_INVESTIMENTOS,
}
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


class PilulaClicavel(Pilula):
    """Pílula que abre algo ao clicar (ex.: "3 a categorizar")."""

    clicada = Signal()

    def __init__(self, texto: str, variante: str = "neutra"):
        super().__init__(texto, variante)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, evento) -> None:
        self.clicada.emit()


class JanelaPrincipal(QMainWindow):
    """Tela inicial: painel (gráficos), resumo e recentes, com layout de 2 colunas ou 1 coluna."""

    def __init__(self, conn: sqlite3.Connection, hoje: date | None = None):
        """`hoje` fixa a data (testes e screenshots); sem ele, vale a data do computador."""
        super().__init__()
        self.conn = conn
        self._hoje_fixo = hoje
        self.setWindowTitle("meu-cofre")
        self.setMinimumSize(640, 400)  # cabeçalho: marca + Contas + Categorias + Novo lançamento
        self.resize(1400, 900)

        self._duas_colunas: bool | None = None
        self._arranjo_cabecalho: int | None = None  # 1, 2 ou 3 linhas
        self._largura_do_arranjo: dict[tuple[bool, int], int] = {}
        self.dialogo: DialogoLancamento | None = None  # diálogo aberto (se houver)
        self.dialogo_contas: DialogoContas | None = None
        self.dialogo_categorias: DialogoCategorias | None = None
        self.dialogo_a_categorizar: DialogoACategorizar | None = None
        hoje = self._hoje()
        self._mes = (hoje.year, hoje.month)  # mês mostrado no painel
        self._montar()
        self._atualizador = AtualizadorIndicadores(self.conn, self._hoje, parent=self)
        self._atualizador.concluido.connect(self._taxas_atualizadas)
        self._atalho_novo = QShortcut(QKeySequence("Ctrl+N"), self, activated=self.novo_lancamento)
        self._aplicar_layout(self.width() >= LARGURA_DUAS_COLUNAS)
        self._reorganizar_cabecalho()
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
        self.aba_metas = AbaMetas(self.conn, self._hoje)
        self.aba_metas.alterado.connect(self._lancamento_gravado)
        self.aba_investimentos = AbaInvestimentos(self.conn, self._hoje)
        self.aba_investimentos.alterado.connect(self._lancamento_gravado)
        self.aba_investimentos.ver_metas.connect(lambda: self.mostrar_aba("metas"))
        self._paginas = QStackedWidget()
        self._paginas.addWidget(rolagem)
        self._paginas.addWidget(self.aba_recorrentes)
        self._paginas.addWidget(self.aba_cartoes)
        self._paginas.addWidget(self.aba_metas)
        self._paginas.addWidget(self.aba_investimentos)
        coluna.addWidget(self._paginas, 1)

    def _montar_cabecalho(self) -> QWidget:
        self._faixa = QWidget()
        self._linhas_cabecalho = QVBoxLayout(self._faixa)
        m = 3 * tema.ESPACO
        self._linhas_cabecalho.setContentsMargins(m, 2 * tema.ESPACO, m, tema.ESPACO)
        self._linhas_cabecalho.setSpacing(tema.ESPACO)
        self._filas = [QHBoxLayout() for _ in range(3)]
        for fila in self._filas:
            fila.setSpacing(2 * tema.ESPACO)
            self._linhas_cabecalho.addLayout(fila)

        self.marca = QLabel("meu-cofre")
        self.marca.setObjectName("Marca")

        self.abas = Segmentado(list(ABAS.values()))
        self.abas.escolhido.connect(self._trocar_aba)

        self.botao_mes_anterior = self._botao_mes("‹", "Mês anterior")
        self.botao_mes_anterior.clicked.connect(lambda: self._mudar_mes(-1))
        self.botao_mes_seguinte = self._botao_mes("›", "Próximo mês")
        self.botao_mes_seguinte.clicked.connect(lambda: self._mudar_mes(+1))
        self.rotulo_mes = QLabel()
        self.rotulo_mes.setObjectName("MesRotulo")
        self.rotulo_mes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._navegacao_mes = (self.botao_mes_anterior, self.rotulo_mes, self.botao_mes_seguinte)

        self.botao_contas = BotaoSecundario("Contas")
        self.botao_contas.setToolTip("Contas e cartões")
        self.botao_contas.clicked.connect(self.abrir_contas)
        self.botao_categorias = BotaoSecundario("Categorias")
        self.botao_categorias.setToolTip("Criar, renomear e desativar categorias")
        self.botao_categorias.clicked.connect(self.abrir_categorias)

        self.botao_novo = BotaoPrimario("+ Novo lançamento")
        self.botao_novo.setToolTip("Novo lançamento (Ctrl+N)")
        self.botao_novo.clicked.connect(self.novo_lancamento)
        return self._faixa

    def _montar_arranjo(self, arranjo: int) -> None:
        """Põe os widgets do cabeçalho em 1, 2 ou 3 linhas."""
        for fila in self._filas:
            while fila.count():
                fila.takeAt(0)
        centro = Qt.AlignmentFlag.AlignVCenter
        a, b, c = self._filas

        def pôr(fila: QHBoxLayout, *widgets: QWidget) -> None:
            for w in widgets:
                fila.addWidget(w, 0, centro)

        if arranjo == 1:
            pôr(a, self.marca)
            a.addSpacing(tema.ESPACO)
            pôr(a, self.abas)
            a.addSpacing(tema.ESPACO)
            pôr(a, *self._navegacao_mes)
            a.addStretch(1)
            pôr(a, self.botao_contas, self.botao_categorias, self.botao_novo)
            return
        pôr(a, self.marca)
        a.addStretch(1)
        pôr(a, self.botao_contas, self.botao_categorias, self.botao_novo)
        pôr(b, self.abas)
        if arranjo == 2:
            b.addSpacing(tema.ESPACO)
            pôr(b, *self._navegacao_mes)
        else:
            pôr(c, *self._navegacao_mes)
            c.addStretch(1)
        b.addStretch(1)

    def _reorganizar_cabecalho(self) -> None:
        """Escolhe o arranjo mais compacto que não aperta nada: o 1º cuja largura mínima cabe na janela.

        A largura mínima de cada arranjo (com e sem o mês, que só aparece no Início) é medida uma vez, com o
        estilo já aplicado.
        """
        nav = self._nav_deve_aparecer()
        for w in self._navegacao_mes:
            w.setVisible(nav)
        escolhido = 3
        for arranjo in (1, 2, 3):
            if (nav, arranjo) not in self._largura_do_arranjo:
                for w in self._faixa.findChildren(QWidget):
                    w.ensurePolished()
                self._montar_arranjo(arranjo)
                self._arranjo_cabecalho = None
                self._linhas_cabecalho.invalidate()  # fora do laço de eventos o tamanho em cache ficaria velho
                self._largura_do_arranjo[(nav, arranjo)] = self._linhas_cabecalho.minimumSize().width()
            if self._largura_do_arranjo[(nav, arranjo)] <= self.width():
                escolhido = arranjo
                break
        if escolhido != self._arranjo_cabecalho:
            self._montar_arranjo(escolhido)
            self._arranjo_cabecalho = escolhido

    def _nav_deve_aparecer(self) -> bool:
        """O mês é do painel da tela inicial, e só vale pros gráficos do mês."""
        return self.aba_atual == "inicio" and self.seletor_painel.atual() != OPCAO_PROXIMOS

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
        self.pilula_a_categorizar = PilulaClicavel("", "atencao")
        self.pilula_a_categorizar.setToolTip("Lançamentos sem categoria: clique pra categorizar")
        self.pilula_a_categorizar.clicada.connect(self.abrir_a_categorizar)
        self.pilula_a_categorizar.setVisible(False)
        cartao.corpo.addWidget(self.pilula_a_categorizar, 0, Qt.AlignmentFlag.AlignLeft)
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
        self._atualizador.disparar()  # em segundo plano; sem MEU_COFRE_SEM_REDE e no máximo a cada 20 min
        self.aba_investimentos.definir_status(self._atualizador.status, atualizando=self._atualizador.em_andamento)
        self._carregar_resumo()
        self._carregar_recentes()
        self._carregar_painel()
        self.aba_recorrentes.recarregar()
        self.aba_cartoes.recarregar()
        self.aba_metas.recarregar()
        self.aba_investimentos.recarregar()

    def _taxas_atualizadas(self, status: dict) -> None:
        """A busca de taxas terminou (e os dados já estão gravados): refaz os números que dependem delas."""
        self.aba_investimentos.definir_status(status)
        self.aba_investimentos.recarregar()

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
                meta=not pd.isna(linha.meta_id),
                transferencia="" if pd.isna(linha.transferencia_destino)
                else f"{linha.conta} → {linha.transferencia_destino}",
            )
            if pd.isna(linha.meta_id):
                item.clicado.connect(self._abrir_lancamento)
            else:  # guardar/retirar de uma meta se mexe na aba Metas, não no lançamento solto
                item.clicado.connect(lambda _id: self.mostrar_aba("metas"))
            self._lista.addWidget(item)
        n_pendentes = len(lancamentos.pendentes(self.conn))
        self.pilula_a_categorizar.definir(f"{n_pendentes} a categorizar", "atencao")
        self.pilula_a_categorizar.setVisible(n_pendentes > 0)
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
        self._reorganizar_cabecalho()

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
        """Troca de aba por código ("inicio", "recorrentes", "cartoes", "metas" ou "investimentos"), como se clicasse nela."""
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

    def abrir_categorias(self) -> DialogoCategorias:
        """Botão "Categorias": abre a janela de categorias; cada mudança recarrega esta tela."""
        dialogo = DialogoCategorias(self.conn, parent=self)
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.alterado.connect(self._lancamento_gravado)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo_categorias", None))
        self.dialogo_categorias = dialogo
        dialogo.open()
        return dialogo

    def abrir_a_categorizar(self) -> DialogoACategorizar:
        """Pílula "N a categorizar": abre a fila de lançamentos sem categoria; cada mudança recarrega esta tela."""
        dialogo = DialogoACategorizar(self.conn, parent=self)
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._lancamento_gravado)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo_a_categorizar", None))
        self.dialogo_a_categorizar = dialogo
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
        self._reorganizar_cabecalho()
