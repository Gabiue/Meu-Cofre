"""Cadastros: contas (editadas em tabela, cartões pontuais) e categorias (criar, renomear, natureza, desativar)."""

import re
import sqlite3
from datetime import date

import pandas as pd

from meu_cofre.lancamentos import para_centavos

NATUREZAS = ["essencial", "flexivel", "superfluo"]
TIPOS_CONTA = ["corrente", "cartao", "dinheiro", "investimento"]


def _vazio(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def _ativa(v) -> int:
    """Célula vazia conta como ativa (linha nova); só um False explícito desativa."""
    return 1 if _vazio(v) else int(bool(v))


def _id_ou_none(v) -> int | None:
    return None if _vazio(v) else int(v)


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


def obter_conta(conn: sqlite3.Connection, conta_id: int) -> sqlite3.Row:
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


def _final(valor: str | None) -> str | None:
    """Os 4 últimos dígitos do cartão: vazio vira None; senão tem de ser exatamente 4 dígitos."""
    texto = (valor or "").strip()
    if not texto:
        return None
    if not (len(texto) == 4 and texto.isascii() and texto.isdigit()):
        raise ValueError("O final do cartão tem 4 dígitos, como 1234.")
    return texto


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
    final: str | None = None,
) -> int:
    """Cria um cartão de crédito ativo e devolve o id. Nome único; dias de 1 a 31 e final (4 dígitos) opcionais."""
    nome = _nome_livre(conn, nome)
    banco = (banco or "").strip() or None
    fechamento = _dia(dia_fechamento, "fechamento")
    vencimento = _dia(dia_vencimento, "vencimento")
    final = _final(final)
    with conn:
        return conn.execute(
            "INSERT INTO contas (nome, tipo, banco, dia_fechamento, dia_vencimento, final)"
            " VALUES (?, 'cartao', ?, ?, ?, ?)",
            (nome, banco, fechamento, vencimento, final),
        ).lastrowid


def _cartao(conn: sqlite3.Connection, conta_id: int) -> sqlite3.Row:
    conta = obter_conta(conn, conta_id)
    if conta["tipo"] != "cartao":
        raise ValueError(f"{conta['nome']} não é um cartão.")
    return conta


def editar_cartao(
    conn: sqlite3.Connection,
    conta_id: int,
    *,
    banco: str | None,
    dia_fechamento: int | None,
    dia_vencimento: int | None,
    final: str | None,
) -> None:
    """Troca banco, dias de fechamento e vencimento e final do cartão (None/vazio limpa o campo).

    Mudar os dias muda a fatura em que cada compra cai (a regra é aplicada na hora de mostrar),
    mas não mexe em faturas já marcadas como pagas.
    """
    _cartao(conn, conta_id)
    banco = (banco or "").strip() or None
    fechamento = _dia(dia_fechamento, "fechamento")
    vencimento = _dia(dia_vencimento, "vencimento")
    final = _final(final)
    with conn:
        conn.execute(
            "UPDATE contas SET banco = ?, dia_fechamento = ?, dia_vencimento = ?, final = ? WHERE id = ?",
            (banco, fechamento, vencimento, final, conta_id),
        )


def cartao_tem_compras(conn: sqlite3.Connection, conta_id: int) -> bool:
    """O cartão já tem algum lançamento? Então mudar fechamento/vencimento muda a fatura de compras antigas."""
    return conn.execute("SELECT 1 FROM lancamentos WHERE conta_id = ? LIMIT 1", (conta_id,)).fetchone() is not None


def definir_final(conn: sqlite3.Connection, conta_id: int, final: str | None) -> None:
    """Os 4 últimos dígitos do cartão (vazio apaga)."""
    _cartao(conn, conta_id)
    final = _final(final)
    with conn:
        conn.execute("UPDATE contas SET final = ? WHERE id = ?", (final, conta_id))


def renomear_conta(conn: sqlite3.Connection, conta_id: int, nome: str) -> None:
    obter_conta(conn, conta_id)
    nome = _nome_livre(conn, nome, ignorar_id=conta_id)
    with conn:
        conn.execute("UPDATE contas SET nome = ? WHERE id = ?", (nome, conta_id))


def definir_saldo_inicial(conn: sqlite3.Connection, conta_id: int, centavos: int) -> None:
    """Saldo de partida de uma conta corrente ou da carteira (pode ser negativo)."""
    conta = obter_conta(conn, conta_id)
    if conta["tipo"] not in _OBRIGATORIAS:
        raise ValueError("Só conta corrente e carteira têm saldo inicial.")
    with conn:
        conn.execute("UPDATE contas SET saldo_inicial_centavos = ? WHERE id = ?", (int(centavos), conta_id))


