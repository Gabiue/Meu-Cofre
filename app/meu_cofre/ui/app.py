"""Ponto de entrada: `uv run meu-cofre`."""

import os
import sys

from PySide6.QtCore import QPoint
from importlib.resources import files

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from meu_cofre import db, indicadores
from meu_cofre.ui import tema
from meu_cofre.ui.janela import JanelaPrincipal


def _tamanho_screenshot() -> tuple[int, int]:
    """Lê MEU_COFRE_TAMANHO=LxA (padrão 1400x900)."""
    try:
        largura, altura = os.environ.get("MEU_COFRE_TAMANHO", "1400x900").lower().split("x")
        return int(largura), int(altura)
    except ValueError:
        return 1400, 900


def _abrir_dialogo_de_revisao(janela: JanelaPrincipal, app: QApplication, modo: str):
    """MEU_COFRE_DIALOGO=novo|cartao|repetir|transferencia|contas|editar:<id>|excluir:<id>|recorrente:<id>|meta|simulador|pagar|ativo|
    aportar|saldo|cotacoes|importar|comprar|provento|rolar:acoes|rolar:proventos: abre o diálogo já preparado (screenshots). `pagar` não é diálogo: abre a confirmação de pagamento da
    aba Cartões. `meta` abre a nova meta com uma prévia preenchida e `simulador` preenche e rola até o simulador da aba
    Metas. `ativo` abre o novo investimento preenchido; `aportar` e `saldo` abrem o formulário na linha do segundo
    investimento da aba Investimentos. `importar` abre "Importar da B3" com a prévia da planilha em MEU_COFRE_PLANILHA;
    `comprar` e `provento` abrem o formulário na primeira ação; `rolar:acoes` e `rolar:proventos` rolam a aba."""
    nome, _, id_texto = modo.partition(":")
    if nome == "importar":
        dialogo = janela.aba_investimentos.importar_b3(os.environ.get("MEU_COFRE_PLANILHA"))
        for _ in range(3):
            app.processEvents()
        return dialogo
    if nome in ("comprar", "provento", "rolar"):  # não são diálogos: formulário inline e rolagem da aba Investimentos
        aba = janela.aba_investimentos
        alvo = aba.cartoes_acoes()[0] if id_texto != "proventos" else aba._cartao_proventos
        if nome != "rolar":
            aba.abrir_form_acao(alvo.id_ativo, nome)
        for _ in range(3):
            app.processEvents()
        aba.verticalScrollBar().setValue(alvo.mapTo(aba.widget(), QPoint(0, 0)).y() - 24)
        app.processEvents()
        return None
    if nome == "ativo":
        dialogo = janela.aba_investimentos.novo_investimento()
        dialogo.nome.setText("CDB Banco Inter")
        dialogo.instituicao.setText("Inter")
        dialogo.taxa.setText("110")
        dialogo.tem_vencimento.setChecked(True)
        for _ in range(3):
            app.processEvents()
        dialogo.resize(dialogo.width(), janela.height() - 60)
        app.processEvents()
        return dialogo
    if nome in ("aportar", "saldo"):  # não é diálogo: abre o formulário inline de um investimento
        cartoes = [c for c in janela.aba_investimentos.cartoes() if not c.posicao.ativo.caixinhas]
        janela.aba_investimentos.abrir_form(cartoes[0].id_ativo, nome)
        for _ in range(3):
            app.processEvents()
        janela.aba_investimentos.ensureWidgetVisible(cartoes[0], 0, 40)
        app.processEvents()
        return None
    if nome == "cotacoes":
        dialogo = janela.aba_investimentos.abrir_cotacoes()
        dialogo.token.setText("brapi_token_de_exemplo")
        dialogo.botao_remover.setEnabled(True)
        for _ in range(3):
            app.processEvents()
        return dialogo
    if nome == "simulador":  # não é diálogo: preenche o "Comprar agora ou parcelar?" da aba Metas e rola até ele
        simulador = janela.aba_metas.simulador
        simulador.valor.setText("1500")
        simulador.parcelas.setCurrentIndex(simulador.parcelas.findData(10))
        for _ in range(3):
            app.processEvents()
        barra = janela.aba_metas.verticalScrollBar()
        barra.setValue(barra.maximum())
        app.processEvents()
        return None
    if nome == "meta":
        dialogo = janela.aba_metas.nova_meta()
        dialogo.nome.setText("Viagem de fim de ano")
        dialogo.valor.setText("4800")
        dialogo.mes.setCurrentIndex(dialogo.mes.findData(12))
        dialogo.ano.setCurrentIndex(dialogo.ano.findData(janela._hoje().year + 1))
        for _ in range(3):
            app.processEvents()
        dialogo.resize(dialogo.width(), janela.height() - 60)
        app.processEvents()
        return dialogo
    if nome == "pagar":
        janela.aba_cartoes.pedir_pagamento_da_proxima()
        for _ in range(3):  # a lista de faturas é refeita: dá tempo do layout assentar
            app.processEvents()
        return None
    if nome == "contas":
        dialogo = janela.abrir_contas()
        dialogo.mostrar_desativados(True)
        for _ in range(3):  # a janela se ajusta à lista em etapas (estilo, depois tamanho)
            app.processEvents()
        dialogo.resize(dialogo.width(), janela.height() - 60)  # a tela offscreen é baixa: dá espaço pra mostrar tudo
        app.processEvents()
        return dialogo
    if nome == "recorrente":
        dialogo = janela.aba_recorrentes.editar(int(id_texto))
        app.processEvents()
        return dialogo
    dialogo = janela.novo_lancamento() if nome in ("novo", "cartao", "repetir", "transferencia") else janela._abrir_lancamento(int(id_texto))
    if dialogo is None:
        return None
    if nome == "cartao":
        dialogo.metodo.clicar("Cartão")
        dialogo.valor.setText("600")
        dialogo.descricao.setText("Tênis")
        dialogo.parcelas.setCurrentIndex(5)
        dialogo._sugerir_categoria()
    elif nome == "transferencia":
        dialogo.tipo.clicar("Transferência")
        dialogo.valor.setText("250")
        dialogo.descricao.setText("Saque pra carteira")
    elif nome == "repetir":
        dialogo.valor.setText("39,90")
        dialogo.descricao.setText("Streaming de vídeo")
        dialogo.metodo.clicar("Cartão")
        dialogo._sugerir_categoria()
        dialogo.repetir.setChecked(True)
        dialogo.assinatura.setChecked(True)
        dialogo.resize(dialogo.width(), janela.height() - 60)  # a tela offscreen é baixa: dá espaço pra mostrar tudo
    elif nome == "excluir":
        dialogo.pedir_exclusao()
    app.processEvents()
    return dialogo


