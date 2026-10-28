"""Janela de edição de uma assinatura ou gasto fixo (aba Assinaturas/fixos).

Mesmo jeito do diálogo de lançamento: erros embaixo do campo, nada de caixa de mensagem.
Salvar chama `recorrentes.editar`: muda os próximos lançamentos; o que já aconteceu fica igual.
Quem abre escuta `gravado(mensagem)` para atualizar a tela.
"""

import sqlite3
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import lancamentos, recorrentes
from meu_cofre.lancamentos import METODOS, formatar_brl, para_centavos
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, Marcador, Segmentado, _repolir

LARGURA = 520
SAIDA, ENTRADA = "Saída", "Entrada"
METODO_POR_ROTULO = {rotulo: codigo for codigo, rotulo in METODOS.items()}


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


def _rotulo_erro() -> QLabel:
    """Linha de erro com altura reservada, para o formulário não pular quando o erro aparece."""
    rotulo = _rotulo("", "CampoErro")
    rotulo.setFixedHeight(22)
    rotulo.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    return rotulo


class DialogoRecorrente(QDialog):
    """Diálogo modal que edita uma recorrente. Emite `gravado(mensagem)` depois de salvar."""

    gravado = Signal(str)

    def __init__(
        self,
        conn: sqlite3.Connection,
        recorrente_id: int,
        parent: QWidget | None = None,
        hoje: date | None = None,
    ):
        super().__init__(parent)
        self.conn = conn
        self.recorrente_id = recorrente_id
        self._hoje = hoje or date.today()
        self._original = recorrentes.obter(conn, recorrente_id)  # ValueError se não existe

        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Editar assinatura ou fixo")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()
        self._ligar()
        self._carregar_listas()
        self._preencher()
        self._atualizar_metodo()
        self._ajustado = False
        self.descricao.setFocus()

    # --- Montagem ------------------------------------------------------------------
    def _montar(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        self._rolagem = QScrollArea()
        self._rolagem.setWidgetResizable(True)
        self._rolagem.setFrameShape(QFrame.Shape.NoFrame)
        self._rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._corpo = QWidget()
        self._corpo.setObjectName("DialogoCorpo")
        self._rolagem.setWidget(self._corpo)
        raiz.addWidget(self._rolagem, 1)

        m = 3 * tema.ESPACO
        form = QVBoxLayout(self._corpo)
        form.setContentsMargins(m, m, m, tema.ESPACO)
        form.setSpacing(0)

        form.addWidget(_rotulo("Editar assinatura ou fixo", "DialogoTitulo"))
        form.addSpacing(4)
        self.aviso = _rotulo("Muda a partir do próximo lançamento; o que já aconteceu fica igual.", "DialogoDetalhe")
        self.aviso.setWordWrap(True)
        form.addWidget(self.aviso)
        form.addSpacing(2 * tema.ESPACO)

        self.tipo = Segmentado([SAIDA, ENTRADA])
        form.addWidget(self.tipo, 0, Qt.AlignmentFlag.AlignLeft)
        form.addSpacing(tema.ESPACO + 4)

        form.addWidget(_rotulo("Valor (R$)", "CampoRotulo"))
        form.addSpacing(4)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        form.addWidget(self.valor)
        self.erro_valor = _rotulo_erro()
        form.addWidget(self.erro_valor)

        form.addWidget(_rotulo("O que é", "CampoRotulo"))
        form.addSpacing(4)
        self.descricao = QLineEdit()
        self.descricao.setPlaceholderText("Ex.: Streaming de vídeo")
        form.addWidget(self.descricao)
        self.erro_descricao = _rotulo_erro()
        form.addWidget(self.erro_descricao)

        form.addWidget(_rotulo("Categoria", "CampoRotulo"))
        form.addSpacing(4)
        self.categoria = CampoLista()
        self.categoria.setPlaceholderText("Escolha uma categoria")
        form.addWidget(self.categoria)
        form.addSpacing(tema.ESPACO + 4)

        # Método e, conforme ele, a conta ou o cartão
        form.addWidget(_rotulo("Método", "CampoRotulo"))
        form.addSpacing(4)
        self.metodo = Segmentado(list(METODOS.values()), METODOS["pix"])
        form.addWidget(self.metodo, 0, Qt.AlignmentFlag.AlignLeft)
        form.addSpacing(tema.ESPACO + 4)

        linha = QHBoxLayout()
        linha.setSpacing(2 * tema.ESPACO)
        coluna_dia = QVBoxLayout()
        coluna_dia.setContentsMargins(0, 0, 0, 0)
        coluna_dia.setSpacing(4)
        coluna_dia.addWidget(_rotulo("Dia do mês", "CampoRotulo"))
        self.dia = CampoLista()
        for d in range(1, 32):
            self.dia.addItem(f"Dia {d}", d)
        coluna_dia.addWidget(self.dia)
        bloco_dia = QWidget()
        bloco_dia.setLayout(coluna_dia)
        linha.addWidget(bloco_dia, 1, Qt.AlignmentFlag.AlignTop)
        coluna_onde = QVBoxLayout()
        coluna_onde.setContentsMargins(0, 0, 0, 0)
        coluna_onde.setSpacing(4)
        self.rotulo_onde = _rotulo("Conta", "CampoRotulo")
        coluna_onde.addWidget(self.rotulo_onde)
        self.conta = CampoLista()
        coluna_onde.addWidget(self.conta)
        self._bloco_onde = QWidget()
        self._bloco_onde.setLayout(coluna_onde)
        linha.addWidget(self._bloco_onde, 1, Qt.AlignmentFlag.AlignTop)
        form.addLayout(linha)

        self.info_destino = _rotulo("", "CampoInfo")
        self.info_destino.setWordWrap(True)
        self.info_destino.setMinimumHeight(24)
        self.info_destino.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        form.addSpacing(4)
        form.addWidget(self.info_destino)

        self.assinatura = Marcador("É assinatura")
        self.assinatura.setToolTip("Assinaturas e gastos fixos ficam juntos na aba; este marca qual é qual")
        form.addWidget(self.assinatura)
        form.addSpacing(tema.ESPACO)
        form.addStretch(1)

        self._barra = barra = QWidget()
        linha_botoes = QHBoxLayout(barra)
        linha_botoes.setContentsMargins(m, tema.ESPACO, m, m)
        linha_botoes.setSpacing(tema.ESPACO + 4)
        linha_botoes.addStretch(1)
        self.botao_cancelar = BotaoSecundario("Cancelar")
        self.botao_cancelar.setAutoDefault(False)
        self.botao_cancelar.setToolTip("Cancelar (Esc)")
        self.botao_salvar = BotaoPrimario("Salvar alterações")
        self.botao_salvar.setAutoDefault(True)
        self.botao_salvar.setDefault(True)
        self.botao_salvar.setToolTip("Salvar (Enter)")
        linha_botoes.addWidget(self.botao_cancelar)
        linha_botoes.addWidget(self.botao_salvar)
        raiz.addWidget(barra)

    def _ligar(self) -> None:
        self.tipo.escolhido.connect(lambda _t: self._recarregar_categorias())
        self.metodo.escolhido.connect(lambda _m: self._atualizar_metodo())
        self.valor.textChanged.connect(lambda _t: self._limpar_erro(self.erro_valor, self.valor))
        self.descricao.textChanged.connect(lambda _t: self._limpar_erro(self.erro_descricao, self.descricao))
        self.botao_cancelar.clicked.connect(self.reject)
        self.botao_salvar.clicked.connect(self.salvar)

    # --- Dados dos combos -------------------------------------------------------------
    @property
    def entrada(self) -> bool:
        return self.tipo.atual() == ENTRADA

    @property
    def codigo_metodo(self) -> str:
        return METODO_POR_ROTULO[self.metodo.atual()]

    def _carregar_listas(self) -> None:
        original = self._original
        self._correntes = [(c["id"], c["nome"]) for c in lancamentos.listar_contas(self.conn) if c["tipo"] == "corrente"]
        self._cartoes = [(c["id"], c["nome"]) for c in lancamentos.cartoes_ativos(self.conn)]
        # A conta já escolhida continua na lista mesmo que tenha sido desativada.
        todas = {i for i, _ in self._correntes + self._cartoes}
        if original["conta_id"] not in todas:
            lista = self._cartoes if original["metodo"] == "cartao" else self._correntes
            lista.append((original["conta_id"], f"{original['conta']} (desativada)"))
        self.metodo.habilitar(METODOS["cartao"], bool(self._cartoes), "Nenhum cartão ativo. Adicione um em Contas.")

    def _recarregar_categorias(self) -> None:
        tipo = "receita" if self.entrada else "despesa"
        anterior = self.categoria.currentData()
        self.categoria.clear()
        for c in lancamentos.listar_categorias(self.conn, tipo):
            self.categoria.addItem(c["nome"], c["id"])
        original = self._original
        if original["categoria_id"] and self.categoria.findData(original["categoria_id"]) < 0 and (
            (original["valor_centavos"] > 0) == self.entrada
        ):
            self.categoria.addItem(original["categoria"], original["categoria_id"])
        indice = self.categoria.findData(anterior) if anterior else -1
        self.categoria.setCurrentIndex(indice)

    def _preencher(self) -> None:
        r = self._original
        self.tipo.definir(ENTRADA if r["valor_centavos"] > 0 else SAIDA)
        self._recarregar_categorias()
        self.categoria.setCurrentIndex(self.categoria.findData(r["categoria_id"]) if r["categoria_id"] else -1)
        self.valor.setText(formatar_brl(abs(r["valor_centavos"])).removeprefix("R$ ").replace(".", ""))
        self.descricao.setText(r["descricao"])
        if r["metodo"] in METODOS:
            self.metodo.definir(METODOS[r["metodo"]])
        self.dia.setCurrentIndex(self.dia.findData(r["dia_do_mes"]))
        self.assinatura.setChecked(bool(r["assinatura"]))

    def _atualizar_metodo(self) -> None:
        """Mostra a conta (pix, débito, boleto) ou o cartão; dinheiro não pergunta nada."""
        metodo = self.codigo_metodo
        opcoes = self._cartoes if metodo == "cartao" else self._correntes
        escolhida = self.conta.currentData()
        self.conta.clear()
        for id_, nome in opcoes:
            self.conta.addItem(nome, id_)
        alvo = self._original["conta_id"] if escolhida is None else escolhida
        indice = self.conta.findData(alvo)
        if indice < 0 and metodo != "cartao":
            indice = max(self.conta.findText("Inter"), 0)
        self.conta.setCurrentIndex(max(indice, 0))
        self.rotulo_onde.setText("Qual cartão" if metodo == "cartao" else "Conta")
        self._bloco_onde.setVisible(metodo != "dinheiro")
        if metodo == "dinheiro":
            self.info_destino.setText("Entra na Carteira" if self.entrada else "Sai da Carteira")
        else:
            self.info_destino.setText("")

    # --- Erros inline --------------------------------------------------------------------
    def _mostrar_erro(self, rotulo: QLabel, campo: QLineEdit, texto: str) -> None:
        rotulo.setText(f"!  {texto}")
        campo.setProperty("erro", True)
        _repolir(campo)

    def _limpar_erro(self, rotulo: QLabel, campo: QLineEdit) -> None:
        if rotulo.text():
            rotulo.setText("")
            campo.setProperty("erro", False)
            _repolir(campo)

    def _validar(self) -> tuple[int, str] | None:
        """Devolve (centavos, descrição) ou mostra os erros e leva o foco ao primeiro campo ruim."""
        centavos, descricao = 0, self.descricao.text().strip()
        primeiro: QLineEdit | None = None

        texto = self.valor.text().strip()
        mensagem = ""
        if not texto:
            mensagem = "Digite o valor, como 39,90."
        else:
            try:
                centavos = para_centavos(texto)
            except ValueError:
                mensagem = "Valor inválido. Use só números, como 39,90."
            else:
                if centavos <= 0:
                    mensagem = "O valor precisa ser maior que zero."
        if mensagem:
            self._mostrar_erro(self.erro_valor, self.valor, mensagem)
            primeiro = self.valor

        if not descricao:
            self._mostrar_erro(self.erro_descricao, self.descricao, "Diga o que é.")
            primeiro = primeiro or self.descricao

        if primeiro:
            primeiro.setFocus()
            primeiro.selectAll()
            return None
        return centavos, descricao

    # --- Gravar -----------------------------------------------------------------------------
    def salvar(self) -> bool:
        """Grava e fecha. Devolve False (sem gravar) se houver erro."""
        validos = self._validar()
        if validos is None:
            return False
        centavos, descricao = validos
        try:
            recorrentes.editar(
                self.conn,
                self.recorrente_id,
                self._hoje,
                descricao=descricao,
                valor_centavos=centavos if self.entrada else -centavos,
                conta_id=self.conta.currentData() or self._original["conta_id"],
                categoria_id=self.categoria.currentData(),
                metodo=self.codigo_metodo,
                dia_do_mes=self.dia.currentData(),
                assinatura=self.assinatura.isChecked(),
            )
        except ValueError as erro:  # regra de negócio que a tela não previu
            self._mostrar_erro(self.erro_valor, self.valor, str(erro))
            return False
        self.gravado.emit(f"Salvo: {descricao} ({formatar_brl(centavos)}), todo dia {self.dia.currentData()}")
        self.accept()
        return True

    # --- Tamanho -------------------------------------------------------------------------------
    def showEvent(self, evento) -> None:
        if not self._ajustado:  # só depois do estilo aplicado os tamanhos dos campos são os de verdade
            self._ajustado = True
            tela = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            self._corpo.layout().activate()
            natural = self._corpo.sizeHint().height() + self._barra.sizeHint().height() + 4
            self.setMinimumHeight(0)
            self.resize(LARGURA, min(natural, int(tela.height() * 0.9)))
        super().showEvent(evento)
