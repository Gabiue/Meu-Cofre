"""Lançamentos: registrar, listar, editar e sugerir categoria."""

import calendar
import re
import sqlite3
import unicodedata
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd

METODOS = {
    "pix": "Pix",
    "debito": "Débito",
    "cartao": "Cartão",
    "dinheiro": "Dinheiro",
    "boleto": "Boleto",
}

# Métodos que definem a conta sozinhos (o resto usa a conta escolhida, Inter por padrão).
CONTA_POR_METODO = {"cartao": "cartao", "dinheiro": "dinheiro"}


# --- Dinheiro -----------------------------------------------------------------


def para_centavos(valor: str | float | Decimal) -> int:
    """Converte reais em centavos sem erro de float. Aceita "1.234,56", "1.500" (milhar) e "1234.56".

    Levanta `ValueError` com mensagem amigável se o texto não for um número.
    """
    original = valor
    if isinstance(valor, str):
        texto = valor.strip().replace("R$", "").replace(" ", "")
        if "," in texto or re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):  # "1.234,56" e "1.500" (milhar)
            texto = texto.replace(".", "").replace(",", ".")
        valor = texto
    try:
        reais = Decimal(str(valor))
        if not reais.is_finite():
            raise InvalidOperation
        return int((reais * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError(f"Valor inválido: {str(original).strip()!r}. Digite só números, como 12,50.") from None


def formatar_brl(centavos: int) -> str:
    """123456 -> "R$ 1.234,56"; -500 -> "-R$ 5,00"."""
    sinal = "-" if centavos < 0 else ""
    inteiro, resto = divmod(abs(int(centavos)), 100)
    return f"{sinal}R$ {inteiro:,}".replace(",", ".") + f",{resto:02d}"


def formatar_brl_inteiro(centavos: int) -> str:
    """Reais cheios, arredondados: 6249 -> "R$ 62"; 6250 -> "R$ 63"."""
    sinal = "-" if centavos < 0 else ""
    reais = (abs(int(centavos)) + 50) // 100
    return f"{sinal}R$ {reais:,}".replace(",", ".")


# --- Datas e parcelas ---------------------------------------------------------


def somar_meses(d: date, meses: int) -> date:
    """Soma meses mantendo o dia; se o dia não existe no mês (31/02), usa o último."""
    total = d.month - 1 + meses
    ano, mes = d.year + total // 12, total % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def dividir_em_parcelas(total_centavos: int, n: int) -> list[int]:
    """Divide um valor em n parcelas inteiras; a sobra de centavos vai na primeira."""
    base, sobra = divmod(total_centavos, n)
    return [base + sobra] + [base] * (n - 1)


# --- Contas e categorias ------------------------------------------------------


def listar_contas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM contas WHERE ativa = 1 ORDER BY id").fetchall()


def listar_categorias(conn: sqlite3.Connection, tipo: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM categorias WHERE ativa = 1"
    params: tuple = ()
    if tipo:
        sql += " AND tipo = ?"
        params = (tipo,)
    return conn.execute(sql + " ORDER BY nome", params).fetchall()


def cartoes_ativos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM contas WHERE tipo = 'cartao' AND ativa = 1 ORDER BY id").fetchall()


def ultimo_cartao_usado(conn: sqlite3.Connection) -> int | None:
    """Cartão do lançamento mais recente com método cartão, entre os cartões ativos; None se não há."""
    linha = conn.execute(
        """
        SELECT ct.id FROM lancamentos l JOIN contas ct ON ct.id = l.conta_id
        WHERE l.metodo = 'cartao' AND ct.tipo = 'cartao' AND ct.ativa = 1
        ORDER BY l.id DESC LIMIT 1
        """
    ).fetchone()
    return linha["id"] if linha else None


def conta_do_metodo(conn: sqlite3.Connection, metodo: str, conta_escolhida_id: int) -> int:
    """Cartão vai pro cartão escolhido (se `conta_escolhida_id` for um cartão ativo; senão, o primeiro
    ativo), dinheiro pra carteira; o resto, pra conta escolhida."""
    tipo = CONTA_POR_METODO.get(metodo)
    if not tipo:
        return conta_escolhida_id
    if tipo == "cartao":
        escolhido = conn.execute(
            "SELECT id FROM contas WHERE id = ? AND tipo = 'cartao' AND ativa = 1", (conta_escolhida_id,)
        ).fetchone()
        if escolhido:
            return escolhido["id"]
    linha = conn.execute(
        "SELECT id FROM contas WHERE tipo = ? AND ativa = 1 ORDER BY id LIMIT 1", (tipo,)
    ).fetchone()
    if not linha:
        if tipo == "cartao":
            raise ValueError("Não há cartão ativo. Adicione um em Contas.")
        raise ValueError(f"Nenhuma conta ativa do tipo '{tipo}' para o método '{metodo}'.")
    return linha["id"]


# --- Sugestão de categoria ----------------------------------------------------


def normalizar(texto: str) -> str:
    """'  Almoço   no RU ' -> 'almoco no ru'."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def descricoes_usadas(conn: sqlite3.Connection, tipo: str | None = None) -> list[str]:
    """Descrições já lançadas, das mais usadas pras menos (alimenta o autocompletar)."""
    sql = """
        SELECT l.descricao, COUNT(*) AS n, MAX(l.data) AS ultima
        FROM lancamentos l LEFT JOIN categorias c ON c.id = l.categoria_id
    """
    params: tuple = ()
    if tipo:
        sql += " WHERE c.tipo = ?"
        params = (tipo,)
    sql += " GROUP BY l.descricao ORDER BY n DESC, ultima DESC"
    return [r["descricao"] for r in conn.execute(sql, params)]


def sugerir_categoria(conn: sqlite3.Connection, descricao: str, tipo: str) -> int | None:
    """Categoria usada da última vez com essa descrição; senão, a primeira regra que casar."""
    alvo = normalizar(descricao)
    if not alvo:
        return None

    historico = conn.execute(
        """
        SELECT l.descricao, l.categoria_id
        FROM lancamentos l JOIN categorias c ON c.id = l.categoria_id
        WHERE c.tipo = ? AND c.ativa = 1 AND c.nome <> 'A categorizar'
        ORDER BY l.data DESC, l.id DESC
        """,
        (tipo,),
    )
    for linha in historico:
        if normalizar(linha["descricao"]) == alvo:
            return linha["categoria_id"]

    return aplicar_regras(conn, descricao, tipo)


def aplicar_regras(conn: sqlite3.Connection, descricao: str, tipo: str | None = None) -> int | None:
    regras = conn.execute(
        """
        SELECT r.padrao, r.tipo_match, r.categoria_id, c.tipo
        FROM regras_categorizacao r JOIN categorias c ON c.id = r.categoria_id
        WHERE c.ativa = 1
        ORDER BY r.prioridade, r.id
        """
    )
    alvo = normalizar(descricao)
    for regra in regras:
        if tipo and regra["tipo"] != tipo:
            continue
        if regra["tipo_match"] == "regex":
            casou = re.search(regra["padrao"], descricao, re.IGNORECASE) is not None
        else:
            casou = normalizar(regra["padrao"]) in alvo
        if casou:
            return regra["categoria_id"]
    return None


# --- Registrar, listar, editar ------------------------------------------------


def registrar(
    conn: sqlite3.Connection,
    *,
    entrada: bool,
    valor_centavos: int,
    descricao: str,
    categoria_id: int | None,
    metodo: str,
    conta_id: int,
    data: date,
    parcelas: int = 1,
) -> list[int]:
    """Registra um lançamento (ou N parcelas, se for compra parcelada no cartão).

    `valor_centavos` é sempre positivo; o sinal vem de `entrada`.
    Retorna os ids criados.
    """
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    descricao = descricao.strip()
    if not descricao:
        raise ValueError("Diga o que foi.")
    if metodo not in METODOS:
        raise ValueError(f"Método desconhecido: {metodo}")

    conta_id = conta_do_metodo(conn, metodo, conta_id)
    sinal = 1 if entrada else -1
    parcelado = not entrada and metodo == "cartao" and parcelas > 1

    if parcelado:
        valores = dividir_em_parcelas(valor_centavos, parcelas)
        grupo = uuid.uuid4().hex
    else:
        valores, grupo = [valor_centavos], None

    ids = []
    with conn:
        for i, valor in enumerate(valores):
            cur = conn.execute(
                """
                INSERT INTO lancamentos
                    (conta_id, data, valor_centavos, descricao, categoria_id, metodo,
                     origem, grupo_parcelas, parcela_atual, parcela_total)
                VALUES (?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)
                """,
                (
                    conta_id,
                    somar_meses(data, i).isoformat(),
                    sinal * valor,
                    descricao,
                    categoria_id,
                    metodo,
                    grupo,
                    i + 1 if parcelado else None,
                    len(valores) if parcelado else None,
                ),
            )
            ids.append(cur.lastrowid)
    return ids


def listar(
    conn: sqlite3.Connection,
    inicio: date | None = None,
    fim: date | None = None,
    conta_id: int | None = None,
    categoria_id: int | None = None,
) -> pd.DataFrame:
    """Lançamentos com nomes de conta e categoria, do mais recente pro mais antigo."""
    filtros, params = [], []
    if inicio:
        filtros.append("l.data >= ?")
        params.append(inicio.isoformat())
    if fim:
        filtros.append("l.data <= ?")
        params.append(fim.isoformat())
    if conta_id:
        filtros.append("l.conta_id = ?")
        params.append(conta_id)
    if categoria_id:
        filtros.append("l.categoria_id = ?")
        params.append(categoria_id)
    onde = ("WHERE " + " AND ".join(filtros)) if filtros else ""

    return pd.read_sql_query(
        f"""
        SELECT l.id, l.data, l.descricao, l.valor_centavos,
               c.nome AS categoria, c.natureza, ct.nome AS conta, l.metodo,
               l.parcela_atual, l.parcela_total, l.status, l.conferido, l.origem
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        {onde}
        ORDER BY l.data DESC, l.id DESC
        """,
        conn,
        params=params,
        parse_dates=["data"],
    )


def recentes(conn: sqlite3.Connection, n: int = 10, filtro: str = "todos") -> pd.DataFrame:
    """Últimos `n` lançamentos por ordem de lançamento (id desc), não por data.

    `filtro`: "todos", "entradas" ou "saidas". Transferências só aparecem em "todos".
    Colunas: id, descricao, categoria, conta, metodo, data, valor_centavos, parcela_atual, parcela_total.
    """
    if filtro not in ("todos", "entradas", "saidas"):
        raise ValueError(f"Filtro desconhecido: {filtro}")
    onde = ""
    if filtro != "todos":
        sinal = ">" if filtro == "entradas" else "<"
        onde = f"""WHERE l.valor_centavos {sinal} 0
              AND COALESCE(c.tipo, '') <> 'transferencia'
              AND COALESCE(l.metodo, '') <> 'transferencia'"""
    return pd.read_sql_query(
        f"""
        SELECT l.id, l.descricao, c.nome AS categoria, ct.nome AS conta, l.metodo, l.data,
               l.valor_centavos, l.parcela_atual, l.parcela_total
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        {onde}
        ORDER BY l.id DESC
        LIMIT ?
        """,
        conn,
        params=(n,),
        parse_dates=["data"],
    )


def obter(conn: sqlite3.Connection, lancamento_id: int) -> dict | None:
    """Um lançamento com todos os campos, mais `conta`, `categoria` e `categoria_tipo` (nomes)."""
    linha = conn.execute(
        """
        SELECT l.*, ct.nome AS conta, c.nome AS categoria, c.tipo AS categoria_tipo
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        WHERE l.id = ?
        """,
        (lancamento_id,),
    ).fetchone()
    return dict(linha) if linha else None


CAMPOS_EDITAVEIS = {"data", "descricao", "valor_centavos", "categoria_id", "conta_id", "metodo"}


def atualizar(conn: sqlite3.Connection, lancamento_id: int, *, entrada: bool | None = None, **campos) -> None:
    """Altera campos de um lançamento.

    `entrada` (opcional) troca o sinal: com ele, `valor_centavos` (se vier) é positivo e o sinal
    vem de `entrada`; sem `valor_centavos`, mantém o valor atual e só vira o sinal. Trocar `metodo`
    recalcula a conta com `conta_do_metodo` (use `conta_id` pra dizer a conta corrente ou o cartão).
    """
    invalidos = set(campos) - CAMPOS_EDITAVEIS
    if invalidos:
        raise ValueError(f"Campos não editáveis: {sorted(invalidos)}")
    if not campos and entrada is None:
        return

    atual = obter(conn, lancamento_id)
    if atual is None:
        raise ValueError(f"Lançamento {lancamento_id} não existe.")

    if "descricao" in campos:
        campos["descricao"] = str(campos["descricao"]).strip()
        if not campos["descricao"]:
            raise ValueError("Diga o que foi.")
    if entrada is not None:
        valor = campos.get("valor_centavos", abs(atual["valor_centavos"]))
        if valor <= 0:
            raise ValueError("O valor precisa ser maior que zero.")
        campos["valor_centavos"] = valor if entrada else -valor
    if "metodo" in campos:
        if campos["metodo"] not in METODOS:
            raise ValueError(f"Método desconhecido: {campos['metodo']}")
        conta = campos.get("conta_id", atual["conta_id"])
        # Mesmo método e mesma conta: não mexe (um cartão já desativado continua onde estava).
        if campos["metodo"] != atual["metodo"] or conta != atual["conta_id"]:
            conta = conta_do_metodo(conn, campos["metodo"], conta)
        campos["conta_id"] = conta
    if isinstance(campos.get("data"), date):
        campos["data"] = campos["data"].isoformat()

    sets = ", ".join(f"{k} = ?" for k in campos)
    with conn:
        conn.execute(f"UPDATE lancamentos SET {sets} WHERE id = ?", (*campos.values(), lancamento_id))


def excluir(conn: sqlite3.Connection, ids: list[int]) -> None:
    with conn:
        conn.executemany("DELETE FROM lancamentos WHERE id = ?", [(i,) for i in ids])


def excluir_parcelas(conn: sqlite3.Connection, grupo_parcelas: str) -> int:
    """Apaga todas as parcelas de uma compra e devolve quantas eram."""
    with conn:
        return conn.execute("DELETE FROM lancamentos WHERE grupo_parcelas = ?", (grupo_parcelas,)).rowcount


def aplicar_edicoes(conn: sqlite3.Connection, original: pd.DataFrame, editado: pd.DataFrame) -> tuple[int, int]:
    """Compara a tabela exibida (antes e depois da edição) e grava só o que mudou.

    Colunas esperadas: id, data, descricao, categoria (nome), valor (reais), excluir.
    Retorna (quantos atualizados, quantos excluídos).
    """
    cat_id = {r["nome"]: r["id"] for r in conn.execute("SELECT id, nome FROM categorias")}
    antes = original.set_index("id")
    excluir_ids, atualizados = [], 0

    for linha in editado.to_dict("records"):
        id_ = int(linha["id"])
        if linha.get("excluir"):
            excluir_ids.append(id_)
            continue
        ant = antes.loc[id_]
        campos = {}
        data = pd.Timestamp(linha["data"]).date()
        if data != pd.Timestamp(ant["data"]).date():
            campos["data"] = data
        descricao = str(linha["descricao"] or "").strip()
        if not descricao:
            raise ValueError("A descrição não pode ficar vazia.")
        if descricao != ant["descricao"]:
            campos["descricao"] = descricao
        if pd.isna(linha["valor"]):
            raise ValueError("O valor não pode ficar vazio.")
        centavos = para_centavos(str(linha["valor"]))
        if centavos == 0:
            raise ValueError("O valor não pode ser zero.")
        if centavos != para_centavos(str(ant["valor"])):
            campos["valor_centavos"] = centavos
        nova_cat = None if pd.isna(linha["categoria"]) else linha["categoria"]
        velha_cat = None if pd.isna(ant["categoria"]) else ant["categoria"]
        if nova_cat != velha_cat:
            campos["categoria_id"] = cat_id.get(nova_cat)
        if campos:
            atualizar(conn, id_, **campos)
            atualizados += 1

    if excluir_ids:
        excluir(conn, excluir_ids)
    return atualizados, len(excluir_ids)
