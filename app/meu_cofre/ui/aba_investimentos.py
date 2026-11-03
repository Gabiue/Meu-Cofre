"""Aba "Investimentos" (docs/design/telas.md, F8 e F9): renda fixa, Tesouro Direto, ações e FIIs, patrimônio e previsão.

Topo: Patrimônio hoje, Investido (valor atual), Rendimento no ano (com "x% do CDI", só renda fixa e Tesouro) e a
Previsão 31/12 (dourado), com o selo das taxas do Banco Central e das cotações. Depois o gráfico do patrimônio no ano,
e a lista agrupada por tipo (as caixinhas das metas na renda fixa) com as ações Aportar, Resgatar, Atualizar saldo e
Editar; em ações e FIIs, Comprar, Vender, Provento e Editar. Os formulários abrem na própria linha. No fim, o cartão
Proventos (recebidos no ano e a estimativa dos próximos 12 meses). "Importar da B3" abre a janela de importação.
`alterado(mensagem)` avisa a janela principal; `ver_metas` pede a aba Metas (o dinheiro das caixinhas se move lá).
"""

import sqlite3
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import cadastros, cotacoes, indicadores, investimentos, renda_variavel
from meu_cofre.investimentos import NOMES_TIPO, Posicao
from meu_cofre.lancamentos import formatar_brl, formatar_brl_inteiro
from meu_cofre.ui import tema
from meu_cofre.ui.aba_metas import FormMovimento, _acao, _compacto, _rotulo
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    Cartao,
    GradeResponsiva,
    NumeroDestaque,
    Pilula,
    RotuloElidido,
    _espacar_letras,
)
from meu_cofre.ui.cartao_acao import CartaoAcao, HistoricoAtivo
from meu_cofre.ui.dialogo_ativo import DialogoAtivo
from meu_cofre.ui.dialogo_importar_b3 import DialogoImportarB3
from meu_cofre.ui.dialogo_meta import reais
from meu_cofre.ui.graficos import EstadoVazio, PainelPatrimonio, PainelProventos

LARGURA_MINIMA_NUMERO = 230  # cabe "R$ 123.456,78" em 30 px
LARGURA_CARTAO_ESTREITO = 520  # abaixo disso, Editar e Encerrar descem pra uma segunda fila
ORDEM_DOS_GRUPOS = ("renda_fixa", "tesouro", "acao", "fii")


# --- Textos ----------------------------------------------------------------------------------------


def _pct(x: float, sinal: bool = False) -> str:
    """12.3456 -> "12,35%"; com `sinal`, "+12,35%"."""
    return f"{x:+.2f}%".replace(".", ",") if sinal else f"{x:.2f}%".replace(".", ",")


def _brl_com_sinal(centavos: int) -> str:
    return f"+{formatar_brl(centavos)}" if centavos > 0 else formatar_brl(centavos).replace("-", "−")


def _curto(dia: date) -> str:
    return f"{dia.day:02d}/{dia.month:02d}"


def texto_da_previsao(prev: dict) -> str:
    """"R$ 8.200 hoje + R$ 4.100 de sobra + R$ 310 de rendimento" (reais cheios; negativos com −; o valor não quebra)."""

    def valor(centavos: int) -> str:
        return formatar_brl_inteiro(abs(centavos)).replace(" ", "\u00a0")

    def sinal(centavos: int) -> str:
        return "−" if centavos < 0 else "+"

    parcelas = prev.get("parcelas_futuras", 0)
    trecho_parcelas = f" + {valor(parcelas)} de parcelas futuras (já na sobra)" if parcelas > 0 else ""
    proventos = prev.get("proventos_projetados", 0)
    trecho_proventos = f" + {valor(proventos)} de proventos" if proventos > 0 else ""
    return (
        f"{valor(prev['patrimonio_hoje'])} hoje{trecho_parcelas} {sinal(prev['sobra_projetada'])} "
        f"{valor(prev['sobra_projetada'])} de sobra "
        f"{sinal(prev['rendimento_projetado'])} {valor(prev['rendimento_projetado'])} de rendimento{trecho_proventos}"
    )


def explicacao_da_previsao(prev: dict) -> str:
    meses = f"{prev['meses_restantes']:.1f}".replace(".", ",")
    texto = (
        f"Patrimônio de hoje + sobra média ({reais(prev['sobra_mensal'])}/mês × {meses} meses) + rendimento dos "
        "investimentos e caixinhas até 31/12, com as taxas de hoje e sem novos aportes."
    )
    if prev.get("proventos_projetados"):
        texto += " Ações e FIIs ficam com a cotação de hoje; só os proventos estimados pelo histórico são somados."
    return texto + (" A sobra está estimada com poucos dados." if prev["sobra_estimativa"] else "")


