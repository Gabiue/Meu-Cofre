"""Aba "Investimentos" (F8): números do topo, gráfico, lista, formulários inline, diálogo, atualização de taxas em
segundo plano e cabeçalho responsivo. Offscreen e sem internet (as taxas do Banco Central são falsas)."""

import time
from datetime import date, timedelta

import pytest
from conftest import id_conta
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMessageBox

from meu_cofre import indicadores
from meu_cofre import investimentos as inv
from meu_cofre import lancamentos as lanc
from meu_cofre import metas
from meu_cofre.ui import tema
from meu_cofre.ui.aba_investimentos import (
    AbaInvestimentos,
    explicacao_da_previsao,
    texto_da_previsao,
    texto_do_selo,
)
from meu_cofre.ui.atualizador import AtualizadorIndicadores
from meu_cofre.ui.dialogo_ativo import DialogoAtivo, ler_taxa, texto_taxa
from meu_cofre.ui.graficos import PainelPatrimonio
from meu_cofre.ui.janela import ABAS, JanelaPrincipal

HOJE = date(2026, 10, 30)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.fixture(autouse=True)
def sem_caixa_de_mensagem(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: pytest.fail("nada de QMessageBox"))


def _cdi(conn, inicio=date(2026, 1, 1), fim=date(2026, 10, 29)):
    d, pontos = inicio, []
    while d <= fim:
        if inv.dia_util(d):
            pontos.append((d, 0.04))
        d += timedelta(days=1)
    indicadores.gravar(conn, {"cdi": pontos})


def _aba(conn, tamanho=(1400, 1400)):
    a = AbaInvestimentos(conn, lambda: HOJE)
    a.resize(*tamanho)
    a.show()
    QApplication.processEvents()
    return a


def _janela(conn, tamanho=(1400, 900)):
    j = JanelaPrincipal(conn, hoje=HOJE)
    j.resize(*tamanho)
    j.show()
    QApplication.processEvents()
    return j


def _novo(conn, nome="CDB Inter", tipo="renda_fixa", indexador="cdi", taxa=110.0, **kw):
    return inv.criar(conn, HOJE, nome=nome, tipo=tipo, indexador=indexador, taxa=taxa, **kw)


def _cenario(conn):
    """Dinheiro em conta, taxas falsas, um CDB, um Tesouro e dinheiro nas caixinhas."""
    _cdi(conn)
    lanc.registrar(
        conn, entrada=True, valor_centavos=2_000_000, descricao="x", categoria_id=None, metodo="pix",
        conta_id=id_conta(conn, "Inter"), data=date(2026, 1, 10),
    )
    cdb = _novo(conn, instituicao="Inter", vencimento=date(2028, 1, 10))
    tesouro = _novo(conn, "Tesouro Selic 2029", tipo="tesouro", indexador="selic", taxa=100.0)
    inv.aportar(conn, cdb, 500_000, id_conta(conn, "Inter"), date(2026, 3, 2))
    inv.aportar(conn, tesouro, 300_000, id_conta(conn, "Inter"), date(2026, 6, 1))
    m = metas.criar(conn, HOJE, nome="Viagem", valor_alvo_centavos=480_000, modo="mensal", mensal_centavos=50_000)
    metas.guardar(conn, m, 100_000, id_conta(conn, "Inter"), date(2026, 7, 1))
    return cdb, tesouro


def _nomes(aba):
    return [c.nome.texto() for c in aba.cartoes()]


# --- Textos puros ------------------------------------------------------------------------------------


def test_texto_da_previsao_decompoe_com_sinais():
    prev = {"patrimonio_hoje": 820_000, "sobra_projetada": 410_000, "rendimento_projetado": 31_000}
    assert texto_da_previsao(prev) == "R$ 8.200 hoje + R$ 4.100 de sobra + R$ 310 de rendimento"
    prev["sobra_projetada"] = -50_000
    assert "− R$ 500 de sobra" in texto_da_previsao(prev)


