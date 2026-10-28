"""Testes da base visual (F1): constroem a interface em modo offscreen, sem tela real."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from meu_cofre.ui import tema
from meu_cofre.ui.componentes import (
    BotaoPrimario,
    BotaoSecundario,
    Cartao,
    ItemLancamento,
    NumeroDestaque,
    Pilula,
    Segmentado,
)
from meu_cofre.ui.janela import JanelaPrincipal

TEXTO_LONGO = "Muito longo " * 40


@pytest.fixture(scope="session")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.mark.parametrize("tamanho", [(1900, 1030), (940, 1000), (940, 500), (600, 400)])
def test_janela_constroi_nos_tamanhos(app, conn, tamanho):
    janela = JanelaPrincipal(conn)
    janela.resize(*tamanho)
    janela.show()
    app.processEvents()
    assert janela.windowTitle() == "meu-cofre"
    assert janela._duas_colunas == (janela.width() >= 1100)  # coerente com a largura real
    janela.close()


def test_janela_alterna_layout_ao_redimensionar(app, conn):
    janela = JanelaPrincipal(conn)
    janela.resize(1500, 900)
    janela.show()
    app.processEvents()
    assert janela._duas_colunas is True
    janela.resize(900, 900)
    app.processEvents()
    assert janela._duas_colunas is False
    # em 1 coluna, recentes fica abaixo do resumo
    assert janela._cartao_recentes.y() > janela._cartao_resumo.y()
    janela.close()


def test_segmentado_emite_opcao(app):
    seg = Segmentado(["Todos", "Entradas", "Saídas"])
    recebidos = []
    seg.escolhido.connect(recebidos.append)
    assert seg.atual() == "Todos"
    seg.clicar("Saídas")
    seg.clicar("Entradas")
    assert recebidos == ["Saídas", "Entradas"]
    assert seg.atual() == "Entradas"
    seg.definir("Todos")  # programático: não emite
    assert recebidos == ["Saídas", "Entradas"]
    assert seg.atual() == "Todos"


def test_segmentado_sem_opcoes_falha(app):
    with pytest.raises(ValueError):
        Segmentado([])


def test_item_lancamento_emite_id_e_formata_valor(app):
    item = ItemLancamento(42, "Padaria", "Mercado", "Carteira", "24 out", -2120)
    recebidos = []
    item.clicado.connect(recebidos.append)
    item.resize(400, 60)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=QPoint(50, 30))
    assert recebidos == [42]
    assert item.valor.text() == "−R$ 21,20"
    entrada = ItemLancamento(1, "Salário", "Salário", "Inter", "27 out", 520000)
    assert entrada.valor.text() == "+R$ 5.200,00"


def test_pilula_variantes_tem_icone(app):
    assert Pilula("ok", "bom").text().startswith("▲")
    assert Pilula("cuidado", "atencao").text().startswith("!")
    assert Pilula("ruim", "ruim").text().startswith("▼")
    assert Pilula("normal").text() == "normal"
    with pytest.raises(ValueError):
        Pilula("x", "inexistente")


def test_componentes_aceitam_textos_longos(app):
    cartao = Cartao(TEXTO_LONGO, TEXTO_LONGO, acao=Segmentado(["A", "B"]))
    cartao.corpo.addWidget(NumeroDestaque(TEXTO_LONGO, "R$ 99.999.999,99", TEXTO_LONGO, destaque=True))
    cartao.corpo.addWidget(Pilula("Texto de pílula razoavelmente longo", "atencao"))
    cartao.corpo.addWidget(BotaoPrimario("+ Botão com texto médio"))
    cartao.corpo.addWidget(BotaoSecundario("Cancelar"))
    cartao.corpo.addWidget(ItemLancamento(1, TEXTO_LONGO, TEXTO_LONGO, TEXTO_LONGO, "1 jan", -99999999))
    cartao.resize(500, 800)
    cartao.show()
    app.processEvents()
    # textos longos são cortados/quebrados: o cartão não alarga além do que foi dado
    assert cartao.width() == 500
    assert cartao.minimumSizeHint().width() <= 500
    cartao.close()
