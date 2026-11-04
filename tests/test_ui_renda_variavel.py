"""Ações e FIIs na aba Investimentos (F9): cartões, formulários inline, proventos, diálogo do ativo, importação da B3 e
busca das cotações em segundo plano. Offscreen e sem internet (cotações falsas gravadas ou injetadas)."""

import sys
import time
from datetime import date
from pathlib import Path

import pytest
import shiboken6
from conftest import id_conta
from PySide6.QtWidgets import QApplication, QMessageBox

from meu_cofre import cotacoes, db, relatorios
from meu_cofre import investimentos as inv
from meu_cofre import renda_variavel as rv
from meu_cofre.ui import tema
from meu_cofre.ui.aba_investimentos import AbaInvestimentos, texto_da_previsao, texto_do_selo
from meu_cofre.ui.atualizador import AtualizadorIndicadores
from meu_cofre.ui.cartao_acao import formatar_quantidade, ler_quantidade
from meu_cofre.ui.dialogo_ativo import DialogoAtivo
from meu_cofre.ui.dialogo_importar_b3 import DialogoImportarB3, descrever_operacao
from meu_cofre.ui.janela import JanelaPrincipal

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import planilha_b3_falsa as falsa  # noqa: E402

HOJE = date(2026, 10, 30)
REF = [(date(2025, 11, 14), 0.40), (date(2026, 2, 13), 0.50), (date(2026, 5, 15), 0.50)]


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _aba(conn, tamanho=(1400, 1400)):
    a = AbaInvestimentos(conn, lambda: HOJE)
    a.resize(*tamanho)
    a.show()
    QApplication.processEvents()
    return a


def _acao(conn, ticker="PETR4", tipo="acao", nome=None):
    return inv.criar(conn, HOJE, nome=nome or f"Ativo {ticker}", tipo=tipo, ticker=ticker)


def _cotar(conn, ticker, preco, proventos=()):
    cotacoes.gravar(conn, {ticker: cotacoes.Dados(preco, date(2026, 10, 29), [], list(proventos))})


def _cenario(conn):
    """Uma ação com 2 compras, uma venda e um provento, cotada; e um FII sem cotação."""
    petr, hglg = _acao(conn, "PETR4", nome="Petrobras PN"), _acao(conn, "HGLG11", "fii", "CSHG Logística")
    rv.comprar(conn, petr, date(2026, 3, 2), 100, 3000)
    rv.comprar(conn, petr, date(2026, 4, 15), 50, 3600)
    rv.vender(conn, petr, date(2026, 9, 10), 60, 4000)
    rv.registrar_provento(conn, petr, "dividendo", date(2026, 5, 20), 4550)
    rv.comprar(conn, hglg, date(2026, 3, 5), 10, 16000)
    _cotar(conn, "PETR4", 3300, REF)
    return petr, hglg


# --- Textos -------------------------------------------------------------------------------------------


def test_quantidade_formata_e_le():
    assert formatar_quantidade(100) == "100" and formatar_quantidade(10.5) == "10,5" and formatar_quantidade(1500) == "1.500"
    assert ler_quantidade("10") == 10 and ler_quantidade("10,5") == 10.5 and ler_quantidade("1.000") == 1000
    assert all(ler_quantidade(t) is None for t in ("", "abc", "0", "-3", "nan", "inf"))