def test_explicacao_da_previsao_cita_a_sobra_e_o_pouco_de_dados():
    prev = {"sobra_mensal": 200_000, "meses_restantes": 2.03, "sobra_estimativa": True}
    assert "R$ 2.000/mês × 2,0 meses" in explicacao_da_previsao(prev)
    assert "poucos dados" in explicacao_da_previsao(prev)
    assert "poucos dados" not in explicacao_da_previsao({**prev, "sobra_estimativa": False})


def test_selo_das_taxas(conn):
    assert texto_do_selo(conn, None, False).startswith("ainda sem taxas do Banco Central")
    assert texto_do_selo(conn, {"sem_rede": True}, False).startswith("sem internet e sem taxas guardadas")
    _cdi(conn)
    assert texto_do_selo(conn, None, False) == "taxas do Banco Central de 29/10 · valores brutos, antes do IR"
    assert texto_do_selo(conn, {"sem_rede": True}, False).startswith("sem internet: taxas de 29/10")
    assert texto_do_selo(conn, None, True).startswith("atualizando as taxas do Banco Central")


def test_ler_e_escrever_taxa():
    assert ler_taxa("110") == 110.0 and ler_taxa(" 6,2 ") == 6.2 and ler_taxa("12.5%") == 12.5
    assert ler_taxa("") is None and ler_taxa("abc") is None and ler_taxa("nan") is None and ler_taxa("inf") is None
    assert texto_taxa(6.2) == "6,2" and texto_taxa(110.0) == "110"


# --- A aba -----------------------------------------------------------------------------------------------------


def test_aba_vazia_mostra_estado_vazio_e_zeros(app, conn):
    aba = _aba(conn)
    assert not aba._vazio.isHidden() and aba.cartoes() == [] and aba.botao_encerrados.isHidden()
    assert aba._patrimonio.valor.text() == "R$ 0,00" and aba._investido.valor.text() == "R$ 0,00"
    assert aba._pilula_cdi.isHidden()
    assert aba._previsao.valor.text() == "R$ 0,00"
    assert aba.selo.text().startswith("ainda sem taxas do Banco Central")


def test_numeros_do_topo(app, conn):
    cdb, tesouro = _cenario(conn)
    aba = _aba(conn)
    pat = inv.patrimonio(conn, HOJE)
    assert aba._patrimonio.valor.text() == lanc.formatar_brl(pat["total"])
    assert aba._investido.valor.text() == lanc.formatar_brl(pat["investimentos"] + pat["caixinhas"])
    assert "das caixinhas" in aba._investido.apoio.text()
    r = inv.resumo(conn, HOJE)
    assert aba._rendimento.valor.text().startswith("+R$ ") and aba._rendimento.apoio.text().endswith("no ano")
    assert not aba._pilula_cdi.isHidden() and aba._pilula_cdi.text().endswith(f"{r.pct_cdi:.0f}% do CDI")
    assert aba._previsao.valor.text() == lanc.formatar_brl(r.previsao["total"])
    assert aba._previsao.valor.property("destaque") is True  # dourado
    assert "hoje" in aba._previsao.apoio.text() and "de rendimento" in aba._previsao.apoio.text()
    assert aba.selo.text().startswith("taxas do Banco Central de 29/10")


def test_pilula_do_cdi_fica_verde_so_a_partir_de_cem_por_cento(app, conn):
    _cdi(conn)
    _aba_ = _novo(conn, taxa=110.0)
    inv.aportar(conn, _aba_, 500_000, id_conta(conn, "Inter"), date(2026, 3, 2))
    assert _aba(conn)._pilula_cdi.property("variante") == "bom"
    inv.editar(conn, HOJE, _aba_, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=80.0)
    assert _aba(conn)._pilula_cdi.property("variante") == "neutra"


