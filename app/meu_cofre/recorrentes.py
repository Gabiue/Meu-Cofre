"""Assinaturas e gastos fixos (recorrentes): criar, gerar os lançamentos de cada mês, editar, pausar e encerrar.

Uma recorrente é um molde mensal (descrição, valor com sinal, conta, dia do mês). `gerar_pendentes` cria
os lançamentos dos meses que faltam (`origem = 'recorrente'`): `previsto` enquanto a data não chegou,
`realizado` a partir do dia. Os meses já gerados ficam em `recorrentes_meses`, então:

- gerar é idempotente;
- apagar o lançamento de um mês não faz ele voltar (a linha do mês fica com `lancamento_id` NULL);
- ao retomar uma recorrente pausada, os meses da pausa são marcados da mesma forma, sem gerar retroativos.

Editar muda o molde e os previstos que ainda vão acontecer; o que já aconteceu fica como está.
Todas as funções que dependem do dia recebem `hoje` (nada de `date.today()` aqui), pra testar e fotografar.
"""

import calendar
import sqlite3
from datetime import date

import pandas as pd

from meu_cofre import lancamentos
from meu_cofre.lancamentos import METODOS

FILTROS = ("assinaturas", "fixos", "ambos")
CAMPOS_EDITAVEIS = {"descricao", "valor_centavos", "conta_id", "categoria_id", "metodo", "dia_do_mes", "assinatura"}

COLUNAS_LISTA = [
    "id", "descricao", "valor_centavos", "dia_do_mes", "conta_id", "conta", "conta_tipo", "metodo",
    "categoria_id", "categoria", "assinatura", "pausada", "ativa", "inicio", "situacao_mes",
    "total_ate_hoje", "desde",
]

# Campos da recorrente que os lançamentos previstos copiam quando ela é editada.
_CAMPOS_DO_LANCAMENTO = ("descricao", "valor_centavos", "conta_id", "categoria_id", "metodo")


# --- Datas -----------------------------------------------------------------------------


def _chave_mes(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _data_no_mes(ano: int, mes: int, dia: int) -> date:
    """O dia pedido no mês; se não existe (31 em fevereiro), o último dia dele."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def _meses(de: date, ate: date):
    """(ano, mes) de cada mês entre `de` e `ate`, inclusive."""
    ano, mes = de.year, de.month
    while (ano, mes) <= (ate.year, ate.month):
        yield ano, mes
        ano, mes = (ano, mes + 1) if mes < 12 else (ano + 1, 1)


# --- Validação -----------------------------------------------------------------------------


def _obter(conn: sqlite3.Connection, recorrente_id: int) -> sqlite3.Row:
    linha = conn.execute("SELECT * FROM recorrentes WHERE id = ?", (recorrente_id,)).fetchone()
    if linha is None:
        raise ValueError(f"Recorrente {recorrente_id} não existe.")
    return linha


def _validar(conn: sqlite3.Connection, campos: dict, atual: sqlite3.Row | None = None) -> dict:
    """Confere e normaliza os campos informados (os que faltam vêm de `atual`, na edição)."""
    campos = dict(campos)
    if "descricao" in campos:
        campos["descricao"] = str(campos["descricao"]).strip()
        if not campos["descricao"]:
            raise ValueError("Diga o que é.")
    if "valor_centavos" in campos and int(campos["valor_centavos"]) == 0:
        raise ValueError("O valor precisa ser diferente de zero.")
    if "dia_do_mes" in campos and not 1 <= int(campos["dia_do_mes"]) <= 31:
        raise ValueError("O dia do mês vai de 1 a 31.")
    if "assinatura" in campos:
        campos["assinatura"] = int(bool(campos["assinatura"]))
    if "metodo" in campos or "conta_id" in campos:
        metodo = campos.get("metodo", atual["metodo"] if atual else None)
        if metodo not in METODOS:
            raise ValueError(f"Método desconhecido: {metodo}")
        conta = campos.get("conta_id", atual["conta_id"] if atual else None)
        mudou = atual is None or metodo != atual["metodo"] or conta != atual["conta_id"]
        # Cartão vai pro cartão escolhido, dinheiro pra carteira; se nada mudou, não mexe (conta já desativada).
        campos["conta_id"] = lancamentos.conta_do_metodo(conn, metodo, conta) if mudou else conta
        campos["metodo"] = metodo
    return campos


# --- Criar ------------------------------------------------------------------------------------


def criar(
    conn: sqlite3.Connection,
    *,
    descricao: str,
    valor_centavos: int,
    conta_id: int,
    categoria_id: int | None,
    metodo: str,
    dia_do_mes: int,
    inicio: date,
    assinatura: bool,
) -> int:
    """Cria a recorrente e devolve o id. `valor_centavos` tem sinal (entrada > 0, saída < 0).

    Ela só começa a gerar lançamentos em `gerar_pendentes`; o primeiro é o do mês de `inicio`
    (se o dia dele não tiver passado de `inicio`, começa no mês seguinte).
    """
    campos = _validar(
        conn,
        dict(descricao=descricao, valor_centavos=valor_centavos, conta_id=conta_id, metodo=metodo,
             dia_do_mes=dia_do_mes, assinatura=assinatura),
    )
    with conn:
        return conn.execute(
            """
            INSERT INTO recorrentes
                (descricao, valor_centavos, conta_id, categoria_id, metodo, dia_do_mes, inicio, assinatura)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (campos["descricao"], int(campos["valor_centavos"]), campos["conta_id"], categoria_id,
             campos["metodo"], int(campos["dia_do_mes"]), inicio.isoformat(), campos["assinatura"]),
        ).lastrowid


