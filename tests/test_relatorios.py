from datetime import date

from conftest import id_cat, id_conta

from meu_cofre import lancamentos as lanc
from meu_cofre import relatorios as rel


def _reg(conn, valor, entrada=False, cat="Mercado", data=date(2026, 10, 5), **kw):
    base = dict(entrada=entrada, valor_centavos=valor, descricao="x", categoria_id=id_cat(conn, cat),
                metodo="pix", conta_id=id_conta(conn, "Inter"), data=data)
    return lanc.registrar(conn, **{**base, **kw})


def test_transferencia_nao_conta(conn):
    _reg(conn, 100000, entrada=True, cat="Salário")
    _reg(conn, 20000, cat="Mercado")
    _reg(conn, 50000, cat="Pagamento de fatura")
    _reg(conn, 9999, cat="Mercado", metodo="pix")
    # metodo transferencia sem categoria de transferência também não conta
    conn.execute("UPDATE lancamentos SET metodo='transferencia' WHERE valor_centavos=-9999")
    r = rel.resumo_mes(conn, 2026, 10)
    assert (r.entradas, r.saidas, r.sobra) == (100000, 20000, 80000)
    assert r.taxa_poupanca == 0.8
    assert rel.gastos_por_categoria(conn, 2026, 10)["categoria"].tolist() == ["Mercado"]


def test_taxa_sem_renda(conn):
    _reg(conn, 1000)
    assert rel.resumo_mes(conn, 2026, 10).taxa_poupanca is None


def test_gastos_por_natureza_e_vazio(conn):
    assert rel.gastos_por_natureza(conn, 2026, 10).empty
    _reg(conn, 1000, cat="Mercado")
    _reg(conn, 500, cat="Delivery")
    _reg(conn, 300, cat="A categorizar")
    nat = rel.gastos_por_natureza(conn, 2026, 10)
    assert dict(zip(nat["natureza"], nat["centavos"])) == {
        "essencial": 1000, "superfluo": 500, "sem natureza": 300}


def test_saldo_com_saldo_inicial(conn):
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 100000 WHERE nome='Inter'")
    _reg(conn, 30000, data=date(2026, 10, 5))
    _reg(conn, 10000, entrada=True, cat="Salário", data=date(2026, 10, 20))
    assert rel.saldo_em(conn, date(2026, 10, 4)) == 100000
    assert rel.saldo_em(conn, date(2026, 10, 10)) == 70000
    assert rel.projecao_fim_do_mes(conn, date(2026, 10, 10)) == 80000
    df = rel.saldo_diario(conn, date(2026, 10, 1), date(2026, 10, 31))
    assert len(df) == 31 and df["centavos"].iloc[0] == 100000 and df["centavos"].iloc[-1] == 80000


def test_saldo_diario_sem_movimento(conn):
    df = rel.saldo_diario(conn, date(2026, 10, 1), date(2026, 10, 31))
    assert len(df) == 31 and (df["centavos"] == 0).all()


def test_historico_mensal(conn):
    _reg(conn, 1000, data=date(2026, 9, 10))
    _reg(conn, 5000, entrada=True, cat="Salário", data=date(2026, 10, 1))
    h = rel.historico_mensal(conn, date(2026, 10, 15), 6)
    assert h["mes"].tolist()[0] == "2026-05" and h["mes"].tolist()[-1] == "2026-10"
    assert h.iloc[-2]["saidas"] == 1000 and h.iloc[-1]["entradas"] == 5000


def test_cartao_conta_no_mes_da_compra(conn):
    _reg(conn, 30000, cat="Compras pessoais", metodo="cartao", data=date(2026, 10, 10), parcelas=3)
    assert rel.resumo_mes(conn, 2026, 10).saidas == 10000
    assert rel.resumo_mes(conn, 2026, 11).saidas == 10000
    assert rel.resumo_mes(conn, 2026, 12).saidas == 10000
