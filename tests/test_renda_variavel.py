"""Renda variável (F9): preço médio, vendas, proventos (com e sem conta), cotação, estimativa e a integração com
patrimônio, previsão e gráfico. Sem Qt e sem internet (cotações gravadas direto no banco)."""

from datetime import date

import pytest
from conftest import id_conta

from meu_cofre import cotacoes, relatorios
from meu_cofre import investimentos as inv
from meu_cofre import renda_variavel as rv

HOJE = date(2026, 10, 30)


def _ativo(conn, ticker="PETR4", tipo="acao", nome=None):
    return inv.criar(conn, HOJE, nome=nome or f"Ativo {ticker}", tipo=tipo, ticker=ticker)


def _cotar(conn, ticker, preco, dia=date(2026, 10, 29), proventos=(), fechamentos=()):
    cotacoes.gravar(conn, {ticker: cotacoes.Dados(preco, dia, list(fechamentos), list(proventos))})


def _inter(conn):
    return id_conta(conn, "Inter")


# --- Ativos com ticker ---------------------------------------------------------------------------------------


def test_ticker_em_maiusculas_unico_e_valido(conn):
    a = inv.criar(conn, HOJE, nome="Petrobras", tipo="acao", ticker=" petr4 ")
    assert inv.obter(conn, a).ticker == "PETR4" and inv.obter(conn, a).indexador is None
    with pytest.raises(ValueError, match="Já existe"):
        _ativo(conn, "PETR4")
    for ruim in ("", None, "PETR", "PETR4F", "123"):
        with pytest.raises(ValueError):
            inv.criar(conn, HOJE, nome="X", tipo="fii", ticker=ruim)


def test_acao_ignora_taxa_e_vencimento_e_rf_ignora_ticker(conn):
    a = inv.criar(conn, HOJE, nome="Vale", tipo="acao", ticker="VALE3", indexador="cdi", taxa=100.0, vencimento=date(2030, 1, 1))
    x = inv.obter(conn, a)
    assert (x.indexador, x.taxa, x.vencimento) == (None, None, None)
    cdb = inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0, ticker="ABCD3")
    assert inv.obter(conn, cdb).ticker is None


def test_editar_troca_o_ticker_mas_nao_o_tipo_de_familia(conn):
    a, b = _ativo(conn, "PETR4"), _ativo(conn, "VALE3")
    inv.editar(conn, HOJE, a, nome="Petrobras PN", tipo="acao", ticker="petr3")
    assert inv.obter(conn, a).ticker == "PETR3" and inv.obter(conn, a).nome == "Petrobras PN"
    inv.editar(conn, HOJE, a, nome="Petrobras PN", tipo="fii", ticker="PETR3")  # ação <-> FII pode
    assert inv.obter(conn, a).tipo == "fii"
    with pytest.raises(ValueError, match="Já existe"):
        inv.editar(conn, HOJE, b, nome="Vale", tipo="acao", ticker="PETR3")
    with pytest.raises(ValueError, match="trocar"):
        inv.editar(conn, HOJE, b, nome="Vale", tipo="renda_fixa", indexador="cdi", taxa=100.0)


def test_renda_fixa_nao_recebe_operacoes_de_renda_variavel(conn):
    cdb = inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    with pytest.raises(ValueError, match="ações e FIIs"):
        rv.comprar(conn, cdb, HOJE, 1, 100)


# --- Preço médio -------------------------------------------------------------------------------------------------


def test_preco_medio_ponderado_com_taxas_e_venda_nao_muda_o_pm(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000, taxas=500)  # custo 300.500
    rv.comprar(conn, a, date(2026, 4, 15), 50, 3600)  # custo 180.000
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 150 and p["custo"] == 480_500
    assert p["preco_medio"] == pytest.approx(3203.3333, abs=1e-3)
    rv.vender(conn, a, date(2026, 9, 10), 60, 4000)  # recebe 240.000; custo da parte vendida 192.200
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 90 and p["custo"] == 288_300
    assert p["preco_medio"] == pytest.approx(3203.3333, abs=1e-3)  # a venda não muda o PM
    assert p["realizado"] == 47_800


