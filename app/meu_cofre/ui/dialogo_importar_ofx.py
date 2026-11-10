"""Janela "Importar extrato" (Contas, #14): escolher o OFX e a conta, ver a prévia e confirmar.

A prévia mostra o que entra (novas, com a categoria sugerida editável), o que bateu com um lançamento feito à mão
(conciliadas), o que já foi importado e as dúvidas (mais de um lançamento casa: o usuário escolhe, ou importa como
nova). O marcador "Acertar o saldo pelo extrato" ajusta o saldo inicial da conta pra que o saldo na data do extrato
bata com o do banco. Nada é gravado antes de "Importar". Erros ficam dentro da janela. Quem abre escuta `gravado(mensagem)`.
"""

import sqlite3
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from meu_cofre import cadastros, importar_ofx, lancamentos
from meu_cofre.importar_ofx import ImportacaoErro, Item, Previa
from meu_cofre.lancamentos import formatar_brl
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, Marcador, Pilula, RotuloElidido

LARGURA = 760
ALTURA = 720
LINHAS_VISIVEIS = 300  # novas com categoria editável; as demais entram com a sugerida
LINHAS_RESUMO = 8  # conciliadas listadas antes do "e mais N"
SEM_CATEGORIA = "A categorizar"


def _rotulo(texto: str, nome: str, quebra: bool = False) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    rotulo.setWordWrap(quebra)
    return rotulo


def _resumo(item: Item) -> str:
    t = item.transacao
    return f"{t.data:%d/%m/%Y} · {t.descricao} · {formatar_brl(t.valor_centavos)}"


