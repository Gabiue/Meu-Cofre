"""Tela inicial com dados reais (F2): resumo, recentes, navegação de mês e gráficos. Offscreen."""

import gc
from datetime import date

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication

from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.graficos import OPCAO_FLUXO, OPCAO_ROSCA, brl_curto, nome_do_mes, ticks_bonitos
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 10, 20)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def _reg(conn, valor, *, entrada=False, cat="Mercado", data=date(2026, 10, 5), metodo="pix",
         conta="Inter", descricao="x", parcelas=1):
    return lanc.registrar(
        conn, entrada=entrada, valor_centavos=valor, descricao=descricao,
        categoria_id=id_cat(conn, cat), metodo=metodo, conta_id=id_conta(conn, conta),
        data=data, parcelas=parcelas,
    )


def _janela(conn, tamanho=(1500, 900)):
    janela = JanelaPrincipal(conn, hoje=HOJE)
    janela.resize(*tamanho)
    janela.show()
    QApplication.processEvents()
    return janela


def _itens(janela):
    return [janela._lista.itemAt(i).widget() for i in range(janela._lista.count())]


def test_banco_vazio_mostra_estados_vazios(app, conn):
    janela = _janela(conn)
    assert janela._em_conta.valor.text() == "R$ 0,00"
    assert janela._devo.valor.text() == "R$ 0,00"
    assert janela._livre.valor.text() == "R$ 0,00"
    assert janela._lista.count() == 0
    assert janela._vazio_recentes.isVisibleTo(janela)
    assert janela._vazio_recentes.titulo.text() == "Nenhum lançamento ainda"
    fluxo = janela._painel_grafico.fluxo
    assert fluxo._pilha.currentWidget() is fluxo._vazio
    assert fluxo._vazio.titulo.text() == "Nenhum gasto em outubro ainda"
    rosca = janela._painel_grafico.rosca
    assert rosca._pilha.currentWidget() is rosca._vazio
    janela.close()