def test_previsao_com_proventos_e_selo_com_cotacoes(conn):
    prev = {"patrimonio_hoje": 800_000, "aportes_planejados": 400_000, "rendimento_projetado": 3000, "rendimento_dos_aportes": 0,
            "proventos_projetados": 11_000, "meta": 200_000}
    assert texto_da_previsao(prev).replace("\u00a0", " ").endswith("+ R$ 30 de rendimento + R$ 110 de proventos")
    assert "proventos" not in texto_da_previsao({**prev, "proventos_projetados": 0})
    assert "cotações" not in texto_do_selo(conn, None, False)  # sem ações nem FIIs, o selo não fala de cotação
    _acao(conn, "PETR4")
    assert " · sem cotações de ações e FIIs ainda" in texto_do_selo(conn, None, False)
    _cotar(conn, "PETR4", 3300)
    assert " · cotações de 29/10" in texto_do_selo(conn, None, False)
    assert "cotações de 29/10 (sem internet)" in texto_do_selo(conn, {"cotacoes": {"sem_rede": True}}, False)
    assert "opcional, com token gratuito da brapi" in texto_do_selo(conn, None, False)  # sem histórico de proventos
    _cotar(conn, "PETR4", 3300, REF)
    assert "brapi" not in texto_do_selo(conn, None, False)


# --- A lista ---------------------------------------------------------------------------------------------


def test_grupos_acoes_e_fiis_com_os_numeros_da_posicao(app, conn):
    petr, hglg = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_da_acao(petr)
    assert c.ticker.text() == "PETR4" and c.nome.texto() == "Petrobras PN"
    assert c.quantidade.text() == "90" and c.apoio_quantidade.text() == "ações"
    assert c.preco_medio.text() == "R$ 32,00" and c.apoio_preco_medio.text() == "custo R$ 2.880"
    assert c.cotacao.text() == "R$ 33,00" and c.apoio_cotacao.text() == "de 29/10"
    assert c.valor_atual.text() == "R$ 2.970,00"
    assert c.resultado.text() == "+R$ 90,00" and c.resultado.property("tom") == "positivo"
    assert c.pilula_resultado.text() == "▲ +3,12%" and c.pilula_resultado.property("variante") == "bom"
    assert c.apoio_resultado.text() == "lucro nas vendas: +R$ 480,00"
    assert c.proventos.text() == "R$ 45,50" and c.apoio_proventos.text().startswith("DY 1,53%")
    f = aba.cartao_da_acao(hglg)
    assert f.apoio_quantidade.text() == "cotas"
    assert [x.id_ativo for x in aba.cartoes_acoes()] == [petr, hglg]


def test_posicao_sem_cotacao_diz_sem_cotacao(app, conn):
    _, hglg = _cenario(conn)
    c = _aba(conn).cartao_da_acao(hglg)
    assert c.cotacao.text() == "—" and c.apoio_cotacao.text() == "sem cotação" and c.valor_atual.text() == "—"
    assert c.resultado.text() == "—" and c.pilula_resultado.text() == "sem cotação" and c.proventos.text() == "—"
    assert c.apoio_proventos.text() == "nenhum recebido"


def test_resultado_negativo_leva_pilula_ruim(app, conn):
    petr, _ = _cenario(conn)
    _cotar(conn, "PETR4", 2500)
    c = _aba(conn).cartao_da_acao(petr)
    assert c.resultado.property("tom") == "negativo" and c.pilula_resultado.text().startswith("▼ −")
    assert c.pilula_resultado.property("variante") == "ruim"


def test_vender_desabilitado_sem_cotas_e_encerrado_so_na_lista_de_encerrados(app, conn):
    petr, _ = _cenario(conn)
    rv.vender(conn, petr, date(2026, 10, 1), 90, 3500)
    aba = _aba(conn)
    assert not aba.cartao_da_acao(petr).botao_vender.isEnabled()
    aba.encerrar(petr)
    assert aba.cartao_da_acao(petr) is None
    aba.mostrar_encerrados(True)
    c = aba.cartao_da_acao(petr)
    assert c.pilula_extra.isVisibleTo(c) and not c.botao_comprar.isVisibleTo(c) and c.botao_reativar.isVisibleTo(c)
    aba.reativar(petr)
    assert aba.cartao_da_acao(petr) is not None and not aba.cartao_da_acao(petr).botao_reativar.isVisibleTo(aba)


def test_encerrar_com_cotas_abre_a_venda_com_o_aviso(app, conn):
    petr, _ = _cenario(conn)
    aba = _aba(conn)
    aba.encerrar(petr)
    c = aba.cartao_da_acao(petr)
    assert c.modo_form == "vender" and "Venda tudo" in c.form.erro.text()
    assert inv.obter(conn, petr).ativo