def test_zerar_a_posicao_zera_o_pm_e_a_recompra_recomeca(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    rv.vender(conn, a, date(2026, 6, 1), 100, 3500, taxas=1000)  # líquido 349.000; lucro 49.000
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 0 and p["preco_medio"] is None and p["custo"] == 0 and p["realizado"] == 49_000
    assert p["resultado"] is None and p["dy_12m"] is None
    rv.comprar(conn, a, date(2026, 7, 1), 10, 2000)
    p = rv.posicao(conn, a, HOJE)
    assert p["preco_medio"] == 2000 and p["custo"] == 20_000 and p["realizado"] == 49_000


def test_venda_e_compra_no_mesmo_dia_a_compra_vale_primeiro(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 6, 1), 10, 1000)
    rv.vender(conn, a, date(2026, 6, 2), 10, 1100)
    rv.comprar(conn, a, date(2026, 6, 2), 5, 1000)
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 5 and p["preco_medio"] == 1000 and p["realizado"] == 1000


def test_nao_vende_mais_do_que_tem_nem_derruba_venda_posterior(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 1), 100, 3000)
    rv.vender(conn, a, date(2026, 8, 1), 80, 3500)
    with pytest.raises(ValueError, match="só tem"):
        rv.vender(conn, a, date(2026, 9, 1), 30, 3500)
    with pytest.raises(ValueError, match="só tem"):
        rv.vender(conn, a, date(2026, 2, 1), 10, 3500)  # antes de comprar
    with pytest.raises(ValueError):
        rv.comprar(conn, a, date(2026, 3, 1), 0, 3000)
    with pytest.raises(ValueError):
        rv.comprar(conn, a, date(2026, 3, 1), 1, 0)
    with pytest.raises(ValueError):
        rv.comprar(conn, a, date(2026, 3, 1), 1, 100, taxas=-1)
    assert rv.posicao(conn, a, HOJE)["quantidade"] == 20


def test_operacao_futura_nao_entra_na_posicao_de_hoje(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 1), 10, 1000)
    rv.comprar(conn, a, date(2026, 12, 1), 10, 1000)
    assert rv.posicao(conn, a, HOJE)["quantidade"] == 10


def test_ativo_encerrado_nao_recebe_operacao_e_so_encerra_zerado(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 1), 10, 1000)
    with pytest.raises(ValueError, match="Venda tudo"):
        inv.encerrar(conn, HOJE, a)
    rv.vender(conn, a, date(2026, 4, 1), 10, 1000)
    inv.encerrar(conn, HOJE, a)
    with pytest.raises(ValueError, match="encerrado"):
        rv.comprar(conn, a, HOJE, 1, 1000)
    assert rv.listar(conn, HOJE) == [] and len(rv.listar(conn, HOJE, incluir_encerrados=True)) == 1


# --- Conta: compra, venda e provento afetando ou não ---------------------------------------------------------------


def test_compra_com_conta_e_transferencia_para_investimentos(conn):
    a = _ativo(conn)
    antes = relatorios.em_conta(conn, HOJE)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000, taxas=250, conta_id=_inter(conn))
    assert relatorios.em_conta(conn, HOJE) == antes - 30_250
    linhas = conn.execute("SELECT conta_id, valor_centavos, transferencia_id, metodo FROM lancamentos ORDER BY id").fetchall()
    assert [l["valor_centavos"] for l in linhas] == [-30_250, 30_250]
    assert linhas[0]["transferencia_id"] == linhas[1]["transferencia_id"] and linhas[0]["metodo"] == "transferencia"
    assert linhas[1]["conta_id"] == inv.conta_investimentos(conn)
    op = rv.operacoes(conn, a)[0]
    assert op.afeta_conta and op.valor_centavos == 30_000 and op.taxas_centavos == 250
    assert conn.execute("SELECT transferencia_id FROM operacoes_rv").fetchone()[0] == linhas[0]["transferencia_id"]