def criar_a_partir_de_lancamento(conn: sqlite3.Connection, lancamento_id: int, assinatura: bool) -> int:
    """Faz de um lançamento já gravado o primeiro de uma recorrente mensal.

    Dia = dia da data do lançamento; início = a data dele. O lançamento passa a apontar pra recorrente e o
    mês dele fica registrado como gerado (não cria duplicata).
    """
    l = lancamentos.obter(conn, lancamento_id)
    if l is None:
        raise ValueError(f"Lançamento {lancamento_id} não existe.")
    if l["recorrente_id"]:
        raise ValueError("Este lançamento já repete todo mês.")
    if l["grupo_parcelas"]:
        raise ValueError("Compra parcelada não repete todo mês.")
    if l["metodo"] not in METODOS:
        raise ValueError("Só lançamentos com Pix, débito, cartão, dinheiro ou boleto podem repetir.")
    data = date.fromisoformat(l["data"])
    recorrente_id = criar(
        conn,
        descricao=l["descricao"],
        valor_centavos=l["valor_centavos"],
        conta_id=l["conta_id"],
        categoria_id=l["categoria_id"],
        metodo=l["metodo"],
        dia_do_mes=data.day,
        inicio=data,
        assinatura=assinatura,
    )
    with conn:
        conn.execute("UPDATE lancamentos SET recorrente_id = ? WHERE id = ?", (recorrente_id, lancamento_id))
        conn.execute(
            "INSERT INTO recorrentes_meses (recorrente_id, mes, lancamento_id) VALUES (?, ?, ?)",
            (recorrente_id, _chave_mes(data), lancamento_id),
        )
    return recorrente_id


# --- Gerar -------------------------------------------------------------------------------------


def _gerar(conn: sqlite3.Connection, rec: sqlite3.Row, hoje: date) -> int:
    """Cria os lançamentos dos meses de `inicio` até o mês de `hoje` que ainda não foram gerados."""
    inicio = date.fromisoformat(rec["inicio"])
    fim = date.fromisoformat(rec["fim"]) if rec["fim"] else None
    feitos = {
        r["mes"] for r in conn.execute("SELECT mes FROM recorrentes_meses WHERE recorrente_id = ?", (rec["id"],))
    }
    criados = 0
    for ano, mes in _meses(inicio, hoje):
        chave = f"{ano}-{mes:02d}"
        if chave in feitos:
            continue
        data = _data_no_mes(ano, mes, rec["dia_do_mes"])
        if data < inicio or (fim is not None and data > fim):
            continue
        lancamento_id = conn.execute(
            """
            INSERT INTO lancamentos
                (conta_id, data, valor_centavos, descricao, categoria_id, metodo, status, origem, recorrente_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'recorrente', ?)
            """,
            (rec["conta_id"], data.isoformat(), rec["valor_centavos"], rec["descricao"], rec["categoria_id"],
             rec["metodo"], "previsto" if data > hoje else "realizado", rec["id"]),
        ).lastrowid
        conn.execute(
            "INSERT INTO recorrentes_meses (recorrente_id, mes, lancamento_id) VALUES (?, ?, ?)",
            (rec["id"], chave, lancamento_id),
        )
        criados += 1
    return criados