def test_lista_agrupada_por_tipo_com_caixinhas_primeiro(app, conn):
    _cenario(conn)
    aba = _aba(conn)
    assert _nomes(aba) == ["Caixinhas (metas)", "CDB Inter", "Tesouro Selic 2029"]
    grupos = [r.text() for r in aba.findChildren(type(aba._patrimonio.rotulo)) if r.objectName() == "GrupoNome"]
    assert grupos == ["RENDA FIXA", "TESOURO DIRETO"]
    cdb = aba.cartoes()[1]
    p = cdb.posicao
    assert cdb.pilula_taxa.text() == "110% do CDI" and cdb.instituicao.texto() == "Inter"
    assert cdb.vencimento.text() == "vence 10/01/2028" and not cdb.vencimento.isHidden()
    assert cdb.aplicado.text() == "R$ 5.000,00" and cdb.valor_atual.text() == lanc.formatar_brl(p.valor_atual)
    assert cdb.rendimento.text() == f"+{lanc.formatar_brl(p.rendimento)}" and cdb.rendimento.property("tom") == "positivo"
    assert cdb.apoio_rendimento.text().startswith("+") and cdb.apoio_valor.text() == "estimado, bruto"
    assert not aba._vazio.isVisible()


def test_valor_estimado_com_taxa_incompleta_leva_aproximado_e_saldo_real_leva_a_data(app, conn):
    _novo(conn)
    cdb = inv.listar(conn, HOJE) and None
    a = _novo(conn, "Outro")
    inv.aportar(conn, a, 100_000, id_conta(conn, "Inter"), date(2026, 10, 1))  # sem série: taxa em falta
    aba = _aba(conn)
    c = aba.cartao_do_ativo(a)
    assert c.valor_atual.text().startswith("≈ ") and c.apoio_valor.text() == "estimado, taxas incompletas"
    inv.registrar_saldo_real(conn, a, 100_500, date(2026, 10, 20))
    aba.recarregar()
    assert aba.cartao_do_ativo(a).apoio_valor.text() == "saldo real de 20/10"


def test_caixinhas_nao_aportam_e_levam_pra_aba_metas(app, conn):
    _cenario(conn)
    aba = _aba(conn)
    cx = aba.cartoes()[0]
    assert cx.pilula_extra.text() == "metas" and not cx.nota.isHidden()
    assert cx.botao_aportar.isHidden() and cx.botao_resgatar.isHidden() and cx.botao_encerrar.isHidden()
    assert cx.botao_editar.text() == "Editar taxa" and not cx.botao_saldo.isHidden()
    pedidos = []
    aba.ver_metas.connect(lambda: pedidos.append(1))
    cx.botao_metas.click()
    assert pedidos == [1]


def test_aportar_pelo_formulario(app, conn):
    cdb, _ = _cenario(conn)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    c = aba.cartao_do_ativo(cdb)
    assert c.form.isHidden()
    c.botao_aportar.click()
    QApplication.processEvents()
    assert not c.form.isHidden() and c.tipo_form == "aportar" and c.form.conta.currentText() == "Inter"
    assert not c.form.conta.isHidden()
    c.form.valor.setText("1.000,00")
    antes = inv.patrimonio(conn, HOJE)["total"]
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    assert avisos == ["Aportado R$ 1.000,00 em CDB Inter"]
    assert aba.cartao_do_ativo(cdb).aplicado.text() == "R$ 6.000,00"
    assert inv.patrimonio(conn, HOJE)["total"] == antes  # transferência não muda o patrimônio


def test_formulario_de_aporte_mostra_erros_inline(app, conn):
    cdb, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_do_ativo(cdb)
    c.botao_aportar.click()
    c.form.valor.setText("")
    c.form.botao_confirmar.click()
    assert "maior que zero" in c.form.erro.text()
    c.form.valor.setText("abc")
    c.form.botao_confirmar.click()
    assert "inválido" in c.form.erro.text()
    c.form.valor.setText("10")
    assert c.form.erro.text() == ""  # digitar limpa o erro


