"""Investimentos (F8): ativos, aportar/resgatar, valor atual por indexador, caixinhas, rendimento, patrimônio e
previsão. Sem Qt e sem internet (a série do Banco Central é falsa, gravada direto em `indicadores`)."""

from datetime import date, timedelta

import pandas as pd
import pytest
from conftest import id_cat, id_conta

from meu_cofre import indicadores
from meu_cofre import investimentos as inv
from meu_cofre import lancamentos as lanc
from meu_cofre import metas, relatorios, renda_variavel

HOJE = date(2026, 10, 30)  # sexta-feira
CDI_DIA = 0.04  # % ao dia (≈ 10,6% a.a.)


# --- Ajudantes ------------------------------------------------------------------------------


def _uteis(inicio, fim):
    """Dias úteis em (inicio, fim]."""
    return [inicio + timedelta(days=k) for k in range(1, (fim - inicio).days + 1) if inv.dia_util(inicio + timedelta(days=k))]


def _serie_diaria(conn, serie, inicio, fim, valor=CDI_DIA):
    """CDI/Selic falso: `valor` % ao dia em cada dia útil de inicio a fim (inclusive)."""
    dias = _uteis(inicio - timedelta(days=1), fim)
    indicadores.gravar(conn, {serie: [(d, valor) for d in dias]})


def _ipca(conn, valor, de=date(2025, 6, 1), ate=date(2026, 9, 1)):
    pontos, d = [], de
    while d <= ate:
        pontos.append((d, valor))
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    indicadores.gravar(conn, {"ipca": pontos})


def _novo(conn, nome="CDB Inter", tipo="renda_fixa", indexador="cdi", taxa=100.0, **kw):
    return inv.criar(conn, HOJE, nome=nome, tipo=tipo, indexador=indexador, taxa=taxa, **kw)


def _aportar(conn, ativo_id, centavos, data, conta="Inter"):
    return inv.aportar(conn, ativo_id, centavos, id_conta(conn, conta), data)


def _lancar(conn, entrada, centavos, data, cat, conta="Inter", metodo="pix"):
    lanc.registrar(
        conn, entrada=entrada, valor_centavos=centavos, descricao="x", categoria_id=id_cat(conn, cat),
        metodo=metodo, conta_id=id_conta(conn, conta), data=data,
    )


def _historico(conn):
    """Julho a setembro: salário 5000, essencial 2000, lazer 1000 -> sobra 2000/mês."""
    for m in (7, 8, 9):
        _lancar(conn, True, 500_000, date(2026, m, 5), "Salário")
        _lancar(conn, False, 200_000, date(2026, m, 10), "Moradia")
        _lancar(conn, False, 100_000, date(2026, m, 15), "Lazer")


# --- Calendário --------------------------------------------------------------------------------


def test_feriados_nacionais_de_2026():
    f = inv.feriados(2026)
    assert date(2026, 4, 5) not in f  # Páscoa é domingo
    assert {date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 6, 4)} <= f  # Carnaval, Sexta Santa, Corpus Christi
    assert {date(2026, 1, 1), date(2026, 4, 21), date(2026, 5, 1), date(2026, 9, 7), date(2026, 10, 12)} <= f
    assert {date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20), date(2026, 12, 25)} <= f
    assert date(2023, 11, 20) not in inv.feriados(2023) and date(2024, 11, 20) in inv.feriados(2024)
    assert not inv.dia_util(date(2026, 10, 31)) and not inv.dia_util(date(2026, 10, 12)) and inv.dia_util(date(2026, 10, 30))


def test_descrever_taxa():
    assert inv.descrever_taxa("cdi", 110.0) == "110% do CDI"
    assert inv.descrever_taxa("selic", 100.0) == "100% da Selic"
    assert inv.descrever_taxa("ipca", 6.2) == "IPCA + 6,2%"
    assert inv.descrever_taxa("pre", 12.5) == "12,5% a.a."
    assert inv.descrever_taxa("pre", 12.55) == "12,55% a.a." and inv.descrever_taxa(None, None) == ""


# --- Cadastro -----------------------------------------------------------------------------------


def test_criar_obter_editar_e_encerrar(conn):
    i = inv.criar(
        conn, HOJE, nome="  CDB Inter  ", tipo="renda_fixa", instituicao=" Inter ", indexador="cdi", taxa=110.0,
        vencimento=date(2028, 1, 10),
    )
    a = inv.obter(conn, i)
    assert (a.nome, a.tipo, a.instituicao, a.indexador, a.taxa, a.vencimento) == ("CDB Inter", "renda_fixa", "Inter", "cdi", 110.0, date(2028, 1, 10))
    assert a.ativo and not a.caixinhas and a.descricao_taxa == "110% do CDI"
    inv.editar(conn, HOJE, i, nome="CDB Inter 2", tipo="renda_fixa", indexador="cdi", taxa=105.5)
    a = inv.obter(conn, i)
    assert (a.nome, a.instituicao, a.taxa, a.vencimento) == ("CDB Inter 2", None, 105.5, None)
    inv.encerrar(conn, HOJE, i)
    assert not inv.obter(conn, i).ativo and inv.listar(conn, HOJE) == []
    assert [p.ativo.id for p in inv.listar(conn, HOJE, incluir_encerrados=True)] == [i]
    inv.reativar(conn, i)
    assert [p.ativo.id for p in inv.listar(conn, HOJE)] == [i]


