import pandas as pd
import pytest
from conftest import id_conta

from meu_cofre import cadastros, db


def test_seed_inicial(conn):
    assert conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 23
    nomes = [r["nome"] for r in conn.execute("SELECT nome FROM contas ORDER BY id")]
    assert nomes == ["Inter", "Caixa", "Cartão de crédito", "Carteira", "Caixinhas"]


def test_seed_nao_recria_categoria_apagada(tmp_path):
    c = db.conectar(tmp_path / "a.db")
    c.execute("DELETE FROM categorias WHERE nome = 'Lazer'")
    c.commit()
    c.close()
    c = db.conectar(tmp_path / "a.db")
    assert c.execute("SELECT COUNT(*) FROM categorias WHERE nome='Lazer'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 22


def test_salvar_categorias(conn):
    df = cadastros.categorias_df(conn)
    df.loc[df["nome"] == "Lazer", "nome"] = "Diversão"
    df.loc[df["nome"] == "Delivery", "ativa"] = False
    nova = pd.DataFrame([{"id": None, "nome": "Pets", "tipo": "despesa", "natureza": "flexivel", "ativa": None}])
    cadastros.salvar_categorias(conn, pd.concat([df, nova], ignore_index=True))
    out = cadastros.categorias_df(conn).set_index("nome")
    assert "Lazer" not in out.index and "Diversão" in out.index
    assert not out.loc["Delivery", "ativa"]
    assert out.loc["Pets", "ativa"] and out.loc["Pets", "natureza"] == "flexivel"


def test_salvar_categorias_invalidas(conn):
    df = cadastros.categorias_df(conn)
    ruim = pd.concat([df, pd.DataFrame([{"id": None, "nome": "lazer", "tipo": "despesa",
                                        "natureza": None, "ativa": True}])])
    with pytest.raises(ValueError, match="repetida"):
        cadastros.salvar_categorias(conn, ruim)
    ruim = pd.concat([df, pd.DataFrame([{"id": None, "nome": "X", "tipo": "xyz",
                                        "natureza": None, "ativa": True}])])
    with pytest.raises(ValueError, match="Tipo"):
        cadastros.salvar_categorias(conn, ruim)
    ruim = pd.concat([df, pd.DataFrame([{"id": None, "nome": "", "tipo": "despesa",
                                        "natureza": None, "ativa": True}])])
    with pytest.raises(ValueError, match="nome"):
        cadastros.salvar_categorias(conn, ruim)


def test_salvar_contas(conn):
    df = cadastros.contas_df(conn)
    df.loc[df["nome"] == "Inter", "saldo_inicial"] = 1234.56
    df.loc[df["nome"] == "Caixa", "ativa"] = False
    nova = pd.DataFrame([{"id": None, "nome": "Nubank", "tipo": "corrente", "banco": "Nu",
                          "saldo_inicial": None, "ativa": None}])
    cadastros.salvar_contas(conn, pd.concat([df, nova], ignore_index=True))
    out = cadastros.contas_df(conn).set_index("nome")
    assert out.loc["Inter", "saldo_inicial"] == 1234.56
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE nome='Inter'").fetchone()[0] == 123456
    assert not out.loc["Caixa", "ativa"]
    assert out.loc["Nubank", "ativa"] and out.loc["Nubank", "saldo_inicial"] == 0
    assert id_conta(conn, "Nubank")


def test_salvar_contas_invalidas(conn):
    df = cadastros.contas_df(conn)
    ruim = pd.concat([df, pd.DataFrame([{"id": None, "nome": "Inter", "tipo": "corrente",
                                        "banco": None, "saldo_inicial": 0, "ativa": True}])])
    with pytest.raises(ValueError, match="repetida"):
        cadastros.salvar_contas(conn, ruim)
