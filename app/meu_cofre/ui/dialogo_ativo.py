"""Janela de novo investimento ou de edição (aba Investimentos).

Tipo (Renda fixa | Tesouro Direto), nome, instituição, indexador (CDI, Selic, IPCA+, prefixado) e taxa, com o
rótulo da taxa mudando conforme o indexador, e vencimento opcional. Embaixo do campo da taxa, como o investimento
vai aparecer na lista. Erros ficam embaixo do campo, nada de caixa de mensagem.
Nas caixinhas das metas só o indexador e a taxa são editáveis. Quem abre escuta `gravado(mensagem)`.
"""

import math
import sqlite3
from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
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

from meu_cofre import investimentos
from meu_cofre.lancamentos import somar_meses
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    CampoData,
    CampoLista,
    Marcador,
    Segmentado,
    _repolir,
)

LARGURA = 520
RENDA_FIXA, TESOURO = "Renda fixa", "Tesouro Direto"
TIPOS = {RENDA_FIXA: "renda_fixa", TESOURO: "tesouro"}
INDEXADORES = {
    "cdi": ("% do CDI", "% do CDI (ex.: 110)", "110"),
    "selic": ("% da Selic", "% da Selic (ex.: 100)", "100"),
    "ipca": ("IPCA +", "Taxa acima do IPCA, % ao ano (ex.: 6,2)", "6,2"),
    "pre": ("Prefixado", "Taxa prefixada, % ao ano (ex.: 12,5)", "12,5"),
}
INDEXADORES_DO_TIPO = {"renda_fixa": ("cdi", "selic", "ipca", "pre"), "tesouro": ("selic", "ipca", "pre")}
EXEMPLOS_DE_NOME = {"renda_fixa": "Ex.: CDB do Banco Inter", "tesouro": "Ex.: Tesouro Selic 2029"}


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


def ler_taxa(texto: str) -> float | None:
    """"110", "6,2", "12.5%" -> número; None se vazio ou inválido."""
    limpo = texto.strip().replace("%", "").replace(",", ".").strip()
    try:
        taxa = float(limpo)
    except ValueError:
        return None
    return taxa if math.isfinite(taxa) else None


def texto_taxa(taxa: float) -> str:
    """6.2 -> "6,2"; 110.0 -> "110" (pra caixa de texto)."""
    return f"{taxa:.4f}".rstrip("0").rstrip(".").replace(".", ",")