def test_so_um_formulario_fica_aberto(app, conn):
    cdb, tesouro = _cenario(conn)
    aba = _aba(conn)
    aba.abrir_form(cdb, "aportar")
    aba.abrir_form(tesouro, "resgatar")
    assert aba.cartao_do_ativo(cdb).form.isHidden() and not aba.cartao_do_ativo(tesouro).form.isHidden()


def test_resgatar_pelo_formulario_e_o_limite_aparece_na_linha(app, conn):
    cdb, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_do_ativo(cdb)
    c.botao_resgatar.click()
    assert "disponíveis" in c.form.titulo.text().lower() and c.form.rotulo_conta.text() == "Devolver pra"
    c.form.valor.setText("999.999,00")
    c.form.botao_confirmar.click()
    assert "Só há" in c.form.erro.text() and not c.form.isHidden()
    c.form.valor.setText("1.000,00")
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    assert aba.cartao_do_ativo(cdb).aplicado.text() == "R$ 4.000,00"


def test_atualizar_saldo_pelo_formulario_sem_pedir_conta(app, conn):
    cdb, _ = _cenario(conn)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    c = aba.cartao_do_ativo(cdb)
    c.botao_saldo.click()
    QApplication.processEvents()
    assert c.tipo_form == "saldo" and c.form.conta.isHidden() and c.form.rotulo_conta.isHidden()
    assert c.form.valor.text().replace(".", "") == lanc.formatar_brl(c.posicao.valor_atual).removeprefix("R$ ").replace(".", "")
    c.form.valor.setText("5.321,00")
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    assert avisos == ["Saldo de CDB Inter atualizado para R$ 5.321,00"]
    novo = aba.cartao_do_ativo(cdb)
    assert novo.apoio_valor.text() == "saldo real de 30/10"
    assert novo.rendimento.text() == "+R$ 321,00"
    c2 = aba.cartao_do_ativo(cdb)
    c2.botao_aportar.click()
    assert not c2.form.conta.isHidden()  # o formulário volta a pedir a conta


def test_encerrar_com_dinheiro_abre_o_resgate_com_o_aviso(app, conn):
    cdb, _ = _cenario(conn)
    aba = _aba(conn)
    c = aba.cartao_do_ativo(cdb)
    c.botao_encerrar.click()
    QApplication.processEvents()
    assert c.tipo_form == "resgatar" and "Resgate tudo" in c.form.erro.text()
    assert inv.obter(conn, cdb).ativo


def test_encerrar_e_reativar(app, conn):
    cdb = _novo(conn)
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.cartao_do_ativo(cdb).botao_encerrar.click()
    QApplication.processEvents()
    assert avisos == ["Investimento encerrado: CDB Inter"] and aba.cartoes() == [] and not aba._vazio.isHidden()
    assert not aba.botao_encerrados.isHidden() and aba.botao_encerrados.text() == "Mostrar encerrados (1)"
    aba.botao_encerrados.click()
    QApplication.processEvents()
    c = aba.cartao_do_ativo(cdb)
    assert c.pilula_extra.text() == "encerrado" and c.botao_aportar.isHidden() and not c.botao_reativar.isHidden()
    c.botao_reativar.click()
    QApplication.processEvents()
    assert [x.nome.texto() for x in aba.cartoes()] == ["CDB Inter"] and avisos[-1] == "Investimento reativado: CDB Inter"


def test_resgatar_desabilitado_sem_dinheiro(app, conn):
    cdb = _novo(conn)
    c = _aba(conn).cartao_do_ativo(cdb)
    assert not c.botao_resgatar.isEnabled() and c.valor_atual.text() == "R$ 0,00"
    assert c.apoio_rendimento.text() == "sem aportes"


def test_cartao_estreito_leva_editar_e_encerrar_pra_segunda_fila(app, conn):
    cdb = _novo(conn)
    aba = _aba(conn, (500, 900))
    c = aba.cartao_do_ativo(cdb)
    assert c.width() < 520 and c._estreito
    assert c.botao_editar.y() > c.botao_aportar.y()
    aba.resize(1400, 900)
    QApplication.processEvents()
    assert not c._estreito and c.botao_editar.y() == pytest.approx(c.botao_aportar.y(), abs=10)


