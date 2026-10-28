"""Cadastros: salvar categorias e contas editadas em tabela."""

import sqlite3

import pandas as pd

from meu_cofre.lancamentos import para_centavos

TIPOS_CATEGORIA = ["despesa", "receita", "transferencia"]
NATUREZAS = ["essencial", "flexivel", "superfluo"]
TIPOS_CONTA = ["corrente", "cartao", "dinheiro", "investimento"]


def _vazio(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def _ativa(v) -> int:
    """Célula vazia conta como ativa (linha nova); só um False explícito desativa."""
    return 1 if _vazio(v) else int(bool(v))


def _id_ou_none(v) -> int | None:
    return None if _vazio(v) else int(v)


def categorias_df(conn: sqlite3.Connection) -> pd.DataFrame:
    """Todas as categorias (inclusive inativas). Colunas: id, nome, tipo, natureza, ativa."""
    df = pd.read_sql_query("SELECT id, nome, tipo, natureza, ativa FROM categorias ORDER BY tipo, nome", conn)
    df["ativa"] = df["ativa"].astype(bool)
    return df


def salvar_categorias(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    """Grava a tabela editada: linhas com id são atualizadas, sem id são criadas.

    Nada é apagado (desative a categoria): lançamentos antigos continuam apontando pra ela.
    """
    linhas, nomes = [], set()
    for r in df.to_dict("records"):
        if _vazio(r.get("nome")) and _vazio(r.get("tipo")):
            continue  # linha em branco
        nome = str(r.get("nome") or "").strip()
        if not nome:
            raise ValueError("Toda categoria precisa de nome.")
        if nome.lower() in nomes:
            raise ValueError(f"Categoria repetida: {nome}")
        nomes.add(nome.lower())
        tipo = r.get("tipo")
        if tipo not in TIPOS_CATEGORIA:
            raise ValueError(f"Tipo inválido em '{nome}'. Use: {', '.join(TIPOS_CATEGORIA)}.")
        natureza = None if _vazio(r.get("natureza")) else r["natureza"]
        if natureza is not None and natureza not in NATUREZAS:
            raise ValueError(f"Natureza inválida em '{nome}'.")
        ativa = _ativa(r.get("ativa"))
        linhas.append((_id_ou_none(r.get("id")), nome, tipo, natureza, ativa))

    try:
        with conn:
            for id_, nome, tipo, natureza, ativa in linhas:
                if id_ is None:
                    conn.execute(
                        "INSERT INTO categorias (nome, tipo, natureza, ativa) VALUES (?, ?, ?, ?)",
                        (nome, tipo, natureza, ativa),
                    )
                else:
                    conn.execute(
                        "UPDATE categorias SET nome=?, tipo=?, natureza=?, ativa=? WHERE id=?",
                        (nome, tipo, natureza, ativa, id_),
                    )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Não foi possível salvar as categorias: {e}") from e


def contas_df(conn: sqlite3.Connection) -> pd.DataFrame:
    """Todas as contas. Colunas: id, nome, tipo, banco, saldo_inicial (reais), ativa."""
    df = pd.read_sql_query(
        "SELECT id, nome, tipo, banco, saldo_inicial_centavos, ativa FROM contas ORDER BY id", conn
    )
    df["saldo_inicial"] = df.pop("saldo_inicial_centavos") / 100
    df["ativa"] = df["ativa"].astype(bool)
    return df[["id", "nome", "tipo", "banco", "saldo_inicial", "ativa"]]


def salvar_contas(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    """Grava a tabela editada de contas (cria as novas, atualiza as existentes)."""
    linhas, nomes = [], set()
    for r in df.to_dict("records"):
        if _vazio(r.get("nome")) and _vazio(r.get("tipo")):
            continue
        nome = str(r.get("nome") or "").strip()
        if not nome:
            raise ValueError("Toda conta precisa de nome.")
        if nome.lower() in nomes:
            raise ValueError(f"Conta repetida: {nome}")
        nomes.add(nome.lower())
        if r.get("tipo") not in TIPOS_CONTA:
            raise ValueError(f"Tipo inválido em '{nome}'. Use: {', '.join(TIPOS_CONTA)}.")
        banco = None if _vazio(r.get("banco")) else str(r["banco"]).strip()
        saldo = 0 if _vazio(r.get("saldo_inicial")) else para_centavos(str(r["saldo_inicial"]))
        ativa = _ativa(r.get("ativa"))
        linhas.append((_id_ou_none(r.get("id")), nome, r["tipo"], banco, saldo, ativa))

    try:
        with conn:
            for id_, nome, tipo, banco, saldo, ativa in linhas:
                if id_ is None:
                    conn.execute(
                        "INSERT INTO contas (nome, tipo, banco, saldo_inicial_centavos, ativa)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (nome, tipo, banco, saldo, ativa),
                    )
                else:
                    conn.execute(
                        "UPDATE contas SET nome=?, tipo=?, banco=?, saldo_inicial_centavos=?, ativa=?"
                        " WHERE id=?",
                        (nome, tipo, banco, saldo, ativa, id_),
                    )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Não foi possível salvar as contas: {e}") from e
