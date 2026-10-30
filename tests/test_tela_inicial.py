"""Lógica da tela inicial (F2): em conta, devo, livre, gastos por dia, top categorias, recentes."""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import lancamentos as lanc
from meu_cofre import relatorios as rel

HOJE = date(2026, 10, 20)  # mês com 31 dias: 12 dias restantes contando hoje


def _reg(conn, valor, *, entrada=False, cat="Mercado", data=date(2026, 10, 5), metodo="pix",
         conta="Inter", descricao="x", parcelas=1):
    return lanc.registrar(
        conn, entrada=entrada, valor_centavos=valor, descricao=descricao,
        categoria_id=id_cat(conn, cat), metodo=metodo, conta_id=id_conta(conn, conta),
        data=data, parcelas=parcelas,
    )


# --- Em conta / Devo / Livre -----------------------------------------------------


def test_tudo_zerado_em_banco_vazio(conn):
    assert rel.em_conta(conn, HOJE) == 0
    assert rel.devo(conn) == 0
    assert rel.livre_ate_fim_do_mes(conn, HOJE) == 0
    assert rel.por_dia_ate_fim_do_mes(conn, HOJE) == 0


def test_em_conta_soma_corrente_e_dinheiro_ate_hoje(conn):
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 100000 WHERE nome = 'Inter'")
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 5000 WHERE nome = 'Carteira'")
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 99999 WHERE nome = 'Cartão de crédito'")
    _reg(conn, 300000, entrada=True, cat="Salário", data=date(2026, 10, 5))
    _reg(conn, 20000, data=date(2026, 10, 10))
    _reg(conn, 1000, metodo="dinheiro", data=date(2026, 10, 12))
    _reg(conn, 40000, data=date(2026, 10, 25))  # futuro: não conta
    _reg(conn, 7000, metodo="cartao", data=date(2026, 10, 3))  # cartão: não conta
    assert rel.em_conta(conn, HOJE) == 100000 + 5000 + 300000 - 20000 - 1000


def test_devo_inclui_parcelas_futuras_e_ignora_contas_correntes(conn):
    _reg(conn, 60000, metodo="cartao", data=date(2026, 10, 10), parcelas=6, descricao="Tênis")
    _reg(conn, 5000, data=date(2026, 10, 11))  # Pix: não é do cartão
    assert rel.devo(conn) == 60000  # as 6 parcelas, inclusive as de nov a mar


def test_devo_diminui_quando_a_fatura_e_paga(conn):
    _reg(conn, 30000, metodo="cartao")
    _reg(conn, 30000, entrada=True, cat="Pagamento de fatura", metodo="pix", conta="Inter")
    # a entrada pelo Pix cai no Inter; o abatimento no cartão é uma entrada na conta do cartão
    conn.execute(
        "UPDATE lancamentos SET conta_id = ? WHERE categoria_id = ?",
        (id_conta(conn, "Cartão de crédito"), id_cat(conn, "Pagamento de fatura")),
    )
    assert rel.devo(conn) == 0