# --- Diálogo ---------------------------------------------------------------------------------------------------


def _dialogo(conn, ativo_id=None):
    d = DialogoAtivo(conn, ativo_id, hoje=HOJE)
    d.show()
    QApplication.processEvents()
    return d


def test_dialogo_cria_o_investimento(app, conn):
    d = _dialogo(conn)
    avisos = []
    d.gravado.connect(avisos.append)
    d.nome.setText("CDB Banco X")
    d.instituicao.setText("Banco X")
    d.taxa.setText("112,5")
    assert d.previa.text() == "Na lista: 112,5% do CDI"
    d.tem_vencimento.setChecked(True)
    assert d.salvar()
    a = inv.obter(conn, conn.execute("SELECT id FROM ativos").fetchone()[0])
    assert (a.nome, a.tipo, a.instituicao, a.indexador, a.taxa) == ("CDB Banco X", "renda_fixa", "Banco X", "cdi", 112.5)
    assert a.vencimento == date(2027, 10, 30)  # sugerido: daqui a 1 ano
    assert avisos == ["Investimento criado: CDB Banco X"]


def test_rotulo_da_taxa_muda_com_o_indexador(app, conn):
    d = _dialogo(conn)
    visto = {}
    for i in range(d.indexador.count()):
        d.indexador.setCurrentIndex(i)
        visto[d.indexador_atual] = (d.rotulo_taxa.text(), d.taxa.placeholderText())
    assert visto["cdi"][0].startswith("% do CDI") and visto["selic"][0].startswith("% da Selic")
    assert visto["ipca"][0].startswith("Taxa acima do IPCA") and visto["pre"][0].startswith("Taxa prefixada")
    assert len({v[0] for v in visto.values()}) == 4
    d.indexador.setCurrentIndex(d.indexador.findData("ipca"))
    d.taxa.setText("6,2")
    assert d.previa.text() == "Na lista: IPCA + 6,2%"


def test_tesouro_nao_oferece_cdi(app, conn):
    d = _dialogo(conn)
    assert d.indexador.findData("cdi") >= 0
    d.tipo.clicar("Tesouro Direto")
    assert d.indexador.findData("cdi") == -1 and d.indexador_atual == "selic"
    assert "Tesouro Selic" in d.nome.placeholderText()
    d.tipo.clicar("Renda fixa")
    assert d.indexador.findData("cdi") >= 0


def test_dialogo_mostra_erros_inline_e_nao_grava(app, conn):
    d = _dialogo(conn)
    assert not d.salvar()
    assert "nome" in d.erro_nome.text() and "Digite a taxa" in d.erro_taxa.text()
    assert d.nome.property("erro") is True and d.taxa.property("erro") is True
    d.nome.setText("X")
    assert d.erro_nome.text() == "" and d.nome.property("erro") is False
    d.taxa.setText("abc")
    assert not d.salvar() and "inválida" in d.erro_taxa.text()
    d.taxa.setText("5000")
    assert not d.salvar() and "alta demais" in d.erro_taxa.text()
    d.taxa.setText("0")
    assert not d.salvar() and "maior que zero" in d.erro_taxa.text()
    assert conn.execute("SELECT COUNT(*) FROM ativos").fetchone()[0] == 0


def test_dialogo_barra_vencimento_no_passado(app, conn):
    d = _dialogo(conn)
    d.nome.setText("X")
    d.taxa.setText("100")
    d.tem_vencimento.setChecked(True)
    d.vencimento.setDate(d.vencimento.date().addYears(-2))
    assert not d.salvar() and "já passou" in d.erro_vencimento.text()