@pytest.mark.parametrize(
    "campos, mensagem",
    [
        ({"nome": "  "}, "nome"),
        ({"tipo": "cripto"}, "Tipo"),
        ({"indexador": None}, "indexador"),
        ({"taxa": None}, "taxa"),
        ({"taxa": 0.0}, "maior que zero"),
        ({"taxa": -3.0}, "maior que zero"),
        ({"taxa": 5000.0}, "alta demais"),
        ({"tipo": "tesouro", "indexador": "cdi"}, "CDI"),
        ({"vencimento": date(2026, 1, 1)}, "vencimento"),
    ],
)
def test_criar_valida_os_campos(conn, campos, mensagem):
    base = {"nome": "X", "tipo": "renda_fixa", "indexador": "cdi", "taxa": 100.0}
    with pytest.raises(ValueError, match=mensagem):
        inv.criar(conn, HOJE, **{**base, **campos})


def test_ipca_aceita_taxa_zero_e_obter_inexistente_levanta(conn):
    assert inv.obter(conn, _novo(conn, indexador="ipca", taxa=0.0)).taxa == 0.0
    with pytest.raises(ValueError, match="não existe"):
        inv.obter(conn, 999)


def test_nao_encerra_com_dinheiro_aplicado(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    with pytest.raises(ValueError, match="Resgate"):
        inv.encerrar(conn, HOJE, i)


# --- Aportar e resgatar -------------------------------------------------------------------------


def test_aportar_e_uma_transferencia_que_sai_do_em_conta(conn):
    _lancar(conn, True, 500_000, date(2026, 10, 5), "Salário")
    i = _novo(conn)
    antes = relatorios.em_conta(conn, HOJE)
    tid = _aportar(conn, i, 150_000, date(2026, 10, 20))
    linhas = conn.execute("SELECT * FROM lancamentos WHERE transferencia_id = ? ORDER BY valor_centavos", (tid,)).fetchall()
    assert [(conn.execute("SELECT nome FROM contas WHERE id = ?", (r["conta_id"],)).fetchone()[0], r["valor_centavos"]) for r in linhas] == [
        ("Inter", -150_000), ("Investimentos", 150_000),
    ]
    assert {r["metodo"] for r in linhas} == {"transferencia"} and linhas[0]["descricao"] == "Aporte: CDB Inter"
    assert conn.execute("SELECT tipo, data, valor_centavos FROM movimentacoes_ativos WHERE transferencia_id = ?", (tid,)).fetchone()[:] == (
        "aporte", "2026-10-20", 150_000,
    )
    assert relatorios.em_conta(conn, HOJE) == antes - 150_000
    r = relatorios.resumo_mes(conn, 2026, 10)
    assert r.entradas == 500_000 and r.saidas == 0  # transferência não é gasto


def test_resgatar_volta_pra_conta_e_nao_passa_do_valor_atual(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 20))  # sem série de CDI: não rende
    with pytest.raises(ValueError, match="Só há"):
        inv.resgatar(conn, i, 100_001, id_conta(conn, "Inter"), HOJE)
    antes = relatorios.em_conta(conn, HOJE)
    inv.resgatar(conn, i, 40_000, id_conta(conn, "Caixa"), HOJE)
    assert relatorios.em_conta(conn, HOJE) == antes + 40_000
    assert inv.valor_atual(conn, i, HOJE)[0] == 60_000
    inv.resgatar(conn, i, 60_000, id_conta(conn, "Inter"), HOJE)
    assert inv.valor_atual(conn, i, HOJE)[0] == 0
    with pytest.raises(ValueError, match="Não há nada"):
        inv.resgatar(conn, i, 1, id_conta(conn, "Inter"), HOJE)


def test_resgatar_tudo_com_rendimento_zera_o_ativo(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    total = inv.valor_atual(conn, i, HOJE)[0]
    assert total > 100_000
    inv.resgatar(conn, i, total, id_conta(conn, "Inter"), HOJE)
    assert inv.valor_atual(conn, i, HOJE)[0] == 0
    inv.encerrar(conn, HOJE, i)


@pytest.mark.parametrize("funcao", ["aportar", "resgatar"])
def test_movimento_valida_valor_e_conta(conn, funcao):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    f = getattr(inv, funcao)
    with pytest.raises(ValueError, match="maior que zero"):
        f(conn, i, 0, id_conta(conn, "Inter"), HOJE)
    for conta in ("Cartão de crédito", "Caixinhas", "Investimentos"):
        with pytest.raises(ValueError, match="conta corrente"):
            f(conn, i, 100, id_conta(conn, conta), HOJE)


def test_nao_movimenta_ativo_encerrado_nem_caixinhas(conn):
    i = _novo(conn)
    inv.encerrar(conn, HOJE, i)
    with pytest.raises(ValueError, match="encerrado"):
        _aportar(conn, i, 100, HOJE)
    metas.guardar(conn, metas.criar(conn, HOJE, nome="M", valor_alvo_centavos=10_000, modo="mensal", mensal_centavos=1000), 5000, id_conta(conn, "Inter"), HOJE)
    cx = inv.ativo_caixinhas(conn)
    with pytest.raises(ValueError, match="aba Metas"):
        _aportar(conn, cx.id, 100, HOJE)


def test_aportar_cria_a_conta_e_a_categoria_se_sumirem(conn):
    conn.execute("DELETE FROM contas WHERE nome = 'Investimentos'")
    conn.execute("UPDATE categorias SET nome = 'Outra' WHERE nome = 'Transferência entre contas'")
    i = _novo(conn)
    _aportar(conn, i, 100, HOJE)
    assert conn.execute("SELECT tipo FROM contas WHERE nome = 'Investimentos'").fetchone()[0] == "investimento"
    assert conn.execute("SELECT tipo FROM categorias WHERE nome = 'Transferência entre contas'").fetchone()[0] == "transferencia"


def test_recentes_mostram_o_aporte_uma_vez_so(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 20))
    df = lanc.recentes(conn, HOJE)
    assert df["conta"].tolist() == ["Inter"] and df["valor_centavos"].tolist() == [-100_000]


