"""Transferência entre contas próprias (issue #1): os dois lados, validações, recentes e relatórios intactos. Sem Qt."""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import lancamentos as lanc
from meu_cofre import metas, relatorios

HOJE = date(2026, 10, 20)


def _saldo(conn, nome):
    return conn.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos WHERE conta_id = ?", (id_conta(conn, nome),)
    ).fetchone()[0]


def _transferir(conn, valor=10_000, origem="Inter", destino="Carteira", data=date(2026, 10, 10), descricao=""):
    return lanc.transferir(
        conn, origem_id=id_conta(conn, origem), destino_id=id_conta(conn, destino),
        valor_centavos=valor, data=data, descricao=descricao,
    )


def _gasto(conn):
    lanc.registrar(
        conn, entrada=False, valor_centavos=4_000, descricao="Mercado", categoria_id=id_cat(conn, "Mercado"),
        metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 8),
    )
    lanc.registrar(
        conn, entrada=True, valor_centavos=300_000, descricao="Salário", categoria_id=id_cat(conn, "Salário"),
        metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5),
    )


def _relatorios(conn):
    return (
        relatorios.resumo_mes(conn, 2026, 10),
        relatorios.gastos_por_categoria(conn, 2026, 10).to_dict("records"),
        relatorios.gastos_por_dia(conn, 2026, 10, HOJE).to_dict("records"),
        relatorios.livre_ate_fim_do_mes(conn, HOJE),
        metas.sobra_media(conn, HOJE),
    )


def test_transferir_move_o_saldo_das_duas_contas(conn):
    antes = (_saldo(conn, "Inter"), _saldo(conn, "Carteira"))
    tid = _transferir(conn, 12_345, descricao="Saque")
    assert _saldo(conn, "Inter") == antes[0] - 12_345 and _saldo(conn, "Carteira") == antes[1] + 12_345
    lados = conn.execute("SELECT * FROM lancamentos WHERE transferencia_id = ? ORDER BY valor_centavos", (tid,)).fetchall()
    assert [l["valor_centavos"] for l in lados] == [-12_345, 12_345]
    assert {l["metodo"] for l in lados} == {"transferencia"} and {l["descricao"] for l in lados} == {"Saque"}
    assert {l["categoria_id"] for l in lados} == {id_cat(conn, "Transferência entre contas")}


def test_descricao_padrao(conn):
    tid = _transferir(conn)
    assert lanc.obter_transferencia(conn, conn.execute("SELECT id FROM lancamentos").fetchone()["id"])["descricao"] == "Transferência"
    assert tid


def test_nenhum_relatorio_muda(conn):
    _gasto(conn)
    antes = _relatorios(conn)
    _transferir(conn, 50_000)
    _transferir(conn, 7_000, origem="Carteira", destino="Inter", data=date(2026, 10, 12))
    assert _relatorios(conn) == antes


@pytest.mark.parametrize(
    "kw",
    [dict(valor=0), dict(valor=-5), dict(destino="Inter")],
)
def test_validacoes_basicas(conn, kw):
    with pytest.raises(ValueError):
        _transferir(conn, **kw)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0


def test_so_contas_correntes_e_carteira_ativas(conn):
    cartao = conn.execute("SELECT nome FROM contas WHERE tipo = 'cartao' LIMIT 1").fetchone()["nome"]
    with pytest.raises(ValueError):
        _transferir(conn, destino=cartao)
    with pytest.raises(ValueError):
        _transferir(conn, destino="Investimentos")
    conn.execute("UPDATE contas SET ativa = 0 WHERE nome = 'Carteira'")
    with pytest.raises(ValueError):
        _transferir(conn)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0