def test_dialogo_edita_e_preenche_do_ativo(app, conn):
    i = _novo(conn, instituicao="Inter", vencimento=date(2028, 1, 10))
    d = _dialogo(conn, i)
    assert (d.nome.text(), d.instituicao.text(), d.taxa.text(), d.indexador_atual, d.tem_vencimento.isChecked()) == ("CDB Inter", "Inter", "110", "cdi", True)
    assert d.vencimento.date().toPython() == date(2028, 1, 10) and d.botao_salvar.text() == "Salvar alterações"
    d.taxa.setText("115")
    d.tem_vencimento.setChecked(False)
    assert d.salvar()
    a = inv.obter(conn, i)
    assert a.taxa == 115.0 and a.vencimento is None


def test_dialogo_das_caixinhas_so_edita_o_indexador_e_a_taxa(app, conn):
    _cenario(conn)
    cx = inv.ativo_caixinhas(conn)
    d = _dialogo(conn, cx.id)
    assert d.titulo.text() == "Taxa das caixinhas"
    assert d._bloco_dados.isHidden() and d._bloco_vencimento.isHidden() and not d.taxa.isHidden()
    d.taxa.setText("95")
    assert d.salvar()
    assert inv.obter(conn, cx.id).taxa == 95.0 and inv.obter(conn, cx.id).nome == "Caixinhas (metas)"


def test_dialogo_de_ativo_inexistente_levanta(app, conn):
    with pytest.raises(ValueError):
        DialogoAtivo(conn, 999, hoje=HOJE)


def test_novo_investimento_pela_aba_atualiza_a_lista(app, conn):
    aba = _aba(conn)
    avisos = []
    aba.alterado.connect(avisos.append)
    aba.botao_novo.click()
    QApplication.processEvents()
    d = aba.dialogo
    assert d is not None
    d.nome.setText("Tesouro Prefixado")
    d.tipo.clicar("Tesouro Direto")
    d.indexador.setCurrentIndex(d.indexador.findData("pre"))
    d.taxa.setText("12,5")
    assert d.salvar()
    QApplication.processEvents()
    assert _nomes(aba) == ["Tesouro Prefixado"] and avisos == ["Investimento criado: Tesouro Prefixado"]
    assert aba.cartoes()[0].pilula_taxa.text() == "12,5% a.a."
    aba.editar_investimento(aba.cartoes()[0].id_ativo)
    assert aba.dialogo.nome.text() == "Tesouro Prefixado"
    aba.dialogo.reject()


# --- Gráfico do patrimônio ---------------------------------------------------------------------------------------


def test_grafico_sem_dados_mostra_estado_vazio(app, conn):
    p = PainelPatrimonio()
    p.resize(800, 400)
    p.definir(inv.serie_patrimonio_no_ano(conn, HOJE), 2026)
    assert p._pilha.currentWidget() is p._vazio


def test_grafico_do_patrimonio_pinta_e_mostra_a_dica_do_ponto(app, conn):
    _cenario(conn)
    p = PainelPatrimonio()
    p.resize(900, 400)
    p.show()
    serie = inv.serie_patrimonio_no_ano(conn, HOJE)
    p.definir(serie, 2026)
    QApplication.processEvents()
    assert p._pilha.currentWidget() is p._conteudo and not p.grab().isNull()
    linha = p._linha
    assert len(linha._pontos) == len(serie)
    ultimo_real = next(i for i in range(len(serie)) if serie.iloc[i]["data"] == serie[~serie["projetado"]]["data"].max())
    assert linha.dica_do_ponto(ultimo_real) == f"30 out · {lanc.formatar_brl(int(serie.iloc[ultimo_real]['centavos']))}"
    assert linha.dica_do_ponto(len(serie) - 1).startswith("31 dez · previsão R$ ")
    # o mouse perto de um ponto marca esse ponto; longe, nenhum
    alvo = linha._posicao(linha._pontos[ultimo_real])
    assert linha._indice_perto(alvo.x() + 3, alvo.y() - 2) == ultimo_real
    assert linha._indice_perto(alvo.x() + 3, alvo.y() + 200) is None