def test_livre_so_conta_do_cartao_o_que_vence_ate_o_fim_do_mes(conn):
    _reg(conn, 500000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    _reg(conn, 40000, metodo="cartao", data=date(2026, 10, 10), parcelas=4, descricao="Tênis")
    # parcelas: 10/10, 10/11, 10/12, 10/01 (R$ 100 cada). Devo = 400; no livre só a do mês.
    assert rel.devo(conn) == 40000
    assert rel.livre_ate_fim_do_mes(conn, HOJE) == 500000 - 10000


def test_livre_desconta_saidas_futuras_do_mes_e_ignora_transferencia(conn):
    _reg(conn, 500000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    _reg(conn, 150000, cat="Moradia", data=date(2026, 10, 28))  # aluguel ainda vai sair
    _reg(conn, 8000, cat="Mercado", data=date(2026, 10, 20))  # hoje: já está em "em conta"
    _reg(conn, 90000, cat="Moradia", data=date(2026, 11, 5))  # mês que vem: fora
    _reg(conn, 70000, cat="Pagamento de fatura", data=date(2026, 10, 30))  # transferência: fora
    _reg(conn, 30000, entrada=True, cat="Renda extra", data=date(2026, 10, 29))  # entrada futura: fora
    assert rel.livre_ate_fim_do_mes(conn, HOJE) == 500000 - 8000 - 150000


def test_livre_negativo(conn):
    _reg(conn, 10000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    _reg(conn, 30000, metodo="cartao", data=date(2026, 10, 10))
    assert rel.livre_ate_fim_do_mes(conn, HOJE) == -20000
    assert rel.por_dia_ate_fim_do_mes(conn, HOJE) < 0


def test_por_dia_divide_pelos_dias_que_faltam_contando_hoje(conn):
    _reg(conn, 120000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    assert rel.por_dia_ate_fim_do_mes(conn, date(2026, 10, 20)) == 120000 // 12
    assert rel.por_dia_ate_fim_do_mes(conn, date(2026, 10, 1)) == 120000 // 31


def test_por_dia_no_ultimo_dia_do_mes_nao_divide_por_zero(conn):
    _reg(conn, 120000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    assert rel.por_dia_ate_fim_do_mes(conn, date(2026, 10, 31)) == 120000
    assert rel.por_dia_ate_fim_do_mes(conn, date(2028, 2, 29)) == 120000  # fevereiro bissexto


# --- Gastos por dia -----------------------------------------------------------------


def test_gastos_por_dia_preenche_dias_sem_gasto(conn):
    _reg(conn, 1000, data=date(2026, 10, 2))
    _reg(conn, 500, data=date(2026, 10, 2))
    _reg(conn, 700, data=date(2026, 10, 4))
    _reg(conn, 9000, entrada=True, cat="Salário", data=date(2026, 10, 3))  # entrada: fora
    _reg(conn, 4000, cat="Pagamento de fatura", data=date(2026, 10, 3))  # transferência: fora
    _reg(conn, 800, data=date(2026, 9, 30))  # outro mês: fora
    df = rel.gastos_por_dia(conn, 2026, 10, date(2026, 10, 5))
    assert list(df.columns) == ["data", "centavos"]
    assert df["centavos"].tolist() == [0, 1500, 0, 700, 0]
    assert df["data"].iloc[0] == date(2026, 10, 1) or df["data"].iloc[0].date() == date(2026, 10, 1)


def test_gastos_por_dia_sem_limite_vai_ate_o_fim_do_mes(conn):
    assert len(rel.gastos_por_dia(conn, 2026, 10, None)) == 31
    assert len(rel.gastos_por_dia(conn, 2026, 2, None)) == 28
    assert len(rel.gastos_por_dia(conn, 2026, 10, date(2026, 12, 1))) == 31  # `ate` além do mês


def test_gastos_por_dia_mes_sem_dados_e_mes_futuro(conn):
    df = rel.gastos_por_dia(conn, 2026, 10, date(2026, 10, 3))
    assert df["centavos"].tolist() == [0, 0, 0]
    assert rel.gastos_por_dia(conn, 2026, 11, date(2026, 10, 20)).empty


# --- Top categorias -------------------------------------------------------------------


def test_top_categorias_vazio(conn):
    df = rel.gastos_top_categorias(conn, 2026, 10)
    assert df.empty
    assert list(df.columns) == ["categoria", "centavos", "percentual"]


def test_top_categorias_sem_resto_nao_cria_outros(conn):
    _reg(conn, 3000, cat="Mercado")
    _reg(conn, 1000, cat="Lazer")
    df = rel.gastos_top_categorias(conn, 2026, 10)
    assert df["categoria"].tolist() == ["Mercado", "Lazer"]
    assert df["percentual"].tolist() == [75.0, 25.0]


def test_top_categorias_agrupa_resto_em_outros(conn):
    nomes = ["Mercado", "Moradia", "Lazer", "Saúde", "Educação", "Delivery", "Assinaturas"]
    for i, nome in enumerate(nomes):
        _reg(conn, (len(nomes) - i) * 1000, cat=nome)  # 7000, 6000, ..., 1000
    df = rel.gastos_top_categorias(conn, 2026, 10)
    assert df["categoria"].tolist() == nomes[:5] + ["Outros"]
    assert df["centavos"].tolist() == [7000, 6000, 5000, 4000, 3000, 3000]  # 2000 + 1000
    assert df["centavos"].sum() == 28000
    assert df["percentual"].sum() == pytest.approx(100)


def test_top_categorias_sem_categoria_vira_a_categorizar(conn):
    lanc.registrar(conn, entrada=False, valor_centavos=2500, descricao="?", categoria_id=None,
                   metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5))
    _reg(conn, 1000, cat="Mercado")
    df = rel.gastos_top_categorias(conn, 2026, 10)
    assert df["categoria"].tolist() == ["A categorizar", "Mercado"]


def test_top_categorias_outros_do_seed_nao_duplica(conn):
    for nome, v in (("Mercado", 9000), ("Outros", 8000), ("Lazer", 7000)):
        _reg(conn, v, cat=nome)
    df = rel.gastos_top_categorias(conn, 2026, 10, n=1)
    assert df["categoria"].tolist() == ["Mercado", "Outros"]
    assert df["centavos"].tolist() == [9000, 15000]


# --- Recentes ----------------------------------------------------------------------------


def test_recentes_ordem_por_data_e_limite(conn):
    # Decisão da F5: recentes ordena por data desc (id desc no empate), não por ordem de lançamento.
    _reg(conn, 100, data=date(2026, 10, 20), descricao="data recente, lançado primeiro")
    _reg(conn, 200, data=date(2026, 1, 1), descricao="data antiga, lançado depois")
    _reg(conn, 300, data=date(2026, 10, 20), descricao="mesma data, lançado por último")
    df = lanc.recentes(conn, HOJE, n=10)
    assert df["descricao"].tolist() == [
        "mesma data, lançado por último", "data recente, lançado primeiro", "data antiga, lançado depois"
    ]
    assert len(lanc.recentes(conn, HOJE, n=1)) == 1
    assert list(df.columns) == ["id", "descricao", "categoria", "conta", "conta_final", "metodo", "data",
                                "valor_centavos", "parcela_atual", "parcela_total", "recorrente_id", "meta_id"]


def test_recentes_nao_mostra_o_futuro(conn):
    _reg(conn, 100, data=date(2026, 10, 20), descricao="hoje")
    _reg(conn, 200, data=date(2026, 10, 21), descricao="amanhã")
    _reg(conn, 60000, metodo="cartao", parcelas=3, data=date(2026, 10, 10), descricao="Tênis")  # parcelas 10/10, 10/11, 10/12
    nomes = lanc.recentes(conn, HOJE)["descricao"].tolist()
    assert nomes == ["hoje", "Tênis"]  # a parcela do dia 10 aparece; as dos meses seguintes e "amanhã" não
    assert lanc.recentes(conn, date(2026, 10, 21))["descricao"].tolist() == ["amanhã", "hoje", "Tênis"]


def test_recentes_vazio(conn):
    assert lanc.recentes(conn, HOJE).empty


def test_recentes_filtros(conn):
    _reg(conn, 5000, entrada=True, cat="Salário", descricao="Salário", data=date(2026, 10, 1))
    _reg(conn, 700, descricao="Padaria", data=date(2026, 10, 2))
    _reg(conn, 3000, cat="Pagamento de fatura", descricao="Fatura", data=date(2026, 10, 3))
    assert lanc.recentes(conn, HOJE, filtro="todos")["descricao"].tolist() == ["Fatura", "Padaria", "Salário"]
    assert lanc.recentes(conn, HOJE, filtro="entradas")["descricao"].tolist() == ["Salário"]
    assert lanc.recentes(conn, HOJE, filtro="saidas")["descricao"].tolist() == ["Padaria"]
    with pytest.raises(ValueError):
        lanc.recentes(conn, HOJE, filtro="outro")


def test_recentes_traz_parcela_e_nomes(conn):
    _reg(conn, 60000, metodo="cartao", parcelas=6, descricao="Tênis", cat="Compras pessoais")
    linha = lanc.recentes(conn, date(2027, 6, 1), n=10).iloc[0]  # a última parcela (a de data mais nova) vem primeiro
    assert (linha["categoria"], linha["conta"], linha["metodo"]) == ("Compras pessoais", "Cartão de crédito", "cartao")
    assert (linha["parcela_atual"], linha["parcela_total"]) == (6, 6)
