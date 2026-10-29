"""Janela "Contas e cartões" (docs/design/telas.md).

Contas (corrente e carteira): saldo inicial editável, com o saldo de hoje ao lado.
Cartões: adicionar, renomear, desativar e reativar; o que está em aberto em cada um.
Tudo é gravado na hora (o saldo, ao sair do campo ou dar Enter) e `alterado(mensagem)` avisa
a janela principal para recarregar. Erros e confirmações aparecem na própria janela.
"""

import sqlite3
from datetime import date

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import cadastros
from meu_cofre.lancamentos import formatar_brl, nome_com_final, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, Cartao, CampoLista, Pilula, _espacar_letras, _repolir

LARGURA = 720
LARGURA_SALDO = 150
SEM_DIA = "Não informar"


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _legenda(texto: str) -> QLabel:
    """Rótulo pequeno em caixa alta, como nos cartões."""
    rotulo = _rotulo(texto.upper(), "NumeroRotulo")
    _espacar_letras(rotulo, 1.0)
    return rotulo


def _compacto(botao: QPushButton, perigo: bool = False) -> QPushButton:
    """Botão menor para as linhas da lista; só o 'Concluído' responde ao Enter."""
    botao.setProperty("compacto", True)
    botao.setProperty("perigo", perigo)
    botao.setAutoDefault(False)
    botao.setDefault(False)
    return botao


def _separador() -> QFrame:
    linha = QFrame()
    linha.setObjectName("Separador")
    linha.setFixedHeight(1)
    return linha