def gerar_pendentes(conn: sqlite3.Connection, hoje: date) -> int:
    """Gera os lançamentos que faltam das recorrentes ativas e não pausadas e promove a `realizado` os
    previstos cuja data já chegou. Pode rodar quantas vezes quiser. Devolve quantos lançamentos criou."""
    criados = 0
    with conn:
        for rec in conn.execute("SELECT * FROM recorrentes WHERE ativa = 1 AND pausada = 0 ORDER BY id").fetchall():
            criados += _gerar(conn, rec, hoje)
        conn.execute(
            "UPDATE lancamentos SET status = 'realizado' "
            "WHERE origem = 'recorrente' AND status = 'previsto' AND data <= ?",
            (hoje.isoformat(),),
        )
    return criados


# --- Editar, pausar, retomar, encerrar ----------------------------------------------------------


def _previstos_futuros(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM lancamentos WHERE recorrente_id = ? AND origem = 'recorrente' AND status = 'previsto' "
        "AND data > ? ORDER BY data",
        (recorrente_id, hoje.isoformat()),
    ).fetchall()


def _apagar_previstos_futuros(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> None:
    """Apaga os previstos que ainda não aconteceram e solta os meses deles (podem ser gerados de novo)."""
    for l in _previstos_futuros(conn, recorrente_id, hoje):
        conn.execute("DELETE FROM recorrentes_meses WHERE lancamento_id = ?", (l["id"],))
        conn.execute("DELETE FROM lancamentos WHERE id = ?", (l["id"],))


def editar(conn: sqlite3.Connection, recorrente_id: int, hoje: date, **campos) -> None:
    """Muda a recorrente e os previstos que ainda vão acontecer (data depois de `hoje`).

    Campos: descricao, valor_centavos (com sinal), conta_id, categoria_id, metodo, dia_do_mes, assinatura.
    O que já aconteceu (realizados e o mês corrente já cobrado) não muda. Se o novo dia faria o previsto
    do mês cair em `hoje` ou antes, esse previsto fica na data que tinha (só passa a valer no mês seguinte).
    """
    invalidos = set(campos) - CAMPOS_EDITAVEIS
    if invalidos:
        raise ValueError(f"Campos não editáveis: {sorted(invalidos)}")
    atual = _obter(conn, recorrente_id)
    if not campos:
        return
    campos = _validar(conn, campos, atual)
    sets = ", ".join(f"{k} = ?" for k in campos)
    with conn:
        conn.execute(f"UPDATE recorrentes SET {sets} WHERE id = ?", (*campos.values(), recorrente_id))
        novo = _obter(conn, recorrente_id)
        for l in _previstos_futuros(conn, recorrente_id, hoje):
            data = date.fromisoformat(l["data"])
            nova_data = _data_no_mes(data.year, data.month, novo["dia_do_mes"])
            valores = {c: novo[c] for c in _CAMPOS_DO_LANCAMENTO}
            valores["data"] = (nova_data if nova_data > hoje else data).isoformat()
            conn.execute(
                f"UPDATE lancamentos SET {', '.join(f'{k} = ?' for k in valores)} WHERE id = ?",
                (*valores.values(), l["id"]),
            )


def pausar(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> None:
    """Para de gerar e apaga os previstos futuros. O que já foi cobrado fica."""
    rec = _obter(conn, recorrente_id)
    if not rec["ativa"] or rec["pausada"]:
        return
    with conn:
        _gerar(conn, rec, hoje)  # fecha as contas até hoje antes de parar
        conn.execute("UPDATE recorrentes SET pausada = 1 WHERE id = ?", (recorrente_id,))
        _apagar_previstos_futuros(conn, recorrente_id, hoje)


def retomar(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> None:
    """Volta a gerar a partir do mês de `hoje`. Os meses anteriores que ficaram sem lançamento (a pausa) são
    marcados em `recorrentes_meses` com `lancamento_id` NULL, pra não virarem cobranças retroativas."""
    rec = _obter(conn, recorrente_id)
    if not rec["ativa"] or not rec["pausada"]:
        return
    with conn:
        _pular_meses_passados(conn, rec, hoje)
        conn.execute("UPDATE recorrentes SET pausada = 0 WHERE id = ?", (recorrente_id,))


def reativar(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> None:
    """Desfaz o encerramento: volta a gerar a partir do mês de `hoje`, sem cobranças retroativas."""
    rec = _obter(conn, recorrente_id)
    if rec["ativa"]:
        return
    with conn:
        _pular_meses_passados(conn, rec, hoje)
        conn.execute("UPDATE recorrentes SET ativa = 1, pausada = 0, fim = NULL WHERE id = ?", (recorrente_id,))


def _pular_meses_passados(conn: sqlite3.Connection, rec: sqlite3.Row, hoje: date) -> None:
    """Marca os meses anteriores a `hoje` que ficaram sem lançamento (pausa ou encerramento) como já tratados
    (`lancamento_id` NULL), pra não virarem cobranças retroativas."""
    inicio = date.fromisoformat(rec["inicio"])
    if (inicio.year, inicio.month) >= (hoje.year, hoje.month):
        return
    mes_passado = date(hoje.year, hoje.month, 1).toordinal() - 1
    for ano, mes in _meses(inicio, date.fromordinal(mes_passado)):
        conn.execute(
            "INSERT OR IGNORE INTO recorrentes_meses (recorrente_id, mes, lancamento_id) VALUES (?, ?, NULL)",
            (rec["id"], f"{ano}-{mes:02d}"),
        )


def encerrar(conn: sqlite3.Connection, recorrente_id: int, hoje: date) -> None:
    """Encerra de vez: `ativa = 0`, `fim = hoje` e apaga os previstos futuros. O histórico fica."""
    rec = _obter(conn, recorrente_id)
    if not rec["ativa"]:
        return
    with conn:
        if not rec["pausada"]:
            _gerar(conn, rec, hoje)
        conn.execute("UPDATE recorrentes SET ativa = 0, pausada = 0, fim = ? WHERE id = ?", (hoje.isoformat(), recorrente_id))
        _apagar_previstos_futuros(conn, recorrente_id, hoje)


# --- Consultas ----------------------------------------------------------------------------------


def obter(conn: sqlite3.Connection, recorrente_id: int) -> dict:
    """A recorrente com `conta` e `categoria` (nomes)."""
    linha = conn.execute(
        """
        SELECT r.*, ct.nome AS conta, c.nome AS categoria
        FROM recorrentes r JOIN contas ct ON ct.id = r.conta_id
        LEFT JOIN categorias c ON c.id = r.categoria_id
        WHERE r.id = ?
        """,
        (recorrente_id,),
    ).fetchone()
    if linha is None:
        raise ValueError(f"Recorrente {recorrente_id} não existe.")
    return dict(linha)


def listar(
    conn: sqlite3.Connection, hoje: date, filtro: str = "ambos", incluir_encerradas: bool = False
) -> pd.DataFrame:
    """Recorrentes com o que a aba mostra. Ordem: ativas primeiro, depois por conta e descrição.

    Colunas: id, descricao, valor_centavos (com sinal), dia_do_mes, conta_id, conta, conta_tipo, metodo,
    categoria_id, categoria, assinatura, pausada, ativa, inicio (date), situacao_mes, dias_para_cobrar,
    total_ate_hoje (centavos, positivo: soma do que já foi cobrado), desde (date).

    `situacao_mes`, olhando o mês de `hoje`: "encerrada", "pausada", "cobrada" (lançamento do mês com data até
    hoje), "prevista" (lançamento do mês ainda por vir; `dias_para_cobrar` = dias até ele) ou "sem_cobranca"
    (nenhum lançamento este mês, por exemplo porque foi apagado). `dias_para_cobrar` é None nos outros casos.
    """
    if filtro not in FILTROS:
        raise ValueError(f"Filtro desconhecido: {filtro}")
    onde = []
    if filtro != "ambos":
        onde.append(f"r.assinatura = {1 if filtro == 'assinaturas' else 0}")
    if not incluir_encerradas:
        onde.append("r.ativa = 1")
    linhas = conn.execute(
        f"""
        SELECT r.id, r.descricao, r.valor_centavos, r.dia_do_mes, r.conta_id, ct.nome AS conta,
               ct.tipo AS conta_tipo, r.metodo, r.categoria_id, c.nome AS categoria,
               r.assinatura, r.pausada, r.ativa, r.inicio
        FROM recorrentes r
        JOIN contas ct ON ct.id = r.conta_id
        LEFT JOIN categorias c ON c.id = r.categoria_id
        {('WHERE ' + ' AND '.join(onde)) if onde else ''}
        ORDER BY r.ativa DESC, ct.tipo, ct.nome, r.descricao, r.id
        """
    ).fetchall()

    ate_hoje = {
        r["recorrente_id"]: r
        for r in conn.execute(
            """
            SELECT recorrente_id, SUM(valor_centavos) AS total, MIN(data) AS primeiro
            FROM lancamentos WHERE recorrente_id IS NOT NULL AND data <= ? GROUP BY recorrente_id
            """,
            (hoje.isoformat(),),
        )
    }
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    do_mes = {
        r["recorrente_id"]: date.fromisoformat(r["data"])
        for r in conn.execute(
            "SELECT recorrente_id, MAX(data) AS data FROM lancamentos "
            "WHERE recorrente_id IS NOT NULL AND data BETWEEN ? AND ? GROUP BY recorrente_id",
            (primeiro_dia.isoformat(), ultimo_dia.isoformat()),
        )
    }

    registros, dias = [], []
    for r in linhas:
        inicio = date.fromisoformat(r["inicio"])
        soma = ate_hoje.get(r["id"])
        data_mes = do_mes.get(r["id"])
        if not r["ativa"]:
            situacao, faltam = "encerrada", None
        elif r["pausada"]:
            situacao, faltam = "pausada", None
        elif data_mes is None:
            situacao, faltam = "sem_cobranca", None
        elif data_mes <= hoje:
            situacao, faltam = "cobrada", None
        else:
            situacao, faltam = "prevista", (data_mes - hoje).days
        registros.append(
            {
                **{k: r[k] for k in r.keys() if k != "inicio"},
                "assinatura": bool(r["assinatura"]),
                "pausada": bool(r["pausada"]),
                "ativa": bool(r["ativa"]),
                "inicio": inicio,
                "situacao_mes": situacao,
                "total_ate_hoje": abs(soma["total"]) if soma else 0,
                "desde": date.fromisoformat(soma["primeiro"]) if soma and soma["primeiro"] else inicio,
            }
        )
        dias.append(faltam)
    df = pd.DataFrame(registros, columns=COLUNAS_LISTA).astype({"assinatura": bool, "pausada": bool, "ativa": bool})
    df["dias_para_cobrar"] = pd.Series(dias, dtype=object, index=df.index)
    return df


def resumo(conn: sqlite3.Connection, hoje: date, filtro: str = "ambos") -> dict:
    """Números do topo da aba, só das recorrentes ativas e não pausadas (do `filtro`).

    Chaves (centavos, positivos): saidas_por_mes, saidas_por_ano (×12), entradas_por_mes;
    este_mes_cobradas e este_mes_a_cobrar: (quantidade, centavos) das saídas deste mês já cobradas e ainda
    por cobrar. Entradas não entram nas contagens do mês.
    """
    df = listar(conn, hoje, filtro)
    df = df[~df["pausada"]]
    saidas = df[df["valor_centavos"] < 0]
    por_mes = int(-saidas["valor_centavos"].sum())
    cobradas = saidas[saidas["situacao_mes"] == "cobrada"]
    a_cobrar = saidas[saidas["situacao_mes"] == "prevista"]
    return {
        "saidas_por_mes": por_mes,
        "saidas_por_ano": por_mes * 12,
        "entradas_por_mes": int(df.loc[df["valor_centavos"] > 0, "valor_centavos"].sum()),
        "este_mes_cobradas": (len(cobradas), int(-cobradas["valor_centavos"].sum())),
        "este_mes_a_cobrar": (len(a_cobrar), int(-a_cobrar["valor_centavos"].sum())),
    }