def test_grafico_refeito_muitas_vezes_nao_quebra(app, conn):
    _cenario(conn)
    aba = _aba(conn)
    for _ in range(5):
        aba.recarregar()
        QApplication.processEvents()
    assert not aba.grafico.grab().isNull()


# --- Janela: aba nova, cabeçalho, taxas em segundo plano ------------------------------------------------------------------


def test_janela_tem_a_quinta_aba(app, conn):
    j = _janela(conn)
    assert list(ABAS.values())[-1] == "Investimentos" and j._paginas.count() == 5
    j.mostrar_aba("investimentos")
    assert j.aba_atual == "investimentos" and j._paginas.currentWidget() is j.aba_investimentos
    j.abas.clicar("Metas")
    assert j.aba_atual == "metas"
    j.aba_investimentos.ver_metas.emit()
    assert j.aba_atual == "metas"
    j.abas.clicar("Investimentos")
    assert j.aba_atual == "investimentos"


def test_aportar_atualiza_os_recentes_sem_duplicar_o_lancamento(app, conn):
    cdb, _ = _cenario(conn)
    j = _janela(conn)
    j.mostrar_aba("investimentos")
    j.aba_investimentos.abrir_form(cdb, "aportar")
    c = j.aba_investimentos.cartao_do_ativo(cdb)
    c.form.valor.setText("250")
    c.form.botao_confirmar.click()
    QApplication.processEvents()
    df = lanc.recentes(conn, HOJE, 20)
    assert df["descricao"].tolist().count("Aporte: CDB Inter") == 2  # o de março e o de agora, cada um uma vez só
    assert "Investimentos" not in df["conta"].tolist()  # o outro lado da transferência não aparece
    assert j.statusBar().currentMessage() == "Aportado R$ 250,00 em CDB Inter"


@pytest.mark.parametrize("largura", [1400, 1200, 1100, 940, 760, 640, 560])
@pytest.mark.parametrize("aba", ["inicio", "recorrentes", "cartoes", "metas", "investimentos"])
def test_cabecalho_nao_espreme_nem_corta_nada(app, conn, largura, aba):
    j = _janela(conn, (largura, 700))
    j.mostrar_aba(aba)
    QApplication.processEvents()
    QApplication.processEvents()
    for botao in (j.botao_novo, j.botao_contas):
        x = botao.mapTo(j, QPoint(0, 0)).x()
        assert x >= 0 and x + botao.width() <= j.width(), f"{botao.text()} cortado em {largura}"
        assert botao.width() >= botao.sizeHint().width()
    assert j.abas.mapTo(j, QPoint(0, 0)).x() + j.abas.width() <= j.width()
    assert j.abas.width() >= j.abas.minimumSizeHint().width()
    for b in j.abas._botoes.values():  # nenhuma aba espremida
        assert b.width() >= b.sizeHint().width()
    assert j.marca.width() >= j.marca.sizeHint().width()
    if aba == "inicio":
        x = j.rotulo_mes.mapTo(j, QPoint(0, 0)).x()
        assert j.botao_mes_anterior.isVisible() and x + j.rotulo_mes.width() <= j.width()


def test_cabecalho_usa_uma_linha_quando_cabe_e_mais_quando_nao(app, conn):
    j = _janela(conn, (1400, 700))
    assert j._arranjo_cabecalho == 1 and j.dica_atalho.isVisible()
    j.resize(940, 700)
    QApplication.processEvents()
    assert j._arranjo_cabecalho == 2 and not j.dica_atalho.isVisible()
    assert j.abas.mapTo(j, QPoint(0, 0)).y() > j.botao_novo.mapTo(j, QPoint(0, 0)).y()
    j.resize(640, 700)
    QApplication.processEvents()
    assert j._arranjo_cabecalho == 3
    assert j.rotulo_mes.mapTo(j, QPoint(0, 0)).y() > j.abas.mapTo(j, QPoint(0, 0)).y()
    j.resize(1400, 700)
    QApplication.processEvents()
    assert j._arranjo_cabecalho == 1  # volta quando há espaço