def test_topo_inclui_renda_variavel(app, conn):
    _cenario(conn)
    aba = _aba(conn)
    pat = inv.patrimonio(conn, HOJE)
    assert pat["renda_variavel"] == 297_000 + 160_000  # PETR4 cotada, HGLG11 pelo custo
    assert aba._patrimonio.valor.text() == "R$ 4.570,00"
    assert "proventos" in aba._previsao.apoio.text()


# --- Formulários inline ------------------------------------------------------------------------------------


def test_comprar_com_conta_pelo_formulario(app, conn):
    petr, _ = _cenario(conn)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    c = aba.cartao_da_acao(petr)
    assert c.form.isHidden()
    c.botao_comprar.click()
    QApplication.processEvents()
    assert c.modo_form == "comprar" and not c.form.isHidden() and c.form.preco.text() == "33,00"  # sugere a cotação
    assert c.form.usar_conta.isChecked() and c.form.conta.currentText() == "Inter"
    c.form.quantidade.setText("10")
    c.form.taxas.setText("2,50")
    antes = relatorios.em_conta(conn, HOJE)
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    assert avisos == ["Compra registrada: PETR4"]
    assert relatorios.em_conta(conn, HOJE) == antes - 33_250
    c = aba.cartao_da_acao(petr)
    assert c.quantidade.text() == "100" and c.form.isHidden()


def test_comprar_sem_conta_nao_mexe_no_dinheiro(app, conn):
    petr, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_da_acao(petr)
    c.botao_comprar.click()
    c.form.quantidade.setText("10")
    c.form.usar_conta.setChecked(False)
    assert not c.form.conta.isEnabled()
    n = conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]
    antes = relatorios.em_conta(conn, HOJE)
    c.form.botao_confirmar.click()
    assert relatorios.em_conta(conn, HOJE) == antes and conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == n
    assert not rv.operacoes(conn, petr)[-1].afeta_conta


def test_formulario_de_compra_mostra_erros_inline(app, conn):
    petr, _ = _cenario(conn)
    c = _aba(conn).cartao_da_acao(petr)
    c.botao_comprar.click()
    c.form.preco.setText("")
    c.form.botao_confirmar.click()
    assert "quantidade" in c.form.erro.text()
    c.form.quantidade.setText("5")
    c.form.botao_confirmar.click()
    assert "preço" in c.form.erro.text()
    c.form.preco.setText("33,00")
    c.form.taxas.setText("abc")
    c.form.botao_confirmar.click()
    assert "Taxas" in c.form.erro.text()
    c.form.taxas.setText("1")
    assert c.form.erro.text() == ""  # digitar limpa o erro
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv WHERE tipo = 'compra'").fetchone()[0] == 3  # nada novo gravado


def test_vender_alem_do_que_tem_mostra_o_erro_no_formulario(app, conn):
    petr, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_da_acao(petr)
    c.botao_vender.click()
    assert "você tem 90" in c.form.titulo.text().lower() and c.form.quantidade.placeholderText() == "até 90"
    c.form.quantidade.setText("100")
    c.form.preco.setText("40")
    c.form.botao_confirmar.click()
    assert "só tem 90" in c.form.erro.text() and not c.form.isHidden()
    c.form.quantidade.setText("90")
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    assert aba.cartao_da_acao(petr).quantidade.text() == "0" and aba.cartao_da_acao(petr).apoio_preco_medio.text() == "sem posição"


