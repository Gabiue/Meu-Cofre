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


def nome_com_final(nome: str, final: str | None) -> str:
    """"Caixa Visa" + "1234" -> "Caixa Visa •••• 1234"; sem final, só o nome."""
    return f"{nome} •••• {final}" if isinstance(final, str) and final else nome


# --- Datas e parcelas ---------------------------------------------------------


def somar_meses(d: date, meses: int) -> date:
    """Soma meses mantendo o dia; se o dia não existe no mês (31/02), usa o último."""
    total = d.month - 1 + meses
    ano, mes = d.year + total // 12, total % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def data_no_mes(ano: int, mes: int, dia: int) -> date:
    """O dia pedido no mês; se não existe (31 em fevereiro), o último dia dele."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


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
    """Primeira regra que casar (é intenção explícita do usuário); senão, a categoria usada da última vez."""
    alvo = normalizar(descricao)
    if not alvo:
        return None

    por_regra = aplicar_regras(conn, descricao, tipo)
    if por_regra is not None:
        return por_regra

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
    return None


def regra_casa(padrao: str, tipo_match: str, descricao: str) -> bool:
    if tipo_match == "regex":
        try:
            return re.search(padrao, descricao, re.IGNORECASE) is not None
        except re.error:
            return False
    alvo = normalizar(padrao)
    return bool(alvo) and alvo in normalizar(descricao)


def aplicar_regras(conn: sqlite3.Connection, descricao: str, tipo: str | None = None) -> int | None:
    """Categoria da primeira regra (por prioridade, depois id) que casa com a descrição; None se nenhuma."""
    regras = conn.execute(
        """
        SELECT r.padrao, r.tipo_match, r.categoria_id, c.tipo
        FROM regras_categorizacao r JOIN categorias c ON c.id = r.categoria_id
        WHERE c.ativa = 1
        ORDER BY r.prioridade, r.id
        """
    )
    for regra in regras:
        if tipo and regra["tipo"] != tipo:
            continue
        if regra_casa(regra["padrao"], regra["tipo_match"], descricao):
            return regra["categoria_id"]
    return None


def padrao_sugerido(descricao: str) -> str:
    """Padrão de regra a partir de uma descrição: sem números nem símbolos ("Uber *trip 8812" -> "uber trip"),
    pra a regra pegar as próximas compras e não só aquela."""
    return " ".join(p for p in re.sub(r"[^a-z ]", " ", normalizar(descricao)).split() if len(p) > 1)


def pendentes(conn: sqlite3.Connection) -> pd.DataFrame:
    """Lançamentos sem categoria (nula ou "A categorizar"), mais recente primeiro; transferências, metas e investimentos ficam de fora.

    Colunas: id, descricao, data, conta, valor_centavos, tipo ("despesa" ou "receita", pelo sinal).
    """
    return pd.read_sql_query(
        """
        SELECT l.id, l.descricao, l.data, ct.nome AS conta, l.valor_centavos,
               CASE WHEN l.valor_centavos > 0 THEN 'receita' ELSE 'despesa' END AS tipo
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        WHERE (l.categoria_id IS NULL OR c.nome = 'A categorizar')
          AND l.transferencia_id IS NULL AND l.meta_id IS NULL AND COALESCE(l.metodo, '') <> 'transferencia'
          AND ct.tipo <> 'investimento'
        ORDER BY l.data DESC, l.id DESC
        """,
        conn,
        parse_dates=["data"],
    )


def _decisoes_dos_pendentes(conn: sqlite3.Connection, regra_id: int | None) -> list[tuple[int, int]]:
    """(lançamento, categoria) que as regras dariam aos pendentes; com `regra_id`, só essa regra."""
    regra = None
    if regra_id is not None:
        regra = conn.execute(
            "SELECT r.padrao, r.tipo_match, r.categoria_id, c.tipo FROM regras_categorizacao r"
            " JOIN categorias c ON c.id = r.categoria_id WHERE r.id = ?",
            (regra_id,),
        ).fetchone()
        if regra is None:
            return []
    decisoes = []
    for p in pendentes(conn).itertuples():
        if regra is None:
            categoria = aplicar_regras(conn, p.descricao, p.tipo)
        else:
            casou = regra["tipo"] == p.tipo and regra_casa(regra["padrao"], regra["tipo_match"], p.descricao)
            categoria = regra["categoria_id"] if casou else None
        if categoria is not None:
            decisoes.append((int(p.id), categoria))
    return decisoes


def contar_pendentes_da_regra(conn: sqlite3.Connection, regra_id: int | None = None) -> int:
    """Quantos pendentes seriam categorizados por `aplicar_regras_aos_pendentes` (sem gravar)."""
    return len(_decisoes_dos_pendentes(conn, regra_id))


def aplicar_regras_aos_pendentes(conn: sqlite3.Connection, regra_id: int | None = None) -> int:
    """Categoriza os pendentes que as regras (ou só a `regra_id`) casam; quem já tem categoria nunca é tocado. Devolve quantos."""
    decisoes = _decisoes_dos_pendentes(conn, regra_id)
    with conn:
        conn.executemany("UPDATE lancamentos SET categoria_id = ? WHERE id = ?", [(c, i) for i, c in decisoes])
    return len(decisoes)


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


def recentes(conn: sqlite3.Connection, hoje: date, n: int = 10, filtro: str = "todos") -> pd.DataFrame:
    """Os últimos `n` lançamentos com data até `hoje`, do mais novo pro mais antigo (data desc, id desc).

    Previstos e parcelas futuras ficam de fora até a data chegar; o lançamento que acabou de ser feito
    hoje continua no topo. `filtro`: "todos", "entradas" ou "saidas" (transferências só em "todos").
    Guardar/retirar de uma meta e aportar/resgatar de um investimento aparecem uma vez só, pelo lado da conta
    (o lado da conta de investimento fica de fora).
    Colunas: id, descricao, categoria, conta, conta_final (final do cartão ou nulo), metodo, data, valor_centavos, parcela_atual, parcela_total,
    recorrente_id (nulo se não for de uma recorrente), meta_id (nulo se não for guardar/retirar de uma meta),
    transferencia_destino (nome da conta que recebeu, só no lado de saída de uma transferência entre contas próprias).
    Essa transferência aparece uma vez só: o lado de entrada fica de fora.
    """
    if filtro not in ("todos", "entradas", "saidas"):
        raise ValueError(f"Filtro desconhecido: {filtro}")
    extra = ""
    if filtro != "todos":
        sinal = ">" if filtro == "entradas" else "<"
        extra = f"""AND l.valor_centavos {sinal} 0
              AND COALESCE(c.tipo, '') <> 'transferencia'
              AND COALESCE(l.metodo, '') <> 'transferencia'"""
    return pd.read_sql_query(
        f"""
        SELECT l.id, l.descricao, c.nome AS categoria, ct.nome AS conta, ct.final AS conta_final, l.metodo, l.data,
               l.valor_centavos, l.parcela_atual, l.parcela_total, l.recorrente_id, l.meta_id,
               CASE WHEN l.valor_centavos < 0 THEN p.nome END AS transferencia_destino
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        LEFT JOIN (SELECT l2.transferencia_id, l2.valor_centavos, ct2.nome, ct2.tipo
                   FROM lancamentos l2 JOIN contas ct2 ON ct2.id = l2.conta_id) p
               ON p.transferencia_id = l.transferencia_id AND (p.valor_centavos > 0) <> (l.valor_centavos > 0)
              AND p.tipo IN ('corrente', 'dinheiro') AND ct.tipo IN ('corrente', 'dinheiro')
        WHERE l.data <= ? AND NOT (ct.tipo = 'investimento' AND (l.meta_id IS NOT NULL OR l.transferencia_id IS NOT NULL))
              AND NOT (l.valor_centavos > 0 AND p.nome IS NOT NULL) {extra}
        ORDER BY l.data DESC, l.id DESC
        LIMIT ?
        """,
        conn,
        params=(hoje.isoformat(), n),
        parse_dates=["data"],
    )


def obter(conn: sqlite3.Connection, lancamento_id: int) -> dict | None:
    """Um lançamento com todos os campos, mais `conta`, `conta_final`, `categoria` e `categoria_tipo` (nomes) e, se vier de
    uma recorrente, `recorrente_dia` (dia do mês), `recorrente_assinatura` e `recorrente_ativa` (senão, None)."""
    linha = conn.execute(
        """
        SELECT l.*, ct.nome AS conta, ct.final AS conta_final, c.nome AS categoria, c.tipo AS categoria_tipo,
               r.dia_do_mes AS recorrente_dia, r.assinatura AS recorrente_assinatura, r.ativa AS recorrente_ativa
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        LEFT JOIN recorrentes r ON r.id = l.recorrente_id
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


