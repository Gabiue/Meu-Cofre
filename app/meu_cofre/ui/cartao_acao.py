"""Uma ação ou um FII na aba Investimentos (F9): ticker, quantidade, preço médio, cotação, valor atual, resultado e
proventos, com as ações Comprar, Vender, Provento e Editar. Os formulários abrem na própria linha (`FormRV`).

Os cartões só mostram o que `renda_variavel.posicao` calculou (um dict); quem grava é a aba.
"""

import math
from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from meu_cofre import renda_variavel
from meu_cofre.lancamentos import formatar_brl, formatar_brl_inteiro, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.aba_metas import _acao, _compacto, _rotulo
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    CampoData,
    CampoLista,
    GradeResponsiva,
    Marcador,
    Pilula,
    RotuloElidido,
    _espacar_letras,
    _repolir,
)

LARGURA_MINIMA_CELULA = 138
TEXTOS_CONTA = {"comprar": "Tirar da conta", "vender": "Devolver pra conta", "provento": "Entrou na conta"}
TITULOS = {"comprar": "Comprar", "vender": "Vender", "provento": "Registrar provento de"}
BOTOES = {"comprar": "Comprar", "vender": "Vender", "provento": "Registrar"}


def formatar_quantidade(q: float) -> str:
    """100 -> "100"; 10.5 -> "10,5"."""
    return f"{q:,.4f}".rstrip("0").rstrip(".").replace(",", "_").replace(".", ",").replace("_", ".")


def formatar_pct(x: float, sinal: bool = False) -> str:
    texto = f"{x:+.2f}%" if sinal else f"{x:.2f}%"
    return texto.replace(".", ",").replace("-", "−")


def brl_com_sinal(centavos: int) -> str:
    if centavos > 0:
        return f"+{formatar_brl(centavos)}"
    return formatar_brl(centavos).replace("-", "−")


def ler_quantidade(texto: str) -> float | None:
    """"10", "10,5", "1.000" -> número; None se vazio, inválido ou não positivo."""
    limpo = texto.strip().replace(" ", "")
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif limpo.count(".") > 1 or (limpo.count(".") == 1 and len(limpo.split(".")[1]) == 3):
        limpo = limpo.replace(".", "")  # 1.000 é mil
    try:
        q = float(limpo)
    except ValueError:
        return None
    return q if math.isfinite(q) and q > 0 else None


# --- O formulário inline --------------------------------------------------------------------------