def desativar_conta(conn: sqlite3.Connection, conta_id: int) -> None:
    """Desativa (não apaga): o histórico fica. Não deixa desativar a última conta corrente ou carteira ativa."""
    conta = obter_conta(conn, conta_id)
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
    obter_conta(conn, conta_id)
    with conn:
        conn.execute("UPDATE contas SET ativa = 1 WHERE id = ?", (conta_id,))


def saldo_da_conta(conn: sqlite3.Connection, conta_id: int, ate: date | None = None) -> int:
    """Saldo inicial + lançamentos da conta (até `ate`, ou todos). Cartão dá negativo (é dívida)."""
    conta = obter_conta(conn, conta_id)
    filtro, params = ("AND data <= ?", [ate.isoformat()]) if ate else ("", [])
    movimentos = conn.execute(
        f"SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos WHERE conta_id = ? {filtro}",
        [conta_id, *params],
    ).fetchone()[0]
    return conta["saldo_inicial_centavos"] + movimentos


def divida_do_cartao(conn: sqlite3.Connection, conta_id: int) -> int:
    """Quanto está em aberto no cartão (positivo), parcelas futuras incluídas."""
    return max(0, -saldo_da_conta(conn, conta_id))


# --- Categorias (janela "Categorias") -----------------------------------------------------------------

CATEGORIA_SISTEMA = "A categorizar"  # o app procura por este nome (lançamentos sem categoria)


def categoria_do_sistema(c: sqlite3.Row) -> bool:
    """Transferências e "A categorizar": o app depende delas pelo nome, então não mudam nem somem."""
    return c["tipo"] == "transferencia" or c["nome"] == CATEGORIA_SISTEMA


def categorias_por_tipo(conn: sqlite3.Connection, tipo: str, incluir_inativas: bool = True) -> list[sqlite3.Row]:
    """Categorias do `tipo` por nome (ativas e, se pedido, desativadas)."""
    ativa = "" if incluir_inativas else "AND ativa = 1"
    return conn.execute(f"SELECT * FROM categorias WHERE tipo = ? {ativa} ORDER BY nome", (tipo,)).fetchall()


def obter_categoria(conn: sqlite3.Connection, categoria_id: int, *, mutavel: bool = False) -> sqlite3.Row:
    linha = conn.execute("SELECT * FROM categorias WHERE id = ?", (categoria_id,)).fetchone()
    if linha is None:
        raise ValueError(f"Categoria {categoria_id} não existe.")
    if mutavel and categoria_do_sistema(linha):
        raise ValueError(f"{linha['nome']} é uma categoria do sistema e não pode ser alterada.")
    return linha


def _nome_categoria_livre(conn: sqlite3.Connection, nome: str, ignorar_id: int | None = None) -> str:
    """Nome sem espaços nas pontas, não vazio e que nenhuma outra categoria usa (sem diferenciar maiúsculas)."""
    nome = nome.strip()
    if not nome:
        raise ValueError("Diga o nome.")
    if conn.execute("SELECT 1 FROM categorias WHERE lower(nome) = lower(?) AND id IS NOT ?", (nome, ignorar_id)).fetchone():
        raise ValueError(f"Já existe uma categoria chamada {nome}.")
    return nome


def _natureza_valida(natureza: str | None, tipo: str) -> None:
    if natureza is None:
        return
    if tipo != "despesa":
        raise ValueError("Só despesa tem natureza.")
    if natureza not in NATUREZAS:
        raise ValueError(f"Natureza inválida. Use: {', '.join(NATUREZAS)}.")


def criar_categoria(conn: sqlite3.Connection, nome: str, tipo: str, natureza: str | None = None) -> int:
    """Cria uma categoria ativa de despesa ou receita e devolve o id. Natureza só pra despesa (ou nenhuma)."""
    if tipo not in ("despesa", "receita"):
        raise ValueError("O tipo é despesa ou receita.")
    nome = _nome_categoria_livre(conn, nome)
    _natureza_valida(natureza, tipo)
    with conn:
        return conn.execute(
            "INSERT INTO categorias (nome, tipo, natureza) VALUES (?, ?, ?)", (nome, tipo, natureza)
        ).lastrowid