def texto_do_selo(conn: sqlite3.Connection, status: dict | None, atualizando: bool) -> str:
    """O que a aba diz sobre a origem das taxas."""
    ultima = indicadores.ultima_data(conn, "cdi")
    if atualizando:
        base = "atualizando as taxas do Banco Central…"
    elif status and status.get("sem_rede"):
        base = f"sem internet: taxas de {_curto(ultima)}" if ultima else "sem internet e sem taxas guardadas: o rendimento não é estimado"
    elif ultima:
        base = f"taxas do Banco Central de {_curto(ultima)}"
    else:
        base = "ainda sem taxas do Banco Central: o rendimento não é estimado"
    nota = " · histórico de preços e proventos: opcional, com token gratuito da brapi" if cotacoes.sem_fonte_de_historico(conn) else ""
    return f"{base}{texto_das_cotacoes(conn, status)}{nota} · valores brutos, antes do IR"


def texto_das_cotacoes(conn: sqlite3.Connection, status: dict | None) -> str:
    """" · cotações de 29/10" (vazio sem ações nem FIIs). Avisa quando a busca das cotações falhou."""
    tickers = cotacoes.tickers_ativos(conn)
    if not tickers:
        return ""
    datas = [d for d in (cotacoes.ultima(conn, t, date.max) for t in tickers) if d]
    ultima = max(d for _p, d in datas) if datas else None
    falhou = bool(status and status.get("cotacoes", {}).get("sem_rede"))
    if ultima is None:
        return " · sem cotações de ações e FIIs ainda" + (" (sem internet)" if falhou else "")
    return f" · cotações de {_curto(ultima)}" + (" (sem internet)" if falhou else "")


def _ativo_de(p: Posicao | dict):
    """O `Ativo` de uma posição, seja de renda fixa (`Posicao`) ou de ação/FII (dict de `renda_variavel.posicao`)."""
    return p["ativo"] if isinstance(p, dict) else p.ativo


def _valor_do_grupo(p: Posicao | dict) -> int:
    """O valor que entra no subtotal do grupo (ação sem cotação ainda não tem valor)."""
    return (p["valor_atual"] or 0) if isinstance(p, dict) else p.valor_atual


# --- Um investimento -------------------------------------------------------------------------------------


class FormAtivo(FormMovimento):
    """Aportar, resgatar ou atualizar o saldo: valor, conta e data (o saldo não pede conta)."""

    def preparar(self, *args, **kwargs) -> None:
        self.conta.setVisible(True)
        self.rotulo_conta.setVisible(True)
        self.layout().setColumnStretch(1, 1)
        super().preparar(*args, **kwargs)

    def preparar_saldo(self, titulo: str, valor_centavos: int, hoje: date) -> None:
        super().preparar(titulo, "", "Atualizar", valor_centavos, hoje, [(0, "")])
        self.conta.setVisible(False)
        self.rotulo_conta.setVisible(False)
        self.layout().setColumnStretch(1, 0)