def _compor_screenshot(janela: JanelaPrincipal, dialogo) -> QPixmap:
    """Janela principal ao fundo, escurecida, com o diálogo centralizado por cima."""
    fundo = janela.grab()
    if dialogo is None:
        return fundo
    pintor = QPainter(fundo)
    pintor.fillRect(fundo.rect(), QColor(0, 0, 0, 120))
    frente = dialogo.grab()
    pintor.drawPixmap(QPoint((fundo.width() - frente.width()) // 2, (fundo.height() - frente.height()) // 2), frente)
    pintor.end()
    return fundo


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("meu-cofre")
    # Casa a janela com o meu-cofre.desktop (ícone certo no lançador e na barra) e define o ícone.
    app.setDesktopFileName("meu-cofre")
    app.setWindowIcon(QIcon(str(files("meu_cofre.ui").joinpath("icone.svg"))))
    tema.aplicar_tema(app)

    destino = os.environ.get("MEU_COFRE_SCREENSHOT")
    if destino:  # modo revisão: nunca vai pra rede
        os.environ[indicadores.VAR_SEM_REDE] = "1"
    conn = db.conectar()  # respeita MEU_COFRE_DB
    janela = JanelaPrincipal(conn)

    if destino:  # modo revisão: salva um PNG e sai
        largura, altura = _tamanho_screenshot()
        janela.resize(largura, altura)
        janela.mostrar_aba(os.environ.get("MEU_COFRE_ABA", "inicio"))
        painel = {"fluxo": "Fluxo", "rosca": "Para onde foi", "proximos": "Próximos meses"}.get(
            os.environ.get("MEU_COFRE_PAINEL", "")
        )
        if painel:
            janela.mostrar_painel(painel)
        janela.show()
        app.processEvents()
        modo_dialogo = os.environ.get("MEU_COFRE_DIALOGO")
        dialogo = _abrir_dialogo_de_revisao(janela, app, modo_dialogo) if modo_dialogo else None
        _compor_screenshot(janela, dialogo).save(destino)
        conn.close()
        return 0

    janela.show()
    codigo = app.exec()
    janela._atualizador.aguardar()  # a busca de taxas, se estiver rodando, termina antes de fechar o banco
    conn.close()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