# --- Valor atual: CDI e Selic -------------------------------------------------------------------------


def test_sem_aportes_o_valor_e_zero(conn):
    assert inv.valor_atual(conn, _novo(conn), HOJE) == (0, False, None)


def test_cdi_composto_a_100_por_cento(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn, taxa=100.0)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))  # o aporte rende a partir do dia seguinte
    assert n == 20
    centavos, estimado, base = inv.valor_atual(conn, i, HOJE)
    assert centavos == round(100_000 * (1 + CDI_DIA / 100) ** n)
    assert not estimado and base is None


def test_cdi_a_110_por_cento_rende_mais(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    a, b = _novo(conn, taxa=100.0), _novo(conn, nome="B", taxa=110.0)
    for i in (a, b):
        _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))
    assert inv.valor_atual(conn, b, HOJE)[0] == round(100_000 * (1 + CDI_DIA / 100 * 1.1) ** n)
    assert inv.valor_atual(conn, b, HOJE)[0] > inv.valor_atual(conn, a, HOJE)[0]


def test_aporte_no_dia_nao_rende_ainda(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, HOJE)
    assert inv.valor_atual(conn, i, HOJE)[0] == 100_000


def test_aportes_e_resgate_em_datas_diferentes(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    inv.resgatar(conn, i, 50_000, id_conta(conn, "Inter"), date(2026, 10, 15))
    _aportar(conn, i, 20_000, date(2026, 10, 20))
    f = 1 + CDI_DIA / 100
    meio = 100_000 * f ** len(_uteis(date(2026, 10, 1), date(2026, 10, 15))) - 50_000
    meio = (meio * f ** len(_uteis(date(2026, 10, 15), date(2026, 10, 20)))) + 20_000
    esperado = meio * f ** len(_uteis(date(2026, 10, 20), HOJE))
    assert inv.valor_atual(conn, i, HOJE)[0] == round(esperado)


def test_selic_usa_a_serie_da_selic(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE, valor=0.04)
    _serie_diaria(conn, "selic", date(2026, 10, 1), HOJE, valor=0.05)
    i = _novo(conn, nome="Tesouro Selic", tipo="tesouro", indexador="selic", taxa=100.0)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))
    assert inv.valor_atual(conn, i, HOJE)[0] == round(100_000 * 1.0005**n)


def test_dia_sem_dado_no_meio_da_serie_nao_e_dia_util(conn):
    # a série pulou o dia 14 (sem expediente local): sem rendimento nele, e sem marcar como estimado
    dias = [d for d in _uteis(date(2026, 9, 30), HOJE) if d != date(2026, 10, 14)]
    indicadores.gravar(conn, {"cdi": [(d, CDI_DIA) for d in dias]})
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert centavos == round(100_000 * (1 + CDI_DIA / 100) ** (len(dias) - 1))  # menos o dia 1/10
    assert not estimado


def test_sem_nenhuma_taxa_nao_rende_e_marca_estimado(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    assert inv.valor_atual(conn, i, HOJE) == (100_000, True, None)


def test_dado_de_ontem_ainda_nao_e_estimativa(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), date(2026, 10, 28))  # falta 29 e 30
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    n = len(_uteis(date(2026, 10, 1), HOJE))  # os 2 dias sem dado usam a última taxa
    assert centavos == round(100_000 * (1 + CDI_DIA / 100) ** n) and not estimado


def test_serie_velha_usa_a_ultima_taxa_e_marca_estimado(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), date(2026, 10, 9))
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert estimado
    assert centavos == round(100_000 * (1 + CDI_DIA / 100) ** len(_uteis(date(2026, 10, 1), HOJE)))


def test_aporte_antes_da_serie_usa_a_primeira_taxa(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 10), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert estimado and centavos == round(100_000 * (1 + CDI_DIA / 100) ** len(_uteis(date(2026, 10, 1), HOJE)))


# --- Saldo real -------------------------------------------------------------------------------------