def test_editar_atualiza_os_dois_lados(conn):
    tid = _transferir(conn, 10_000)
    conn.execute("INSERT INTO contas (nome, tipo) VALUES ('Nubank', 'corrente')")
    lanc.atualizar_transferencia(
        conn, tid, origem_id=id_conta(conn, "Carteira"), destino_id=id_conta(conn, "Nubank"),
        valor_centavos=2_500, data=date(2026, 10, 15), descricao="Devolvi",
    )
    lados = conn.execute("SELECT * FROM lancamentos WHERE transferencia_id = ? ORDER BY valor_centavos", (tid,)).fetchall()
    assert [(l["conta_id"], l["valor_centavos"], l["data"], l["descricao"]) for l in lados] == [
        (id_conta(conn, "Carteira"), -2_500, "2026-10-15", "Devolvi"),
        (id_conta(conn, "Nubank"), 2_500, "2026-10-15", "Devolvi"),
    ]
    with pytest.raises(ValueError):  # mesmas regras do transferir
        lanc.atualizar_transferencia(
            conn, tid, origem_id=id_conta(conn, "Nubank"), destino_id=id_conta(conn, "Nubank"),
            valor_centavos=1, data=HOJE,
        )


def test_excluir_apaga_os_dois_lados(conn):
    tid = _transferir(conn)
    _transferir(conn, 500)
    lanc.excluir_transferencia(conn, tid)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE transferencia_id = ?", (tid,)).fetchone()[0] == 0


def test_obter_transferencia_so_reconhece_contas_proprias(conn):
    tid = _transferir(conn, 9_900, data=date(2026, 10, 3), descricao="Pix")
    ids = [r["id"] for r in conn.execute("SELECT id FROM lancamentos WHERE transferencia_id = ?", (tid,))]
    for id_ in ids:  # qualquer um dos lados leva à mesma transferência
        assert lanc.obter_transferencia(conn, id_) == dict(
            transferencia_id=tid, origem_id=id_conta(conn, "Inter"), destino_id=id_conta(conn, "Carteira"),
            valor_centavos=9_900, data="2026-10-03", descricao="Pix",
        )
    # pagamento de fatura (conta -> cartão) e lançamento comum não são transferência entre contas
    from meu_cofre import faturas

    cartao = conn.execute("SELECT id FROM contas WHERE tipo = 'cartao' LIMIT 1").fetchone()["id"]
    lanc.registrar(
        conn, entrada=False, valor_centavos=3_000, descricao="Compra", categoria_id=id_cat(conn, "Lazer"),
        metodo="cartao", conta_id=cartao, data=date(2026, 10, 1),
    )
    faturas.pagar_fatura(conn, cartao, "2026-10", 3_000, id_conta(conn, "Inter"), date(2026, 10, 9))
    comuns = conn.execute("SELECT id FROM lancamentos WHERE transferencia_id IS NULL OR transferencia_id <> ?", (tid,))
    assert all(lanc.obter_transferencia(conn, r["id"]) is None for r in comuns)


def test_recentes_mostram_uma_linha_so(conn):
    _gasto(conn)
    _transferir(conn, 10_000, descricao="Saque")
    df = lanc.recentes(conn, HOJE, 10)
    linha = df[df["descricao"] == "Saque"]
    assert len(linha) == 1 and df["descricao"].tolist().count("Saque") == 1
    assert (linha.iloc[0]["conta"], linha.iloc[0]["transferencia_destino"]) == ("Inter", "Carteira")
    assert linha.iloc[0]["valor_centavos"] == -10_000
    assert df[df["descricao"] != "Saque"]["transferencia_destino"].isna().all()
    # filtros de entradas e saídas não mostram transferência
    assert "Saque" not in lanc.recentes(conn, HOJE, 10, "saidas")["descricao"].tolist()
    assert "Saque" not in lanc.recentes(conn, HOJE, 10, "entradas")["descricao"].tolist()


def test_recentes_nao_quebram_fatura_e_meta(conn):
    from meu_cofre import faturas

    cartao = conn.execute("SELECT id FROM contas WHERE tipo = 'cartao' LIMIT 1").fetchone()["id"]
    lanc.registrar(
        conn, entrada=False, valor_centavos=3_000, descricao="Compra", categoria_id=id_cat(conn, "Lazer"),
        metodo="cartao", conta_id=cartao, data=date(2026, 10, 1),
    )
    faturas.pagar_fatura(conn, cartao, "2026-10", 3_000, id_conta(conn, "Inter"), date(2026, 10, 9))
    df = lanc.recentes(conn, HOJE, 10)
    assert len(df) == 3  # compra + os dois lados do pagamento, como antes
    assert df["transferencia_destino"].isna().all()