def test_resumo_com_dados(app, conn):
    _reg(conn, 500000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    _reg(conn, 40000, metodo="cartao", data=date(2026, 10, 10), parcelas=4)
    janela = _janela(conn)
    assert janela._em_conta.valor.text() == "R$ 5.000,00"
    assert janela._devo.valor.text() == "R$ 400,00"
    assert janela._livre.valor.text() == "R$ 4.900,00"  # só a parcela do mês entra no livre
    assert janela._pilula_livre.text() == "≈ R$ 408 por dia"  # 4900 / 12 dias
    assert janela._pilula_livre.property("variante") == "neutra"
    janela.close()


def test_pilula_de_atencao_quando_livre_negativo(app, conn):
    _reg(conn, 10000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    _reg(conn, 22000, metodo="cartao", data=date(2026, 10, 10))
    janela = _janela(conn)
    assert janela._livre.valor.text() == "-R$ 120,00"
    assert janela._pilula_livre.property("variante") == "atencao"
    assert "R$ 120 acima do disponível" in janela._pilula_livre.text()
    janela.close()


def test_recentes_filtro_parcela_e_clique(app, conn):
    _reg(conn, 520000, entrada=True, cat="Salário", descricao="Salário", data=date(2026, 10, 19))
    _reg(conn, 60000, metodo="cartao", parcelas=6, descricao="Tênis", cat="Compras pessoais",
         data=date(2026, 10, 20))
    _reg(conn, 4000, descricao="Padaria", data=date(2026, 10, 19))
    janela = _janela(conn)
    itens = _itens(janela)
    assert len(itens) == 8  # 1 + 6 parcelas + 1
    assert not janela._vazio_recentes.isVisibleTo(janela)
    assert itens[0].descricao.texto() == "Padaria"
    assert itens[0].data.text() == "ontem"
    assert itens[1].parcela.text() == "(6/6)"
    assert itens[0].parcela.isHidden()

    janela.seletor_recentes.clicar("Entradas")
    assert [i.descricao.texto() for i in _itens(janela)] == ["Salário"]
    janela.seletor_recentes.clicar("Saídas")
    assert len(_itens(janela)) == 7
    janela.seletor_recentes.clicar("Todos")

    ids = []
    for item in _itens(janela):
        item.clicado.connect(ids.append)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(_itens(janela)[0], Qt.MouseButton.LeftButton, pos=QPoint(40, 30))
    assert ids == [int(lanc.recentes(conn, 1).iloc[0]["id"])]
    janela.close()


def test_recentes_vazio_por_filtro(app, conn):
    _reg(conn, 4000)
    janela = _janela(conn)
    janela.seletor_recentes.clicar("Entradas")
    assert janela._lista.count() == 0
    assert janela._vazio_recentes.titulo.text() == "Nenhuma entrada ainda"
    janela.close()


def test_navegacao_de_mes_nao_passa_do_mes_atual(app, conn):
    _reg(conn, 5000, data=date(2026, 9, 12))
    janela = _janela(conn)
    assert janela.rotulo_mes.text() == "Outubro de 2026"
    assert not janela.botao_mes_seguinte.isEnabled()
    janela.botao_mes_seguinte.click()
    assert janela.rotulo_mes.text() == "Outubro de 2026"

    janela.botao_mes_anterior.click()
    assert janela.rotulo_mes.text() == "Setembro de 2026"
    assert janela.botao_mes_seguinte.isEnabled()
    fluxo = janela._painel_grafico.fluxo
    assert fluxo._pilha.currentWidget() is fluxo._vista  # setembro tem gasto
    # o mês escolhido só afeta o painel: resumo e recentes continuam "agora"
    assert janela._lista.count() == 1

    for _ in range(10):  # atravessa a virada de ano
        janela.botao_mes_anterior.click()
    assert janela.rotulo_mes.text() == "Novembro de 2025"
    janela.botao_mes_seguinte.click()
    assert janela.rotulo_mes.text() == "Dezembro de 2025"
    janela.close()


def test_mes_sem_gasto_nao_diz_ainda(app, conn):
    janela = _janela(conn)
    janela.botao_mes_anterior.click()
    assert janela._painel_grafico.fluxo._vazio.titulo.text() == "Nenhum gasto em setembro"
    janela.close()


def test_atualizar_recarrega_tudo(app, conn):
    janela = _janela(conn)
    assert janela._lista.count() == 0
    _reg(conn, 12345, descricao="Mercado novo", data=date(2026, 10, 20))
    janela.atualizar()
    assert [i.descricao.texto() for i in _itens(janela)] == ["Mercado novo"]
    assert janela._livre.valor.text() == "-R$ 123,45"
    assert janela._painel_grafico.fluxo._pilha.currentWidget() is janela._painel_grafico.fluxo._vista
    janela.close()


def test_graficos_com_dados_nos_dois_tamanhos(app, conn):
    for dia in range(1, 21):
        _reg(conn, 1000 * dia, cat=["Mercado", "Moradia", "Lazer", "Saúde", "Educação", "Delivery", "Outros"][dia % 7],
             data=date(2026, 10, dia))
    for tamanho in ((1900, 1030), (940, 1000), (600, 700)):
        janela = _janela(conn, tamanho)
        janela.seletor_painel.clicar(OPCAO_ROSCA)
        QApplication.processEvents()
        assert janela._painel_grafico.currentWidget() is janela._painel_grafico.rosca
        rosca = janela._painel_grafico.rosca
        assert rosca._pilha.currentWidget() is rosca._conteudo
        janela.seletor_painel.clicar(OPCAO_FLUXO)
        assert janela._painel_grafico.currentWidget() is janela._painel_grafico.fluxo
        janela.close()


def test_rosca_cores_em_ordem_fixa_e_outros_cinza(app, conn):
    for nome, valor in (("Mercado", 700), ("Moradia", 600), ("Lazer", 500), ("Saúde", 400), ("Educação", 300),
                        ("Delivery", 200), ("Assinaturas", 100)):
        _reg(conn, valor * 100, cat=nome)
    janela = _janela(conn)
    legenda = janela._painel_grafico.rosca._legenda
    cores = []
    for i in range(legenda._grade.rowCount()):
        quadro = legenda._grade.itemAtPosition(i, 0).widget()
        cores.append(quadro.styleSheet().split("background: ")[1].split(";")[0])
    assert cores == [*tema.SERIES, tema.SERIE_OUTROS]
    janela.close()


def test_brl_curto_e_ticks():
    assert brl_curto(150000) == "R$ 1,5 mil"
    assert brl_curto(8500) == "R$ 85"
    assert brl_curto(0) == "R$ 0"
    assert brl_curto(250000000) == "R$ 2,5 mi"
    assert ticks_bonitos(0, 1850) == [0, 500, 1000, 1500, 2000]
    assert ticks_bonitos(0, 0)[0] == 0  # sem dados não quebra
    assert nome_do_mes(2026, 3) == "Março de 2026"


def test_atualizar_varias_vezes_com_dados_nao_cai(app, conn):
    """Regressão: trocar o gráfico do fluxo ao atualizar com dados derrubava o app (visto ao editar um lançamento)."""
    _reg(conn, 5000)
    janela = _janela(conn)
    for _ in range(4):
        janela.atualizar()
        QApplication.processEvents()
        gc.collect()
    janela.close()



def test_recente_sem_categoria_mostra_a_categorizar(app, conn):
    """Regressão: categoria nula vinha do pandas como NaN e aparecia "nan" na lista."""
    from PySide6.QtWidgets import QLabel

    from meu_cofre.ui.componentes import ItemLancamento

    lanc.registrar(conn, entrada=False, valor_centavos=1000, descricao="Sem categoria",
                   categoria_id=None, metodo="pix", conta_id=id_conta(conn, "Inter"),
                   data=date(2026, 10, 5))
    _reg(conn, 2000, descricao="Com categoria")  # lista mista: é aí que o pandas vira NaN
    janela = _janela(conn)
    textos = [lbl.texto() if hasattr(lbl, "texto") else lbl.text()
              for item in janela.findChildren(ItemLancamento) for lbl in item.findChildren(QLabel)]
    assert not any("nan" in t for t in textos)
    assert any("A categorizar" in t for t in textos)