def test_saldo_real_recalibra_a_partir_da_data(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    inv.registrar_saldo_real(conn, i, 101_000, date(2026, 10, 15))
    centavos, _estimado, base = inv.valor_atual(conn, i, HOJE)
    assert base == date(2026, 10, 15)
    assert centavos == round(101_000 * (1 + CDI_DIA / 100) ** len(_uteis(date(2026, 10, 15), HOJE)))
    assert inv.valor_atual(conn, i, date(2026, 10, 14))[2] is None  # antes da data, a estimativa não muda


def test_saldo_real_no_mesmo_dia_vale_por_ultimo_e_substitui_o_anterior(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, HOJE)
    inv.registrar_saldo_real(conn, i, 90_000, HOJE)
    inv.registrar_saldo_real(conn, i, 120_000, HOJE)
    assert inv.valor_atual(conn, i, HOJE)[0] == 120_000
    assert conn.execute("SELECT COUNT(*) FROM movimentacoes_ativos WHERE tipo = 'saldo_real'").fetchone()[0] == 1


def test_saldo_real_nao_altera_o_aplicado_e_vira_rendimento(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    inv.registrar_saldo_real(conn, i, 103_000, HOJE)
    p = inv.listar(conn, HOJE)[0]
    assert (p.aplicado, p.aportado, p.valor_atual, p.rendimento) == (100_000, 100_000, 103_000, 3_000)
    assert p.rendimento_pct == pytest.approx(3.0) and p.base_data == HOJE


def test_saldo_real_valida(conn):
    i = _novo(conn)
    with pytest.raises(ValueError, match="negativo"):
        inv.registrar_saldo_real(conn, i, -1, HOJE)
    inv.registrar_saldo_real(conn, i, 0, HOJE)  # zero vale (zerou o investimento)


# --- IPCA e prefixado --------------------------------------------------------------------------------


def test_ipca_mais_taxa_pro_rata(conn):
    _ipca(conn, 0.5, ate=date(2026, 10, 1))
    i = _novo(conn, nome="Tesouro IPCA+", tipo="tesouro", indexador="ipca", taxa=6.0)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))
    du_mes = len(_uteis(date(2026, 9, 30), date(2026, 10, 31)))
    esperado = 100_000 * (1.005 ** (1 / du_mes)) ** n * (1.06 ** (1 / 252)) ** n
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert centavos == round(esperado) and not estimado


def test_ipca_atravessando_meses_usa_o_de_cada_um(conn):
    indicadores.gravar(conn, {"ipca": [(date(2026, 9, 1), 0.2), (date(2026, 10, 1), 0.6)]})
    i = _novo(conn, nome="IPCA+", tipo="tesouro", indexador="ipca", taxa=0.0)
    _aportar(conn, i, 100_000, date(2026, 9, 15))
    n_set = len(_uteis(date(2026, 9, 15), date(2026, 9, 30)))
    n_out = len(_uteis(date(2026, 9, 30), HOJE))
    du_set, du_out = len(_uteis(date(2026, 8, 31), date(2026, 9, 30))), len(_uteis(date(2026, 9, 30), date(2026, 10, 31)))
    esperado = 100_000 * 1.002 ** (n_set / du_set) * 1.006 ** (n_out / du_out)
    assert inv.valor_atual(conn, i, HOJE)[0] == round(esperado)


def test_ipca_de_mes_sem_divulgacao_usa_a_media_dos_12_meses(conn):
    _ipca(conn, 0.4, ate=date(2026, 9, 1))  # outubro ainda não saiu
    i = _novo(conn, nome="IPCA+", tipo="tesouro", indexador="ipca", taxa=0.0)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))
    du = len(_uteis(date(2026, 9, 30), date(2026, 10, 31)))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert centavos == round(100_000 * 1.004 ** (n / du)) and not estimado  # mês corrente sem IPCA é normal


def test_ipca_muito_atrasado_marca_estimado(conn):
    _ipca(conn, 0.4, ate=date(2026, 3, 1))
    i = _novo(conn, nome="IPCA+", tipo="tesouro", indexador="ipca", taxa=5.0)
    _aportar(conn, i, 100_000, date(2026, 6, 1))
    assert inv.valor_atual(conn, i, HOJE)[1] is True


def test_prefixado_por_dias_uteis(conn):
    i = _novo(conn, nome="Tesouro Pré", tipo="tesouro", indexador="pre", taxa=12.0)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    n = len(_uteis(date(2026, 10, 1), HOJE))
    centavos, estimado, _ = inv.valor_atual(conn, i, HOJE)
    assert centavos == round(100_000 * 1.12 ** (n / 252)) and not estimado  # não depende de série nenhuma


def test_prefixado_ao_longo_de_um_ano_rende_a_taxa(conn):
    i = _novo(conn, nome="Pré", indexador="pre", taxa=12.0)
    inicio = date(2025, 10, 30)
    _aportar(conn, i, 100_000, inicio)
    centavos = inv.valor_atual(conn, i, HOJE)[0]
    assert 111_000 < centavos < 113_000  # ≈ 12% em ~252 dias úteis


def test_vencimento_para_de_render(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    i = _novo(conn)
    conn.execute("UPDATE ativos SET vencimento = '2026-10-15' WHERE id = ?", (i,))  # venceu no meio do mês
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    assert inv.valor_atual(conn, i, HOJE)[0] == round(100_000 * (1 + CDI_DIA / 100) ** len(_uteis(date(2026, 10, 1), date(2026, 10, 15))))


def test_aporte_futuro_nao_conta_hoje(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 11, 10))
    assert inv.valor_atual(conn, i, HOJE)[0] == 0