def test_compra_sem_conta_so_registra_a_posicao(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert not rv.operacoes(conn, a)[0].afeta_conta and rv.posicao(conn, a, HOJE)["quantidade"] == 10


def test_venda_com_conta_devolve_o_liquido(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000)
    antes = relatorios.em_conta(conn, HOJE)
    rv.vender(conn, a, date(2026, 6, 1), 4, 4000, taxas=100, conta_id=_inter(conn))
    assert relatorios.em_conta(conn, HOJE) == antes + 15_900
    with pytest.raises(ValueError, match="taxas"):
        rv.vender(conn, a, date(2026, 6, 2), 1, 100, taxas=200)


def test_conta_precisa_ser_do_dia_a_dia(conn):
    a = _ativo(conn)
    with pytest.raises(ValueError, match="conta"):
        rv.comprar(conn, a, HOJE, 1, 1000, conta_id=id_conta(conn, "Cartão de crédito"))
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 0


def test_provento_com_conta_entra_como_rendimento_ligado_a_operacao(conn):
    a = _ativo(conn)
    antes = relatorios.em_conta(conn, HOJE)
    op = rv.registrar_provento(conn, a, "dividendo", date(2026, 5, 20), 4550, conta_id=_inter(conn))
    assert relatorios.em_conta(conn, HOJE) == antes + 4550
    lanc = conn.execute(
        "SELECT l.valor_centavos, l.descricao, c.nome AS categoria, c.tipo FROM operacoes_rv o "
        "JOIN lancamentos l ON l.id = o.lancamento_id JOIN categorias c ON c.id = l.categoria_id WHERE o.id = ?", (op,)
    ).fetchone()
    assert (lanc["valor_centavos"], lanc["categoria"], lanc["tipo"]) == (4550, "Rendimentos", "receita")
    assert lanc["descricao"] == "Dividendo: PETR4"


def test_provento_sem_conta_nao_mexe_no_dinheiro_mas_conta_nos_proventos(conn):
    a = _ativo(conn)
    rv.registrar_provento(conn, a, "jcp", date(2026, 5, 20), 2210)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert rv.posicao(conn, a, HOJE)["proventos_total"] == 2210


def test_provento_validacoes(conn):
    a = _ativo(conn)
    with pytest.raises(ValueError):
        rv.registrar_provento(conn, a, "bonus", HOJE, 100)
    with pytest.raises(ValueError):
        rv.registrar_provento(conn, a, "dividendo", HOJE, 0)


# --- Posição: cotação, resultado, proventos, DY ---------------------------------------------------------------------------


def test_posicao_sem_cotacao_nao_inventa_valor(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000)
    p = rv.posicao(conn, a, HOJE)
    assert p["cotacao"] is None and p["cotacao_data"] is None
    assert p["valor_atual"] is None and p["resultado"] is None and p["resultado_pct"] is None and p["dy_12m"] is None
    assert p["custo"] == 30_000 and p["quantidade"] == 10


def test_posicao_com_cotacao_resultado_e_dy(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    rv.registrar_provento(conn, a, "dividendo", date(2026, 5, 20), 4000)  # dentro dos 12 meses
    rv.registrar_provento(conn, a, "dividendo", date(2025, 9, 20), 9000)  # fora dos 12 meses
    _cotar(conn, "PETR4", 3300)
    p = rv.posicao(conn, a, HOJE)
    assert (p["cotacao"], p["cotacao_data"]) == (3300, date(2026, 10, 29))
    assert p["valor_atual"] == 330_000 and p["resultado"] == 30_000 and p["resultado_pct"] == pytest.approx(10.0)
    assert p["proventos_12m"] == 4000 and p["proventos_total"] == 13_000
    assert p["dy_12m"] == pytest.approx(4000 / 330_000 * 100)


def test_cotacao_so_ate_a_data_pedida(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000)
    _cotar(conn, "PETR4", 3300, dia=date(2026, 11, 5))  # depois de "hoje"
    assert rv.posicao(conn, a, HOJE)["cotacao"] is None


def test_listar_traz_acoes_e_fiis_na_ordem(conn):
    a, b = _ativo(conn, "PETR4"), _ativo(conn, "HGLG11", "fii")
    inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    assert [p["ativo"].id for p in rv.listar(conn, HOJE)] == [a, b]


# --- Estimativa de proventos ----------------------------------------------------------------------------------------------------


REF = [
    (date(2025, 10, 30), 9.0),  # exatamente 12 meses atrás: fora
    (date(2025, 11, 14), 0.40),
    (date(2026, 2, 13), 0.50),
    (date(2026, 5, 15), 0.50),
]


def test_estimativa_repete_o_historico_por_cota_vezes_a_quantidade(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 3300, proventos=REF)
    df = rv.estimar_proventos(conn, a, HOJE)
    assert list(df.columns) == ["mes", "centavos"] and len(df) == 12
    assert df["mes"].iloc[0] == "2026-10" and df["mes"].iloc[-1] == "2027-09"
    assert dict(zip(df["mes"], df["centavos"])) == {
        "2026-10": 0, "2026-11": 4000, "2026-12": 0, "2027-01": 0, "2027-02": 5000, "2027-03": 0,
        "2027-04": 0, "2027-05": 5000, "2027-06": 0, "2027-07": 0, "2027-08": 0, "2027-09": 0,
    }


def test_estimativa_usa_a_quantidade_de_hoje(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    rv.vender(conn, a, date(2026, 8, 1), 50, 3000)
    _cotar(conn, "PETR4", 3300, proventos=REF)
    assert rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 7000  # 50 cotas × (0,40+0,50+0,50)


def test_estimativa_sem_historico_ou_sem_cotas_e_zero(conn):
    a = _ativo(conn)
    assert rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 0
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    assert len(rv.estimar_proventos(conn, a, HOJE)) == 12 and rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 0
    _cotar(conn, "PETR4", 3300, proventos=REF)
    rv.vender(conn, a, date(2026, 8, 1), 100, 3000)
    assert rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 0


def test_estimativa_da_carteira_soma_os_ativos_e_ignora_encerrados(conn):
    a, b = _ativo(conn, "PETR4"), _ativo(conn, "HGLG11", "fii")
    for ativo, t in ((a, "PETR4"), (b, "HGLG11")):
        rv.comprar(conn, ativo, date(2026, 3, 2), 100, 3000)
        _cotar(conn, t, 3300, proventos=REF)
    assert rv.estimar_proventos_carteira(conn, HOJE)["centavos"].sum() == 28_000
    rv.vender(conn, b, date(2026, 8, 1), 100, 3000)
    inv.encerrar(conn, HOJE, b)
    assert rv.estimar_proventos_carteira(conn, HOJE)["centavos"].sum() == 14_000


def test_recebidos_no_ano(conn):
    a = _ativo(conn)
    rv.registrar_provento(conn, a, "dividendo", date(2026, 5, 20), 4000)
    rv.registrar_provento(conn, a, "rendimento", date(2026, 10, 1), 1000)
    rv.registrar_provento(conn, a, "dividendo", date(2025, 12, 31), 9000)
    assert rv.proventos_recebidos_no_ano(conn, HOJE) == 5000


# --- Patrimônio, previsão e gráfico ------------------------------------------------------------------------------------------


def test_patrimonio_inclui_renda_variavel_pela_ultima_cotacao(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 3300)
    pat = inv.patrimonio(conn, HOJE)
    assert pat["renda_variavel"] == 330_000 and pat["investimentos"] == 330_000 and pat["total"] == 330_000
    assert inv.resumo(conn, HOJE).patrimonio["total"] == 330_000


def test_patrimonio_sem_cotacao_usa_o_custo(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000, taxas=500)
    assert inv.patrimonio(conn, HOJE)["renda_variavel"] == 300_500


def test_comprar_com_conta_leva_o_dinheiro_pro_patrimonio_quando_o_preco_nao_mexe(conn):
    from test_investimentos import _lancar

    _lancar(conn, True, 1_000_000, date(2026, 10, 2), "Salário")
    assert inv.patrimonio(conn, HOJE)["total"] == 0  # dinheiro na conta corrente não é patrimônio guardado
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 10, 5), 100, 3000, conta_id=_inter(conn))
    _cotar(conn, "PETR4", 3000)
    pat = inv.patrimonio(conn, HOJE)
    assert pat["renda_variavel"] == 300_000 and pat["total"] == 300_000


def test_previsao_nao_supoe_alta_e_soma_so_os_proventos_ate_31_12(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 3300, proventos=REF)
    prev = inv.previsao_fim_do_ano(conn, HOJE)
    assert prev["rendimento_projetado"] == 0  # o preço não sobe na projeção
    assert prev["proventos_projetados"] == 4000  # só o de novembro cai antes de 31/12
    assert prev["total"] == prev["patrimonio_hoje"] + 4000
    assert inv.resumo(conn, HOJE).previsao["proventos_projetados"] == 4000


def test_previsao_sem_renda_variavel_nao_muda(conn):
    prev = inv.previsao_fim_do_ano(conn, HOJE)
    assert prev["proventos_projetados"] == 0 and prev["total"] == prev["patrimonio_hoje"]


def test_serie_do_ano_acompanha_as_cotacoes_e_fecha_em_hoje(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 4200, fechamentos=[(date(2026, 3, 2), 3500), (date(2026, 6, 1), 4000)])
    df = inv.serie_patrimonio_no_ano(conn, HOJE).set_index("data")
    assert df.loc["2026-01-01", "centavos"] == 0  # antes da compra
    assert df.loc["2026-03-05", "centavos"] == 350_000  # primeira cotação
    assert df.loc["2026-06-04", "centavos"] == 400_000
    hoje_linha = df[~df["projetado"]].iloc[-1]
    assert hoje_linha["centavos"] == inv.patrimonio(conn, HOJE)["total"] == 420_000
    assert df.iloc[-1]["projetado"] and df.iloc[-1]["centavos"] >= 420_000  # em 31/12: preço parado + proventos


def test_percentual_do_cdi_continua_so_da_renda_fixa(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 6000)  # dobrou
    r = inv.resumo(conn, HOJE)
    assert r.rendimento_ano.centavos == 0 and r.pct_cdi is None and r.patrimonio["total"] == 600_000


# --- Sem histórico público: o do próprio usuário, e o gráfico sem histórico de preços ----------------------------------------


def test_estimativa_sem_historico_publico_usa_o_que_o_usuario_recebeu(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 1, 5), 100, 3000)
    rv.registrar_provento(conn, a, "dividendo", date(2026, 2, 13), 5000)  # 100 cotas -> R$ 0,50 por cota
    rv.comprar(conn, a, date(2026, 3, 1), 100, 3000)  # agora 200 cotas
    rv.registrar_provento(conn, a, "jcp", date(2026, 5, 15), 6000)  # 200 cotas -> R$ 0,30 por cota
    rv.registrar_provento(conn, a, "dividendo", date(2025, 9, 1), 9999)  # fora dos 12 meses
    df = rv.estimar_proventos(conn, a, HOJE)
    meses = dict(zip(df["mes"], df["centavos"]))
    assert meses["2027-02"] == 10_000 and meses["2027-05"] == 6000  # × 200 cotas de hoje
    assert df["centavos"].sum() == 16_000


