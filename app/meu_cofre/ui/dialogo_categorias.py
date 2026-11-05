"""Janela "Categorias": criar, renomear, mudar a natureza, desativar e reativar (docs/design/telas.md).

Duas seções, Despesas e Receitas, e a seção Regras (contém/regex -> categoria, prioridade; #3). A natureza (essencial, flexível, supérfluo) só existe nas despesas e alimenta a
reserva de emergência e os relatórios. Tudo é gravado na hora e `alterado(mensagem)` avisa a janela principal.
Desativar só tira a categoria das escolhas: os lançamentos antigos continuam com ela. Erros ficam na própria janela.
"""

import sqlite3

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from meu_cofre import cadastros, lancamentos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, Cartao, Pilula, _repolir
from meu_cofre.ui.formulario_regra import FormularioRegra
from meu_cofre.ui.dialogo_contas import LARGURA, _compacto, _esvaziar, _legenda, _rotulo, _separador

SECOES = {"despesa": "Despesas", "receita": "Receitas"}
NATUREZAS = {"essencial": "Essencial", "flexivel": "Flexível", "superfluo": "Supérfluo", None: "Sem natureza"}


def _combo_natureza(atual: str | None) -> CampoLista:
    combo = CampoLista()
    for codigo, rotulo in NATUREZAS.items():
        combo.addItem(rotulo, codigo)
    combo.setCurrentIndex(combo.findData(atual))
    combo.setFixedWidth(150)
    return combo


