"""Cadastros: salvar categorias e contas editadas em tabela."""

import sqlite3
from datetime import date

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


# --- Contas e cartões (janela "Contas e cartões") ---------------------------------------------------

# Tipos que o dia a dia exige: sempre sobra pelo menos uma conta ativa de cada.
_OBRIGATORIAS = {"corrente": "conta corrente", "dinheiro": "carteira"}


def _conta(conn: sqlite3.Connection, conta_id: int) -> sqlite3.Row:
    linha = conn.execute("SELECT * FROM contas WHERE id = ?", (conta_id,)).fetchone()
    if linha is None:
        raise ValueError(f"Conta {conta_id} não existe.")
    return linha


def _nome_livre(conn: sqlite3.Connection, nome: str, ignorar_id: int | None = None) -> str:
    """Nome sem espaços nas pontas, não vazio e que nenhuma outra conta usa (sem diferenciar maiúsculas)."""
    nome = nome.strip()
    if not nome:
        raise ValueError("Diga o nome.")
    repetida = conn.execute(
        "SELECT 1 FROM contas WHERE lower(nome) = lower(?) AND id IS NOT ?", (nome, ignorar_id)
    ).fetchone()
    if repetida:
        raise ValueError(f"Já existe uma conta chamada {nome}.")
    return nome


def _dia(valor: int | None, rotulo: str) -> int | None:
    if valor is None:
        return None
    if not 1 <= valor <= 31:
        raise ValueError(f"O dia de {rotulo} vai de 1 a 31.")
    return valor


def contas_por_tipo(
    conn: sqlite3.Connection, tipos: tuple[str, ...], incluir_inativas: bool = False
) -> list[sqlite3.Row]:
    """Contas dos `tipos` (ordem de cadastro). Por padrão só as ativas."""
    marcas = ", ".join("?" * len(tipos))
    ativa = "" if incluir_inativas else "AND ativa = 1"
    return conn.execute(
        f"SELECT * FROM contas WHERE tipo IN ({marcas}) {ativa} ORDER BY id", tipos
    ).fetchall()


def criar_cartao(
    conn: sqlite3.Connection,
    nome: str,
    banco: str | None = None,
    dia_fechamento: int | None = None,
    dia_vencimento: int | None = None,
) -> int:
    """Cria um cartão de crédito ativo e devolve o id. Nome único; dias de 1 a 31 (opcionais)."""
    nome = _nome_livre(conn, nome)
    banco = (banco or "").strip() or None
    fechamento = _dia(dia_fechamento, "fechamento")
    vencimento = _dia(dia_vencimento, "vencimento")
    with conn:
        return conn.execute(
            "INSERT INTO contas (nome, tipo, banco, dia_fechamento, dia_vencimento) VALUES (?, 'cartao', ?, ?, ?)",
            (nome, banco, fechamento, vencimento),
        ).lastrowid


def renomear_conta(conn: sqlite3.Connection, conta_id: int, nome: str) -> None:
    _conta(conn, conta_id)
    nome = _nome_livre(conn, nome, ignorar_id=conta_id)
    with conn:
        conn.execute("UPDATE contas SET nome = ? WHERE id = ?", (nome, conta_id))


def definir_saldo_inicial(conn: sqlite3.Connection, conta_id: int, centavos: int) -> None:
    """Saldo de partida de uma conta corrente ou da carteira (pode ser negativo)."""
    conta = _conta(conn, conta_id)
    if conta["tipo"] not in _OBRIGATORIAS:
        raise ValueError("Só conta corrente e carteira têm saldo inicial.")
    with conn:
        conn.execute("UPDATE contas SET saldo_inicial_centavos = ? WHERE id = ?", (int(centavos), conta_id))


def desativar_conta(conn: sqlite3.Connection, conta_id: int) -> None:
    """Desativa (não apaga): o histórico fica. Não deixa desativar a última conta corrente ou carteira ativa."""
    conta = _conta(conn, conta_id)
    if not conta["ativa"]:
        return
    if conta["tipo"] in _OBRIGATORIAS:
        outras = conn.execute(
            "SELECT 1 FROM contas WHERE tipo = ? AND ativa = 1 AND id <> ?", (conta["tipo"], conta_id)
        ).fetchone()
        if not outras:
            raise ValueError(f"Precisa ficar pelo menos uma {_OBRIGATORIAS[conta['tipo']]} ativa.")
    with conn:
        conn.execute("UPDATE contas SET ativa = 0 WHERE id = ?", (conta_id,))


def reativar_conta(conn: sqlite3.Connection, conta_id: int) -> None:
    _conta(conn, conta_id)
    with conn:
        conn.execute("UPDATE contas SET ativa = 1 WHERE id = ?", (conta_id,))


def saldo_da_conta(conn: sqlite3.Connection, conta_id: int, ate: date | None = None) -> int:
    """Saldo inicial + lançamentos da conta (até `ate`, ou todos). Cartão dá negativo (é dívida)."""
    conta = _conta(conn, conta_id)
    filtro, params = ("AND data <= ?", [ate.isoformat()]) if ate else ("", [])
    movimentos = conn.execute(
        f"SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos WHERE conta_id = ? {filtro}",
        [conta_id, *params],
    ).fetchone()[0]
    return conta["saldo_inicial_centavos"] + movimentos


def divida_do_cartao(conn: sqlite3.Connection, conta_id: int) -> int:
    """Quanto está em aberto no cartão (positivo), parcelas futuras incluídas."""
    return max(0, -saldo_da_conta(conn, conta_id))
