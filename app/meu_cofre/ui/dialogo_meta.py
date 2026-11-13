"""Janela de nova meta ou de edição de uma meta (aba Metas).

Nome, valor e, em Segmentado, Prazo (mês e ano; o app calcula o mensal) ou Mensal (quanto guarda por mês;
o app calcula a data). Embaixo, a **prévia ao vivo**: o mensal ou a data, se cabe na sobra e quais outras
metas atrasam por causa desta. Erros ficam embaixo do campo, nada de caixa de mensagem.
Quem abre escuta `gravado(mensagem)` para atualizar a tela.
"""

import sqlite3
from datetime import date

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from meu_cofre import metas
from meu_cofre.lancamentos import formatar_brl, formatar_brl_inteiro, para_centavos, somar_meses
from meu_cofre.ui import tema
from meu_cofre.ui.componentes import BotaoPrimario, BotaoSecundario, CampoLista, Marcador, Pilula, Segmentado, _repolir
from meu_cofre.ui.graficos import MESES, MESES_CURTOS

LARGURA = 560
PRAZO, MENSAL = "Prazo", "Mensal"
ANOS_A_FRENTE = 12
MESES_PADRAO = 6  # prazo sugerido numa meta nova: daqui a 6 meses


# --- Textos compartilhados com a aba ---------------------------------------------------------------


def mes_ano(dia: date) -> str:
    """"mar/2027"."""
    return f"{MESES_CURTOS[dia.month - 1]}/{dia.year}"


def reais(centavos: int) -> str:
    """"R$ 250" quando é redondo, "R$ 250,34" quando não."""
    return formatar_brl_inteiro(centavos) if centavos % 100 == 0 else formatar_brl(centavos)


def plural_meses(n: int) -> str:
    return f"{n} mês" if n == 1 else f"{n} meses"


def texto_atrasos(atrasos: list[metas.Atraso]) -> str:
    """"Viagem (+2 meses), Cadeira (deixa de caber)"."""
    return ", ".join(
        f"{a.nome} ({'deixa de caber' if a.meses is None else '+' + plural_meses(a.meses)})" for a in atrasos
    )


def pilula_viabilidade(v: metas.Viabilidade) -> tuple[str, str]:
    """(texto, variante da pílula) da situação da meta."""
    if v.situacao == "cabe":
        return "cabe na sobra", "bom"
    if v.situacao == "parcial":
        return f"atrasa até {mes_ano(v.prevista)}", "atencao"
    if v.situacao == "nao_cabe":
        return "não cabe agora", "ruim"
    return "meta cumprida", "feito"


def explicacao_viabilidade(v: metas.Viabilidade, sobra: int) -> str:
    """Uma frase que explica a pílula: quanto a sobra deu à meta e no que isso dá."""
    if v.situacao == "cabe":
        return f"Cabe na sua sobra média de {reais(sobra)} por mês."
    if v.situacao == "parcial":
        return (
            f"Só sobram {reais(v.alocado)} por mês pra ela (pede {reais(v.necessario)}): "
            f"fica pronta em {mes_ano(v.prevista)}, {plural_meses(v.atraso_meses)} depois do pedido."
        )
    if v.situacao == "nao_cabe":
        if sobra <= 0:
            return "Sua sobra média não é positiva, então não sobra nada pra guardar."
        return "A sobra média já está toda comprometida com as metas acima dela."
    return "Você já guardou o suficiente."


def _rotulo(texto: str, nome: str) -> QLabel:
    rotulo = QLabel(texto)
    rotulo.setObjectName(nome)
    return rotulo


LADO_DA_FOTO = 480  # a foto guardada: quadrada e pequena (cabe folgada no detalhe da meta)