class CartaoAtivo(QFrame):
    """Um investimento: nome, instituição, taxa, aplicado, valor atual, rendimento e as ações."""

    aportar = Signal(int)
    resgatar = Signal(int)
    atualizar_saldo = Signal(int)
    editar = Signal(int)
    encerrar = Signal(int)
    reativar = Signal(int)
    ver_metas = Signal()
    historico = Signal(int)
    movimento = Signal(int, str, int, int, date)  # id, "aportar" | "resgatar" | "saldo", centavos, conta, data

    def __init__(self, p: Posicao, parent: QWidget | None = None):
        super().__init__(parent)
        self.posicao = p
        a = p.ativo
        self.id_ativo = a.id
        self._tipo_form: str | None = None
        self._estreito: bool | None = None
        self.setObjectName("AtivoCard")
        encerrado = not a.ativo

        self.nome = RotuloElidido(a.nome, "AtivoNome")
        self.nome.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # as pílulas ficam coladas no nome
        self.nome.setProperty("encerrado", encerrado)
        self.instituicao = RotuloElidido(a.instituicao or "", "AtivoInstituicao")
        self.instituicao.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.instituicao.setVisible(bool(a.instituicao))
        self.pilula_taxa = Pilula(a.descricao_taxa)
        self.pilula_extra = Pilula("metas" if a.caixinhas else "encerrado" if encerrado else "")
        self.pilula_extra.setVisible(a.caixinhas or encerrado)
        self.vencimento = _rotulo(f"vence {a.vencimento:%d/%m/%Y}" if a.vencimento else "", "AtivoInstituicao")
        self.vencimento.setVisible(a.vencimento is not None)
        topo = QHBoxLayout()
        topo.setSpacing(tema.ESPACO)
        for w in (self.nome, self.instituicao, self.pilula_taxa, self.pilula_extra):
            topo.addWidget(w)
        topo.addStretch(1)
        topo.addWidget(self.vencimento)

        self.aplicado = _rotulo(formatar_brl(p.aplicado), "AtivoValor")
        aproximado = "≈ " if p.estimado else ""
        self.valor_atual = _rotulo(f"{aproximado}{formatar_brl(p.valor_atual)}", "AtivoValor")
        self.apoio_valor = _rotulo(
            f"saldo real de {_curto(p.base_data)}" if p.base_data else ("estimado, taxas incompletas" if p.estimado else "estimado, bruto"),
            "ItemDetalhe",
        )
        self.rendimento = _rotulo(_brl_com_sinal(p.rendimento), "AtivoValor")
        self.rendimento.setProperty("tom", "positivo" if p.rendimento > 0 else "neutro")
        self.apoio_rendimento = _rotulo(_pct(p.rendimento_pct, sinal=True) if p.rendimento_pct is not None else "sem aportes", "ItemDetalhe")
        valores = QGridLayout()
        valores.setHorizontalSpacing(2 * tema.ESPACO)
        valores.setVerticalSpacing(2)
        for coluna, (titulo, valor, apoio) in enumerate(
            (("Aplicado", self.aplicado, None), ("Valor atual", self.valor_atual, self.apoio_valor), ("Rendimento", self.rendimento, self.apoio_rendimento))
        ):
            rotulo = _rotulo(titulo.upper(), "NumeroRotulo")
            _espacar_letras(rotulo, 1.0)
            valores.addWidget(rotulo, 0, coluna)
            valores.addWidget(valor, 1, coluna)
            if apoio is not None:
                valores.addWidget(apoio, 2, coluna)
            valores.setColumnStretch(coluna, 1)

        self.nota = _rotulo("O dinheiro das metas rende aqui. Guardar e retirar: aba Metas.", "ItemDetalhe")
        self.nota.setVisible(a.caixinhas)

        self.form = FormAtivo()
        self.form.setVisible(False)
        self.form.cancelado.connect(self.fechar_form)
        self.form.confirmado.connect(self._confirmado)

        self.lista_historico = HistoricoAtivo()
        self.lista_historico.setVisible(False)

        self._montar_acoes(p)

        c = 2 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(c, c, c, c)
        coluna.setSpacing(tema.ESPACO)
        coluna.addLayout(topo)
        coluna.addLayout(valores)
        coluna.addWidget(self.nota)
        coluna.addWidget(self.form)
        coluna.addWidget(self.lista_historico)
        coluna.addLayout(self._fila_principal)
        coluna.addLayout(self._fila_extra)

    def _montar_acoes(self, p: Posicao) -> None:
        a = p.ativo
        encerrado = not a.ativo
        self.botao_aportar = _compacto(BotaoPrimario("Aportar"))
        self.botao_aportar.clicked.connect(lambda: self.aportar.emit(self.id_ativo))
        self.botao_resgatar = _compacto(BotaoSecundario("Resgatar"))
        self.botao_resgatar.setEnabled(p.valor_atual > 0)
        self.botao_resgatar.setToolTip("" if p.valor_atual > 0 else "Não há nada aplicado")
        self.botao_resgatar.clicked.connect(lambda: self.resgatar.emit(self.id_ativo))
        self.botao_saldo = _compacto(BotaoSecundario("Atualizar saldo"))
        self.botao_saldo.setToolTip("Registra o saldo que o banco mostra hoje; a estimativa passa a partir dele")
        self.botao_saldo.clicked.connect(lambda: self.atualizar_saldo.emit(self.id_ativo))
        self.botao_metas = _compacto(BotaoSecundario("Ver metas"))
        self.botao_metas.clicked.connect(self.ver_metas.emit)
        self.botao_editar = _acao("Editar taxa" if a.caixinhas else "Editar")
        self.botao_editar.clicked.connect(lambda: self.editar.emit(self.id_ativo))
        self.botao_historico = _acao("Histórico")
        self.botao_historico.setToolTip("Ver, corrigir ou excluir aportes, resgates e saldos")
        self.botao_historico.clicked.connect(lambda: self.historico.emit(self.id_ativo))
        self.botao_encerrar = _acao("Encerrar", perigo=True)
        self.botao_encerrar.setToolTip("Tira da lista (o histórico fica). Resgate tudo antes")
        self.botao_encerrar.clicked.connect(lambda: self.encerrar.emit(self.id_ativo))
        self.botao_reativar = _acao("Reativar")
        self.botao_reativar.clicked.connect(lambda: self.reativar.emit(self.id_ativo))

        self._fila_principal = QHBoxLayout()
        self._fila_principal.setSpacing(tema.ESPACO)
        self._fila_extra = QHBoxLayout()
        self._fila_extra.setSpacing(tema.ESPACO)
        principais = [self.botao_saldo, self.botao_metas] if a.caixinhas else [self.botao_aportar, self.botao_resgatar, self.botao_saldo]
        if encerrado:
            principais = []
        for w in (self.botao_aportar, self.botao_resgatar, self.botao_saldo, self.botao_metas, self.botao_reativar, self.botao_encerrar, self.botao_historico):
            w.setVisible(False)
        for w in principais:
            w.setVisible(True)
            self._fila_principal.addWidget(w)
        # caixinhas: o dinheiro se move na aba Metas, então não há o que corrigir aqui
        historico = [] if a.caixinhas else [self.botao_historico]
        self._discretas = historico + ([self.botao_reativar] if encerrado else [self.botao_editar] + ([] if a.caixinhas else [self.botao_encerrar]))
        self.botao_editar.setVisible(not encerrado)
        for w in self._discretas:
            w.setVisible(True)
        self._acomodar_discretas(False)

    def _acomodar_discretas(self, estreito: bool) -> None:
        """Editar/Encerrar ficam à direita da fila de botões; em cartão estreito, descem pra própria fila."""
        if estreito == self._estreito:
            return
        self._estreito = estreito
        for fila in (self._fila_principal, self._fila_extra):
            for w in self._discretas:
                fila.removeWidget(w)
        for i in range(self._fila_principal.count() - 1, -1, -1):  # tira o esticador antigo
            if self._fila_principal.itemAt(i).spacerItem():
                self._fila_principal.takeAt(i)
        while self._fila_extra.count():
            self._fila_extra.takeAt(0)
        if estreito:
            self._fila_extra.addStretch(1)
            destino = self._fila_extra
        else:
            self._fila_principal.addStretch(1)
            destino = self._fila_principal
        for w in self._discretas:
            destino.addWidget(w)

    def resizeEvent(self, evento) -> None:
        self._acomodar_discretas(evento.size().width() < LARGURA_CARTAO_ESTREITO)
        super().resizeEvent(evento)

    # --- Formulário: aportar, resgatar e atualizar saldo -----------------------------------------------
    def abrir_form(self, tipo: str, hoje: date, contas: list[tuple[int, str]]) -> None:
        """`tipo`: "aportar", "resgatar" ou "saldo"."""
        self._tipo_form = tipo
        p = self.posicao
        nome = p.ativo.nome
        if tipo == "aportar":
            self.form.preparar(f"Aportar em {nome}", "Tirar de", "Aportar", None, hoje, contas)
        elif tipo == "resgatar":
            self.form.preparar(
                f"Resgatar de {nome} · ≈ {formatar_brl(p.valor_atual)} disponíveis", "Devolver pra", "Resgatar", None, hoje, contas
            )
        else:
            self.form.preparar_saldo(f"Saldo de {nome} no banco hoje", p.valor_atual, hoje)
        self.form.setVisible(True)
        self.form.valor.setFocus()
        self.form.valor.selectAll()

    def fechar_form(self) -> None:
        self._tipo_form = None
        self.form.setVisible(False)

    def mostrar_historico(self, itens: list[dict] | None) -> None:
        """Abre o histórico com `itens` (de `investimentos.historico`); None fecha."""
        if itens is not None:
            self.lista_historico.mostrar(itens)
        self.lista_historico.setVisible(itens is not None)

    @property
    def tipo_form(self) -> str | None:
        return self._tipo_form

    def _confirmado(self, centavos: int, conta_id: int, data: date) -> None:
        self.movimento.emit(self.id_ativo, self._tipo_form, centavos, conta_id, data)


