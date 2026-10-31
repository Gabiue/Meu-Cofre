"""Janela "Importar da B3" (aba Investimentos, F9): escolher a planilha, ver a prévia e confirmar.

A prévia mostra o que entra (operações novas), o que já foi importado (duplicadas), o que a planilha tem e o app não
usa (ignoradas, com o motivo) e os ativos novos que serão criados, com o tipo (Ação ou FII) editável. Nada é gravado
antes de "Importar". Erros ficam dentro da janela. Quem abre escuta `gravado(mensagem)`.
"""

import sqlite3
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from meu_cofre import importar_b3
from meu_cofre.importar_b3 import AtivoNovo, ImportacaoErro, OperacaoB3, Previa
from meu_cofre.lancamentos import formatar_brl
from meu_cofre.ui import tema
from meu_cofre.ui.cartao_acao import formatar_quantidade
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, Pilula, RotuloElidido

LARGURA = 640
ALTURA = 680
LINHAS_VISIVEIS = 8  # operações novas listadas antes do "e mais N"
NOMES_TIPO = {"acao": "Ação", "fii": "FII"}
NOMES_OPERACAO = {"compra": "Compra", "venda": "Venda", "dividendo": "Dividendo", "jcp": "JCP", "rendimento": "Rendimento"}


def _rotulo(texto: str, nome: str, quebra: bool = False) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    rotulo.setWordWrap(quebra)
    return rotulo


def descrever_operacao(o: OperacaoB3) -> str:
    """"02/03/2026 · Compra · PETR4 · 100 × R$ 35,00 = R$ 3.500,00" (proventos: só o valor)."""
    base = f"{o.data:%d/%m/%Y} · {NOMES_OPERACAO[o.tipo]} · {o.ticker}"
    if o.tipo in ("compra", "venda"):
        return f"{base} · {formatar_quantidade(o.quantidade)} × {formatar_brl(o.preco_centavos)} = {formatar_brl(o.valor_centavos)}"
    return f"{base} · {formatar_brl(o.valor_centavos)}"


class LinhaAtivoNovo(QFrame):
    """Um ativo que será criado: ticker, nome e o tipo (Ação ou FII) editável."""

    def __init__(self, ativo: AtivoNovo, parent: QWidget | None = None):
        super().__init__(parent)
        self.ativo = ativo
        self.setObjectName("AtivoCard")
        self.ticker = _rotulo(ativo.ticker, "AtivoNome")
        self.nome = RotuloElidido(ativo.nome, "AtivoInstituicao")
        self.tipo = CampoLista()
        self.tipo.setMinimumWidth(120)
        for codigo, nome in NOMES_TIPO.items():
            self.tipo.addItem(nome, codigo)
        self.tipo.setCurrentIndex(self.tipo.findData(ativo.tipo))
        self.tipo.currentIndexChanged.connect(lambda _i: setattr(self.ativo, "tipo", self.tipo.currentData()))
        linha = QHBoxLayout(self)
        m = tema.ESPACO + 4
        linha.setContentsMargins(m, tema.ESPACO, m, tema.ESPACO)
        linha.setSpacing(tema.ESPACO + 4)
        linha.addWidget(self.ticker)
        linha.addWidget(self.nome, 1)
        linha.addWidget(self.tipo)