class DialogoAtivo(QDialog):
    """Diálogo modal de novo investimento (`ativo_id=None`) ou de edição. Emite `gravado(mensagem)` ao salvar."""

    gravado = Signal(str)

    def __init__(self, conn: sqlite3.Connection, ativo_id: int | None = None, parent: QWidget | None = None, hoje: date | None = None):
        super().__init__(parent)
        self.conn = conn
        self.ativo_id = ativo_id
        self._hoje = hoje or date.today()
        self._original = investimentos.obter(conn, ativo_id) if ativo_id is not None else None  # ValueError se não existe
        self._caixinhas = bool(self._original and self._original.caixinhas)

        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Novo investimento" if ativo_id is None else "Editar investimento")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()
        self._ligar()
        self._preencher()
        self._ajustado = False
        (self.taxa if self._caixinhas else self.nome).setFocus()

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

        titulo = "Novo investimento" if self.ativo_id is None else ("Taxa das caixinhas" if self._caixinhas else "Editar investimento")
        self.titulo = _rotulo(titulo, "DialogoTitulo")
        form.addWidget(self.titulo)
        form.addSpacing(4)
        aviso = (
            "As caixinhas das metas rendem pelo indexador e taxa abaixo. O dinheiro delas se move na aba Metas."
            if self._caixinhas
            else "Renda fixa e Tesouro Direto. O rendimento é estimado pelas taxas do Banco Central, bruto, sem IR."
        )
        self.aviso = _rotulo(aviso, "DialogoDetalhe")
        self.aviso.setWordWrap(True)
        form.addWidget(self.aviso)
        form.addSpacing(2 * tema.ESPACO)

        # Campos que as caixinhas não editam ficam juntos, num bloco que some.
        self._bloco_dados = QWidget()
        dados = QVBoxLayout(self._bloco_dados)
        dados.setContentsMargins(0, 0, 0, 0)
        dados.setSpacing(0)
        dados.addWidget(_rotulo("Tipo", "CampoRotulo"))
        dados.addSpacing(4)
        self.tipo = Segmentado(list(TIPOS))
        dados.addWidget(self.tipo, 0, Qt.AlignmentFlag.AlignLeft)
        dados.addSpacing(tema.ESPACO + 4)
        dados.addWidget(_rotulo("Nome", "CampoRotulo"))
        dados.addSpacing(4)
        self.nome = QLineEdit()
        dados.addWidget(self.nome)
        self.erro_nome = _rotulo_erro()
        dados.addWidget(self.erro_nome)
        dados.addWidget(_rotulo("Instituição (opcional)", "CampoRotulo"))
        dados.addSpacing(4)
        self.instituicao = QLineEdit()
        self.instituicao.setPlaceholderText("Ex.: Inter, Nubank, Tesouro Direto")
        dados.addWidget(self.instituicao)
        dados.addSpacing(tema.ESPACO + 6)
        form.addWidget(self._bloco_dados)

        form.addWidget(_rotulo("Indexador", "CampoRotulo"))
        form.addSpacing(4)
        self.indexador = CampoLista()
        form.addWidget(self.indexador)
        form.addSpacing(tema.ESPACO + 6)

        self.rotulo_taxa = _rotulo("", "CampoRotulo")
        form.addWidget(self.rotulo_taxa)
        form.addSpacing(4)
        self.taxa = QLineEdit()
        self.taxa.setObjectName("CampoValor")
        form.addWidget(self.taxa)
        self.erro_taxa = _rotulo_erro()
        form.addWidget(self.erro_taxa)
        self.previa = _rotulo("", "CampoInfo")
        form.addWidget(self.previa)
        form.addSpacing(tema.ESPACO + 4)

        self._bloco_vencimento = QWidget()
        venc = QVBoxLayout(self._bloco_vencimento)
        venc.setContentsMargins(0, 0, 0, 0)
        venc.setSpacing(6)
        self.tem_vencimento = Marcador("Tem data de vencimento")
        venc.addWidget(self.tem_vencimento)
        self.vencimento = CampoData()
        venc.addWidget(self.vencimento)
        self.erro_vencimento = _rotulo_erro()
        venc.addWidget(self.erro_vencimento)
        form.addWidget(self._bloco_vencimento)
        form.addStretch(1)

        self._barra = QWidget()
        botoes = QHBoxLayout(self._barra)
        botoes.setContentsMargins(m, tema.ESPACO, m, m)
        botoes.setSpacing(tema.ESPACO + 4)
        botoes.addStretch(1)
        self.botao_cancelar = BotaoSecundario("Cancelar")
        self.botao_cancelar.setAutoDefault(False)
        self.botao_cancelar.setToolTip("Cancelar (Esc)")
        self.botao_salvar = BotaoPrimario("Criar investimento" if self.ativo_id is None else "Salvar alterações")
        self.botao_salvar.setAutoDefault(True)
        self.botao_salvar.setDefault(True)
        self.botao_salvar.setToolTip("Salvar (Enter)")
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_salvar)
        raiz.addWidget(self._barra)

        self._bloco_dados.setVisible(not self._caixinhas)
        self._bloco_vencimento.setVisible(not self._caixinhas)

    def _ligar(self) -> None:
        self.tipo.escolhido.connect(lambda _t: self._tipo_mudou())
        self.indexador.currentIndexChanged.connect(lambda _i: self._indexador_mudou())
        self.nome.textChanged.connect(lambda _t: self._limpar_erro(self.erro_nome, self.nome))
        self.taxa.textChanged.connect(lambda _t: self._taxa_mudou())
        self.tem_vencimento.toggled.connect(lambda ligado: self._vencimento_ligado(ligado))
        self.vencimento.dateChanged.connect(lambda _d: self.erro_vencimento.setText(""))
        self.botao_cancelar.clicked.connect(self.reject)
        self.botao_salvar.clicked.connect(self.salvar)

    def _preencher(self) -> None:
        a = self._original
        tipo = a.tipo if a is not None else "renda_fixa"
        self.tipo.definir(next(rotulo for rotulo, codigo in TIPOS.items() if codigo == tipo))
        self._carregar_indexadores(a.indexador if a is not None else None)
        if a is not None:
            self.nome.setText(a.nome)
            self.instituicao.setText(a.instituicao or "")
            self.taxa.setText(texto_taxa(a.taxa) if a.taxa is not None else "")
            if a.vencimento is not None:
                self.vencimento.setDate(QDate(a.vencimento.year, a.vencimento.month, a.vencimento.day))
        else:
            padrao = somar_meses(self._hoje, 12)
            self.vencimento.setDate(QDate(padrao.year, padrao.month, padrao.day))
        self.tem_vencimento.setChecked(a is not None and a.vencimento is not None)
        self._vencimento_ligado(self.tem_vencimento.isChecked())
        self._tipo_mudou(manter_indexador=True)
        self._indexador_mudou()

    # --- Estado ------------------------------------------------------------------------
    @property
    def tipo_atual(self) -> str:
        return TIPOS[self.tipo.atual()]

    @property
    def indexador_atual(self) -> str:
        return self.indexador.currentData()

    def _carregar_indexadores(self, escolhido: str | None) -> None:
        """Os indexadores que o tipo aceita (Tesouro não rende pelo CDI); mantém o escolhido se ainda vale."""
        self.indexador.blockSignals(True)
        self.indexador.clear()
        for codigo in INDEXADORES_DO_TIPO[self.tipo_atual]:
            self.indexador.addItem(INDEXADORES[codigo][0], codigo)
        self.indexador.setCurrentIndex(max(self.indexador.findData(escolhido), 0))
        self.indexador.blockSignals(False)

    def _tipo_mudou(self, manter_indexador: bool = False) -> None:
        atual = self.indexador_atual if self.indexador.count() else None
        self._carregar_indexadores(atual)
        self.nome.setPlaceholderText(EXEMPLOS_DE_NOME[self.tipo_atual])
        if not manter_indexador:
            self._indexador_mudou()

    def _indexador_mudou(self) -> None:
        _nome, rotulo, exemplo = INDEXADORES[self.indexador_atual]
        self.rotulo_taxa.setText(rotulo)
        self.taxa.setPlaceholderText(exemplo)
        self._limpar_erro(self.erro_taxa, self.taxa)
        self._atualizar_previa()

    def _taxa_mudou(self) -> None:
        self._limpar_erro(self.erro_taxa, self.taxa)
        self._atualizar_previa()

    def _atualizar_previa(self) -> None:
        taxa = ler_taxa(self.taxa.text())
        self.previa.setText(f"Na lista: {investimentos.descrever_taxa(self.indexador_atual, taxa)}" if taxa is not None else "")

    def _vencimento_ligado(self, ligado: bool) -> None:
        self.vencimento.setVisible(ligado)
        self.erro_vencimento.setVisible(ligado)

    # --- Erros inline --------------------------------------------------------------------
    def _mostrar_erro(self, rotulo: QLabel, campo: QLineEdit | None, texto: str) -> None:
        rotulo.setText(f"!  {texto}")
        if campo is not None:
            campo.setProperty("erro", True)
            _repolir(campo)

    def _limpar_erro(self, rotulo: QLabel, campo: QLineEdit) -> None:
        if rotulo.text():
            rotulo.setText("")
        if campo.property("erro"):
            campo.setProperty("erro", False)
            _repolir(campo)

    def _campos(self) -> dict:
        """Os campos no formato de `investimentos.criar`/`editar` (sem validar)."""
        vencimento = self.vencimento.date().toPython() if self.tem_vencimento.isChecked() and not self._caixinhas else None
        return {
            "nome": self.nome.text().strip(),
            "tipo": self.tipo_atual,
            "instituicao": self.instituicao.text().strip() or None,
            "indexador": self.indexador_atual,
            "taxa": ler_taxa(self.taxa.text()),
            "vencimento": vencimento,
        }

    def _validar(self) -> dict | None:
        """Devolve os campos prontos pra gravar, ou mostra os erros e leva o foco ao primeiro campo ruim."""
        campos = self._campos()
        primeiro: QLineEdit | None = None
        if not self._caixinhas and not campos["nome"]:
            self._mostrar_erro(self.erro_nome, self.nome, "Dê um nome ao investimento.")
            primeiro = self.nome
        texto = self.taxa.text().strip()
        if not texto:
            self._mostrar_erro(self.erro_taxa, self.taxa, "Digite a taxa, como 110 ou 6,2.")
        elif campos["taxa"] is None:
            self._mostrar_erro(self.erro_taxa, self.taxa, "Taxa inválida. Use só números, como 110 ou 6,2.")
        else:
            try:  # as regras do banco (limites, Tesouro sem CDI, vencimento) aparecem junto do campo da taxa
                investimentos._validar(
                    self._hoje, campos["nome"] or "x", campos["tipo"], None, campos["indexador"], campos["taxa"], None
                )
            except ValueError as erro:
                self._mostrar_erro(self.erro_taxa, self.taxa, str(erro))
        if self.erro_taxa.text():
            primeiro = primeiro or self.taxa
        venc, atual = campos["vencimento"], self._original.vencimento if self._original else None
        if venc is not None and venc < self._hoje and venc != atual:
            self.erro_vencimento.setText("!  Esse vencimento já passou.")
        if primeiro:
            primeiro.setFocus()
            primeiro.selectAll()
            return None
        if self.erro_vencimento.text():
            self.vencimento.setFocus()
            return None
        return campos

    # --- Gravar -----------------------------------------------------------------------------
    def salvar(self) -> bool:
        """Grava e fecha. Devolve False (sem gravar) se houver erro."""
        campos = self._validar()
        if campos is None:
            return False
        try:
            if self.ativo_id is None:
                investimentos.criar(self.conn, self._hoje, **campos)
                mensagem = f"Investimento criado: {campos['nome']}"
            else:
                investimentos.editar(self.conn, self._hoje, self.ativo_id, **campos)
                mensagem = f"Investimento salvo: {self._original.nome if self._caixinhas else campos['nome']}"
        except ValueError as erro:  # regra de negócio que a tela não previu
            self._mostrar_erro(self.erro_taxa, self.taxa, str(erro))
            return False
        self.gravado.emit(mensagem)
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