def test_historico_publico_tem_prioridade_sobre_o_do_usuario(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 1, 5), 100, 3000)
    rv.registrar_provento(conn, a, "dividendo", date(2026, 2, 13), 5000)
    _cotar(conn, "PETR4", 3300, proventos=[(date(2026, 5, 15), 0.10)])
    assert rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 1000


def test_estimativa_sem_nenhum_historico_continua_zero(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 1, 5), 100, 3000)
    assert rv.estimar_proventos(conn, a, HOJE)["centavos"].sum() == 0


def test_previsao_usa_o_historico_do_usuario_quando_nao_ha_publico(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2025, 6, 1), 100, 3000)
    rv.registrar_provento(conn, a, "dividendo", date(2025, 11, 20), 5000)  # em 12 meses: cai em 20/11/2026
    assert inv.previsao_fim_do_ano(conn, HOJE)["proventos_projetados"] == 5000


def test_grafico_sem_historico_de_precos_usa_a_cotacao_anterior_ou_o_custo(conn):
    a = _ativo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    _cotar(conn, "PETR4", 4200)  # uma cotação só, de 29/10 (sem fechamentos antigos)
    df = inv.serie_patrimonio_no_ano(conn, HOJE).set_index("data")
    assert df.loc["2026-03-05", "centavos"] == 300_000  # antes de qualquer cotação: o custo
    assert df.loc["2026-10-22", "centavos"] == 300_000
    assert df[~df["projetado"]].iloc[-1]["centavos"] == 420_000  # hoje: a cotação
    cotacoes.gravar(conn, {"PETR4": cotacoes.Dados(4200, date(2026, 10, 29), [(date(2026, 6, 1), 4000)])})
    df = inv.serie_patrimonio_no_ano(conn, HOJE).set_index("data")
    assert df.loc["2026-03-05", "centavos"] == 300_000 and df.loc["2026-06-04", "centavos"] == 400_000
    assert df.loc["2026-10-08", "centavos"] == 400_000  # a mais próxima até a data