def _esvaziar(layout) -> None:
    """Remove e apaga tudo que está dentro de um layout (widgets e layouts aninhados)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _esvaziar(item.layout())


def _saldo_texto(centavos: int) -> str:
    """123456 -> "1.234,56" (o campo já tem o R$ no rótulo)."""
    return formatar_brl(centavos).removeprefix("R$ ")


def detalhe_cartao(c: sqlite3.Row) -> str:
    """"Caixa · fecha dia 1 · vence dia 10" (o final do cartão vai no nome)."""
    partes = [c["banco"]] if c["banco"] else []
    if c["dia_fechamento"]:
        partes.append(f"fecha dia {c['dia_fechamento']}")
    if c["dia_vencimento"]:
        partes.append(f"vence dia {c['dia_vencimento']}")
    return " · ".join(partes) or "Sem banco nem datas"


class DialogoContas(QDialog):
    """Diálogo modal de contas e cartões. Emite `alterado(mensagem)` depois de cada mudança gravada."""

    alterado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None, hoje: date | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje or date.today()
        self._acao: tuple[str, int] | None = None  # ("renomear" | "editar" | "desativar", id do cartão)
        self._adicionando = False
        self._mostrar_desativados = False
        self._saldos: dict[int, QLineEdit] = {}
        self._saldos_hoje: dict[int, QLabel] = {}
        self._erros_saldo: dict[int, QLabel] = {}

        self.setObjectName("Dialogo")
        self.setProperty("tela", "contas")
        self.setModal(True)
        self.setWindowTitle("Contas e cartões")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()
        self._carregar_contas()
        self._carregar_cartoes()
        self._ajustado = False

    # --- Montagem ------------------------------------------------------------------
    def _montar(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._corpo = QWidget()
        self._corpo.setObjectName("DialogoCorpo")
        rolagem.setWidget(self._corpo)
        raiz.addWidget(rolagem, 1)

        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(self._corpo)
        coluna.setContentsMargins(m, m, m, tema.ESPACO)
        coluna.setSpacing(2 * tema.ESPACO)
        coluna.addWidget(_rotulo("Contas e cartões", "DialogoTitulo"))

        self._cartao_contas = Cartao("Contas", "Saldo inicial é o que você tinha na conta quando começou a usar o app")
        self._lista_contas = self._cartao_contas.corpo
        self._lista_contas.setSpacing(0)
        coluna.addWidget(self._cartao_contas)

        self.botao_adicionar = _compacto(BotaoSecundario("+ Adicionar cartão"))
        self.botao_adicionar.clicked.connect(self.iniciar_adicao)
        self._cartao_cartoes = Cartao("Cartões", "Cartão cancelado? Desative: o histórico fica", acao=self.botao_adicionar)
        self._lista_cartoes = QVBoxLayout()
        self._lista_cartoes.setSpacing(0)
        self._cartao_cartoes.corpo.addLayout(self._lista_cartoes)
        self.erro_cartoes = _rotulo("", "CampoErro")
        self.erro_cartoes.setWordWrap(True)
        self.erro_cartoes.setVisible(False)
        self._cartao_cartoes.corpo.addWidget(self.erro_cartoes)
        self.botao_desativados = QPushButton()
        self.botao_desativados.setObjectName("BotaoLink")
        self.botao_desativados.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_desativados.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.botao_desativados.clicked.connect(lambda: self.mostrar_desativados(not self._mostrar_desativados))
        self._cartao_cartoes.corpo.addWidget(self.botao_desativados, 0, Qt.AlignmentFlag.AlignLeft)
        self._form = self._montar_formulario()
        self._cartao_cartoes.corpo.addWidget(self._form)
        coluna.addWidget(self._cartao_cartoes)
        coluna.addStretch(1)

        barra = QWidget()
        linha = QHBoxLayout(barra)
        linha.setContentsMargins(m, tema.ESPACO, m, m)
        linha.addStretch(1)
        self.botao_concluir = BotaoPrimario("Concluído")
        self.botao_concluir.setAutoDefault(False)  # Enter nos campos só confirma o campo, não fecha a janela
        self.botao_concluir.clicked.connect(self.accept)
        linha.addWidget(self.botao_concluir)
        raiz.addWidget(barra)

    def _montar_formulario(self) -> QFrame:
        """Mini-formulário "Novo cartão" (escondido até clicar em + Adicionar cartão)."""
        quadro = QFrame()
        quadro.setObjectName("FormCartao")
        grade = QGridLayout(quadro)
        p = 2 * tema.ESPACO
        grade.setContentsMargins(p, p, p, p)
        grade.setHorizontalSpacing(2 * tema.ESPACO)
        grade.setVerticalSpacing(4)
        grade.setColumnStretch(0, 1)
        grade.setColumnStretch(1, 1)

        grade.addWidget(_legenda("Novo cartão"), 0, 0, 1, 2)
        grade.setRowMinimumHeight(0, 22)
        self.novo_nome = QLineEdit()
        self.novo_nome.setPlaceholderText("Ex.: Caixa Visa")
        self.novo_banco = QLineEdit()
        self.novo_banco.setPlaceholderText("Opcional")
        self.novo_final = self._campo_final()
        self.novo_fechamento = self._combo_dia()
        self.novo_vencimento = self._combo_dia()
        for linha, (texto0, campo0, texto1, campo1) in enumerate(
            (
                ("Nome", self.novo_nome, "Banco", self.novo_banco),
                ("Final do cartão (4 dígitos)", self.novo_final, None, None),
                ("Dia de fechamento", self.novo_fechamento, "Dia de vencimento", self.novo_vencimento),
            )
        ):
            base = 1 + linha * 2
            grade.addWidget(_rotulo(texto0, "CampoRotulo"), base, 0)
            grade.addWidget(campo0, base + 1, 0)
            if campo1 is not None:
                grade.addWidget(_rotulo(texto1, "CampoRotulo"), base, 1)
                grade.addWidget(campo1, base + 1, 1)
        self.erro_novo = _rotulo("", "CampoErro")
        self.erro_novo.setWordWrap(True)
        self.erro_novo.setFixedHeight(22)
        grade.addWidget(self.erro_novo, 7, 0, 1, 2)

        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        self.botao_cancelar_novo = _compacto(BotaoSecundario("Cancelar"))
        self.botao_cancelar_novo.clicked.connect(self.cancelar_adicao)
        self.botao_salvar_novo = BotaoPrimario("Adicionar cartão")
        self.botao_salvar_novo.setProperty("compacto", True)
        self.botao_salvar_novo.setAutoDefault(False)
        self.botao_salvar_novo.clicked.connect(self.adicionar_cartao)
        botoes.addWidget(self.botao_cancelar_novo)
        botoes.addWidget(self.botao_salvar_novo)
        grade.addLayout(botoes, 8, 0, 1, 2)

        self.novo_nome.textChanged.connect(lambda _t: self._limpar_erro_novo())
        self.novo_final.textChanged.connect(lambda _t: self._limpar_erro_novo())
        self.novo_nome.returnPressed.connect(self.adicionar_cartao)
        self.novo_banco.returnPressed.connect(self.adicionar_cartao)
        self.novo_final.returnPressed.connect(self.adicionar_cartao)
        quadro.setVisible(False)
        return quadro

    @staticmethod
    def _campo_final() -> QLineEdit:
        campo = QLineEdit()
        campo.setPlaceholderText("Opcional, ex.: 1234")
        campo.setMaxLength(4)
        campo.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,4}")))
        return campo

    @staticmethod
    def _combo_dia() -> CampoLista:
        combo = CampoLista()
        combo.addItem(SEM_DIA, None)
        for dia in range(1, 32):
            combo.addItem(f"Dia {dia}", dia)
        return combo

    # --- Contas -------------------------------------------------------------------------
    def _carregar_contas(self) -> None:
        """Monta as linhas das contas (uma vez só: o saldo é editado no lugar, sem refazer a lista)."""
        _esvaziar(self._lista_contas)
        self._saldos.clear()
        self._saldos_hoje.clear()
        self._erros_saldo.clear()
        contas = cadastros.contas_por_tipo(self.conn, ("corrente", "dinheiro"))
        for i, conta in enumerate(contas):
            if i:
                self._lista_contas.addWidget(_separador())
            self._lista_contas.addWidget(self._linha_conta(conta))

    def _linha_conta(self, conta: sqlite3.Row) -> QWidget:
        id_ = conta["id"]
        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO + 4, 0, tema.ESPACO + 4)
        caixa.setSpacing(2 * tema.ESPACO)

        textos = QVBoxLayout()
        textos.setSpacing(2)
        textos.addWidget(_rotulo(conta["nome"], "ContaNome"))
        self._saldos_hoje[id_] = _rotulo("", "ContaDetalhe")
        textos.addWidget(self._saldos_hoje[id_])
        caixa.addLayout(textos, 1)

        campo = QLineEdit(_saldo_texto(conta["saldo_inicial_centavos"]))
        campo.setAlignment(Qt.AlignmentFlag.AlignRight)
        campo.setFixedWidth(LARGURA_SALDO)
        campo.setPlaceholderText("0,00")
        campo.setToolTip("Quanto havia nesta conta quando você começou. Pode ser negativo.")
        campo.editingFinished.connect(lambda i=id_: self.salvar_saldo(i))
        campo.textChanged.connect(lambda _t, i=id_: self._limpar_erro_saldo(i))
        erro = _rotulo("", "CampoErro")
        erro.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._saldos[id_], self._erros_saldo[id_] = campo, erro
        direita = QVBoxLayout()
        direita.setSpacing(4)
        campo_e_legenda = QHBoxLayout()
        campo_e_legenda.setSpacing(tema.ESPACO + 4)
        campo_e_legenda.addWidget(_legenda("Saldo inicial (R$)"))
        campo_e_legenda.addWidget(campo)
        direita.addLayout(campo_e_legenda)
        direita.addWidget(erro, 0, Qt.AlignmentFlag.AlignRight)
        caixa.addLayout(direita)

        self._atualizar_saldo_hoje(id_)
        erro.setVisible(False)
        return linha

    def _atualizar_saldo_hoje(self, conta_id: int) -> None:
        saldo = cadastros.saldo_da_conta(self.conn, conta_id, self._hoje)
        self._saldos_hoje[conta_id].setText(f"Saldo de hoje {formatar_brl(saldo)}")

    def _limpar_erro_saldo(self, conta_id: int) -> None:
        erro = self._erros_saldo.get(conta_id)
        if erro is not None and erro.text():
            erro.setText("")
            erro.setVisible(False)
            campo = self._saldos[conta_id]
            campo.setProperty("erro", False)
            _repolir(campo)

    def salvar_saldo(self, conta_id: int, texto: str | None = None) -> bool:
        """Grava o saldo inicial digitado (ou `texto`). Campo vazio vale zero. Devolve False se inválido."""
        campo = self._saldos[conta_id]
        if texto is not None:
            campo.setText(texto)
        try:
            centavos = para_centavos(campo.text() or "0")
        except ValueError:
            self._mostrar_erro_saldo(conta_id, "Valor inválido. Use só números, como 1.234,56.")
            return False
        atual = self.conn.execute(
            "SELECT nome, saldo_inicial_centavos FROM contas WHERE id = ?", (conta_id,)
        ).fetchone()
        campo.setText(_saldo_texto(centavos))
        if atual["saldo_inicial_centavos"] == centavos:
            return True
        try:
            cadastros.definir_saldo_inicial(self.conn, conta_id, centavos)
        except ValueError as erro:
            self._mostrar_erro_saldo(conta_id, str(erro))
            return False
        self._atualizar_saldo_hoje(conta_id)
        self.alterado.emit(f"Saldo inicial de {atual['nome']}: {formatar_brl(centavos)}")
        return True

    def _mostrar_erro_saldo(self, conta_id: int, texto: str) -> None:
        self._erros_saldo[conta_id].setText(f"!  {texto}")
        self._erros_saldo[conta_id].setVisible(True)
        campo = self._saldos[conta_id]
        campo.setProperty("erro", True)
        _repolir(campo)

    # --- Cartões -------------------------------------------------------------------------
    def _carregar_cartoes(self) -> None:
        _esvaziar(self._lista_cartoes)
        todos = cadastros.contas_por_tipo(self.conn, ("cartao",), incluir_inativas=True)
        ativos = [c for c in todos if c["ativa"]]
        desativados = [c for c in todos if not c["ativa"]]

        if not ativos:
            vazio = _rotulo("Nenhum cartão ativo. Sem cartão, o método Cartão fica desligado nos lançamentos.", "ContaDetalhe")
            vazio.setWordWrap(True)
            self._lista_cartoes.addWidget(vazio)
        linhas = ativos + (desativados if self._mostrar_desativados else [])
        for i, cartao in enumerate(linhas):
            if i:
                self._lista_cartoes.addWidget(_separador())
            self._lista_cartoes.addWidget(self._linha_cartao(cartao))

        self.botao_desativados.setVisible(bool(desativados))
        self.botao_desativados.setText(
            "Esconder cartões desativados"
            if self._mostrar_desativados
            else f"Mostrar cartões desativados ({len(desativados)})"
        )
        self._form.setVisible(self._adicionando)
        self.botao_adicionar.setVisible(not self._adicionando)
        if self.isVisible():  # a lista mudou de tamanho: ajusta a janela depois que os novos widgets forem estilizados
            QTimer.singleShot(0, self._ajustar_altura)

    def _linha_cartao(self, c: sqlite3.Row) -> QWidget:
        id_ = c["id"]
        if self._acao == ("desativar", id_):
            return self._linha_confirmar_desativar(c)
        if self._acao == ("renomear", id_):
            return self._linha_renomear(c)
        if self._acao == ("editar", id_):
            return self._linha_editar(c)

        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO + 4, 0, tema.ESPACO + 4)
        caixa.setSpacing(2 * tema.ESPACO)

        textos = QVBoxLayout()
        textos.setSpacing(2)
        nome = _rotulo(nome_com_final(c["nome"], c["final"]), "ContaNome")
        nome.setProperty("inativo", not c["ativa"])
        topo = QHBoxLayout()
        topo.setSpacing(tema.ESPACO)
        topo.addWidget(nome)
        if not c["ativa"]:
            topo.addWidget(Pilula("desativado"))
        topo.addStretch(1)
        textos.addLayout(topo)
        textos.addWidget(_rotulo(detalhe_cartao(c), "ContaDetalhe"))
        caixa.addLayout(textos, 1)

        aberto = cadastros.divida_do_cartao(self.conn, id_)
        if aberto or c["ativa"]:  # cartão desativado sem dívida não precisa mostrar "R$ 0,00"
            valores = QVBoxLayout()
            valores.setSpacing(2)
            valores.addWidget(_legenda("Em aberto"), 0, Qt.AlignmentFlag.AlignRight)
            valor = _rotulo(formatar_brl(aberto), "ContaValor")
            valor.setToolTip("Tudo que foi comprado no cartão e ainda não foi pago, parcelas futuras incluídas")
            valores.addWidget(valor, 0, Qt.AlignmentFlag.AlignRight)
            caixa.addLayout(valores)

        if c["ativa"]:
            renomear = _compacto(BotaoSecundario("Renomear"))
            renomear.clicked.connect(lambda _c=False, i=id_: self.pedir_renomear(i))
            desativar = _compacto(BotaoSecundario("Desativar"), perigo=True)
            desativar.clicked.connect(lambda _c=False, i=id_: self.pedir_desativar(i))
            editar = _compacto(BotaoSecundario("Editar"))
            editar.clicked.connect(lambda _c=False, i=id_: self.pedir_editar(i))
            caixa.addWidget(renomear)
            caixa.addWidget(editar)
            caixa.addWidget(desativar)
        else:
            reativar = _compacto(BotaoSecundario("Reativar"))
            reativar.clicked.connect(lambda _c=False, i=id_: self.reativar(i))
            caixa.addWidget(reativar)
        return linha

    def _linha_confirmar_desativar(self, c: sqlite3.Row) -> QWidget:
        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO + 4, 0, tema.ESPACO + 4)
        caixa.setSpacing(tema.ESPACO + 4)
        texto = _rotulo(f"Desativar {c['nome']}? Os lançamentos antigos continuam.", "ConfirmaTexto")
        texto.setWordWrap(True)
        caixa.addWidget(texto, 1)
        self.botao_nao = _compacto(BotaoSecundario("Não"))
        self.botao_nao.clicked.connect(self.cancelar_acao)
        self.botao_sim = QPushButton("Sim, desativar")
        self.botao_sim.setObjectName("BotaoPerigo")
        self.botao_sim.setProperty("compacto", True)
        self.botao_sim.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_sim.setAutoDefault(False)
        self.botao_sim.clicked.connect(self.confirmar_desativar)
        caixa.addWidget(self.botao_nao)
        caixa.addWidget(self.botao_sim)
        return linha

    def _linha_renomear(self, c: sqlite3.Row) -> QWidget:
        linha = QWidget()
        coluna = QVBoxLayout(linha)
        coluna.setContentsMargins(0, tema.ESPACO + 4, 0, tema.ESPACO)
        coluna.setSpacing(4)
        coluna.addWidget(_legenda(f"Novo nome de {c['nome']}"))
        edicao = QHBoxLayout()
        edicao.setSpacing(tema.ESPACO + 4)
        self.campo_renomear = QLineEdit(c["nome"])
        self.campo_renomear.selectAll()
        self.campo_renomear.returnPressed.connect(self.salvar_renome)
        self.campo_renomear.textChanged.connect(lambda _t: self._limpar_erro_renomear())
        edicao.addWidget(self.campo_renomear, 1)
        cancelar = _compacto(BotaoSecundario("Cancelar"))
        cancelar.clicked.connect(self.cancelar_acao)
        salvar = BotaoPrimario("Salvar")
        salvar.setProperty("compacto", True)
        salvar.setAutoDefault(False)
        salvar.clicked.connect(self.salvar_renome)
        edicao.addWidget(cancelar)
        edicao.addWidget(salvar)
        coluna.addLayout(edicao)
        self.erro_renomear = _rotulo("", "CampoErro")
        self.erro_renomear.setFixedHeight(22)
        coluna.addWidget(self.erro_renomear)
        self.campo_renomear.setFocus()
        return linha

    def _linha_editar(self, c: sqlite3.Row) -> QWidget:
        """Banco, final e dias de fechamento e vencimento do cartão (o nome é no Renomear)."""
        quadro = QFrame()
        quadro.setObjectName("FormCartao")
        grade = QGridLayout(quadro)
        p = 2 * tema.ESPACO
        grade.setContentsMargins(p, p, p, p)
        grade.setHorizontalSpacing(2 * tema.ESPACO)
        grade.setVerticalSpacing(4)
        grade.setColumnStretch(0, 1)
        grade.setColumnStretch(1, 1)
        grade.addWidget(_legenda(f"Editar {c['nome']}"), 0, 0, 1, 2)
        grade.setRowMinimumHeight(0, 22)
        self.editar_banco = QLineEdit(c["banco"] or "")
        self.editar_banco.setPlaceholderText("Opcional")
        self.editar_final = self._campo_final()
        self.editar_final.setText(c["final"] or "")
        self.editar_fechamento = self._combo_dia()
        self.editar_fechamento.setCurrentIndex(max(self.editar_fechamento.findData(c["dia_fechamento"]), 0))
        self.editar_vencimento = self._combo_dia()
        self.editar_vencimento.setCurrentIndex(max(self.editar_vencimento.findData(c["dia_vencimento"]), 0))
        for linha, (texto0, campo0, texto1, campo1) in enumerate(
            (
                ("Banco", self.editar_banco, "Final do cartão (4 dígitos)", self.editar_final),
                ("Dia de fechamento", self.editar_fechamento, "Dia de vencimento", self.editar_vencimento),
            )
        ):
            base = 1 + linha * 2
            grade.addWidget(_rotulo(texto0, "CampoRotulo"), base, 0)
            grade.addWidget(_rotulo(texto1, "CampoRotulo"), base, 1)
            grade.addWidget(campo0, base + 1, 0)
            grade.addWidget(campo1, base + 1, 1)
        self.erro_editar = _rotulo("", "CampoErro")
        self.erro_editar.setWordWrap(True)
        self.erro_editar.setFixedHeight(22)
        grade.addWidget(self.erro_editar, 5, 0, 1, 2)
        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        cancelar = _compacto(BotaoSecundario("Cancelar"))
        cancelar.clicked.connect(self.cancelar_acao)
        salvar = BotaoPrimario("Salvar")
        salvar.setProperty("compacto", True)
        salvar.setAutoDefault(False)
        salvar.clicked.connect(self.salvar_edicao)
        botoes.addWidget(cancelar)
        botoes.addWidget(salvar)
        grade.addLayout(botoes, 6, 0, 1, 2)
        self.editar_banco.returnPressed.connect(self.salvar_edicao)
        self.editar_final.returnPressed.connect(self.salvar_edicao)
        self.editar_final.textChanged.connect(lambda _t: self.erro_editar.setText(""))
        return quadro

    # --- Ações dos cartões ------------------------------------------------------------------
    def _mostrar_erro_geral(self, texto: str) -> None:
        self.erro_cartoes.setText(f"!  {texto}")
        self.erro_cartoes.setVisible(True)

    def _limpar_erro_geral(self) -> None:
        self.erro_cartoes.setText("")
        self.erro_cartoes.setVisible(False)

    def _gravou(self, mensagem: str) -> None:
        self._acao = None
        self._carregar_cartoes()
        self.alterado.emit(mensagem)

    def mostrar_desativados(self, mostrar: bool) -> None:
        self._mostrar_desativados = mostrar
        self._carregar_cartoes()

    def pedir_desativar(self, conta_id: int) -> None:
        self._limpar_erro_geral()
        self._acao = ("desativar", conta_id)
        self._carregar_cartoes()

    def confirmar_desativar(self) -> None:
        if not self._acao or self._acao[0] != "desativar":
            return
        id_ = self._acao[1]
        nome = cadastros._conta(self.conn, id_)["nome"]
        try:
            cadastros.desativar_conta(self.conn, id_)
        except ValueError as erro:
            self._acao = None
            self._carregar_cartoes()
            self._mostrar_erro_geral(str(erro))
            return
        self._gravou(f"Cartão desativado: {nome}")

    def pedir_renomear(self, conta_id: int) -> None:
        self._limpar_erro_geral()
        self._acao = ("renomear", conta_id)
        self._carregar_cartoes()

    def salvar_renome(self) -> bool:
        if not self._acao or self._acao[0] != "renomear":
            return False
        try:
            cadastros.renomear_conta(self.conn, self._acao[1], self.campo_renomear.text())
        except ValueError as erro:
            self.erro_renomear.setText(f"!  {erro}")
            self.campo_renomear.setProperty("erro", True)
            _repolir(self.campo_renomear)
            return False
        self._gravou(f"Cartão renomeado: {self.campo_renomear.text().strip()}")
        return True

    def _limpar_erro_renomear(self) -> None:
        if self.erro_renomear.text():
            self.erro_renomear.setText("")
            self.campo_renomear.setProperty("erro", False)
            _repolir(self.campo_renomear)

    def pedir_editar(self, conta_id: int) -> None:
        self._limpar_erro_geral()
        self._acao = ("editar", conta_id)
        self._carregar_cartoes()

    def salvar_edicao(self) -> bool:
        if not self._acao or self._acao[0] != "editar":
            return False
        id_ = self._acao[1]
        nome = cadastros._conta(self.conn, id_)["nome"]
        try:
            cadastros.editar_cartao(
                self.conn,
                id_,
                banco=self.editar_banco.text(),
                dia_fechamento=self.editar_fechamento.currentData(),
                dia_vencimento=self.editar_vencimento.currentData(),
                final=self.editar_final.text(),
            )
        except ValueError as erro:
            self.erro_editar.setText(f"!  {erro}")
            return False
        self._gravou(f"Cartão atualizado: {nome}")
        return True

    def cancelar_acao(self) -> None:
        self._acao = None
        self._carregar_cartoes()

    def reativar(self, conta_id: int) -> None:
        self._limpar_erro_geral()
        nome = cadastros._conta(self.conn, conta_id)["nome"]
        cadastros.reativar_conta(self.conn, conta_id)
        self._gravou(f"Cartão reativado: {nome}")

    # --- Adicionar cartão -----------------------------------------------------------------------
    def iniciar_adicao(self) -> None:
        self._limpar_erro_geral()
        self._acao = None
        self._adicionando = True
        for campo in (self.novo_nome, self.novo_banco, self.novo_final):
            campo.clear()
        self.novo_fechamento.setCurrentIndex(0)
        self.novo_vencimento.setCurrentIndex(0)
        self._limpar_erro_novo()
        self._carregar_cartoes()
        self.novo_nome.setFocus()

    def cancelar_adicao(self) -> None:
        self._adicionando = False
        self._carregar_cartoes()

    def adicionar_cartao(self) -> bool:
        try:
            cadastros.criar_cartao(
                self.conn,
                self.novo_nome.text(),
                self.novo_banco.text(),
                self.novo_fechamento.currentData(),
                self.novo_vencimento.currentData(),
                self.novo_final.text(),
            )
        except ValueError as erro:
            self.erro_novo.setText(f"!  {erro}")
            self.novo_nome.setProperty("erro", True)
            _repolir(self.novo_nome)
            self.novo_nome.setFocus()
            return False
        nome = self.novo_nome.text().strip()
        self._adicionando = False
        self._gravou(f"Cartão adicionado: {nome}")
        return True

    def _limpar_erro_novo(self) -> None:
        self.erro_novo.setText("")
        self.novo_nome.setProperty("erro", False)
        _repolir(self.novo_nome)

    # --- Tamanho ----------------------------------------------------------------------------------
    def showEvent(self, evento) -> None:
        if not self._ajustado:  # só depois do estilo aplicado os tamanhos dos campos são os de verdade
            self._ajustado = True
            self._ajustar_altura()
        super().showEvent(evento)

    def _ajustar_altura(self) -> None:
        """Altura pelo conteúdo (mínimo 420), limitada à tela: se faltar espaço, a janela rola."""
        tela = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        self._corpo.layout().activate()
        natural = self._corpo.sizeHint().height() + self.botao_concluir.sizeHint().height() + 3 * tema.ESPACO + 4
        self.setMinimumHeight(0)
        self.resize(LARGURA, min(max(natural, 420), int(tela.height() * 0.9)))