def renomear_categoria(conn: sqlite3.Connection, categoria_id: int, nome: str) -> None:
    obter_categoria(conn, categoria_id, mutavel=True)
    nome = _nome_categoria_livre(conn, nome, ignorar_id=categoria_id)
    with conn:
        conn.execute("UPDATE categorias SET nome = ? WHERE id = ?", (nome, categoria_id))


def definir_natureza(conn: sqlite3.Connection, categoria_id: int, natureza: str | None) -> None:
    """Essencial, flexível, supérfluo ou nenhuma (None). Alimenta a reserva de emergência e os relatórios."""
    categoria = obter_categoria(conn, categoria_id, mutavel=True)
    _natureza_valida(natureza, categoria["tipo"])
    with conn:
        conn.execute("UPDATE categorias SET natureza = ? WHERE id = ?", (natureza, categoria_id))


def desativar_categoria(conn: sqlite3.Connection, categoria_id: int) -> None:
    """Some das escolhas de novos lançamentos; os antigos mantêm a categoria."""
    obter_categoria(conn, categoria_id, mutavel=True)
    with conn:
        conn.execute("UPDATE categorias SET ativa = 0 WHERE id = ?", (categoria_id,))


def reativar_categoria(conn: sqlite3.Connection, categoria_id: int) -> None:
    obter_categoria(conn, categoria_id)
    with conn:
        conn.execute("UPDATE categorias SET ativa = 1 WHERE id = ?", (categoria_id,))


# --- Regras de categorização (seção "Regras" da janela Categorias) -----------------------------------

TIPOS_MATCH = {"contem": "contém", "regex": "regex"}


def _regra_validada(conn: sqlite3.Connection, padrao: str, categoria_id: int, tipo_match: str, prioridade: int) -> tuple:
    padrao = padrao.strip()
    if not padrao:
        raise ValueError("Diga o trecho da descrição.")
    if tipo_match not in TIPOS_MATCH:
        raise ValueError("O tipo é contém ou regex.")
    if tipo_match == "regex":
        try:
            re.compile(padrao)
        except re.error as e:
            raise ValueError(f"Regex inválida: {e}.") from e
    elif not any(ch.isalnum() for ch in padrao):
        raise ValueError("O trecho precisa ter letras ou números.")
    categoria = obter_categoria(conn, categoria_id)
    if categoria_do_sistema(categoria) or not categoria["ativa"]:
        raise ValueError("Escolha uma categoria ativa.")
    return padrao, tipo_match, categoria_id, int(prioridade)


def listar_regras(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Regras por prioridade (menor vence) e depois id, com `categoria` (nome) e `categoria_ativa`."""
    return conn.execute(
        "SELECT r.*, c.nome AS categoria, c.ativa AS categoria_ativa FROM regras_categorizacao r"
        " JOIN categorias c ON c.id = r.categoria_id ORDER BY r.prioridade, r.id"
    ).fetchall()


def criar_regra(conn: sqlite3.Connection, padrao: str, categoria_id: int, tipo_match: str = "contem", prioridade: int = 100) -> int:
    """Cria a regra e devolve o id. Padrão não vazio, regex que compila, categoria ativa. Menor prioridade vence."""
    dados = _regra_validada(conn, padrao, categoria_id, tipo_match, prioridade)
    with conn:
        return conn.execute(
            "INSERT INTO regras_categorizacao (padrao, tipo_match, categoria_id, prioridade) VALUES (?, ?, ?, ?)", dados
        ).lastrowid


def editar_regra(conn: sqlite3.Connection, regra_id: int, padrao: str, categoria_id: int, tipo_match: str = "contem", prioridade: int = 100) -> None:
    if conn.execute("SELECT 1 FROM regras_categorizacao WHERE id = ?", (regra_id,)).fetchone() is None:
        raise ValueError(f"Regra {regra_id} não existe.")
    dados = _regra_validada(conn, padrao, categoria_id, tipo_match, prioridade)
    with conn:
        conn.execute(
            "UPDATE regras_categorizacao SET padrao = ?, tipo_match = ?, categoria_id = ?, prioridade = ? WHERE id = ?",
            (*dados, regra_id),
        )


def excluir_regra(conn: sqlite3.Connection, regra_id: int) -> None:
    """Apaga só a regra; lançamentos já categorizados por ela não mudam."""
    with conn:
        conn.execute("DELETE FROM regras_categorizacao WHERE id = ?", (regra_id,))