class DialogoCategorias(QDialog):
    """Diálogo modal de categorias. Emite `alterado(mensagem)` depois de cada mudança gravada."""

    alterado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._acao: tuple[str, int] | None = None  # ("renomear" | "desativar", id da categoria)
        self._adicionando: str | None = None  # tipo da seção com o formulário "Nova categoria" aberto
        self._mostrar_desativadas: set[str] = set()
        self._listas: dict[str, QVBoxLayout] = {}
        self._botoes_desativadas: dict[str, QPushButton] = {}
        self._botoes_adicionar: dict[str, QPushButton] = {}
        self._formularios: dict[str, QFrame] = {}
        self.novo_nome: dict[str, QLineEdit] = {}
        self.nova_natureza: CampoLista | None = None  # só nas despesas
        self.erro_novo: dict[str, QLabel] = {}
        self.formulario_regra: FormularioRegra | None = None  # aberto por "+ Nova regra" ou "Editar"
        self.oferta_regra: QWidget | None = None  # "Aplicar a N lançamentos..." depois de salvar uma regra
        self.texto_oferta: QLabel | None = None

        self.setObjectName("Dialogo")
        self.setProperty("tela", "contas")  # mesmo fundo da janela de contas
        self.setModal(True)
        self.setWindowTitle("Categorias")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()
        self._carregar()
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
        self._rolagem = rolagem

        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(self._corpo)
        coluna.setContentsMargins(m, m, m, tema.ESPACO)
        coluna.setSpacing(2 * tema.ESPACO)
        coluna.addWidget(_rotulo("Categorias", "DialogoTitulo"))
        dicas = {
            "despesa": "A natureza alimenta a reserva de emergência e os relatórios",
            "receita": "Categoria que não serve mais? Desative: o histórico fica",
        }
        for tipo, titulo in SECOES.items():
            coluna.addWidget(self._montar_secao(tipo, titulo, dicas[tipo]))
        coluna.addWidget(self._montar_regras())
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

    def _montar_secao(self, tipo: str, titulo: str, dica: str) -> Cartao:
        adicionar = _compacto(BotaoSecundario("+ Nova categoria"))
        adicionar.clicked.connect(lambda _c=False, t=tipo: self.iniciar_adicao(t))
        self._botoes_adicionar[tipo] = adicionar
        cartao = Cartao(titulo, dica, acao=adicionar)
        self._listas[tipo] = QVBoxLayout()
        self._listas[tipo].setSpacing(0)
        cartao.corpo.addLayout(self._listas[tipo])
        desativadas = QPushButton()
        desativadas.setObjectName("BotaoLink")
        desativadas.setCursor(Qt.CursorShape.PointingHandCursor)
        desativadas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        desativadas.clicked.connect(lambda _c=False, t=tipo: self.mostrar_desativadas(t, t not in self._mostrar_desativadas))
        self._botoes_desativadas[tipo] = desativadas
        cartao.corpo.addWidget(desativadas, 0, Qt.AlignmentFlag.AlignLeft)
        self._formularios[tipo] = self._montar_formulario(tipo)
        cartao.corpo.addWidget(self._formularios[tipo])
        return cartao

    def _montar_regras(self) -> Cartao:
        nova = _compacto(BotaoSecundario("+ Nova regra"))
        nova.clicked.connect(lambda _c=False: self.iniciar_regra())
        self.botao_nova_regra = nova
        cartao = Cartao("Regras", "Descrição que casa vira a categoria sozinha; menor prioridade vence", acao=nova)
        self._lista_regras = QVBoxLayout()
        self._lista_regras.setSpacing(0)
        cartao.corpo.addLayout(self._lista_regras)
        self._area_regra = QVBoxLayout()
        self._area_regra.setSpacing(tema.ESPACO)
        cartao.corpo.addLayout(self._area_regra)
        return cartao

    def _montar_formulario(self, tipo: str) -> QFrame:
        """Mini-formulário "Nova categoria" (escondido até clicar em + Nova categoria)."""
        quadro = QFrame()
        quadro.setObjectName("FormCartao")
        coluna = QVBoxLayout(quadro)
        p = 2 * tema.ESPACO
        coluna.setContentsMargins(p, p, p, p)
        coluna.setSpacing(4)
        coluna.addWidget(_legenda("Nova categoria"))
        nome = QLineEdit()
        nome.setPlaceholderText("Ex.: Pets" if tipo == "despesa" else "Ex.: Freelance")
        self.novo_nome[tipo] = nome
        campos = QHBoxLayout()
        campos.setSpacing(tema.ESPACO + 4)
        campos.addWidget(nome, 1)
        if tipo == "despesa":
            self.nova_natureza = _combo_natureza("flexivel")
            campos.addWidget(self.nova_natureza)
        coluna.addLayout(campos)
        erro = _rotulo("", "CampoErro")
        erro.setFixedHeight(22)
        self.erro_novo[tipo] = erro
        coluna.addWidget(erro)
        botoes = QHBoxLayout()
        botoes.setSpacing(tema.ESPACO)
        botoes.addStretch(1)
        cancelar = _compacto(BotaoSecundario("Cancelar"))
        cancelar.clicked.connect(self.cancelar_adicao)
        salvar = BotaoPrimario("Adicionar")
        salvar.setProperty("compacto", True)
        salvar.setAutoDefault(False)
        salvar.clicked.connect(lambda _c=False, t=tipo: self.adicionar_categoria(t))
        botoes.addWidget(cancelar)
        botoes.addWidget(salvar)
        coluna.addLayout(botoes)
        nome.textChanged.connect(lambda _t, t=tipo: self._limpar_erro_novo(t))
        nome.returnPressed.connect(lambda t=tipo: self.adicionar_categoria(t))
        quadro.setVisible(False)
        return quadro

    # --- Listas --------------------------------------------------------------------------
    def _carregar(self) -> None:
        for tipo, lista in self._listas.items():
            _esvaziar(lista)
            todas = cadastros.categorias_por_tipo(self.conn, tipo)
            desativadas = [c for c in todas if not c["ativa"]]
            linhas = [c for c in todas if c["ativa"]] + (desativadas if tipo in self._mostrar_desativadas else [])
            for i, categoria in enumerate(linhas):
                if i:
                    lista.addWidget(_separador())
                lista.addWidget(self._linha(categoria))
            self._botoes_desativadas[tipo].setVisible(bool(desativadas))
            self._botoes_desativadas[tipo].setText(
                "Esconder categorias desativadas"
                if tipo in self._mostrar_desativadas
                else f"Mostrar categorias desativadas ({len(desativadas)})"
            )
            self._formularios[tipo].setVisible(self._adicionando == tipo)
            self._botoes_adicionar[tipo].setVisible(self._adicionando != tipo)
        self._carregar_regras()
        if self.isVisible():  # a lista mudou de tamanho: ajusta a janela depois que os novos widgets forem estilizados
            QTimer.singleShot(0, self._ajustar_altura)
            QTimer.singleShot(80, self._ajustar_altura)

    def _carregar_regras(self) -> None:
        _esvaziar(self._lista_regras)
        regras = cadastros.listar_regras(self.conn)
        for i, r in enumerate(regras):
            if i:
                self._lista_regras.addWidget(_separador())
            self._lista_regras.addWidget(self._linha_regra(r))
        if not regras:
            self._lista_regras.addWidget(_rotulo("Nenhuma regra ainda. Crie uma com + Nova regra.", "CampoInfo"))

    def _linha_regra(self, r: sqlite3.Row) -> QWidget:
        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO, 0, tema.ESPACO)
        caixa.setSpacing(2 * tema.ESPACO)
        nome = _rotulo(f"{cadastros.TIPOS_MATCH[r['tipo_match']]} '{r['padrao']}'  →  {r['categoria']}", "ContaNome")
        nome.setProperty("inativo", not r["categoria_ativa"])
        caixa.addWidget(nome)
        caixa.addWidget(Pilula(f"prioridade {r['prioridade']}"))
        if not r["categoria_ativa"]:
            caixa.addWidget(Pilula("categoria desativada", "atencao"))
        caixa.addStretch(1)
        editar = _compacto(BotaoSecundario("Editar"))
        editar.clicked.connect(lambda _c=False, i=r["id"]: self.iniciar_regra(i))
        excluir = _compacto(BotaoSecundario("Excluir"), perigo=True)
        excluir.clicked.connect(lambda _c=False, i=r["id"]: self.excluir_regra(i))
        caixa.addWidget(editar)
        caixa.addWidget(excluir)
        return linha

    def _linha(self, c: sqlite3.Row) -> QWidget:
        id_ = c["id"]
        if self._acao == ("desativar", id_):
            return self._linha_confirmar_desativar(c)
        if self._acao == ("renomear", id_):
            return self._linha_renomear(c)

        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO, 0, tema.ESPACO)
        caixa.setSpacing(2 * tema.ESPACO)
        nome = _rotulo(c["nome"], "ContaNome")
        nome.setProperty("inativo", not c["ativa"])
        caixa.addWidget(nome)
        if not c["ativa"]:
            caixa.addWidget(Pilula("desativada"))
        elif cadastros.categoria_do_sistema(c):
            caixa.addWidget(Pilula("do sistema"))
        caixa.addStretch(1)

        if not c["ativa"]:
            reativar = _compacto(BotaoSecundario("Reativar"))
            reativar.clicked.connect(lambda _c=False, i=id_: self.reativar(i))
            caixa.addWidget(reativar)
        elif not cadastros.categoria_do_sistema(c):
            if c["tipo"] == "despesa":
                combo = _combo_natureza(c["natureza"])
                combo.setToolTip("Essencial entra no cálculo da reserva de emergência")
                combo.activated.connect(lambda _i, i=id_, cb=combo: self.definir_natureza(i, cb.currentData()))
                caixa.addWidget(combo)
            renomear = _compacto(BotaoSecundario("Renomear"))
            renomear.clicked.connect(lambda _c=False, i=id_: self.pedir_renomear(i))
            desativar = _compacto(BotaoSecundario("Desativar"), perigo=True)
            desativar.clicked.connect(lambda _c=False, i=id_: self.pedir_desativar(i))
            caixa.addWidget(renomear)
            caixa.addWidget(desativar)
        return linha

    def _linha_confirmar_desativar(self, c: sqlite3.Row) -> QWidget:
        linha = QWidget()
        caixa = QHBoxLayout(linha)
        caixa.setContentsMargins(0, tema.ESPACO, 0, tema.ESPACO)
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
        coluna.setContentsMargins(0, tema.ESPACO, 0, tema.ESPACO)
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

    # --- Ações ------------------------------------------------------------------------------
    def _gravou(self, mensagem: str) -> None:
        self._acao = None
        self._carregar()
        self.alterado.emit(mensagem)

    def _nome(self, categoria_id: int) -> str:
        return cadastros.obter_categoria(self.conn, categoria_id)["nome"]

    def mostrar_desativadas(self, tipo: str, mostrar: bool) -> None:
        (self._mostrar_desativadas.add if mostrar else self._mostrar_desativadas.discard)(tipo)
        self._carregar()

    def definir_natureza(self, categoria_id: int, natureza: str | None) -> None:
        nome = self._nome(categoria_id)
        cadastros.definir_natureza(self.conn, categoria_id, natureza)
        self._gravou(f"Natureza de {nome}: {NATUREZAS[natureza].lower()}")

    def pedir_desativar(self, categoria_id: int) -> None:
        self._acao = ("desativar", categoria_id)
        self._carregar()

    def confirmar_desativar(self) -> None:
        if not self._acao or self._acao[0] != "desativar":
            return
        nome = self._nome(self._acao[1])
        cadastros.desativar_categoria(self.conn, self._acao[1])
        self._gravou(f"Categoria desativada: {nome}")

    def reativar(self, categoria_id: int) -> None:
        nome = self._nome(categoria_id)
        cadastros.reativar_categoria(self.conn, categoria_id)
        self._gravou(f"Categoria reativada: {nome}")

    def pedir_renomear(self, categoria_id: int) -> None:
        self._acao = ("renomear", categoria_id)
        self._carregar()

    def salvar_renome(self) -> bool:
        if not self._acao or self._acao[0] != "renomear":
            return False
        try:
            cadastros.renomear_categoria(self.conn, self._acao[1], self.campo_renomear.text())
        except ValueError as erro:
            self.erro_renomear.setText(f"!  {erro}")
            self.campo_renomear.setProperty("erro", True)
            _repolir(self.campo_renomear)
            return False
        self._gravou(f"Categoria renomeada: {self.campo_renomear.text().strip()}")
        return True

    def _limpar_erro_renomear(self) -> None:
        if self.erro_renomear.text():
            self.erro_renomear.setText("")
            self.campo_renomear.setProperty("erro", False)
            _repolir(self.campo_renomear)

    def cancelar_acao(self) -> None:
        self._acao = None
        self._carregar()

    # --- Nova categoria ---------------------------------------------------------------------
    def iniciar_adicao(self, tipo: str) -> None:
        self._acao = None
        self._adicionando = tipo
        self.novo_nome[tipo].clear()
        if self.nova_natureza is not None:
            self.nova_natureza.setCurrentIndex(self.nova_natureza.findData("flexivel"))
        self._limpar_erro_novo(tipo)
        self._carregar()
        self.novo_nome[tipo].setFocus()

    def cancelar_adicao(self) -> None:
        self._adicionando = None
        self._carregar()

    def adicionar_categoria(self, tipo: str) -> bool:
        natureza = self.nova_natureza.currentData() if tipo == "despesa" else None
        try:
            cadastros.criar_categoria(self.conn, self.novo_nome[tipo].text(), tipo, natureza)
        except ValueError as erro:
            self.erro_novo[tipo].setText(f"!  {erro}")
            self.novo_nome[tipo].setProperty("erro", True)
            _repolir(self.novo_nome[tipo])
            self.novo_nome[tipo].setFocus()
            return False
        nome = self.novo_nome[tipo].text().strip()
        self._adicionando = None
        self._gravou(f"Categoria criada: {nome}")
        return True

    def _limpar_erro_novo(self, tipo: str) -> None:
        self.erro_novo[tipo].setText("")
        self.novo_nome[tipo].setProperty("erro", False)
        _repolir(self.novo_nome[tipo])

    # --- Regras -----------------------------------------------------------------------------
    def _fechar_area_regra(self) -> None:
        _esvaziar(self._area_regra)
        self.formulario_regra = self.oferta_regra = self.texto_oferta = None

    def iniciar_regra(self, regra_id: int | None = None) -> None:
        """Abre o formulário de regra (nova, ou a `regra_id` pra editar)."""
        self._fechar_area_regra()
        regra = next((r for r in cadastros.listar_regras(self.conn) if r["id"] == regra_id), None)
        self.formulario_regra = FormularioRegra(self.conn)
        self.formulario_regra.salvo.connect(self._regra_salva)
        self.formulario_regra.cancelado.connect(self._fechar_area_regra)
        self.formulario_regra.abrir(regra)
        self._area_regra.addWidget(self.formulario_regra)
        self.botao_nova_regra.setVisible(False)
        self._depois_de_mudar_regras(rolar=True)

    def _regra_salva(self, regra_id: int) -> None:
        editando = self.formulario_regra.regra_id is not None
        self._fechar_area_regra()
        self.botao_nova_regra.setVisible(True)
        n = lancamentos.contar_pendentes_da_regra(self.conn, regra_id)
        if n:
            self._mostrar_oferta(regra_id, n)
        self._carregar()
        self.alterado.emit("Regra atualizada" if editando else "Regra criada")

    def _mostrar_oferta(self, regra_id: int, n: int) -> None:
        self.oferta_regra = QWidget()
        caixa = QHBoxLayout(self.oferta_regra)
        caixa.setContentsMargins(0, 0, 0, 0)
        caixa.setSpacing(tema.ESPACO + 4)
        self.texto_oferta = _rotulo(f"Aplicar a {n} lançamentos sem categoria que casam?" if n != 1
                                    else "Aplicar a 1 lançamento sem categoria que casa?", "ConfirmaTexto")
        self.texto_oferta.setWordWrap(True)
        caixa.addWidget(self.texto_oferta, 1)
        agora_nao = _compacto(BotaoSecundario("Agora não"))
        agora_nao.clicked.connect(self._fechar_area_regra)
        self.botao_aplicar = BotaoPrimario("Aplicar")
        self.botao_aplicar.setProperty("compacto", True)
        self.botao_aplicar.setAutoDefault(False)
        self.botao_aplicar.clicked.connect(lambda _c=False: self.aplicar_aos_pendentes(regra_id))
        caixa.addWidget(agora_nao)
        caixa.addWidget(self.botao_aplicar)
        self._area_regra.addWidget(self.oferta_regra)

    def aplicar_aos_pendentes(self, regra_id: int) -> int:
        n = lancamentos.aplicar_regras_aos_pendentes(self.conn, regra_id)
        self._fechar_area_regra()
        self._carregar()
        self.alterado.emit(f"{n} lançamento{'s' if n != 1 else ''} categorizado{'s' if n != 1 else ''} pela regra")
        return n

    def excluir_regra(self, regra_id: int) -> None:
        cadastros.excluir_regra(self.conn, regra_id)
        self._fechar_area_regra()
        self._carregar()
        self.alterado.emit("Regra excluída")

    def _depois_de_mudar_regras(self, rolar: bool = False) -> None:
        if self.isVisible():
            QTimer.singleShot(0, self._ajustar_altura)
            QTimer.singleShot(80, self._ajustar_altura)
            if rolar:
                QTimer.singleShot(100, lambda: self.formulario_regra and self._rolagem.ensureWidgetVisible(self.formulario_regra, 0, 3 * tema.ESPACO))

    # --- Tamanho ----------------------------------------------------------------------------
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
        self._corpo.updateGeometry()
        self._rolagem.widget().resize(self._rolagem.viewport().width(), max(self._corpo.sizeHint().height(), self._rolagem.viewport().height()))
        if self._adicionando:  # formulário aberto: rola até ele
            self._rolagem.ensureWidgetVisible(self._formularios[self._adicionando], 0, 3 * tema.ESPACO)
