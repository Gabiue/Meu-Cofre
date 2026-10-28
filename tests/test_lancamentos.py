from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import lancamentos as lanc


def test_para_centavos():
    assert lanc.para_centavos("1.234,56") == 123456
    assert lanc.para_centavos("10") == 1000
    assert lanc.para_centavos("R$ 5,5") == 550
    assert lanc.para_centavos(0.1 + 0.2) == 30
    assert lanc.para_centavos(str(19.99)) == 1999


def test_formatar_brl():
    assert lanc.formatar_brl(123456) == "R$ 1.234,56"
    assert lanc.formatar_brl(-500) == "-R$ 5,00"
    assert lanc.formatar_brl(0) == "R$ 0,00"
    assert lanc.formatar_brl(100000000) == "R$ 1.000.000,00"


def test_somar_meses():
    assert lanc.somar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert lanc.somar_meses(date(2028, 1, 31), 1) == date(2028, 2, 29)  # bissexto
    assert lanc.somar_meses(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert lanc.somar_meses(date(2026, 1, 31), 2) == date(2026, 3, 31)


@pytest.mark.parametrize("total,n", [(10000, 3), (1, 5), (99999, 12), (1000, 1)])
def test_dividir_em_parcelas(total, n):
    p = lanc.dividir_em_parcelas(total, n)
    assert len(p) == n and sum(p) == total
    assert max(p) - min(p) <= n  # só a primeira leva a sobra


def _reg(conn, **kw):
    base = dict(entrada=False, valor_centavos=1000, descricao="Teste", categoria_id=None,
                metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 5))
    return lanc.registrar(conn, **{**base, **kw})


def test_registrar_sinal(conn):
    _reg(conn, entrada=False)
    _reg(conn, entrada=True, valor_centavos=500)
    valores = sorted(r[0] for r in conn.execute("SELECT valor_centavos FROM lancamentos"))
    assert valores == [-1000, 500]


def test_registrar_conta_por_metodo(conn):
    _reg(conn, metodo="cartao")
    _reg(conn, metodo="dinheiro")
    _reg(conn, metodo="boleto", conta_id=id_conta(conn, "Caixa"))
    df = lanc.listar(conn)
    por_metodo = dict(zip(df["metodo"], df["conta"]))
    assert por_metodo == {"cartao": "Cartão de crédito", "dinheiro": "Carteira", "boleto": "Caixa"}


def test_registrar_parcelado(conn):
    ids = _reg(conn, metodo="cartao", valor_centavos=10000, parcelas=3, data=date(2026, 11, 30))
    assert len(ids) == 3
    linhas = conn.execute("SELECT * FROM lancamentos ORDER BY data").fetchall()
    assert [l["data"] for l in linhas] == ["2026-11-30", "2026-12-30", "2027-01-30"]
    assert [l["parcela_atual"] for l in linhas] == [1, 2, 3]
    assert {l["parcela_total"] for l in linhas} == {3}
    assert sum(l["valor_centavos"] for l in linhas) == -10000
    assert {l["status"] for l in linhas} == {"realizado"}


def test_parcelas_so_no_cartao_saida(conn):
    assert len(_reg(conn, metodo="pix", parcelas=5)) == 1
    assert len(_reg(conn, metodo="cartao", entrada=True, parcelas=5)) == 1


def test_registrar_validacoes(conn):
    with pytest.raises(ValueError):
        _reg(conn, valor_centavos=0)
    with pytest.raises(ValueError):
        _reg(conn, descricao="   ")
    with pytest.raises(ValueError):
        _reg(conn, metodo="cheque")
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0


def test_sugerir_categoria_historico(conn):
    _reg(conn, descricao="Almoço no RU", categoria_id=id_cat(conn, "Alimentação fora"))
    esperado = id_cat(conn, "Alimentação fora")
    assert lanc.sugerir_categoria(conn, "ALMOCO   no ru", "despesa") == esperado
    assert lanc.sugerir_categoria(conn, "outra coisa", "despesa") is None
    assert lanc.sugerir_categoria(conn, "", "despesa") is None


def test_sugerir_categoria_regra(conn):
    conn.execute("INSERT INTO regras_categorizacao (padrao, categoria_id) VALUES ('ifood', ?)",
                 (id_cat(conn, "Delivery"),))
    assert lanc.sugerir_categoria(conn, "iFood pedido", "despesa") == id_cat(conn, "Delivery")
    # histórico vence a regra
    _reg(conn, descricao="iFood pedido", categoria_id=id_cat(conn, "Lazer"))
    assert lanc.sugerir_categoria(conn, "ifood PEDIDO", "despesa") == id_cat(conn, "Lazer")


def test_descricoes_usadas_por_tipo(conn):
    _reg(conn, descricao="Café", categoria_id=id_cat(conn, "Alimentação fora"))
    _reg(conn, descricao="Café", categoria_id=id_cat(conn, "Alimentação fora"))
    _reg(conn, descricao="Freela", entrada=True, categoria_id=id_cat(conn, "Renda extra"))
    assert lanc.descricoes_usadas(conn, "despesa") == ["Café"]
    assert lanc.descricoes_usadas(conn, "receita") == ["Freela"]


def test_atualizar_e_excluir(conn):
    (i,) = _reg(conn)
    lanc.atualizar(conn, i, descricao="Novo", data=date(2026, 10, 7))
    linha = conn.execute("SELECT * FROM lancamentos WHERE id=?", (i,)).fetchone()
    assert linha["descricao"] == "Novo" and linha["data"] == "2026-10-07"
    with pytest.raises(ValueError):
        lanc.atualizar(conn, i, status="previsto")
    lanc.excluir(conn, [i])
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0


def test_aplicar_edicoes(conn):
    a, = _reg(conn, descricao="A", valor_centavos=1000)
    b, = _reg(conn, descricao="B")
    df = lanc.listar(conn)
    original = df.assign(valor=df["valor_centavos"] / 100, excluir=False)[
        ["id", "data", "descricao", "categoria", "valor", "excluir"]]
    editado = original.copy()
    editado.loc[editado["id"] == a, ["descricao", "valor", "categoria"]] = ["A2", -12.5, "Lazer"]
    editado.loc[editado["id"] == b, "excluir"] = True
    assert lanc.aplicar_edicoes(conn, original, editado) == (1, 1)
    linha = conn.execute("SELECT * FROM lancamentos").fetchone()
    assert (linha["descricao"], linha["valor_centavos"], linha["categoria_id"]) == (
        "A2", -1250, id_cat(conn, "Lazer"))
    # sem mudanças
    assert lanc.aplicar_edicoes(conn, original.iloc[:1], original.iloc[:1]) == (0, 0)