class LinhaNova(QFrame):
    """Uma transação nova: data, descrição, valor e a categoria (sugerida, editável)."""

    def __init__(self, item: Item, categorias: list[tuple[str, int]], parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("AtivoCard")
        t = item.transacao
        self.categoria = CampoLista()
        self.categoria.setMinimumWidth(190)
        self.categoria.addItem(SEM_CATEGORIA, None)
        for nome, id_ in categorias:
            self.categoria.addItem(nome, id_)
        if item.categoria_id is not None:
            self.categoria.setCurrentIndex(max(0, self.categoria.findData(item.categoria_id)))
        self.categoria.currentIndexChanged.connect(lambda _i: setattr(self.item, "categoria_id", self.categoria.currentData()))
        linha = QHBoxLayout(self)
        m = tema.ESPACO + 4
        linha.setContentsMargins(m, tema.ESPACO, m, tema.ESPACO)
        linha.setSpacing(tema.ESPACO + 4)
        linha.addWidget(_rotulo(f"{t.data:%d/%m}", "ItemData"))
        self.descricao = RotuloElidido(t.descricao, "ItemDescricao")
        linha.addWidget(self.descricao, 1)
        linha.addWidget(_rotulo(formatar_brl(t.valor_centavos), "ItemValor"))
        linha.addWidget(self.categoria)


class LinhaDuvida(QFrame):
    """Transação que casa com mais de um lançamento feito à mão: escolher qual, ou importar como nova."""

    mudou = Signal()

    def __init__(self, item: Item, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("AtivoCard")
        self.escolha = CampoLista()
        for c in item.candidatos:
            valor = formatar_brl(c["valor_centavos"])
            if c["valor_centavos"] != item.transacao.valor_centavos:  # o extrato manda: o valor do lançamento será acertado
                valor += f" → {formatar_brl(item.transacao.valor_centavos)}"
            self.escolha.addItem(f"{c['data']:%d/%m} · {c['descricao']} · {valor}", c["id"])
        self.escolha.addItem("Importar como nova", None)
        self.escolha.setCurrentIndex(self.escolha.findData(item.lancamento_id) if item.lancamento_id else self.escolha.count() - 1)
        self.escolha.currentIndexChanged.connect(self._escolheu)
        coluna = QVBoxLayout(self)
        m = tema.ESPACO + 4
        coluna.setContentsMargins(m, tema.ESPACO, m, tema.ESPACO)
        coluna.setSpacing(4)
        self.descricao = RotuloElidido(_resumo(item), "ItemDescricao")
        coluna.addWidget(self.descricao)
        coluna.addWidget(self.escolha)

    def _escolheu(self, _i: int) -> None:
        self.item.lancamento_id = self.escolha.currentData()
        self.mudou.emit()


class LinhaAntes(QFrame):
    """Transação anterior ao primeiro lançamento da conta: já está no saldo inicial, só entra se marcada."""

    mudou = Signal()

    def __init__(self, item: Item, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("AtivoCard")
        self.marcador = Marcador("Importar mesmo assim")
        self.marcador.toggled.connect(self._marcou)
        coluna = QVBoxLayout(self)
        m = tema.ESPACO + 4
        coluna.setContentsMargins(m, tema.ESPACO, m, tema.ESPACO)
        coluna.setSpacing(4)
        self.descricao = RotuloElidido(_resumo(item), "ItemDescricao")
        coluna.addWidget(self.descricao)
        coluna.addWidget(self.marcador)

    def _marcou(self, marcado: bool) -> None:
        self.item.importar = marcado
        self.mudou.emit()


class DialogoImportarOfx(QDialog):
    """Diálogo modal. `carregar(caminho)` lê o OFX e monta a prévia; `confirmar()` grava (`Importar`)."""

    gravado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None, hoje: date | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje or date.today()
        self.extrato: importar_ofx.Extrato | None = None
        self.previa: Previa | None = None
        self.arquivo_caminho = ""
        self.linhas_novas: list[LinhaNova] = []
        self.linhas_duvidas: list[LinhaDuvida] = []
        self.linhas_antes: list[LinhaAntes] = []
        self._categorias: dict[str, list[tuple[str, int]]] = {
            tipo: [(c["nome"], c["id"]) for c in lancamentos.listar_categorias(conn, tipo) if c["nome"] != cadastros.CATEGORIA_SISTEMA]
            for tipo in ("receita", "despesa")
        }
        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Importar extrato")
        self.setFixedWidth(LARGURA)
        self.resize(LARGURA, ALTURA)
        self._montar()

    # --- Montagem ------------------------------------------------------------------
    def _montar(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)
        corpo = QWidget()
        corpo.setObjectName("DialogoCorpo")
        m = 3 * tema.ESPACO
        coluna = QVBoxLayout(corpo)
        coluna.setContentsMargins(m, m, m, tema.ESPACO)
        coluna.setSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo("Importar extrato", "DialogoTitulo"))
        coluna.addWidget(
            _rotulo(
                "Escolha o extrato da conta em formato OFX. O que você já lançou à mão é conferido, não duplicado, e "
                "importar o mesmo extrato de novo não repete nada.",
                "DialogoDetalhe",
                quebra=True,
            )
        )
        escolha = QHBoxLayout()
        escolha.setSpacing(tema.ESPACO + 4)
        self.botao_escolher = BotaoSecundario("Escolher arquivo…")
        self.botao_escolher.setAutoDefault(False)
        self.botao_escolher.clicked.connect(self.escolher_arquivo)
        self.arquivo = RotuloElidido("Nenhum arquivo escolhido", "DialogoDetalhe")
        escolha.addWidget(self.botao_escolher)
        escolha.addWidget(self.arquivo, 1)
        escolha.addWidget(_rotulo("Conta", "DialogoDetalhe"))
        self.conta = CampoLista()
        self.conta.setMinimumWidth(170)
        for c in cadastros.contas_por_tipo(self.conn, ("corrente",)):
            self.conta.addItem(c["nome"], c["id"])
        self.conta.currentIndexChanged.connect(lambda _i: self._prever())
        escolha.addWidget(self.conta)
        coluna.addLayout(escolha)
        self.erro = _rotulo("", "CampoErro", quebra=True)
        self.erro.setVisible(False)
        coluna.addWidget(self.erro)

        self._rolagem = QScrollArea()
        self._rolagem.setWidgetResizable(True)
        self._rolagem.setFrameShape(QFrame.Shape.NoFrame)
        self._rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._previa = QWidget()
        self._previa.setObjectName("DialogoCorpo")
        self._conteudo = QVBoxLayout(self._previa)
        self._conteudo.setContentsMargins(0, tema.ESPACO, 0, 0)
        self._conteudo.setSpacing(tema.ESPACO + 4)
        self._conteudo.addStretch(1)
        self._rolagem.setWidget(self._previa)
        coluna.addWidget(self._rolagem, 1)

        self.marcador_saldo = Marcador("")
        self.marcador_saldo.setChecked(True)
        self.marcador_saldo.toggled.connect(lambda _c: self._atualizar_rodape())
        self.detalhe_saldo = _rotulo("", "ItemDetalhe", quebra=True)
        coluna.addWidget(self.marcador_saldo)
        coluna.addWidget(self.detalhe_saldo)
        raiz.addWidget(corpo, 1)

        barra = QWidget()
        botoes = QHBoxLayout(barra)
        botoes.setContentsMargins(m, tema.ESPACO, m, m)
        botoes.setSpacing(tema.ESPACO + 4)
        botoes.addStretch(1)
        self.botao_cancelar = BotaoSecundario("Cancelar")
        self.botao_cancelar.setAutoDefault(False)
        self.botao_cancelar.clicked.connect(self.reject)
        self.botao_importar = BotaoPrimario("Importar")
        self.botao_importar.setAutoDefault(True)
        self.botao_importar.setDefault(True)
        self.botao_importar.setEnabled(False)
        self.botao_importar.clicked.connect(self.confirmar)
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_importar)
        raiz.addWidget(barra)
        self._atualizar_rodape()

    # --- Escolher e ler -------------------------------------------------------------
    def escolher_arquivo(self) -> None:
        pasta = Path.home() / "Downloads"
        caminho, _filtro = QFileDialog.getOpenFileName(
            self, "Extrato OFX", str(pasta if pasta.exists() else Path.home()), "Extratos OFX (*.ofx *.OFX)"
        )
        if caminho:
            self.carregar(caminho)

    def carregar(self, caminho: Path | str) -> bool:
        """Lê o OFX, escolhe a conta pelo banco e mostra a prévia. False (com o erro na janela) se o arquivo não serve."""
        self.arquivo_caminho = str(caminho)
        self.arquivo.setTexto(Path(caminho).name)
        try:
            self.extrato = importar_ofx.ler(caminho)
        except ImportacaoErro as erro:
            self.extrato = None
            self._mostrar_erro(str(erro))
            self._prever()
            return False
        sugerida = importar_ofx.conta_sugerida(self.conn, self.extrato)
        self.conta.blockSignals(True)
        if sugerida is not None:
            self.conta.setCurrentIndex(self.conta.findData(sugerida))
        self.conta.blockSignals(False)
        self._mostrar_erro("")
        self._prever()
        return True

    def _mostrar_erro(self, texto: str) -> None:
        self.erro.setText(f"!  {texto}" if texto else "")
        self.erro.setVisible(bool(texto))

    def _prever(self) -> None:
        self.previa = None
        if self.extrato is not None and self.conta.currentData() is not None:
            self.previa = importar_ofx.prever(
                self.conn, self.extrato, self.conta.currentData(), self._hoje, Path(self.arquivo_caminho).name
            )
        self._montar_previa()

    def _esvaziar(self) -> None:
        while self._conteudo.count():
            widget = self._conteudo.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.linhas_novas, self.linhas_duvidas, self.linhas_antes = [], [], []

    def _grupo(self, titulo: str, detalhe: str = "") -> tuple[QWidget, QVBoxLayout]:
        caixa = QWidget()
        coluna = QVBoxLayout(caixa)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(tema.ESPACO)
        coluna.addWidget(_rotulo(titulo.upper(), "GrupoNome"))
        if detalhe:
            coluna.addWidget(_rotulo(detalhe, "ItemDetalhe", quebra=True))
        return caixa, coluna

    def _montar_previa(self) -> None:
        self._esvaziar()
        p = self.previa
        if p is None:
            self._conteudo.addStretch(1)
            self._atualizar_rodape()
            return
        pilulas = QHBoxLayout()
        pilulas.setSpacing(tema.ESPACO)
        self.pilula_novas = Pilula(f"{len(p.novas)} novas", "bom" if p.novas else "neutra")
        self.pilula_conciliadas = Pilula(f"{len(p.conciliadas)} conciliadas", "feito" if p.conciliadas else "neutra")
        self.pilula_ja = Pilula(f"{len(p.ja_importadas)} já importadas")
        self.pilula_duvidas = Pilula(f"{len(p.duvidas)} dúvidas", "atencao" if p.duvidas else "neutra")
        for w in (self.pilula_novas, self.pilula_conciliadas, self.pilula_ja, self.pilula_duvidas):
            pilulas.addWidget(w)
        pilulas.addStretch(1)
        topo = QWidget()
        topo.setLayout(pilulas)
        self._conteudo.addWidget(topo)

        if p.duvidas:
            grupo, col = self._grupo("Dúvidas", "Um lançamento seu parece casar com estas transações (mais de um, ou investimento com centavos de diferença). Escolha qual é.")
            for item in p.duvidas:
                linha = LinhaDuvida(item)
                linha.mudou.connect(self._atualizar_rodape)
                self.linhas_duvidas.append(linha)
                col.addWidget(linha)
            self._conteudo.addWidget(grupo)
        if p.novas:
            grupo, col = self._grupo("Novas", "Confira a categoria sugerida. Pix e transferências ficam em A categorizar.")
            for item in p.novas[:LINHAS_VISIVEIS]:
                linha = LinhaNova(item, self._categorias["receita" if item.transacao.valor_centavos > 0 else "despesa"])
                self.linhas_novas.append(linha)
                col.addWidget(linha)
            if len(p.novas) > LINHAS_VISIVEIS:
                col.addWidget(_rotulo(f"e mais {len(p.novas) - LINHAS_VISIVEIS} com a categoria sugerida", "ItemDetalhe"))
            self._conteudo.addWidget(grupo)
        if p.antes:
            grupo, col = self._grupo("Antes do início do uso", "Antes do início do uso: já está no saldo inicial. Não são importadas, a menos que você marque.")
            for item in p.antes:
                linha = LinhaAntes(item)
                linha.mudou.connect(self._atualizar_rodape)
                self.linhas_antes.append(linha)
                col.addWidget(linha)
            self._conteudo.addWidget(grupo)
        if p.conciliadas:
            grupo, col = self._grupo("Conciliadas com o que você lançou", "Ficam marcadas como conferidas, sem duplicar.")
            for item in p.conciliadas[:LINHAS_RESUMO]:
                col.addWidget(_rotulo(_resumo(item), "ItemDetalhe", quebra=True))
            if len(p.conciliadas) > LINHAS_RESUMO:
                col.addWidget(_rotulo(f"e mais {len(p.conciliadas) - LINHAS_RESUMO}", "ItemDetalhe"))
            self._conteudo.addWidget(grupo)
        if not p.novas and not p.conciliadas and not p.duvidas:
            self._conteudo.addWidget(_rotulo("Não há nada novo pra importar neste extrato.", "DialogoDetalhe", quebra=True))
        self._conteudo.addStretch(1)
        self._atualizar_rodape()

    def _atualizar_rodape(self) -> None:
        """Marcador do saldo e botão "Importar N", conforme as escolhas atuais."""
        p = self.previa
        diferenca = importar_ofx.diferenca_saldo(self.conn, p) if p else None
        e = self.extrato
        tem_saldo = p is not None and diferenca is not None
        self.marcador_saldo.setVisible(tem_saldo)
        self.detalhe_saldo.setVisible(tem_saldo)
        if tem_saldo:
            self.marcador_saldo.setText(f"Acertar o saldo pelo extrato ({formatar_brl(e.saldo_centavos)} em {e.saldo_data:%d/%m})")
            self.detalhe_saldo.setText(
                f"O saldo inicial da conta {'muda' if diferenca else 'não muda'}"
                + (f" {formatar_brl(diferenca)}." if diferenca else ": já está batendo.")
            )
        acertar = tem_saldo and self.marcador_saldo.isChecked() and diferenca != 0
        gravar = (p.a_gravar + sum(1 for i in p.itens if i.destino == "conciliada")) if p else 0
        self.botao_importar.setEnabled(bool(gravar or acertar))
        self.botao_importar.setText(f"Importar {gravar}" if gravar else "Acertar o saldo" if acertar else "Importar")

    # --- Gravar -----------------------------------------------------------------------
    def confirmar(self) -> bool:
        """Grava a prévia e fecha. Devolve False (sem gravar) se não há o que fazer ou houve erro."""
        if self.previa is None or not self.botao_importar.isEnabled():
            return False
        try:
            r = importar_ofx.confirmar(self.conn, self.previa, ajustar_saldo=self.marcador_saldo.isVisible() and self.marcador_saldo.isChecked())
        except ImportacaoErro as erro:
            self._mostrar_erro(str(erro))
            return False
        partes = [f"{r['novas']} lançamentos importados"]
        if r["conciliadas"]:
            partes.append(f"{r['conciliadas']} conciliados")
        if r["diferenca_saldo"]:
            partes.append(f"saldo acertado ({formatar_brl(r['diferenca_saldo'])} no saldo inicial)")
        self.gravado.emit(" · ".join(partes))
        self.accept()
        return True