# --- A aba --------------------------------------------------------------------------------------


class AbaInvestimentos(QScrollArea):
    """Conteúdo da aba. `hoje` é uma função (a janela principal pode fixar a data nos testes)."""

    alterado = Signal(str)
    ver_metas = Signal()

    def __init__(self, conn: sqlite3.Connection, hoje: Callable[[], date] = date.today, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje
        self._mostrar_encerrados = False
        self._historicos_abertos: set[int] = set()
        self._status: dict | None = None
        self._atualizando = False
        self.dialogo: DialogoAtivo | None = None
        self.dialogo_b3: DialogoImportarB3 | None = None

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        conteudo = QWidget()
        conteudo.setObjectName("Conteudo")
        coluna = QVBoxLayout(conteudo)
        m = 3 * tema.ESPACO
        coluna.setContentsMargins(m, tema.ESPACO, m, m)
        coluna.setSpacing(m)
        coluna.addWidget(self._montar_resumo())
        coluna.addWidget(self._montar_grafico())
        coluna.addWidget(self._montar_lista())
        coluna.addWidget(self._montar_proventos())
        coluna.addStretch(1)
        self.setWidget(conteudo)
        self.recarregar()

    # --- Montagem --------------------------------------------------------------------
    def _montar_resumo(self) -> Cartao:
        self.botao_novo = BotaoPrimario("+ Novo investimento")
        self.botao_novo.setToolTip("Cadastrar um CDB, uma caixinha, um título do Tesouro, uma ação ou um FII...")
        self.botao_novo.clicked.connect(lambda: self.novo_investimento())
        self.botao_importar = BotaoSecundario("Importar da B3")
        self.botao_importar.setToolTip("Trazer compras, vendas e proventos da planilha de movimentações da B3")
        self.botao_importar.clicked.connect(lambda: self.importar_b3())
        botoes = QWidget()
        linha = QHBoxLayout(botoes)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(tema.ESPACO)
        linha.addWidget(self.botao_importar)
        linha.addWidget(self.botao_novo)
        cartao = Cartao("Investimentos", "Patrimônio, rendimento e a previsão até o fim do ano", acao=botoes)
        self._patrimonio = NumeroDestaque("Patrimônio hoje", "", "")
        self._investido = NumeroDestaque("Investido", "", "")
        self._rendimento = NumeroDestaque("Rendimento no ano", "", "")
        self._pilula_cdi = Pilula("", "neutra")
        self._rendimento.adicionar(self._pilula_cdi)
        self._previsao = NumeroDestaque("Previsão 31/12", "", "", destaque=True)
        grade = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_NUMERO, espaco=3 * tema.ESPACO)
        for numero in (self._patrimonio, self._investido, self._rendimento, self._previsao):
            grade.adicionar(numero)
        cartao.corpo.addWidget(grade)
        self.selo = _rotulo("", "SeloTaxas")
        self.selo.setWordWrap(True)
        cartao.corpo.addWidget(self.selo)
        return cartao

    def _montar_grafico(self) -> Cartao:
        self._cartao_grafico = Cartao("Patrimônio no ano", "")
        self._cartao_grafico.setMinimumHeight(340)
        self.grafico = PainelPatrimonio()
        self._cartao_grafico.corpo.addWidget(self.grafico, 1)
        return self._cartao_grafico

    def _montar_lista(self) -> Cartao:
        self._cartao_lista = Cartao(
            "Seus investimentos", "Renda fixa estimada pelas taxas do Banco Central, ações e FIIs pela última cotação; tudo bruto, sem IR"
        )
        self._lista = QVBoxLayout()
        self._lista.setSpacing(tema.ESPACO + 4)
        self._cartao_lista.corpo.addLayout(self._lista)
        self._vazio = EstadoVazio(
            "lista", "Nenhum investimento ainda", "Cadastre um CDB, uma caixinha ou um título do Tesouro pra acompanhar o rendimento"
        )
        self._vazio.setMinimumHeight(200)
        self._cartao_lista.corpo.addWidget(self._vazio)
        self.botao_encerrados = QPushButton()
        self.botao_encerrados.setObjectName("BotaoLink")
        self.botao_encerrados.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_encerrados.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_encerrados.clicked.connect(lambda: self.mostrar_encerrados(not self._mostrar_encerrados))
        self._cartao_lista.corpo.addWidget(self.botao_encerrados, 0, Qt.AlignmentFlag.AlignLeft)
        return self._cartao_lista

    def _montar_proventos(self) -> Cartao:
        self._cartao_proventos = Cartao("Proventos", "Dividendos, JCP e rendimentos de ações e FIIs")
        self._recebidos = NumeroDestaque("Recebidos no ano", "", "")
        self._estimativa = NumeroDestaque("Próximos 12 meses (estimativa)", "", "pelo que cada cota pagou nos últimos 12 meses", destaque=True)
        grade = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_NUMERO, espaco=3 * tema.ESPACO)
        grade.adicionar(self._recebidos)
        grade.adicionar(self._estimativa)
        self._cartao_proventos.corpo.addWidget(grade)
        self.grafico_proventos = PainelProventos()
        self.grafico_proventos.setMinimumHeight(260)
        self._cartao_proventos.corpo.addWidget(self.grafico_proventos, 1)
        return self._cartao_proventos

    # --- Dados ------------------------------------------------------------------------
    def recarregar(self) -> None:
        hoje = self._hoje()
        r = investimentos.resumo(self.conn, hoje)
        posicoes = investimentos.listar(self.conn, hoje, incluir_encerrados=True)
        acoes = renda_variavel.listar(self.conn, hoje, incluir_encerrados=True)
        ativas = [p for p in posicoes if p.ativo.ativo] + [p for p in acoes if p["ativo"].ativo]
        encerrados = [p for p in posicoes if not p.ativo.ativo] + [p for p in acoes if not p["ativo"].ativo]

        self._carregar_numeros(r)
        self._cartao_grafico.titulo.setText(f"PATRIMÔNIO EM {hoje.year}")
        self.grafico.definir(investimentos.serie_patrimonio_no_ano(self.conn, hoje), hoje.year)
        self._carregar_selo()

        self._carregar_proventos(hoje, [p for p in acoes if p["ativo"].ativo])

        self._esvaziar()
        for tipo in ORDEM_DOS_GRUPOS:
            grupo = [p for p in ativas if _ativo_de(p).tipo == tipo]
            if grupo:
                self._lista.addWidget(self._montar_grupo(tipo, grupo))
        if self._mostrar_encerrados and encerrados:
            titulo = _rotulo("ENCERRADOS", "GrupoNome")
            _espacar_letras(titulo, 1.0)
            self._lista.addWidget(titulo)
            for p in encerrados:
                self._lista.addWidget(self._montar_cartao(p))
        self._vazio.setVisible(not ativas)
        self.botao_encerrados.setVisible(bool(encerrados))
        self.botao_encerrados.setText(
            "Esconder encerrados" if self._mostrar_encerrados else f"Mostrar encerrados ({len(encerrados)})"
        )

    def _carregar_numeros(self, r: investimentos.ResumoInvestimentos) -> None:
        pat = r.patrimonio
        self._patrimonio.definir(formatar_brl(pat["total"]), "em conta + investido − cartão")
        partes = []
        if pat["caixinhas"]:
            partes.append(f"inclui {reais(pat['caixinhas'])} das caixinhas")
        if pat["renda_variavel"]:
            partes.append(f"{reais(pat['renda_variavel'])} em ações e FIIs")
        self._investido.definir(formatar_brl(r.investido), ", ".join(partes) if partes else "valor atual, bruto")
        rend = r.rendimento_ano
        self._rendimento.definir(
            _brl_com_sinal(rend.centavos), _pct(rend.percentual, sinal=True) + " no ano" if rend.percentual is not None else "ainda sem aplicações"
        )
        if r.pct_cdi is None:
            self._pilula_cdi.setVisible(False)
        else:
            self._pilula_cdi.definir(f"{r.pct_cdi:.0f}% do CDI", "bom" if r.pct_cdi >= 100 else "neutra")
            self._pilula_cdi.setToolTip("O que rendeu no ano comparado ao que o mesmo dinheiro teria rendido a 100% do CDI")
            self._pilula_cdi.setVisible(True)
        self._rendimento.setToolTip("Só renda fixa e Tesouro: ações e FIIs variam com o preço e aparecem no resultado de cada um")
        self._previsao.definir(formatar_brl(r.previsao["total"]), texto_da_previsao(r.previsao))
        self._previsao.setToolTip(explicacao_da_previsao(r.previsao))

    def _carregar_proventos(self, hoje: date, ativas: list[dict]) -> None:
        """O cartão Proventos: só aparece quando há ação ou FII ativo."""
        self._cartao_proventos.setVisible(bool(ativas))
        if not ativas:
            return
        estimativa = renda_variavel.estimar_proventos_carteira(self.conn, hoje)
        self._recebidos.definir(
            formatar_brl(renda_variavel.proventos_recebidos_no_ano(self.conn, hoje)), f"em {hoje.year}, até hoje"
        )
        self._estimativa.definir(formatar_brl(int(estimativa["centavos"].sum())))
        self.grafico_proventos.definir(estimativa)

    def _carregar_selo(self) -> None:
        self.selo.setText(texto_do_selo(self.conn, self._status, self._atualizando))

    def definir_status(self, status: dict | None = None, atualizando: bool = False) -> None:
        """Chamado pela janela: o resultado da última busca de taxas (ou que uma está em andamento)."""
        self._status, self._atualizando = status, atualizando
        self._carregar_selo()

    def _esvaziar(self) -> None:
        while self._lista.count():
            widget = self._lista.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _montar_grupo(self, tipo: str, posicoes: list) -> QWidget:
        caixa = QWidget()
        coluna = QVBoxLayout(caixa)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(tema.ESPACO)
        cabecalho = QHBoxLayout()
        nome = _rotulo("FIIs" if tipo == "fii" else NOMES_TIPO[tipo].upper(), "GrupoNome")
        _espacar_letras(nome, 1.0)
        subtotal = _rotulo(formatar_brl(sum(_valor_do_grupo(p) for p in posicoes)), "GrupoSubtotal")
        cabecalho.addWidget(nome)
        cabecalho.addStretch(1)
        cabecalho.addWidget(subtotal)
        coluna.addLayout(cabecalho)
        for p in posicoes:
            coluna.addWidget(self._montar_cartao(p))
        return caixa

    def _montar_cartao(self, p: Posicao | dict) -> CartaoAtivo | CartaoAcao:
        if isinstance(p, dict):
            return self._montar_cartao_acao(p)
        cartao = CartaoAtivo(p)
        cartao.aportar.connect(lambda i: self.abrir_form(i, "aportar"))
        cartao.resgatar.connect(lambda i: self.abrir_form(i, "resgatar"))
        cartao.atualizar_saldo.connect(lambda i: self.abrir_form(i, "saldo"))
        cartao.editar.connect(self.editar_investimento)
        cartao.encerrar.connect(self.encerrar)
        cartao.reativar.connect(self.reativar)
        cartao.ver_metas.connect(self.ver_metas.emit)
        cartao.movimento.connect(self._movimento)
        self._ligar_historico(cartao)
        return cartao

    def _ligar_historico(self, cartao: CartaoAtivo | CartaoAcao) -> None:
        """Histórico de operações do ativo: abre e fecha no cartão; salvar e excluir gravam e recarregam a aba."""
        cartao.historico.connect(self.alternar_historico)
        cartao.lista_historico.salvar.connect(lambda dados, i=cartao.id_ativo: self._salvar_operacao(i, dados))
        cartao.lista_historico.excluir.connect(lambda fonte, op, i=cartao.id_ativo: self._excluir_operacao(i, fonte, op))
        if cartao.id_ativo in self._historicos_abertos:
            cartao.mostrar_historico(investimentos.historico(self.conn, cartao.id_ativo))

    def _montar_cartao_acao(self, p: dict) -> CartaoAcao:
        cartao = CartaoAcao(p)
        cartao.comprar.connect(lambda i: self.abrir_form_acao(i, "comprar"))
        cartao.vender.connect(lambda i: self.abrir_form_acao(i, "vender"))
        cartao.provento.connect(lambda i: self.abrir_form_acao(i, "provento"))
        cartao.editar.connect(self.editar_investimento)
        cartao.encerrar.connect(self.encerrar)
        cartao.reativar.connect(self.reativar)
        cartao.operacao.connect(self._operacao_rv)
        self._ligar_historico(cartao)
        return cartao

    def cartoes(self) -> list[CartaoAtivo]:
        return self.findChildren(CartaoAtivo)

    def cartao_do_ativo(self, ativo_id: int) -> CartaoAtivo | None:
        return next((c for c in self.cartoes() if c.id_ativo == ativo_id), None)

    def cartoes_acoes(self) -> list[CartaoAcao]:
        return self.findChildren(CartaoAcao)

    def cartao_da_acao(self, ativo_id: int) -> CartaoAcao | None:
        return next((c for c in self.cartoes_acoes() if c.id_ativo == ativo_id), None)

    # --- Ações ------------------------------------------------------------------------------
    def mostrar_encerrados(self, mostrar: bool) -> None:
        self._mostrar_encerrados = mostrar
        self.recarregar()

    def _contas_do_formulario(self) -> list[tuple[int, str]]:
        return [(c["id"], c["nome"]) for c in cadastros.contas_por_tipo(self.conn, ("corrente", "dinheiro"))]

    def abrir_form(self, ativo_id: int, tipo: str) -> None:
        """Abre o formulário na linha do investimento; só um fica aberto por vez."""
        self._fechar_formularios(menos=ativo_id)
        cartao = self.cartao_do_ativo(ativo_id)
        if cartao is not None:
            cartao.abrir_form(tipo, self._hoje(), self._contas_do_formulario())

    def _fechar_formularios(self, menos: int | None = None) -> None:
        for c in self.cartoes() + self.cartoes_acoes():
            if c.id_ativo != menos:
                c.fechar_form()

    def abrir_form_acao(self, ativo_id: int, modo: str) -> None:
        """Abre Comprar, Vender ou Provento na linha da ação ou FII; só um formulário fica aberto por vez."""
        self._fechar_formularios(menos=ativo_id)
        cartao = self.cartao_da_acao(ativo_id)
        if cartao is not None:
            cartao.abrir_form(modo, self._hoje(), self._contas_do_formulario())

    def _operacao_rv(self, ativo_id: int, dados: dict) -> None:
        """Grava a compra, venda ou provento do formulário. Erro de regra fica dentro do formulário."""
        cartao = self.cartao_da_acao(ativo_id)
        try:
            ticker = investimentos.obter(self.conn, ativo_id).ticker
            modo, conta = dados["modo"], dados["conta_id"]
            if modo == "comprar":
                renda_variavel.comprar(self.conn, ativo_id, dados["data"], dados["quantidade"], dados["preco"], dados["taxas"], conta)
                mensagem = f"Compra registrada: {ticker}"
            elif modo == "vender":
                renda_variavel.vender(self.conn, ativo_id, dados["data"], dados["quantidade"], dados["preco"], dados["taxas"], conta)
                mensagem = f"Venda registrada: {ticker}"
            else:
                renda_variavel.registrar_provento(self.conn, ativo_id, dados["tipo"], dados["data"], dados["preco"], conta)
                mensagem = f"{renda_variavel.NOMES_PROVENTO[dados['tipo']]} registrado: {ticker}"
        except ValueError as erro:
            if cartao is not None:
                cartao.form.mostrar_erro(str(erro))
            return
        self._avisar(mensagem)

    def _cartao_qualquer(self, ativo_id: int) -> CartaoAtivo | CartaoAcao | None:
        return self.cartao_do_ativo(ativo_id) or self.cartao_da_acao(ativo_id)

    def alternar_historico(self, ativo_id: int) -> None:
        """"Histórico": mostra ou esconde as operações do ativo na própria linha."""
        cartao = self._cartao_qualquer(ativo_id)
        if cartao is None:
            return
        if ativo_id in self._historicos_abertos:
            self._historicos_abertos.discard(ativo_id)
            cartao.mostrar_historico(None)
        else:
            self._historicos_abertos.add(ativo_id)
            cartao.mostrar_historico(investimentos.historico(self.conn, ativo_id))

    def _salvar_operacao(self, ativo_id: int, d: dict) -> None:
        """Grava a correção de uma operação. Erro de regra fica dentro do formulário."""
        try:
            if d["fonte"] == "mov":
                investimentos.editar_movimentacao(self.conn, d["id"], valor=d["valor"], data=d["data"])
            else:
                renda_variavel.editar_operacao(
                    self.conn, d["id"], data=d["data"], quantidade=d["quantidade"], preco=d["preco"], taxas=d["taxas"], valor=d["valor"]
                )
        except ValueError as erro:
            self._erro_no_historico(ativo_id, str(erro))
            return
        self._avisar("Operação corrigida")

    def _excluir_operacao(self, ativo_id: int, fonte: str, operacao_id: int) -> None:
        try:
            if fonte == "mov":
                investimentos.excluir_movimentacao(self.conn, operacao_id)
            else:
                renda_variavel.excluir_operacao(self.conn, operacao_id)
        except ValueError as erro:
            self._erro_no_historico(ativo_id, str(erro))
            return
        self._avisar("Operação excluída")

    def _erro_no_historico(self, ativo_id: int, texto: str) -> None:
        cartao = self._cartao_qualquer(ativo_id)
        if cartao is not None:
            cartao.lista_historico.mostrar_erro(texto)

    def _movimento(self, ativo_id: int, tipo: str, centavos: int, conta_id: int, data: date) -> None:
        cartao = self.cartao_do_ativo(ativo_id)
        try:
            nome = investimentos.obter(self.conn, ativo_id).nome
            if tipo == "aportar":
                investimentos.aportar(self.conn, ativo_id, centavos, conta_id, data)
                mensagem = f"Aportado {formatar_brl(centavos)} em {nome}"
            elif tipo == "resgatar":
                investimentos.resgatar(self.conn, ativo_id, centavos, conta_id, data)
                mensagem = f"Resgatado {formatar_brl(centavos)} de {nome}"
            else:
                investimentos.registrar_saldo_real(self.conn, ativo_id, centavos, data)
                mensagem = f"Saldo de {nome} atualizado para {formatar_brl(centavos)}"
        except ValueError as erro:
            if cartao is not None:
                cartao.form.mostrar_erro(str(erro))
            return
        self._avisar(mensagem)

    def encerrar(self, ativo_id: int) -> None:
        try:
            nome = investimentos.obter(self.conn, ativo_id).nome
            investimentos.encerrar(self.conn, self._hoje(), ativo_id)
        except ValueError as erro:  # ainda há dinheiro ou cotas: o aviso aparece na própria linha
            cartao = self.cartao_do_ativo(ativo_id)
            if cartao is not None:
                cartao.abrir_form("resgatar", self._hoje(), self._contas_do_formulario())
                cartao.form.mostrar_erro(str(erro))
            elif (acao := self.cartao_da_acao(ativo_id)) is not None:
                self.abrir_form_acao(ativo_id, "vender")
                acao.form.mostrar_erro(str(erro))
            return
        self._avisar(f"Investimento encerrado: {nome}")

    def reativar(self, ativo_id: int) -> None:
        investimentos.reativar(self.conn, ativo_id)
        self._avisar(f"Investimento reativado: {investimentos.obter(self.conn, ativo_id).nome}")

    def _avisar(self, mensagem: str) -> None:
        self.recarregar()
        self.alterado.emit(mensagem)

    # --- Diálogos ---------------------------------------------------------------------------
    def _abrir_dialogo(self, ativo_id: int | None) -> DialogoAtivo | None:
        try:
            dialogo = DialogoAtivo(self.conn, ativo_id, parent=self, hoje=self._hoje())
        except ValueError:  # o investimento sumiu
            self.recarregar()
            return None
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._avisar)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo", None))
        self.dialogo = dialogo
        dialogo.open()
        return dialogo

    def importar_b3(self, caminho: str | None = None) -> DialogoImportarB3:
        """"Importar da B3": abre a janela (já com a prévia de `caminho`, se dado)."""
        dialogo = DialogoImportarB3(self.conn, parent=self, hoje=self._hoje())
        dialogo.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogo.gravado.connect(self._avisar)
        dialogo.finished.connect(lambda _codigo: setattr(self, "dialogo_b3", None))
        self.dialogo_b3 = dialogo
        dialogo.open()
        if caminho:
            dialogo.carregar(caminho)
        return dialogo

    def novo_investimento(self) -> DialogoAtivo | None:
        """"+ Novo investimento"."""
        return self._abrir_dialogo(None)

    def editar_investimento(self, ativo_id: int) -> DialogoAtivo | None:
        return self._abrir_dialogo(ativo_id)