class FormRV(QFrame):
    """Comprar, vender ou registrar um provento. Emite `confirmado(dados)` com: `modo`, `quantidade`, `preco` (centavos
    por cota; no provento, o valor recebido), `taxas`, `data`, `conta_id` (None se não mexe na conta) e `tipo`
    (dividendo | jcp | rendimento, só no provento)."""

    confirmado = Signal(dict)
    cancelado = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FormMeta")
        self.modo = "comprar"
        self._grade = QGridLayout(self)
        p = 2 * tema.ESPACO
        self._grade.setContentsMargins(p, p, p, p)
        self._grade.setHorizontalSpacing(2 * tema.ESPACO)
        self._grade.setVerticalSpacing(4)
        for c in range(3):
            self._grade.setColumnStretch(c, 1)

        self.titulo = _rotulo("", "NumeroRotulo")
        self.titulo.setWordWrap(True)
        _espacar_letras(self.titulo, 1.0)
        self.titulo.setContentsMargins(0, 0, 0, 4)
        self._grade.addWidget(self.titulo, 0, 0, 1, 3)

        self.quantidade = QLineEdit()
        self.preco = QLineEdit()
        self.taxas = QLineEdit()
        self.tipo = CampoLista()
        for codigo in renda_variavel.TIPOS_PROVENTO:
            self.tipo.addItem(renda_variavel.NOMES_PROVENTO[codigo], codigo)
        self.data = CampoData()
        self.usar_conta = Marcador("")
        self.conta = CampoLista()
        for campo in (self.preco, self.taxas):
            campo.setObjectName("CampoValor")
            campo.setPlaceholderText("0,00")
        self.quantidade.setObjectName("CampoValor")
        self.quantidade.setPlaceholderText("0")

        self.rotulo_a, self.rotulo_b, self.rotulo_c = (_rotulo("", "CampoRotulo") for _ in range(3))
        self.rotulo_data = _rotulo("Data", "CampoRotulo")
        self.rotulo_conta = _rotulo("Conta", "CampoRotulo")
        self.erro = _rotulo("", "CampoErro")
        self.erro.setWordWrap(True)
        self.erro.setFixedHeight(22)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        self.botao_cancelar = _compacto(BotaoSecundario("Cancelar"))
        self.botao_cancelar.clicked.connect(self.cancelado.emit)
        self.botao_confirmar = _compacto(BotaoPrimario("Comprar"))
        self.botao_confirmar.clicked.connect(self.confirmar)
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_confirmar)
        self._grade.addWidget(self.erro, 5, 0, 1, 3)
        self._grade.addLayout(botoes, 6, 0, 1, 3)

        self.usar_conta.toggled.connect(self.conta.setEnabled)
        for campo in (self.quantidade, self.preco, self.taxas):
            campo.returnPressed.connect(self.confirmar)
            campo.textChanged.connect(lambda _t: self._limpar_erro())

    def _dispor(self, itens: list[tuple[QWidget, int, int, int]]) -> None:
        """Põe na grade só os widgets de `itens` (widget, linha, coluna, colunas ocupadas); os outros somem."""
        todos = (
            self.quantidade, self.preco, self.taxas, self.tipo, self.data, self.usar_conta, self.conta,
            self.rotulo_a, self.rotulo_b, self.rotulo_c, self.rotulo_data, self.rotulo_conta,
        )
        for w in todos:
            self._grade.removeWidget(w)
            w.setVisible(False)
        for w, linha, coluna, largura in itens:
            self._grade.addWidget(w, linha, coluna, 1, largura)
            w.setVisible(True)

    def preparar(
        self, modo: str, titulo: str, hoje: date, contas: list[tuple[int, str]], preco_sugerido: int | None = None, maximo: float | None = None
    ) -> None:
        """Deixa o formulário pronto. `preco_sugerido` (centavos) preenche o preço; `maximo` (cotas) vira a dica da venda."""
        self.modo = modo
        if modo == "provento":
            self.rotulo_a.setText("Tipo")
            self.rotulo_b.setText("Valor recebido (R$)")
            self.rotulo_c.setText("Data")
            self._dispor([
                (self.rotulo_a, 1, 0, 1), (self.rotulo_b, 1, 1, 1), (self.rotulo_c, 1, 2, 1),
                (self.tipo, 2, 0, 1), (self.preco, 2, 1, 1), (self.data, 2, 2, 1),
                (self.rotulo_conta, 3, 0, 3), (self.usar_conta, 4, 0, 1), (self.conta, 4, 1, 1),
            ])
        else:
            self.rotulo_a.setText("Quantidade")
            self.rotulo_b.setText("Preço por cota (R$)")
            self.rotulo_c.setText("Taxas (R$, opcional)")
            self._dispor([
                (self.rotulo_a, 1, 0, 1), (self.rotulo_b, 1, 1, 1), (self.rotulo_c, 1, 2, 1),
                (self.quantidade, 2, 0, 1), (self.preco, 2, 1, 1), (self.taxas, 2, 2, 1),
                (self.rotulo_data, 3, 0, 1), (self.rotulo_conta, 3, 1, 2),
                (self.data, 4, 0, 1), (self.usar_conta, 4, 1, 1), (self.conta, 4, 2, 1),
            ])
        self.usar_conta.setText(TEXTOS_CONTA[modo])
        self.titulo.setText(titulo.upper())
        self.botao_confirmar.setText(BOTOES[modo])
        self.quantidade.setText("")
        self.quantidade.setPlaceholderText(f"até {formatar_quantidade(maximo)}" if modo == "vender" and maximo else "0")
        self.preco.setText(formatar_brl(preco_sugerido).removeprefix("R$ ") if preco_sugerido and modo != "provento" else "")
        self.taxas.setText("")
        self.conta.clear()
        for id_, nome in contas:
            self.conta.addItem(nome, id_)
        self.conta.setCurrentIndex(max(self.conta.findText("Inter"), 0))
        self.usar_conta.setChecked(bool(contas))
        self.usar_conta.setEnabled(bool(contas))
        self.conta.setEnabled(bool(contas))
        self.data.setDate(QDate(hoje.year, hoje.month, hoje.day))
        self._limpar_erro()

    def foco(self) -> None:
        campo = self.preco if self.modo == "provento" else self.quantidade
        campo.setFocus()
        campo.selectAll()

    def _dinheiro(self, campo: QLineEdit, vazio_vale_zero: bool = False) -> int | None:
        texto = campo.text().strip()
        if not texto:
            return 0 if vazio_vale_zero else None
        try:
            return para_centavos(texto)
        except ValueError:
            return None

    def confirmar(self) -> bool:
        """Confere os campos (erros embaixo, no próprio formulário) e emite `confirmado`. Devolve False se faltou algo."""
        dados: dict = {"modo": self.modo, "data": self.data.date().toPython(), "conta_id": None, "taxas": 0, "quantidade": None, "tipo": None}
        if self.modo != "provento":
            dados["quantidade"] = ler_quantidade(self.quantidade.text())
            if dados["quantidade"] is None:
                return self._erro("Digite a quantidade de cotas (maior que zero).", self.quantidade)
        dados["preco"] = self._dinheiro(self.preco)
        if not dados["preco"] or dados["preco"] <= 0:
            return self._erro("Digite o valor recebido." if self.modo == "provento" else "Digite o preço por cota, como 38,50.", self.preco)
        if self.modo != "provento":
            taxas = self._dinheiro(self.taxas, vazio_vale_zero=True)
            if taxas is None or taxas < 0:
                return self._erro("Taxas inválidas. Use só números, como 4,90, ou deixe vazio.", self.taxas)
            dados["taxas"] = taxas
        else:
            dados["tipo"] = self.tipo.currentData()
        if self.usar_conta.isChecked():
            if self.conta.currentData() is None:
                return self._erro("Escolha a conta ou desmarque a opção.", None)
            dados["conta_id"] = int(self.conta.currentData())
        self.confirmado.emit(dados)
        return True

    def _erro(self, texto: str, campo: QLineEdit | None) -> bool:
        self.mostrar_erro(texto, campo)
        if campo is not None:
            campo.setFocus()
        return False

    def mostrar_erro(self, texto: str, campo: QLineEdit | None = None) -> None:
        self.erro.setText(f"!  {texto}")
        for c in (self.quantidade, self.preco, self.taxas):
            c.setProperty("erro", c is campo)
            _repolir(c)

    def _limpar_erro(self) -> None:
        if self.erro.text():
            self.erro.setText("")
            for c in (self.quantidade, self.preco, self.taxas):
                c.setProperty("erro", False)
                _repolir(c)