class DialogoImportarB3(QDialog):
    """Diálogo modal. `carregar(caminho)` lê a planilha e monta a prévia; `confirmar()` grava (`Importar`)."""

    gravado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None, hoje: date | None = None):
        super().__init__(parent)
        self.conn = conn
        self._hoje = hoje or date.today()
        self.previa: Previa | None = None
        self.linhas_ativos: list[LinhaAtivoNovo] = []
        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Importar da B3")
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
        coluna.addWidget(_rotulo("Importar da B3", "DialogoTitulo"))
        coluna.addWidget(
            _rotulo(
                "Escolha a planilha de movimentações da Área do Investidor da B3 (.xlsx). Compras, vendas e proventos de "
                "ações e FIIs entram só como histórico da carteira: nenhuma conta é alterada. Importar de novo a mesma "
                "planilha não duplica nada.",
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

    # --- Escolher e ler -------------------------------------------------------------
    def escolher_arquivo(self) -> None:
        pasta = Path.home() / "Downloads"
        caminho, _filtro = QFileDialog.getOpenFileName(
            self, "Planilha de movimentações da B3", str(pasta if pasta.exists() else Path.home()), "Planilhas (*.xlsx)"
        )
        if caminho:
            self.carregar(caminho)

    def carregar(self, caminho: Path | str) -> bool:
        """Lê a planilha e mostra a prévia. Devolve False (com o erro na janela) se o arquivo não serve."""
        self.arquivo.setTexto(Path(caminho).name)
        try:
            self.previa = importar_b3.prever(self.conn, caminho)
        except ImportacaoErro as erro:
            self.previa = None
            self._mostrar_erro(str(erro))
            self._montar_previa()
            return False
        self._mostrar_erro("")
        self._montar_previa()
        return True

    def _mostrar_erro(self, texto: str) -> None:
        self.erro.setText(f"!  {texto}" if texto else "")
        self.erro.setVisible(bool(texto))

    def _esvaziar(self) -> None:
        while self._conteudo.count():
            widget = self._conteudo.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.linhas_ativos = []

    def _secao(self, titulo: str, linhas: list[str], extra: str = "") -> QWidget:
        caixa = QWidget()
        coluna = QVBoxLayout(caixa)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(4)
        coluna.addWidget(_rotulo(titulo.upper(), "GrupoNome"))
        for texto in linhas:
            coluna.addWidget(_rotulo(texto, "ItemDetalhe", quebra=True))
        if extra:
            coluna.addWidget(_rotulo(extra, "ItemDetalhe"))
        return caixa

    def _montar_previa(self) -> None:
        self._esvaziar()
        p = self.previa
        if p is None:
            self._conteudo.addStretch(1)
            self.botao_importar.setEnabled(False)
            self.botao_importar.setText("Importar")
            return
        pilulas = QHBoxLayout()
        pilulas.setSpacing(tema.ESPACO)
        self.pilula_novas = Pilula(f"{len(p.novas)} novas", "bom" if p.novas else "neutra")
        self.pilula_duplicadas = Pilula(f"{len(p.duplicadas)} já importadas")
        self.pilula_ignoradas = Pilula(f"{len(p.ignoradas)} ignoradas", "atencao" if p.ignoradas else "neutra")
        for w in (self.pilula_novas, self.pilula_duplicadas, self.pilula_ignoradas):
            pilulas.addWidget(w)
        pilulas.addStretch(1)
        topo = QWidget()
        topo.setLayout(pilulas)
        self._conteudo.addWidget(topo)

        if p.ativos_novos:
            grupo = QWidget()
            col = QVBoxLayout(grupo)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(tema.ESPACO)
            col.addWidget(_rotulo("ATIVOS QUE SERÃO CRIADOS", "GrupoNome"))
            col.addWidget(_rotulo("Confira o tipo: units de ações também terminam em 11.", "ItemDetalhe"))
            for ativo in p.ativos_novos:
                linha = LinhaAtivoNovo(ativo)
                self.linhas_ativos.append(linha)
                col.addWidget(linha)
            self._conteudo.addWidget(grupo)
        if p.novas:
            mostradas = [descrever_operacao(o) for o in p.novas[:LINHAS_VISIVEIS]]
            resto = len(p.novas) - LINHAS_VISIVEIS
            self._conteudo.addWidget(self._secao("Operações novas", mostradas, f"e mais {resto}" if resto > 0 else ""))
        if p.ignoradas:
            self._conteudo.addWidget(
                self._secao("Ignoradas", [f"Linha {i.linha}: {i.motivo}" for i in p.ignoradas])
            )
        if not p.novas:
            self._conteudo.addWidget(
                _rotulo("Não há nada novo pra importar nesta planilha.", "DialogoDetalhe", quebra=True)
            )
        self._conteudo.addStretch(1)
        self.botao_importar.setEnabled(bool(p.novas))
        self.botao_importar.setText(f"Importar {len(p.novas)} operações" if len(p.novas) != 1 else "Importar 1 operação")

    # --- Gravar -----------------------------------------------------------------------
    def confirmar(self) -> bool:
        """Grava a prévia e fecha. Devolve False (sem gravar) se não há o que gravar ou houve erro."""
        if self.previa is None or not self.previa.novas:
            return False
        try:
            r = importar_b3.confirmar(self.conn, self.previa, self._hoje)
        except ImportacaoErro as erro:
            self._mostrar_erro(str(erro))
            return False
        partes = [f"{r['operacoes']} operações importadas da B3"]
        if r["ativos"]:
            partes.append(f"{r['ativos']} ativos criados")
        self.gravado.emit(" · ".join(partes))
        self.accept()
        return True