# --- Transferência entre contas próprias ---------------------------------------------------------------------

CATEGORIA_TRANSFERENCIA = "Transferência entre contas"
TIPOS_TRANSFERENCIA = ("corrente", "dinheiro")  # cartão e investimento têm fluxos próprios (pagar fatura, aportar)


def _validar_transferencia(conn: sqlite3.Connection, origem_id: int, destino_id: int, valor_centavos: int) -> None:
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    if origem_id == destino_id:
        raise ValueError("Escolha duas contas diferentes.")
    for id_ in (origem_id, destino_id):
        conta = conn.execute("SELECT tipo, ativa FROM contas WHERE id = ?", (id_,)).fetchone()
        if conta is None or not conta["ativa"] or conta["tipo"] not in TIPOS_TRANSFERENCIA:
            raise ValueError("Só dá pra transferir entre contas correntes e a carteira, ativas.")


def _categoria_transferencia(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM categorias WHERE nome = ?", (CATEGORIA_TRANSFERENCIA,)).fetchone()
    if linha:
        return linha["id"]
    with conn:
        return conn.execute(
            "INSERT INTO categorias (nome, tipo) VALUES (?, 'transferencia')", (CATEGORIA_TRANSFERENCIA,)
        ).lastrowid


def transferir(
    conn: sqlite3.Connection,
    *,
    origem_id: int,
    destino_id: int,
    valor_centavos: int,
    data: date,
    descricao: str = "",
) -> str:
    """Move dinheiro entre duas contas próprias (corrente ou carteira): saída na origem e entrada no destino,
    com o mesmo `transferencia_id` (devolvido). Não é gasto nem receita."""
    _validar_transferencia(conn, origem_id, destino_id, valor_centavos)
    descricao = descricao.strip() or "Transferência"
    categoria_id = _categoria_transferencia(conn)
    transferencia_id = uuid.uuid4().hex
    with conn:
        for conta_id, sinal in ((origem_id, -1), (destino_id, 1)):
            conn.execute(
                """
                INSERT INTO lancamentos
                    (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem, transferencia_id)
                VALUES (?, ?, ?, ?, ?, 'transferencia', 'manual', ?)
                """,
                (conta_id, data.isoformat(), sinal * valor_centavos, descricao, categoria_id, transferencia_id),
            )
    return transferencia_id


def obter_transferencia(conn: sqlite3.Connection, lancamento_id: int) -> dict | None:
    """Se o lançamento é um dos lados de uma transferência entre contas próprias: `transferencia_id`, `origem_id`,
    `destino_id`, `valor_centavos` (positivo), `data` (ISO), `descricao`. Senão (fatura, meta, aporte...), None."""
    linha = conn.execute("SELECT transferencia_id FROM lancamentos WHERE id = ?", (lancamento_id,)).fetchone()
    if linha is None or linha["transferencia_id"] is None:
        return None
    lados = conn.execute(
        """
        SELECT l.conta_id, l.valor_centavos, l.data, l.descricao, ct.tipo
        FROM lancamentos l JOIN contas ct ON ct.id = l.conta_id
        WHERE l.transferencia_id = ? AND l.meta_id IS NULL
        """,
        (linha["transferencia_id"],),
    ).fetchall()
    if len(lados) != 2 or any(l["tipo"] not in TIPOS_TRANSFERENCIA for l in lados):
        return None
    saida, entrada = sorted(lados, key=lambda l: l["valor_centavos"])
    if saida["valor_centavos"] != -entrada["valor_centavos"]:
        return None
    return dict(
        transferencia_id=linha["transferencia_id"], origem_id=saida["conta_id"], destino_id=entrada["conta_id"],
        valor_centavos=entrada["valor_centavos"], data=saida["data"], descricao=saida["descricao"],
    )


def atualizar_transferencia(
    conn: sqlite3.Connection,
    transferencia_id: str,
    *,
    origem_id: int,
    destino_id: int,
    valor_centavos: int,
    data: date,
    descricao: str = "",
) -> None:
    """Edita a transferência inteira: os dois lados mudam juntos (mesmas regras de `transferir`)."""
    _validar_transferencia(conn, origem_id, destino_id, valor_centavos)
    descricao = descricao.strip() or "Transferência"
    with conn:
        for conta_id, sinal in ((origem_id, -1), (destino_id, 1)):
            conn.execute(
                """
                UPDATE lancamentos SET conta_id = ?, valor_centavos = ?, data = ?, descricao = ?
                WHERE transferencia_id = ? AND valor_centavos * ? > 0
                """,
                (conta_id, sinal * valor_centavos, data.isoformat(), descricao, transferencia_id, sinal),
            )


def excluir_transferencia(conn: sqlite3.Connection, transferencia_id: str) -> None:
    """Apaga os dois lados."""
    with conn:
        conn.execute("DELETE FROM lancamentos WHERE transferencia_id = ?", (transferencia_id,))