# --- O cartão ----------------------------------------------------------------------------------------


class CartaoAcao(QFrame):
    """Uma ação ou FII: ticker em destaque + nome, seis números e as ações."""

    comprar = Signal(int)
    vender = Signal(int)
    provento = Signal(int)
    editar = Signal(int)
    encerrar = Signal(int)
    reativar = Signal(int)
    operacao = Signal(int, dict)  # id do ativo, dados do formulário (ver FormRV)

    def __init__(self, p: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.posicao = p
        a = p["ativo"]
        self.id_ativo = a.id
        self._modo_form: str | None = None
        self.setObjectName("AtivoCard")
        encerrado = not a.ativo

        self.ticker = _rotulo(a.ticker or "", "AtivoTicker")
        self.ticker.setProperty("encerrado", encerrado)
        self.nome = RotuloElidido(a.nome, "AtivoInstituicao")
        self.nome.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.pilula_extra = Pilula("encerrado")
        self.pilula_extra.setVisible(encerrado)
        topo = QHBoxLayout()
        topo.setSpacing(tema.ESPACO + 2)
        topo.addWidget(self.ticker)
        topo.addWidget(self.nome, 1)
        topo.addWidget(self.pilula_extra)

        self.celulas = GradeResponsiva(largura_minima_item=LARGURA_MINIMA_CELULA, espaco=2 * tema.ESPACO, colunas_permitidas=(6, 3, 2, 1))
        self._montar_celulas(p)

        self.form = FormRV()
        self.form.setVisible(False)
        self.form.cancelado.connect(self.fechar_form)
        self.form.confirmado.connect(lambda dados: self.operacao.emit(self.id_ativo, dados))

        self._montar_acoes(p, encerrado)
        c = 2 * tema.ESPACO
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(c, c, c, c)
        coluna.setSpacing(tema.ESPACO)
        coluna.addLayout(topo)
        coluna.addWidget(self.celulas)
        coluna.addWidget(self.form)
        coluna.addLayout(self._fila)

    # --- Números ---------------------------------------------------------------------
    def _celula(self, titulo: str, valor: QWidget, apoio: QWidget | None = None) -> QWidget:
        caixa = QWidget()
        coluna = QVBoxLayout(caixa)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(2)
        rotulo = _rotulo(titulo.upper(), "NumeroRotulo")
        _espacar_letras(rotulo, 1.0)
        coluna.addWidget(rotulo)
        coluna.addWidget(valor, 0, Qt.AlignmentFlag.AlignLeft)
        if apoio is not None:
            coluna.addWidget(apoio, 0, Qt.AlignmentFlag.AlignLeft)
        coluna.addStretch(1)
        return caixa

    def _montar_celulas(self, p: dict) -> None:
        a = p["ativo"]
        unidade = "cotas" if a.tipo == "fii" else "ações"
        sem_cotacao = p["cotacao"] is None
        self.quantidade = _rotulo(formatar_quantidade(p["quantidade"]), "AtivoValor")
        self.apoio_quantidade = _rotulo(unidade, "ItemDetalhe")
        self.preco_medio = _rotulo(formatar_brl(round(p["preco_medio"])) if p["preco_medio"] else "—", "AtivoValor")
        self.apoio_preco_medio = _rotulo(f"custo {formatar_brl_inteiro(p['custo'])}" if p["custo"] else "sem posição", "ItemDetalhe")
        self.cotacao = _rotulo("—" if sem_cotacao else formatar_brl(p["cotacao"]), "AtivoValor")
        self.apoio_cotacao = _rotulo("sem cotação" if sem_cotacao else f"de {p['cotacao_data']:%d/%m}", "ItemDetalhe")
        self.valor_atual = _rotulo("—" if p["valor_atual"] is None else formatar_brl(p["valor_atual"]), "AtivoValor")
        self.apoio_valor = _rotulo("sem cotação" if sem_cotacao else "pela última cotação", "ItemDetalhe")
        resultado = p["resultado"]
        self.resultado = _rotulo("—" if resultado is None else brl_com_sinal(resultado), "AtivoValor")
        self.resultado.setProperty("tom", "neutro" if not resultado else "positivo" if resultado > 0 else "negativo")
        pct = p["resultado_pct"]
        if pct is None:
            self.pilula_resultado = Pilula("sem cotação" if p["quantidade"] else "sem posição")
        else:
            self.pilula_resultado = Pilula(formatar_pct(pct, sinal=True), "bom" if pct > 0 else "ruim" if pct < 0 else "neutra")
        self.apoio_resultado = _rotulo(f"lucro nas vendas: {brl_com_sinal(p['realizado'])}" if p["realizado"] else "", "ItemDetalhe")
        self.apoio_resultado.setVisible(bool(p["realizado"]))
        self.proventos = _rotulo(formatar_brl(p["proventos_12m"]) if p["proventos_12m"] else "—", "AtivoValor")
        if p["dy_12m"] is not None and p["proventos_12m"]:
            apoio = f"DY {formatar_pct(p['dy_12m'])} em 12 meses"
        else:
            apoio = f"{formatar_brl_inteiro(p['proventos_total'])} no total" if p["proventos_total"] else "nenhum recebido"
        self.apoio_proventos = _rotulo(apoio, "ItemDetalhe")
        coluna_resultado = QWidget()
        sub = QVBoxLayout(coluna_resultado)
        sub.setContentsMargins(0, 0, 0, 0)
        sub.setSpacing(2)
        sub.addWidget(self.resultado, 0, Qt.AlignmentFlag.AlignLeft)
        sub.addWidget(self.pilula_resultado, 0, Qt.AlignmentFlag.AlignLeft)
        for titulo, valor, apoio_w in (
            (f"Quantidade", self.quantidade, self.apoio_quantidade),
            ("Preço médio", self.preco_medio, self.apoio_preco_medio),
            ("Cotação", self.cotacao, self.apoio_cotacao),
            ("Valor atual", self.valor_atual, self.apoio_valor),
            ("Resultado", coluna_resultado, self.apoio_resultado),
            ("Proventos 12 m", self.proventos, self.apoio_proventos),
        ):
            self.celulas.adicionar(self._celula(titulo, valor, apoio_w))
        self.cotacao.setToolTip(
            "Cotação de fonte pública, atualizada em segundo plano. Sem internet vale a última guardada."
            if not sem_cotacao
            else "Ainda não chegou a cotação deste ticker. Confira o ticker e a conexão."
        )
        self.preco_medio.setToolTip("Média ponderada das compras, com taxas. Vender não muda o preço médio.")
        self.apoio_proventos.setToolTip("DY: proventos dos últimos 12 meses ÷ valor atual da posição")

    # --- Ações ---------------------------------------------------------------------------
    def _montar_acoes(self, p: dict, encerrado: bool) -> None:
        self.botao_comprar = _compacto(BotaoPrimario("Comprar"))
        self.botao_comprar.clicked.connect(lambda: self.comprar.emit(self.id_ativo))
        self.botao_vender = _compacto(BotaoSecundario("Vender"))
        self.botao_vender.setEnabled(p["quantidade"] > 0)
        self.botao_vender.setToolTip("" if p["quantidade"] > 0 else "Não há cotas na carteira")
        self.botao_vender.clicked.connect(lambda: self.vender.emit(self.id_ativo))
        self.botao_provento = _compacto(BotaoSecundario("Provento"))
        self.botao_provento.setToolTip("Registrar dividendo, JCP ou rendimento recebido")
        self.botao_provento.clicked.connect(lambda: self.provento.emit(self.id_ativo))
        self.botao_editar = _acao("Editar")
        self.botao_editar.clicked.connect(lambda: self.editar.emit(self.id_ativo))
        self.botao_encerrar = _acao("Encerrar", perigo=True)
        self.botao_encerrar.setToolTip("Tira da lista (o histórico fica). Venda todas as cotas antes")
        self.botao_encerrar.clicked.connect(lambda: self.encerrar.emit(self.id_ativo))
        self.botao_reativar = _acao("Reativar")
        self.botao_reativar.clicked.connect(lambda: self.reativar.emit(self.id_ativo))
        self._fila = QHBoxLayout()
        self._fila.setSpacing(tema.ESPACO)
        principais = [] if encerrado else [self.botao_comprar, self.botao_vender, self.botao_provento]
        for w in (self.botao_comprar, self.botao_vender, self.botao_provento, self.botao_editar, self.botao_encerrar, self.botao_reativar):
            w.setVisible(False)
        for w in principais:
            w.setVisible(True)
            self._fila.addWidget(w)
        self._fila.addStretch(1)
        discretas = [self.botao_reativar] if encerrado else [self.botao_editar, self.botao_encerrar]
        for w in discretas:
            w.setVisible(True)
            self._fila.addWidget(w)

    def abrir_form(self, modo: str, hoje: date, contas: list[tuple[int, str]]) -> None:
        """`modo`: "comprar", "vender" ou "provento"."""
        self._modo_form = modo
        p = self.posicao
        a = p["ativo"]
        titulo = f"{TITULOS[modo]} {a.ticker}"
        if modo == "vender":
            titulo += f" · você tem {formatar_quantidade(p['quantidade'])}"
        self.form.preparar(modo, titulo, hoje, contas, preco_sugerido=p["cotacao"], maximo=p["quantidade"])
        self.form.setVisible(True)
        self.form.foco()

    def fechar_form(self) -> None:
        self._modo_form = None
        self.form.setVisible(False)

    @property
    def modo_form(self) -> str | None:
        return self._modo_form