def test_provento_pelo_formulario_com_e_sem_conta(app, conn):
    petr, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_da_acao(petr)
    c.botao_provento.click()
    assert c.modo_form == "provento" and c.form.quantidade.isHidden() and not c.form.tipo.isHidden()
    c.form.tipo.setCurrentIndex(c.form.tipo.findData("jcp"))
    c.form.preco.setText("12,34")
    antes = relatorios.em_conta(conn, HOJE)
    c.form.botao_confirmar.click()
    assert relatorios.em_conta(conn, HOJE) == antes + 1234
    c = aba.cartao_da_acao(petr)
    assert c.proventos.text() == "R$ 57,84"
    c.botao_provento.click()
    c.form.preco.setText("1")
    c.form.usar_conta.setChecked(False)
    c.form.botao_confirmar.click()
    assert relatorios.em_conta(conn, HOJE) == antes + 1234
    assert rv.posicao(conn, petr, HOJE)["proventos_total"] == 4550 + 1234 + 100


def test_so_um_formulario_fica_aberto_entre_acoes_e_renda_fixa(app, conn):
    petr, hglg = _cenario(conn)
    cdb = inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    aba = _aba(conn)
    aba.cartao_da_acao(petr).botao_comprar.click()
    aba.cartao_da_acao(hglg).botao_provento.click()
    assert aba.cartao_da_acao(petr).form.isHidden() and not aba.cartao_da_acao(hglg).form.isHidden()
    aba.cartao_do_ativo(cdb).botao_aportar.click()
    assert aba.cartao_da_acao(hglg).form.isHidden() and not aba.cartao_do_ativo(cdb).form.isHidden()


# --- Cartão Proventos -------------------------------------------------------------------------------------


def test_cartao_proventos_so_aparece_com_acao_ou_fii(app, conn):
    aba = _aba(conn)
    assert aba._cartao_proventos.isHidden()
    _cenario(conn)
    aba.recarregar()
    assert not aba._cartao_proventos.isHidden()


def test_cartao_proventos_mostra_recebidos_estimativa_e_barras(app, conn):
    _cenario(conn)
    aba = _aba(conn)
    assert aba._recebidos.valor.text() == "R$ 45,50" and aba._recebidos.apoio.text() == "em 2026, até hoje"
    assert aba._estimativa.valor.text() == "R$ 126,00"  # 90 cotas × (0,40 + 0,50 + 0,50)
    assert aba.grafico_proventos._pilha.currentWidget() is aba.grafico_proventos._conteudo
    barras = aba.grafico_proventos._barras
    assert len(barras._meses) == 12 and barras._meses[0]["longo"] == "Out/2026"
    assert "Fev/2027" in barras.dica_do_mes(4) and "R$ 45" in barras.dica_do_mes(4) and "nenhum" in barras.dica_do_mes(0)
    barras.resize(900, 300)
    assert not barras.grab().isNull()


def test_sem_historico_o_painel_de_proventos_mostra_estado_vazio(app, conn):
    petr = _acao(conn)
    rv.comprar(conn, petr, date(2026, 3, 2), 100, 3000)
    aba = _aba(conn)
    assert aba.grafico_proventos._pilha.currentWidget() is aba.grafico_proventos._vazio
    assert aba._estimativa.valor.text() == "R$ 0,00"


# --- Diálogo do ativo ---------------------------------------------------------------------------------------