def test_fora_do_inicio_o_cabecalho_cabe_numa_linha_a_partir_de_menos_largura(app, conn):
    j = _janela(conn, (1150, 700))
    assert j._arranjo_cabecalho == 2  # com o mês, não cabe
    j.mostrar_aba("metas")
    QApplication.processEvents()
    assert j._arranjo_cabecalho == 1 and not j.rotulo_mes.isVisible()
    j.mostrar_aba("inicio")
    QApplication.processEvents()
    assert j._arranjo_cabecalho == 2 and j.rotulo_mes.isVisible()


# --- Taxas em segundo plano ----------------------------------------------------------------------------------------


class _Falso:
    def __init__(self, falha=None):
        self.pedidos, self.falha = [], falha

    def __call__(self, serie, de, ate):
        self.pedidos.append(serie)
        if self.falha:
            raise self.falha
        return {"cdi": [(date(2026, 10, 29), 0.04)], "selic": [(date(2026, 10, 29), 0.04)], "ipca": [(date(2026, 9, 1), 0.4)]}[serie]


def _esperar(condicao, limite=5.0):
    fim = time.monotonic() + limite
    while not condicao() and time.monotonic() < fim:
        QApplication.processEvents()
        time.sleep(0.005)
    assert condicao()


def test_sem_rede_ligada_nada_e_buscado(app, conn):
    falso = _Falso()
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=falso)  # o conftest já define MEU_COFRE_SEM_REDE=1
    assert not a.disparar() and not a.disparar(forcar=True) and falso.pedidos == [] and not a.em_andamento


def test_atualiza_em_segundo_plano_e_grava_na_thread_principal(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    falso = _Falso()
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=falso)
    avisos = []
    a.concluido.connect(avisos.append)
    assert a.disparar() and a.em_andamento
    assert not a.disparar(forcar=True)  # uma busca por vez
    _esperar(lambda: avisos)
    assert not a.em_andamento and avisos[0]["ok"] and avisos[0]["ate"] == date(2026, 10, 29)
    assert sorted(falso.pedidos) == ["cdi", "ipca", "selic"]
    assert indicadores.ultima_data(conn, "cdi") == date(2026, 10, 29)
    a.aguardar()


def test_nao_repete_a_busca_antes_do_intervalo(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    falso = _Falso()
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=falso)
    avisos = []
    a.concluido.connect(avisos.append)
    a.disparar()
    _esperar(lambda: avisos)
    n = len(falso.pedidos)
    assert not a.disparar() and len(falso.pedidos) == n
    a.aguardar()


def test_falha_de_rede_vira_status_e_a_janela_segue(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    indicadores.gravar(conn, {"cdi": [(date(2026, 10, 20), 0.04)]})
    a = AtualizadorIndicadores(conn, lambda: HOJE, buscar=_Falso(falha=OSError("sem rede")))
    avisos = []
    a.concluido.connect(avisos.append)
    assert a.disparar()
    _esperar(lambda: avisos)
    assert avisos[0]["sem_rede"] and not avisos[0]["ok"] and not a.em_andamento
    assert indicadores.ultima_data(conn, "cdi") == date(2026, 10, 20)
    a.aguardar()


def test_janela_busca_as_taxas_ao_abrir_e_atualiza_o_selo(app, conn, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    falso = _Falso()
    monkeypatch.setattr(indicadores, "_buscar_http", falso)
    j = _janela(conn)
    assert j._atualizador.em_andamento or falso.pedidos  # saiu a busca, sem travar a abertura
    assert "atualizando" in j.aba_investimentos.selo.text() or falso.pedidos
    _esperar(lambda: not j._atualizador.em_andamento)
    QApplication.processEvents()
    assert j.aba_investimentos.selo.text().startswith("taxas do Banco Central de 29/10")
    j._atualizador.aguardar()