def preparar_foto(caminho: str) -> bytes | None:
    """A imagem recortada no quadrado do meio e reduzida a LADO_DA_FOTO, em PNG; None se não abrir."""
    img = QImage(caminho)
    if img.isNull():
        return None
    lado = min(img.width(), img.height())
    img = img.copy((img.width() - lado) // 2, (img.height() - lado) // 2, lado, lado)
    img = img.scaled(LADO_DA_FOTO, LADO_DA_FOTO, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    dados = QByteArray()
    buffer = QBuffer(dados)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buffer, "PNG")
    return bytes(dados)


def foto_arredondada(foto: QPixmap, lado: int, raio: int = 10) -> QPixmap:
    """A foto no tamanho `lado` (preenchendo o quadrado) com os cantos arredondados."""
    escala = 2
    px = QPixmap(lado * escala, lado * escala)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    caminho = QPainterPath()
    caminho.addRoundedRect(QRectF(px.rect()), raio * escala, raio * escala)
    p.setClipPath(caminho)
    p.drawPixmap(px.rect(), foto.scaled(px.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation))
    p.end()
    px.setDevicePixelRatio(escala)
    return px


def _rotulo_erro() -> QLabel:
    """Linha de erro com altura reservada, para o formulário não pular quando o erro aparece."""
    rotulo = _rotulo("", "CampoErro")
    rotulo.setFixedHeight(22)
    rotulo.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    return rotulo


def _texto_reais(centavos: int) -> str:
    """Valor pra caixa de texto, sem o "R$" e sem ponto de milhar: 150000 -> "1500,00"."""
    return formatar_brl(centavos).removeprefix("R$ ").replace(".", "")


class DialogoMeta(QDialog):
    """Diálogo modal de nova meta (`meta_id=None`) ou de edição. Emite `gravado(mensagem)` depois de salvar.

    Os argumentos `nome`, `valor_centavos`, `mensal_centavos` e `reserva` pré-preenchem uma meta nova (a sugestão
    de reserva e o "Criar como meta" do simulador usam isso).
    """

    gravado = Signal(str)

    def __init__(
        self,
        conn: sqlite3.Connection,
        meta_id: int | None = None,
        parent: QWidget | None = None,
        hoje: date | None = None,
        *,
        nome: str = "",
        valor_centavos: int | None = None,
        mensal_centavos: int | None = None,
        reserva: bool = False,
    ):
        super().__init__(parent)
        self.conn = conn
        self.meta_id = meta_id
        self._hoje = hoje or date.today()
        self._original = metas.obter(conn, meta_id) if meta_id is not None else None  # ValueError se não existe

        self.setObjectName("Dialogo")
        self.setModal(True)
        self.setWindowTitle("Nova meta" if meta_id is None else "Editar meta")
        self.setMinimumWidth(LARGURA)
        self.setMaximumWidth(LARGURA)
        self._montar()
        self._ligar()
        self._preencher(nome, valor_centavos, mensal_centavos, reserva)
        self._atualizar_modo()
        self._ajustado = False
        self.nome.setFocus()

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

        self.titulo = _rotulo("Nova meta" if self.meta_id is None else "Editar meta", "DialogoTitulo")
        form.addWidget(self.titulo)
        form.addSpacing(4)
        self.aviso = _rotulo("O que você quer juntar, e até quando ou quanto por mês.", "DialogoDetalhe")
        self.aviso.setWordWrap(True)
        form.addWidget(self.aviso)
        form.addSpacing(2 * tema.ESPACO)

        form.addWidget(_rotulo("Nome da meta", "CampoRotulo"))
        form.addSpacing(4)
        self.nome = QLineEdit()
        self.nome.setPlaceholderText("Ex.: Cadeira nova")
        form.addWidget(self.nome)
        self.erro_nome = _rotulo_erro()
        form.addWidget(self.erro_nome)

        form.addWidget(_rotulo("Por quê (opcional)", "CampoRotulo"))
        form.addSpacing(4)
        self.descricao = QLineEdit()
        self.descricao.setPlaceholderText("Ex.: melhorar o setup pra estudar")
        self.descricao.setMaxLength(metas.DESCRICAO_MAX)
        form.addWidget(self.descricao)
        form.addSpacing(tema.ESPACO + 4)
        form.addWidget(self._montar_foto())
        self.erro_foto = _rotulo_erro()
        form.addWidget(self.erro_foto)

        form.addWidget(_rotulo("Quanto você quer juntar (R$)", "CampoRotulo"))
        form.addSpacing(4)
        self.valor = QLineEdit()
        self.valor.setObjectName("CampoValor")
        self.valor.setPlaceholderText("0,00")
        form.addWidget(self.valor)
        self.erro_valor = _rotulo_erro()
        form.addWidget(self.erro_valor)

        form.addWidget(_rotulo("Como você quer definir", "CampoRotulo"))
        form.addSpacing(4)
        self.modo = Segmentado([PRAZO, MENSAL])
        form.addWidget(self.modo, 0, Qt.AlignmentFlag.AlignLeft)
        form.addSpacing(tema.ESPACO + 4)

        # Prazo: mês e ano
        self._bloco_prazo = QWidget()
        linha = QHBoxLayout(self._bloco_prazo)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(2 * tema.ESPACO)
        coluna_mes = QVBoxLayout()
        coluna_mes.setSpacing(4)
        coluna_mes.addWidget(_rotulo("Pronta até (mês)", "CampoRotulo"))
        self.mes = CampoLista()
        for i, nome in enumerate(MESES, start=1):
            self.mes.addItem(nome.capitalize(), i)
        coluna_mes.addWidget(self.mes)
        coluna_ano = QVBoxLayout()
        coluna_ano.setSpacing(4)
        coluna_ano.addWidget(_rotulo("Ano", "CampoRotulo"))
        self.ano = CampoLista()
        primeiro = min(self._hoje.year, self._original.prazo.year) if self._original and self._original.prazo else self._hoje.year
        for a in range(primeiro, self._hoje.year + ANOS_A_FRENTE + 1):
            self.ano.addItem(str(a), a)
        coluna_ano.addWidget(self.ano)
        linha.addLayout(coluna_mes, 3)
        linha.addLayout(coluna_ano, 2)
        form.addWidget(self._bloco_prazo)

        # Mensal: quanto por mês
        self._bloco_mensal = QWidget()
        coluna = QVBoxLayout(self._bloco_mensal)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(4)
        coluna.addWidget(_rotulo("Quanto guardar por mês (R$)", "CampoRotulo"))
        self.mensal = QLineEdit()
        self.mensal.setPlaceholderText("0,00")
        coluna.addWidget(self.mensal)
        form.addWidget(self._bloco_mensal)

        self.erro_prazo = _rotulo_erro()
        form.addWidget(self.erro_prazo)

        self.reserva = Marcador("É a minha reserva de emergência")
        self.reserva.setToolTip("A reserva fica no topo da fila de prioridade. Só pode haver uma.")
        outra_reserva = metas.tem_reserva(self.conn) and not (self._original and self._original.reserva)
        self.reserva.setVisible(not outra_reserva)
        form.addWidget(self.reserva)
        form.addSpacing(tema.ESPACO + 4)

        # Prévia ao vivo
        self.previa = QFrame()
        self.previa.setObjectName("PreviaMeta")
        caixa = QVBoxLayout(self.previa)
        p = 2 * tema.ESPACO
        caixa.setContentsMargins(p, p, p, p)
        caixa.setSpacing(6)
        caixa.addWidget(_rotulo("Prévia", "NumeroRotulo"))
        self.previa_destaque = _rotulo("", "PreviaDestaque")
        self.previa_destaque.setWordWrap(True)
        caixa.addWidget(self.previa_destaque)
        self.previa_detalhe = _rotulo("", "PreviaDetalhe")
        self.previa_detalhe.setWordWrap(True)
        caixa.addWidget(self.previa_detalhe)
        self.previa_pilula = Pilula("", "neutra")
        caixa.addWidget(self.previa_pilula, 0, Qt.AlignmentFlag.AlignLeft)
        self.previa_explicacao = _rotulo("", "PreviaDetalhe")
        self.previa_explicacao.setWordWrap(True)
        caixa.addWidget(self.previa_explicacao)
        self.previa_atrasos = _rotulo("", "PreviaDetalhe")
        self.previa_atrasos.setWordWrap(True)
        caixa.addWidget(self.previa_atrasos)
        self.previa_nota = _rotulo("", "CampoDica")
        self.previa_nota.setWordWrap(True)
        caixa.addWidget(self.previa_nota)
        form.addWidget(self.previa)
        form.addSpacing(tema.ESPACO)
        form.addStretch(1)

        self._barra = barra = QWidget()
        botoes = QHBoxLayout(barra)
        botoes.setContentsMargins(m, tema.ESPACO, m, m)
        botoes.setSpacing(tema.ESPACO + 4)
        botoes.addStretch(1)
        self.botao_cancelar = BotaoSecundario("Cancelar")
        self.botao_cancelar.setAutoDefault(False)
        self.botao_cancelar.setToolTip("Cancelar (Esc)")
        self.botao_salvar = BotaoPrimario("Criar meta" if self.meta_id is None else "Salvar alterações")
        self.botao_salvar.setAutoDefault(True)
        self.botao_salvar.setDefault(True)
        self.botao_salvar.setToolTip("Salvar (Enter)")
        botoes.addWidget(self.botao_cancelar)
        botoes.addWidget(self.botao_salvar)
        raiz.addWidget(barra)

    def _ligar(self) -> None:
        self.modo.escolhido.connect(lambda _m: self._atualizar_modo())
        self.nome.textChanged.connect(lambda _t: self._limpar_erro(self.erro_nome, self.nome))
        self.valor.textChanged.connect(lambda _t: self._campo_mudou(self.erro_valor, self.valor))
        self.mensal.textChanged.connect(lambda _t: self._campo_mudou(self.erro_prazo, self.mensal))
        self.mes.currentIndexChanged.connect(lambda _i: self._campo_mudou(self.erro_prazo, None))
        self.ano.currentIndexChanged.connect(lambda _i: self._campo_mudou(self.erro_prazo, None))
        self.reserva.toggled.connect(lambda _c: self._atualizar_previa())
        self.botao_cancelar.clicked.connect(self.reject)
        self.botao_salvar.clicked.connect(self.salvar)

    def _montar_foto(self) -> QWidget:
        """Foto da meta (opcional): miniatura, "Escolher foto..." e "Tirar foto". Só grava ao salvar."""
        caixa = QWidget()
        linha = QHBoxLayout(caixa)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(2 * tema.ESPACO)
        self.miniatura = QLabel()
        self.miniatura.setObjectName("FotoMeta")
        self.miniatura.setFixedSize(64, 64)
        self.miniatura.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.botao_foto = BotaoSecundario("Escolher foto…")
        self.botao_foto.setProperty("compacto", True)
        self.botao_foto.setAutoDefault(False)
        self.botao_foto.clicked.connect(lambda: self.escolher_foto())
        self.botao_tirar_foto = QPushButton("Tirar foto")
        self.botao_tirar_foto.setObjectName("BotaoLink")
        self.botao_tirar_foto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_tirar_foto.clicked.connect(self.tirar_foto)
        linha.addWidget(self.miniatura)
        linha.addWidget(self.botao_foto)
        linha.addWidget(self.botao_tirar_foto)
        linha.addStretch(1)
        self._foto_alterada = False
        self._foto: bytes | None = None
        return caixa

    def escolher_foto(self, caminho: str | None = None) -> None:
        """Abre o seletor de arquivo (ou usa `caminho`, nos testes) e prepara a foto: recorte quadrado, reduzida."""
        if caminho is None:
            caminho, _filtro = QFileDialog.getOpenFileName(self, "Foto da meta", "", "Imagens (*.png *.jpg *.jpeg *.webp *.bmp)")
            if not caminho:
                return
        png = preparar_foto(caminho)
        if png is None:
            self.erro_foto.setText("!  Não deu pra abrir essa imagem.")
            return
        self.erro_foto.setText("")
        self._foto, self._foto_alterada = png, True
        self._mostrar_miniatura()

    def tirar_foto(self) -> None:
        self._foto, self._foto_alterada = None, True
        self._mostrar_miniatura()

    def _mostrar_miniatura(self) -> None:
        if self._foto:
            px = QPixmap()
            px.loadFromData(self._foto)
            self.miniatura.setPixmap(foto_arredondada(px, 64))
            self.miniatura.setText("")
        else:
            self.miniatura.setPixmap(QPixmap())
            self.miniatura.setText("sem foto")
        self.botao_tirar_foto.setVisible(bool(self._foto))
        self.botao_foto.setText("Trocar foto…" if self._foto else "Escolher foto…")

    def _preencher(self, nome: str, valor_centavos: int | None, mensal_centavos: int | None, reserva: bool) -> None:
        m = self._original
        if m is not None:
            self.descricao.setText(m.descricao)
            caminho = metas.caminho_imagem(self.conn, m)
            self._foto = caminho.read_bytes() if caminho else None
        self._mostrar_miniatura()
        if m is not None:
            nome, valor_centavos, mensal_centavos, reserva = m.nome, m.valor_alvo_centavos, m.mensal_centavos, m.reserva
        self.nome.setText(nome)
        if valor_centavos:
            self.valor.setText(_texto_reais(valor_centavos))
        padrao = somar_meses(self._hoje.replace(day=1), MESES_PADRAO)
        prazo = m.prazo if m is not None and m.prazo else padrao
        self.mes.setCurrentIndex(self.mes.findData(prazo.month))
        self.ano.setCurrentIndex(max(self.ano.findData(prazo.year), 0))
        if mensal_centavos:
            self.mensal.setText(_texto_reais(mensal_centavos))
        self.reserva.setChecked(reserva)
        self.modo.definir(MENSAL if (m is not None and m.modo == "mensal") or (m is None and mensal_centavos) else PRAZO)

    # --- Estado ------------------------------------------------------------------------
    @property
    def modo_atual(self) -> str:
        return "mensal" if self.modo.atual() == MENSAL else "prazo"

    def _atualizar_modo(self) -> None:
        mensal = self.modo_atual == "mensal"
        self._bloco_prazo.setVisible(not mensal)
        self._bloco_mensal.setVisible(mensal)
        self._limpar_erro(self.erro_prazo, self.mensal)
        self._atualizar_previa()

    def _campo_mudou(self, rotulo: QLabel, campo: QLineEdit | None) -> None:
        if campo is not None:
            self._limpar_erro(rotulo, campo)
        elif rotulo.text():
            rotulo.setText("")
        self._atualizar_previa()

    # --- Leitura dos campos ---------------------------------------------------------------
    def _prazo(self) -> date:
        return date(self.ano.currentData(), self.mes.currentData(), 1)

    def _centavos(self, campo: QLineEdit) -> int | None:
        """Valor do campo em centavos; None se vazio ou inválido (a validação mostra o erro)."""
        try:
            return para_centavos(campo.text()) if campo.text().strip() else None
        except ValueError:
            return None

    def _campos(self) -> dict:
        """Os campos no formato de `metas.criar`/`metas.editar` (sem validar)."""
        mensal = self.modo_atual == "mensal"
        return {
            "nome": self.nome.text().strip(),
            "valor_alvo_centavos": self._centavos(self.valor),
            "modo": self.modo_atual,
            "prazo": None if mensal else self._prazo(),
            "mensal_centavos": self._centavos(self.mensal) if mensal else None,
            "reserva": self.reserva.isChecked(),
        }

    # --- Prévia ao vivo ------------------------------------------------------------------------
    def _atualizar_previa(self) -> None:
        campos = self._campos()
        campos["nome"] = campos["nome"] or "Esta meta"
        if not campos["valor_alvo_centavos"] or campos["valor_alvo_centavos"] <= 0:
            return self._previa_vazia("Digite o valor da meta pra ver quanto guardar e se cabe.")
        if campos["modo"] == "mensal" and not campos["mensal_centavos"]:
            return self._previa_vazia("Diga quanto quer guardar por mês pra ver quando fica pronta.")
        try:
            if self.meta_id is None:
                previa = metas.simular_nova_meta(self.conn, self._hoje, **campos)
            else:
                previa = metas.simular_edicao(self.conn, self._hoje, self.meta_id, **campos)
        except ValueError as erro:
            return self._previa_vazia(str(erro))
        self._mostrar_previa(previa, campos)

    def _previa_vazia(self, texto: str) -> None:
        """Sem dados pra calcular: só uma dica no lugar da prévia."""
        self.previa_detalhe.setText(texto)
        self.previa_detalhe.setVisible(True)
        for w in (self.previa_destaque, self.previa_pilula, self.previa_explicacao, self.previa_atrasos, self.previa_nota):
            w.setVisible(False)

    def _mostrar_previa(self, previa: metas.Previa, campos: dict) -> None:
        v = previa.meta
        if v.situacao == "cumprida":
            destaque, detalhe = "Meta já cumprida", "O que está guardado já cobre o valor."
        elif campos["modo"] == "prazo":
            destaque = f"Guardar {reais(v.necessario)} por mês"
            detalhe = f"até {mes_ano(campos['prazo'])} · {plural_meses(metas.meses_ate_prazo(self._hoje, campos['prazo']))}"
        else:
            destaque = f"Fica pronta em {mes_ano(v.pedida)}"
            detalhe = f"guardando {reais(v.necessario)} por mês · {plural_meses(metas.meses_para_mensal(v.restante, v.necessario))}"
        self.previa_destaque.setText(destaque)
        self.previa_detalhe.setText(detalhe)
        texto, variante = pilula_viabilidade(v)
        self.previa_pilula.definir(texto, variante)
        self.previa_explicacao.setText(explicacao_viabilidade(v, previa.sobra))
        outras = [x for x in previa.todas if x.meta_id != v.meta_id]
        if previa.atrasos:
            self.previa_atrasos.setText(f"Isso atrasa: {texto_atrasos(previa.atrasos)}")
        elif outras:
            self.previa_atrasos.setText("Não atrasa nenhuma outra meta.")
        notas = []
        if self._original is not None and self._original.guardado:
            notas.append(f"Já guardado: {reais(self._original.guardado)}")
        if previa.estimativa:
            notas.append("Sem mês completo de lançamentos ainda: a sobra aparece depois dele")
        self.previa_nota.setText(" · ".join(notas))
        for w in (self.previa_destaque, self.previa_detalhe, self.previa_pilula):
            w.setVisible(True)
        self.previa_explicacao.setVisible(True)
        self.previa_atrasos.setVisible(bool(previa.atrasos or outras))
        self.previa_nota.setVisible(bool(notas))

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

    def _validar(self) -> dict | None:
        """Devolve os campos prontos pra gravar, ou mostra os erros e leva o foco ao primeiro campo ruim."""
        campos = self._campos()
        primeiro: QLineEdit | None = None

        if not campos["nome"]:
            self._mostrar_erro(self.erro_nome, self.nome, "Dê um nome à meta.")
            primeiro = self.nome

        texto = self.valor.text().strip()
        mensagem = ""
        if not texto:
            mensagem = "Digite o valor da meta, como 1.500,00."
        elif campos["valor_alvo_centavos"] is None:
            mensagem = "Valor inválido. Use só números, como 1.500,00."
        elif campos["valor_alvo_centavos"] <= 0:
            mensagem = "O valor precisa ser maior que zero."
        if mensagem:
            self._mostrar_erro(self.erro_valor, self.valor, mensagem)
            primeiro = primeiro or self.valor

        if campos["modo"] == "mensal":
            texto = self.mensal.text().strip()
            if not texto:
                mensagem = "Diga quanto quer guardar por mês."
            elif campos["mensal_centavos"] is None:
                mensagem = "Valor inválido. Use só números, como 150,00."
            elif campos["mensal_centavos"] <= 0:
                mensagem = "O valor por mês precisa ser maior que zero."
            else:
                mensagem = ""
            if mensagem:
                self._mostrar_erro(self.erro_prazo, self.mensal, mensagem)
                primeiro = primeiro or self.mensal
        else:
            atual = self._original.prazo if self._original else None
            passou = campos["prazo"].year * 12 + campos["prazo"].month < self._hoje.year * 12 + self._hoje.month
            if passou and campos["prazo"] != atual:
                self._mostrar_erro(self.erro_prazo, None, "Esse prazo já passou. Escolha o mês atual ou um mês à frente.")
                primeiro = primeiro or self.mes

        if primeiro:
            primeiro.setFocus()
            if isinstance(primeiro, QLineEdit):
                primeiro.selectAll()
            return None
        return campos

    # --- Gravar -----------------------------------------------------------------------------
    def salvar(self) -> bool:
        """Grava e fecha. Devolve False (sem gravar) se houver erro."""
        campos = self._validar()
        if campos is None:
            return False
        try:
            descricao = self.descricao.text()
            if self.meta_id is None:
                meta_id = metas.criar(self.conn, self._hoje, **campos, descricao=descricao)
                mensagem = f"Meta criada: {campos['nome']}"
            else:
                meta_id = self.meta_id
                metas.editar(self.conn, self._hoje, meta_id, **campos, descricao=descricao)
                mensagem = f"Meta salva: {campos['nome']}"
            if self._foto_alterada:
                metas.trocar_imagem(self.conn, meta_id, self._foto)
        except ValueError as erro:  # regra de negócio que a tela não previu (ex.: já existe uma reserva)
            self._mostrar_erro(self.erro_valor, self.valor, str(erro))
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