def _dialogo(conn, ativo_id=None):
    d = DialogoAtivo(conn, ativo_id, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_dialogo_acao_troca_taxa_por_ticker_em_maiusculas(app, conn):
    d = _dialogo(conn)
    assert list(d.tipo._botoes) == ["Renda fixa", "Tesouro Direto", "Ação", "FII"]
    assert not d._bloco_taxa.isHidden() and d._bloco_ticker.isHidden()
    d.tipo.definir("Ação")
    d._tipo_mudou()
    QApplication.processEvents()
    assert d._bloco_taxa.isHidden() and not d._bloco_ticker.isHidden() and d._bloco_vencimento.isHidden()
    d.ticker.setFocus()
    d.ticker.insert("petr4")
    d.ticker.textEdited.emit("petr4")
    assert d.ticker.text() == "PETR4"
    avisos = []
    d.gravado.connect(avisos.append)
    d.nome.setText("Petrobras PN")
    assert d.salvar()
    a = inv.obter(conn, conn.execute("SELECT id FROM ativos").fetchone()[0])
    assert (a.tipo, a.ticker, a.indexador, a.taxa, a.nome) == ("acao", "PETR4", None, None, "Petrobras PN")
    assert avisos == ["Investimento criado: Petrobras PN"]


def test_dialogo_valida_ticker_inline(app, conn):
    _acao(conn, "PETR4")
    d = _dialogo(conn)
    d.tipo.definir("FII")
    d._tipo_mudou()
    d.nome.setText("Fundo")
    assert not d.salvar() and "ticker" in d.erro_ticker.text().lower()
    d.ticker.setText("hglg")
    assert not d.salvar() and "inválido" in d.erro_ticker.text()
    d.ticker.textEdited.emit("petr4")
    assert d.erro_ticker.text() == "" and d.ticker.text() == "PETR4"
    assert not d.salvar() and "Já existe" in d.erro_ticker.text()
    d.ticker.setText("HGLG11")
    assert d.salvar()
    assert conn.execute("SELECT COUNT(*) FROM ativos").fetchone()[0] == 2
    d2 = _dialogo(conn)
    d2.tipo.definir("Ação")
    d2._tipo_mudou()
    assert not d2.salvar() and "nome" in d2.erro_nome.text() and "ticker" in d2.erro_ticker.text().lower()


def test_dialogo_edita_acao_e_so_alterna_dentro_da_familia(app, conn):
    petr = _acao(conn, "PETR4", nome="Petrobras")
    cdb = inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    d = _dialogo(conn, petr)
    assert d.ticker.text() == "PETR4" and d.nome.text() == "Petrobras" and d.tipo.atual() == "Ação"
    assert d.tipo_atual == "acao" and not d._bloco_ticker.isHidden()
    d.ticker.setText("PETR3")
    assert d.salvar() and inv.obter(conn, petr).ticker == "PETR3"
    assert list(d.tipo._botoes) == ["Ação", "FII"]  # a outra família não é oferecida na edição
    rf = _dialogo(conn, cdb)
    assert list(rf.tipo._botoes) == ["Renda fixa", "Tesouro Direto"]
    assert rf.tipo_atual == "renda_fixa" and rf._bloco_ticker.isHidden()


# --- Importar da B3 -------------------------------------------------------------------------------------------


@pytest.fixture
def planilha(tmp_path):
    return falsa.gerar(tmp_path / "movimentacao.xlsx")


def _dialogo_b3(conn):
    d = DialogoImportarB3(conn, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_importar_mostra_a_previa_e_so_grava_ao_confirmar(app, conn, planilha):
    d = _dialogo_b3(conn)
    assert not d.botao_importar.isEnabled() and d.arquivo.texto() == "Nenhum arquivo escolhido"
    assert d.carregar(planilha)
    assert d.arquivo.texto() == "movimentacao.xlsx"
    assert d.pilula_novas.text() == "▲ 10 novas" and d.pilula_duplicadas.text() == "0 já importadas" and d.pilula_ignoradas.text() == "! 4 ignoradas"
    assert [l.ticker.text() for l in d.linhas_ativos] == ["PETR4", "VALE3", "HGLG11", "MXRF11"]
    assert [l.tipo.currentData() for l in d.linhas_ativos] == ["acao", "acao", "fii", "fii"]
    assert d.botao_importar.isEnabled() and d.botao_importar.text() == "Importar 10 operações"
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 0
    avisos = []
    d.gravado.connect(avisos.append)
    assert d.confirmar()
    assert avisos == ["10 operações importadas da B3 · 4 ativos criados"] and d.result() == DialogoImportarB3.DialogCode.Accepted
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 10


def test_tipo_do_ativo_novo_pode_ser_trocado_na_previa(app, conn, planilha):
    d = _dialogo_b3(conn)
    d.carregar(planilha)
    mxrf = next(l for l in d.linhas_ativos if l.ticker.text() == "MXRF11")
    mxrf.tipo.setCurrentIndex(mxrf.tipo.findData("acao"))
    d.confirmar()
    assert conn.execute("SELECT tipo FROM ativos WHERE ticker = 'MXRF11'").fetchone()[0] == "acao"


def test_reimportar_mostra_duplicadas_e_nao_deixa_importar(app, conn, planilha):
    d = _dialogo_b3(conn)
    d.carregar(planilha)
    d.confirmar()
    d2 = _dialogo_b3(conn)
    d2.carregar(planilha)
    assert d2.pilula_duplicadas.text() == "10 já importadas" and d2.pilula_novas.text() == "0 novas"
    assert not d2.botao_importar.isEnabled() and not d2.confirmar()
    assert not d2.linhas_ativos


def test_arquivo_invalido_mostra_o_erro_na_janela(app, conn, tmp_path):
    ruim = tmp_path / "x.xlsx"
    ruim.write_text("não é planilha")
    d = _dialogo_b3(conn)
    assert not d.carregar(ruim)
    assert not d.erro.isHidden() and d.erro.text().startswith("!  Não consegui abrir") and not d.botao_importar.isEnabled()
    assert d.carregar(falsa.gerar(tmp_path / "ok.xlsx")) and d.erro.isHidden()  # um bom arquivo depois limpa o erro
    assert d.confirmar()


def test_descrever_operacao():
    from meu_cofre.importar_b3 import OperacaoB3

    compra = OperacaoB3(2, "PETR4", "", "compra", date(2026, 3, 2), 100, 3500, 350_000, "k")
    provento = OperacaoB3(3, "VALE3", "", "jcp", date(2026, 6, 10), 40, None, 2210, "k2")
    assert descrever_operacao(compra) == "02/03/2026 · Compra · PETR4 · 100 × R$ 35,00 = R$ 3.500,00"
    assert descrever_operacao(provento) == "10/06/2026 · JCP · VALE3 · R$ 22,10"


def test_botao_da_aba_abre_a_janela_e_a_lista_se_atualiza(app, conn, planilha):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    d = aba.importar_b3(str(planilha))
    QApplication.processEvents()
    assert aba.dialogo_b3 is d and d.previa is not None and len(d.previa.novas) == 10
    d.confirmar()
    QApplication.processEvents()
    assert avisos and {c.ticker.text() for c in aba.cartoes_acoes()} == {"PETR4", "VALE3", "HGLG11", "MXRF11"}
    assert not aba._cartao_proventos.isHidden()
    aba.botao_importar.click()  # o botão da aba abre a janela (sem arquivo)
    QApplication.processEvents()
    assert aba.dialogo_b3 is not None and aba.dialogo_b3.previa is None
    aba.dialogo_b3.reject()


# --- Cotações em segundo plano --------------------------------------------------------------------------------


def _esperar(condicao, limite=5.0):
    fim = time.monotonic() + limite
    while not condicao() and time.monotonic() < fim:
        QApplication.processEvents()
        time.sleep(0.005)
    assert condicao()


class _FalsoCotacao:
    def __init__(self, falha=None):
        self.pedidos, self.falha = [], falha

    def __call__(self, ticker, **_opcoes):
        self.pedidos.append(ticker)
        if self.falha:
            raise self.falha
        return cotacoes.Dados(3300, date(2026, 10, 29), [], [(date(2026, 5, 15), 0.5)])


def _taxas_falsas(serie, de, ate):
    return {"cdi": [(date(2026, 10, 29), 0.04)], "selic": [(date(2026, 10, 29), 0.04)], "ipca": [(date(2026, 9, 1), 0.4)]}[serie]


def test_cotacoes_sao_buscadas_junto_com_as_taxas_so_dos_ativos_ativos(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    _acao(conn, "PETR4")
    encerrada = _acao(conn, "VALE3")
    inv.encerrar(conn, HOJE, encerrada)
    falso = _FalsoCotacao()
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_taxas_falsas, buscar_cotacao=falso)
    avisos = []
    a.concluido.connect(avisos.append)
    assert a.disparar()
    _esperar(lambda: avisos)
    assert falso.pedidos == ["PETR4"]
    assert avisos[0]["ok"] and avisos[0]["cotacoes"] == {"ok": True, "sem_rede": False, "novos": 1, "erros": {}, "fontes": {"PETR4": ""}}
    assert cotacoes.ultima(conn, "PETR4", HOJE) == (3300, date(2026, 10, 29))
    assert cotacoes.proventos_por_cota(conn, "PETR4") == [(date(2026, 5, 15), 0.5)]
    a.aguardar()


def test_cotacao_que_falha_vira_status_sem_derrubar_as_taxas(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    _acao(conn, "PETR4")
    _cotar(conn, "PETR4", 3000)
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_taxas_falsas, buscar_cotacao=_FalsoCotacao(OSError("sem rede")))
    avisos = []
    a.concluido.connect(avisos.append)
    a.disparar()
    _esperar(lambda: avisos)
    assert avisos[0]["ok"] and avisos[0]["cotacoes"]["sem_rede"] and "PETR4" in avisos[0]["cotacoes"]["erros"]
    assert cotacoes.ultima(conn, "PETR4", HOJE)[0] == 3000  # a última guardada continua
    a.aguardar()


def test_so_cotacoes_tambem_dispara_quando_as_taxas_estao_em_dia(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    _acao(conn, "PETR4")
    falso_taxas = lambda *a: []  # noqa: E731 (nada novo)
    a1 = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_taxas_falsas, buscar_cotacao=_FalsoCotacao())
    a1.disparar()
    _esperar(lambda: not a1.em_andamento)
    a1.aguardar()
    falso = _FalsoCotacao()
    a2 = AtualizadorIndicadores(conn, lambda: date(2026, 10, 30), buscar=falso_taxas, buscar_cotacao=falso)
    assert a2.disparar()  # taxas em dia, mas há cotação pra buscar
    _esperar(lambda: not a2.em_andamento)
    assert falso.pedidos == ["PETR4"]
    a2.aguardar()


def test_sem_rede_ligada_nenhuma_cotacao_e_buscada(app, conn):
    _acao(conn, "PETR4")
    falso = _FalsoCotacao()
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar_cotacao=falso)
    assert not a.disparar(forcar=True) and falso.pedidos == []


def test_janela_atualiza_a_aba_quando_as_cotacoes_chegam(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    petr = _acao(conn, "PETR4")
    rv.comprar(conn, petr, date(2026, 3, 2), 100, 3000)
    monkeypatch.setattr(cotacoes, "_buscar_http", _FalsoCotacao())
    from meu_cofre import indicadores

    monkeypatch.setattr(indicadores, "_buscar_http", _taxas_falsas)
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.show()
    _esperar(lambda: not j._atualizador.em_andamento)
    QApplication.processEvents()
    assert j.aba_investimentos.cartao_da_acao(petr).cotacao.text() == "R$ 33,00"
    assert "cotações de 29/10" in j.aba_investimentos.selo.text()
    j._atualizador.aguardar()


def _rede_b3_com_yahoo_429(monkeypatch):
    """B3 responde; o Yahoo dá 429. Devolve a lista das URLs pedidas."""
    import urllib.error

    pedidos = []

    def obter(url, cabecalhos=None):
        pedidos.append(url)
        if "yahoo" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return {"BizSts": {"cd": "OK"}, "Msg": {"dtTm": "2026-10-29 18:00:00"},
                "Trad": [{"scty": {"SctyQtn": {"curPrc": 33.0}, "mkt": {"nm": "Vista"}, "symb": "PETR4"}}]}

    monkeypatch.setattr(cotacoes, "_obter_json", obter)
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    return pedidos


def test_yahoo_429_pausa_o_yahoo_e_depois_da_pausa_volta(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    _acao(conn, "PETR4")
    pedidos = _rede_b3_com_yahoo_429(monkeypatch)
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_taxas_falsas)
    a.disparar()
    _esperar(lambda: not a.em_andamento)
    QApplication.processEvents()
    assert any("yahoo" in u for u in pedidos) and cotacoes.yahoo_pausado(conn)  # o preço veio da B3 e o 429 valeu mesmo assim

    pedidos.clear()
    a.disparar(forcar=True)
    _esperar(lambda: not a.em_andamento)
    assert pedidos and not any("yahoo" in u for u in pedidos)  # pausado: nem tenta

    db.gravar_preferencia(conn, cotacoes.PREF_YAHOO, "2000-01-01T00:00:00")  # a pausa venceu
    pedidos.clear()
    a.disparar(forcar=True)
    _esperar(lambda: not a.em_andamento)
    assert any("yahoo" in u for u in pedidos)
    a.aguardar()


def test_atualizador_usa_o_token_salvo_no_app(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    _acao(conn, "PETR4")
    db.gravar_preferencia(conn, cotacoes.PREF_TOKEN, "salvo")
    chamadas = []
    monkeypatch.setattr(cotacoes, "_buscar_http", lambda ticker, **opcoes: chamadas.append(opcoes) or cotacoes.Dados(3300, date(2026, 10, 29)))
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_taxas_falsas)
    a.disparar()
    _esperar(lambda: not a.em_andamento)
    assert chamadas == [{"token": "salvo", "yahoo_pausado": False}]
    a.aguardar()


def test_dialogo_cotacoes_salva_testa_e_remove_o_token(app, conn, monkeypatch):
    from meu_cofre.ui.dialogo_cotacoes import DialogoCotacoes

    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    monkeypatch.setattr(cotacoes, "testar_token", lambda token: "Token inválido ou sem permissão." if token == "ruim" else None)
    d = DialogoCotacoes(conn)
    avisos = []
    d.alterado.connect(lambda: avisos.append(1))
    d.token.setText("ruim")
    d.testar()
    assert d.resultado.text() == "testando…"
    _esperar(lambda: d.botao_testar.isEnabled() and d.resultado.text() != "testando…")
    assert "inválido" in d.resultado.text()
    d.token.setText(" bom ")
    d.salvar()
    assert cotacoes.token_brapi(conn) == "bom" and avisos == [1]
    d2 = DialogoCotacoes(conn)
    assert d2.token.text() == "bom" and d2.botao_remover.isEnabled()
    d2.alterado.connect(lambda: avisos.append(2))
    d2.remover()
    assert cotacoes.token_brapi(conn) == "" and avisos == [1, 2]
    # no app a janela tem pai e some ao fechar; aqui ela e o pool da thread do teste morreriam só no fim do Python
    shiboken6.delete(d)
    shiboken6.delete(d2)


def test_dialogo_cotacoes_com_token_do_ambiente_nao_deixa_editar(app, conn, monkeypatch):
    from meu_cofre.ui.dialogo_cotacoes import DialogoCotacoes

    monkeypatch.setenv(cotacoes.VAR_BRAPI, "amb")
    d = DialogoCotacoes(conn)
    assert not d.token.isEnabled() and not d.botao_salvar.isEnabled() and cotacoes.VAR_BRAPI in d.resultado.text()


def test_salvar_o_token_forca_nova_busca_e_atualiza_o_selo(app, conn, monkeypatch):
    _acao(conn, "PETR4")
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    j = JanelaPrincipal(conn, hoje=HOJE)
    assert "token gratuito da brapi" in j.aba_investimentos.selo.text()
    chamadas = []
    monkeypatch.setattr(j._atualizador, "disparar", lambda forcar=False: chamadas.append(forcar))
    d = j.aba_investimentos.abrir_cotacoes()
    d.token.setText("t")
    d.salvar()
    assert chamadas == [True] and "token gratuito da brapi" not in j.aba_investimentos.selo.text()