# --- Caixinhas das metas ------------------------------------------------------------------------------------


def _guardar(conn, valor, data):
    m = conn.execute("SELECT id FROM metas LIMIT 1").fetchone()
    mid = m["id"] if m else metas.criar(conn, HOJE, nome="Viagem", valor_alvo_centavos=1_000_000, modo="mensal", mensal_centavos=50_000)
    metas.guardar(conn, mid, valor, id_conta(conn, "Inter"), data)
    return mid


def test_caixinhas_so_aparecem_depois_de_guardar_algo(conn):
    assert inv.ativo_caixinhas(conn) is None and inv.listar(conn, HOJE) == []
    _guardar(conn, 100_000, date(2026, 10, 1))
    cx = inv.ativo_caixinhas(conn)
    assert (cx.nome, cx.tipo, cx.indexador, cx.taxa, cx.caixinhas) == ("Caixinhas (metas)", "renda_fixa", "cdi", 100.0, True)
    assert [p.ativo.id for p in inv.listar(conn, HOJE)] == [cx.id]
    assert conn.execute("SELECT COUNT(*) FROM ativos WHERE caixinhas = 1").fetchone()[0] == 1


def test_caixinhas_rendem_sem_mexer_no_progresso_da_meta(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    mid = _guardar(conn, 100_000, date(2026, 10, 1))
    p = inv.listar(conn, HOJE)[0]
    assert p.aplicado == 100_000 and p.valor_atual == round(100_000 * (1 + CDI_DIA / 100) ** 20) and p.rendimento > 0
    assert metas.guardado(conn, mid) == 100_000  # a meta continua pelo que foi guardado
    assert inv.patrimonio(conn, HOJE)["caixinhas"] == p.valor_atual


def test_retirar_da_meta_reduz_o_aplicado_das_caixinhas(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    mid = _guardar(conn, 100_000, date(2026, 10, 1))
    metas.retirar(conn, mid, 40_000, id_conta(conn, "Inter"), date(2026, 10, 15))
    p = inv.listar(conn, HOJE)[0]
    assert p.aplicado == 60_000 and p.aportado == 100_000
    f = 1 + CDI_DIA / 100
    esperado = (100_000 * f ** len(_uteis(date(2026, 10, 1), date(2026, 10, 15))) - 40_000) * f ** len(_uteis(date(2026, 10, 15), HOJE))
    assert p.valor_atual == round(esperado)


def test_taxa_das_caixinhas_e_editavel_mas_o_resto_nao(conn):
    _serie_diaria(conn, "cdi", date(2026, 10, 1), HOJE)
    _guardar(conn, 100_000, date(2026, 10, 1))
    cx = inv.ativo_caixinhas(conn)
    inv.editar(conn, HOJE, cx.id, nome="Outro nome", tipo="tesouro", indexador="cdi", taxa=90.0)
    novo = inv.obter(conn, cx.id)
    assert (novo.nome, novo.tipo, novo.taxa) == ("Caixinhas (metas)", "renda_fixa", 90.0)
    assert inv.listar(conn, HOJE)[0].valor_atual == round(100_000 * (1 + CDI_DIA / 100 * 0.9) ** 20)
    with pytest.raises(ValueError, match="não se encerram"):
        inv.encerrar(conn, HOJE, cx.id)


def test_saldo_real_nas_caixinhas(conn):
    _guardar(conn, 100_000, date(2026, 10, 1))
    inv.registrar_saldo_real(conn, inv.ativo_caixinhas(conn).id, 100_500, HOJE)
    assert inv.listar(conn, HOJE)[0].valor_atual == 100_500


# --- Rendimento e % do CDI ---------------------------------------------------------------------------------


def test_rendimento_de_um_aporte(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    r = inv.rendimento(conn, i, date(2026, 10, 1), HOJE)
    assert r.centavos == inv.valor_atual(conn, i, HOJE)[0] - 100_000
    assert r.percentual == pytest.approx(r.centavos / 100_000 * 100 / (1 - 1 / 30), rel=0.02)  # capital médio ≈ 29/30 do período
    # num recorte menor, rende só nos dias dele
    meio = inv.rendimento(conn, i, date(2026, 10, 10), date(2026, 10, 20))
    assert meio.centavos == inv.valor_atual(conn, i, date(2026, 10, 20))[0] - inv.valor_atual(conn, i, date(2026, 10, 9))[0]


def test_rendimento_com_saldo_real_conta_a_correcao(conn):
    i = _novo(conn)
    _aportar(conn, i, 100_000, date(2026, 10, 1))
    inv.registrar_saldo_real(conn, i, 101_000, date(2026, 10, 20))
    assert inv.rendimento(conn, i, date(2026, 10, 1), HOJE).centavos == 1_000


def test_rendimento_sem_aplicacao_no_periodo(conn):
    i = _novo(conn)
    r = inv.rendimento(conn, i, date(2026, 1, 1), HOJE)
    assert r.centavos == 0 and r.percentual is None


def test_rendimento_do_portfolio_soma_ativos_e_caixinhas(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    a, b = _novo(conn), _novo(conn, nome="B", taxa=110.0)
    _aportar(conn, a, 100_000, date(2026, 10, 1))
    _aportar(conn, b, 200_000, date(2026, 10, 1))
    _guardar(conn, 50_000, date(2026, 10, 1))
    total = inv.rendimento_portfolio(conn, date(2026, 1, 1), HOJE).centavos
    soma = sum(p.rendimento for p in inv.listar(conn, HOJE))
    assert total == soma and total > 0


def test_cem_por_cento_do_cdi_rende_cem_por_cento_do_cdi(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    i = _novo(conn, taxa=100.0)
    _aportar(conn, i, 100_000, date(2026, 3, 2))
    inv.resgatar(conn, i, 30_000, id_conta(conn, "Inter"), date(2026, 6, 15))
    _aportar(conn, i, 50_000, date(2026, 8, 3))
    pct = inv.comparar_cdi(conn, date(2026, 1, 1), HOJE, ativo_id=i)
    assert pct == pytest.approx(100.0, abs=0.05)
    assert inv.comparar_cdi(conn, date(2026, 1, 1), HOJE) == pytest.approx(100.0, abs=0.05)  # o portfólio é o mesmo ativo


def test_cento_e_dez_por_cento_do_cdi(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    i = _novo(conn, taxa=110.0)
    _aportar(conn, i, 100_000, date(2026, 2, 2))
    assert inv.comparar_cdi(conn, date(2026, 1, 1), HOJE, ativo_id=i) == pytest.approx(110.0, abs=0.5)


def test_ativo_abaixo_do_cdi_e_prefixado_baixo(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    i = _novo(conn, indexador="pre", taxa=8.0)
    _aportar(conn, i, 100_000, date(2026, 2, 2))
    pct = inv.comparar_cdi(conn, date(2026, 1, 1), HOJE, ativo_id=i)
    assert 60 < pct < 90


def test_comparar_cdi_sem_serie_ou_sem_dinheiro(conn):
    i = _novo(conn)
    assert inv.comparar_cdi(conn, date(2026, 1, 1), HOJE) is None
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    assert inv.comparar_cdi(conn, date(2026, 1, 1), HOJE, ativo_id=i) is None  # nada aplicado
    with pytest.raises(ValueError, match="não existe"):
        inv.comparar_cdi(conn, date(2026, 1, 1), HOJE, ativo_id=999)


# --- Patrimônio e previsão ----------------------------------------------------------------------------------


def _acao(conn, ticker="PETR4"):
    return inv.criar(conn, HOJE, nome=ticker, tipo="acao", ticker=ticker)


def test_patrimonio_e_so_investimentos_mais_caixinhas(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    _lancar(conn, True, 1_000_000, date(2026, 10, 2), "Salário")
    _lancar(conn, False, 30_000, date(2026, 10, 3), "Mercado", conta="Cartão de crédito", metodo="cartao")
    i = _novo(conn)
    _aportar(conn, i, 200_000, date(2026, 10, 5))
    _guardar(conn, 100_000, date(2026, 10, 6))
    p = inv.patrimonio(conn, HOJE)
    assert set(p) == {"investimentos", "caixinhas", "renda_variavel", "total"}
    assert p["investimentos"] == inv.valor_atual(conn, i, HOJE)[0] > 200_000
    assert p["caixinhas"] == inv.listar(conn, HOJE)[0].valor_atual > 100_000
    assert p["total"] == p["investimentos"] + p["caixinhas"]


def test_patrimonio_nao_muda_com_conta_corrente_nem_cartao(conn):
    i = _novo(conn)
    _aportar(conn, i, 400_000, date(2026, 10, 5))
    antes = inv.patrimonio(conn, HOJE)
    _lancar(conn, True, 1_000_000, date(2026, 10, 6), "Salário")
    _lancar(conn, False, 250_000, date(2026, 10, 7), "Moradia")
    _lancar(conn, False, 30_000, date(2026, 10, 8), "Mercado", conta="Cartão de crédito", metodo="cartao")
    assert inv.patrimonio(conn, HOJE) == antes
    assert antes["total"] == 400_000


def test_aportar_e_resgatar_mexem_no_patrimonio(conn):
    i = _novo(conn)
    _aportar(conn, i, 400_000, date(2026, 10, 5))
    assert inv.patrimonio(conn, HOJE)["total"] == 400_000
    inv.resgatar(conn, i, 100_000, id_conta(conn, "Inter"), date(2026, 10, 6))
    assert inv.patrimonio(conn, HOJE)["total"] == 300_000


def test_patrimonio_de_banco_vazio(conn):
    assert inv.patrimonio(conn, HOJE) == {"investimentos": 0, "caixinhas": 0, "renda_variavel": 0, "total": 0}


# --- Meta de aporte -------------------------------------------------------------------------------------------


def test_meta_de_aporte_grava_le_e_apaga(conn):
    assert inv.meta_aporte(conn) is None
    inv.definir_meta_aporte(conn, 100_000)
    assert inv.meta_aporte(conn) == 100_000
    inv.definir_meta_aporte(conn, 0)
    assert inv.meta_aporte(conn) is None
    inv.definir_meta_aporte(conn, 5_000)
    inv.definir_meta_aporte(conn, None)
    assert inv.meta_aporte(conn) is None
    with pytest.raises(ValueError, match="negativa"):
        inv.definir_meta_aporte(conn, -1)


def test_aportes_do_mes_soma_aportes_menos_resgates_por_ativo(conn):
    a, b = _novo(conn, "CDB A"), _novo(conn, "CDB B")
    _aportar(conn, a, 300_000, date(2026, 10, 2))
    _aportar(conn, a, 100_000, date(2026, 10, 20))
    inv.resgatar(conn, a, 50_000, id_conta(conn, "Inter"), date(2026, 10, 25))
    _aportar(conn, b, 80_000, date(2026, 10, 3))
    inv.registrar_saldo_real(conn, b, 999_999, date(2026, 10, 15))  # saldo real não é aporte
    _aportar(conn, b, 777_000, date(2026, 9, 30))  # outro mês
    _aportar(conn, b, 555_000, date(2026, 11, 1))
    assert inv.aportes_do_mes(conn, 2026, 10) == [("CDB A", 350_000), ("CDB B", 80_000)]


def test_aportes_do_mes_conta_caixinhas_acoes_e_some_quem_zerou(conn):
    a = _novo(conn, "CDB A")
    _aportar(conn, a, 100_000, date(2026, 10, 2))
    inv.resgatar(conn, a, 100_000, id_conta(conn, "Inter"), date(2026, 10, 3))  # zerou: some da lista
    _guardar(conn, 60_000, date(2026, 10, 4))
    petr = _acao(conn)
    renda_variavel.comprar(conn, petr, date(2026, 10, 6), 10, 3000, 150, id_conta(conn, "Inter"))  # 300,00 + 1,50 de taxa
    renda_variavel.comprar(conn, petr, date(2026, 10, 7), 5, 3000, 0, None)  # só a posição: não mexeu na conta
    assert inv.aportes_do_mes(conn, 2026, 10) == [(inv.NOME_CAIXINHAS, 60_000), ("PETR4", 30_150)]
    renda_variavel.vender(conn, petr, date(2026, 10, 8), 4, 3500, 100, id_conta(conn, "Inter"))
    assert dict(inv.aportes_do_mes(conn, 2026, 10))["PETR4"] == 30_150 - (14_000 - 100)


def _progresso(conn, meta, aportado):
    inv.definir_meta_aporte(conn, meta)
    if aportado:
        _aportar(conn, _novo(conn, f"CDB {aportado}"), aportado, date(2026, 10, 5))
    return inv.progresso_do_mes(conn, HOJE)


@pytest.mark.parametrize(
    "aportado, situacao, falta",
    [(94_900, "faltando", 5_100), (95_000, "batida", 0), (100_000, "batida", 0), (105_000, "batida", 0), (105_100, "passou", 0)],
)
def test_progresso_do_mes_bordas_da_margem_de_50_reais(conn, aportado, situacao, falta):
    p = _progresso(conn, 100_000, aportado)
    assert (p.meta, p.aportado, p.situacao, p.falta) == (100_000, aportado, situacao, falta)


def test_progresso_sem_meta_e_sem_aporte(conn):
    p = inv.progresso_do_mes(conn, HOJE)
    assert (p.meta, p.aportado, p.por_ativo, p.situacao, p.falta) == (None, 0, [], "sem_meta", 0)
    inv.definir_meta_aporte(conn, 100_000)
    p = inv.progresso_do_mes(conn, HOJE)
    assert (p.situacao, p.falta) == ("faltando", 100_000)


def test_progresso_soma_varios_ativos_e_o_resgate_desconta(conn):
    inv.definir_meta_aporte(conn, 100_000)
    a, b = _novo(conn, "CDB A"), _novo(conn, "CDB B")
    _aportar(conn, a, 60_000, date(2026, 10, 2))
    _aportar(conn, b, 60_000, date(2026, 10, 3))
    assert inv.progresso_do_mes(conn, HOJE).situacao == "passou"  # 1200 de 1000
    inv.resgatar(conn, b, 20_000, id_conta(conn, "Inter"), date(2026, 10, 4))
    p = inv.progresso_do_mes(conn, HOJE)
    assert (p.aportado, p.situacao) == (100_000, "batida") and p.por_ativo == [("CDB A", 60_000), ("CDB B", 40_000)]


# --- Previsão -------------------------------------------------------------------------------------------------


def test_previsao_com_meta_soma_o_que_falta_do_mes_e_a_meta_dos_proximos(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 40_000, date(2026, 10, 5))
    v = inv.previsao_fim_do_ano(conn, HOJE)
    pat = inv.patrimonio(conn, HOJE)["total"]
    assert v["alvo"] == date(2026, 12, 31) and v["patrimonio_hoje"] == pat and v["meta"] == 100_000
    assert v["aportes_planejados"] == 60_000 + 2 * 100_000  # falta de outubro + novembro + dezembro
    assert v["rendimento_projetado"] > 0 and v["rendimento_dos_aportes"] > 0
    # o aporte de dezembro rende menos que o de hoje: o rendimento dos aportes fica entre 0 e o da taxa cheia sobre tudo
    assert v["rendimento_dos_aportes"] < v["aportes_planejados"] * v["rendimento_projetado"] / pat
    assert v["total"] == pat + v["aportes_planejados"] + v["rendimento_projetado"] + v["rendimento_dos_aportes"] + v["proventos_projetados"]


def test_previsao_com_meta_batida_so_soma_os_meses_seguintes(conn):
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 100_000, date(2026, 10, 5))
    v = inv.previsao_fim_do_ano(conn, HOJE)
    assert v["aportes_planejados"] == 200_000 and v["total"] == 100_000 + 200_000  # sem taxa, sem rendimento


def test_previsao_sem_meta_e_so_o_rendimento(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    i = _novo(conn)
    _aportar(conn, i, 1_000_000, date(2026, 10, 1))
    v = inv.previsao_fim_do_ano(conn, HOJE)
    assert v["meta"] is None and v["aportes_planejados"] == 0 and v["rendimento_dos_aportes"] == 0
    atual = inv.valor_atual(conn, i, HOJE)[0]
    dias = len(_uteis(HOJE, date(2026, 12, 31)))
    assert v["rendimento_projetado"] == round(atual * (1 + CDI_DIA / 100) ** dias) - atual
    assert v["total"] == v["patrimonio_hoje"] + v["rendimento_projetado"]


def test_previsao_nao_muda_com_dinheiro_na_conta_corrente(conn):
    _historico(conn)
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 400_000, date(2026, 10, 1))
    antes = inv.previsao_fim_do_ano(conn, HOJE), inv.serie_patrimonio_no_ano(conn, HOJE)
    _lancar(conn, True, 900_000, date(2026, 10, 12), "Salário")
    _lancar(conn, False, 300_000, date(2026, 10, 13), "Moradia")
    _lancar(conn, False, 30_000, date(2026, 10, 14), "Mercado", conta="Cartão de crédito", metodo="cartao")
    depois = inv.previsao_fim_do_ano(conn, HOJE), inv.serie_patrimonio_no_ano(conn, HOJE)
    assert depois[0] == antes[0]
    pd.testing.assert_frame_equal(depois[1], antes[1])


def test_previsao_em_31_de_dezembro_com_meta_so_falta_do_mes(conn):
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 30_000, date(2026, 12, 2))
    v = inv.previsao_fim_do_ano(conn, date(2026, 12, 31))
    assert v["aportes_planejados"] == 70_000 and v["patrimonio_hoje"] == 30_000


def test_previsao_inclui_as_caixinhas(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    _guardar(conn, 1_000_000, date(2026, 10, 1))
    v = inv.previsao_fim_do_ano(conn, HOJE)
    assert v["rendimento_projetado"] > 9_000  # ≈ 10,6% a.a. em ~2 meses sobre R$ 10 mil


def test_serie_do_patrimonio_no_ano(conn):
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 500_000, date(2026, 6, 10))
    df = inv.serie_patrimonio_no_ano(conn, HOJE)
    assert list(df.columns) == ["data", "centavos", "projetado"] and df["centavos"].dtype == "int64"
    real, proj = df[~df["projetado"]], df[df["projetado"]]
    assert real["data"].iloc[0] == pd.Timestamp("2026-01-01") and real["data"].iloc[-1] == pd.Timestamp("2026-10-30")
    assert (real["data"].diff().dropna().dt.days[:-1] == 7).all()
    assert real["centavos"].iloc[-1] == inv.patrimonio(conn, HOJE)["total"]
    assert list(proj["data"].dt.date) == [date(2026, 11, 30), date(2026, 12, 31)]  # 31/10 fica de fora: colado em hoje
    assert proj["centavos"].iloc[0] >= real["centavos"].iloc[-1]
    assert proj["centavos"].iloc[-1] == inv.previsao_fim_do_ano(conn, HOJE)["total"]
    assert df["data"].is_monotonic_increasing
    # antes do primeiro aporte não havia nada guardado; o aporte entra no dia dele
    assert real[real["data"] == pd.Timestamp("2026-06-04")]["centavos"].iloc[0] == 0
    assert real[real["data"] == pd.Timestamp("2026-06-11")]["centavos"].iloc[0] >= 500_000


def test_serie_em_31_de_dezembro_nao_tem_projecao(conn):
    df = inv.serie_patrimonio_no_ano(conn, date(2026, 12, 31))
    assert not df["projetado"].any() and df["data"].iloc[-1] == pd.Timestamp("2026-12-31")


def test_resumo_junta_os_numeros_do_topo(conn):
    _historico(conn)
    _serie_diaria(conn, "cdi", date(2026, 1, 1), HOJE)
    inv.definir_meta_aporte(conn, 100_000)
    _aportar(conn, _novo(conn), 1_000_000, date(2026, 2, 2))
    _aportar(conn, _novo(conn, "CDB B"), 30_000, date(2026, 10, 2))
    r = inv.resumo(conn, HOJE)
    assert r.patrimonio == inv.patrimonio(conn, HOJE) and r.previsao == inv.previsao_fim_do_ano(conn, HOJE)
    assert r.progresso == inv.progresso_do_mes(conn, HOJE) and r.progresso.aportado == 30_000
    assert r.rendimento_ano == inv.rendimento_portfolio(conn, date(2026, 1, 1), HOJE)
    assert r.pct_cdi == pytest.approx(100.0, abs=0.05)
