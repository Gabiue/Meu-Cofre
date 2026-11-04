"""Faturas de cartão: a regra de que compra cai em qual fatura, o total de cada uma e o pagamento.

Modelo mental: o cartão é uma "caixa" por fatura. Comprar no cartão é um débito futuro; o dinheiro só sai da
conta quando a fatura é paga, e aí o pagamento é uma **transferência** conta -> cartão (não é gasto de novo).

**Referência da fatura** = o mês do **vencimento** ("AAAA-MM"): a "fatura de novembro" vence em novembro.

Regra (docs/design/telas.md): com fechamento no dia F e vencimento no dia V, a fatura fecha no dia F do mês
de fechamento C e cobre as compras de [dia F de C-1, dia F de C), com a compra do próprio dia F já na fatura
seguinte. Ela vence no dia V do mês C se V > F; senão, no mês seguinte a C. Exemplos:

- fecha 1, vence 10 (Caixa Visa): fatura de novembro = compras de 01/10 a 31/10, fecha 01/11, vence 10/11;
- fecha 25, vence 5: fatura de novembro = compras de 25/09 a 24/10, fecha 25/10, vence 05/11.

Cartão sem fechamento cadastrado: a fatura é o mês do calendário e vence no dia V (ou 10) do mês seguinte
(compras de outubro = fatura de novembro). Dia que não existe no mês (31 em fevereiro) vira o último dia dele.
Todas as funções que dependem do dia recebem `hoje` (nada de `date.today()` aqui).
"""

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from meu_cofre.lancamentos import data_no_mes, nome_com_final
from meu_cofre.relatorios import top_com_outros

DIA_VENCIMENTO_PADRAO = 10
SITUACOES = ("aberta", "fechada", "parcial", "paga", "futura")
CATEGORIA_PAGAMENTO = "Pagamento de fatura"

_COLUNAS = ["id", "data", "descricao", "valor_centavos", "categoria", "parcela_atual", "parcela_total"]


# --- Regra da fatura (sem banco) ---------------------------------------------------------------


def _soma_mes(ano: int, mes: int, n: int) -> tuple[int, int]:
    indice = ano * 12 + mes - 1 + n
    return indice // 12, indice % 12 + 1


def _ano_mes(referencia: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", referencia or ""):
        raise ValueError(f"Referência inválida: {referencia!r}. Use AAAA-MM.")
    return int(referencia[:4]), int(referencia[5:])


def _chave(ano: int, mes: int) -> str:
    return f"{ano}-{mes:02d}"


def _mes_de_fechamento(ano: int, mes: int, dia_fechamento: int | None, dia_vencimento: int | None) -> tuple[int, int]:
    """Mês em que fecha a fatura que vence em (ano, mes)."""
    if dia_fechamento is None:
        return ano, mes  # sem fechamento: fecha no dia 1 do mês do vencimento (cobre o mês anterior inteiro)
    vence_no_mes_do_fechamento = (dia_vencimento or DIA_VENCIMENTO_PADRAO) > dia_fechamento
    return (ano, mes) if vence_no_mes_do_fechamento else _soma_mes(ano, mes, -1)


def referencia_da_compra(dia: date, dia_fechamento: int | None, dia_vencimento: int | None = None) -> str:
    """Fatura ('AAAA-MM' do vencimento) em que uma compra feita em `dia` cai."""
    if dia_fechamento is None:
        return _chave(*_soma_mes(dia.year, dia.month, 1))
    ja_fechou = dia >= data_no_mes(dia.year, dia.month, dia_fechamento)  # a compra do dia do fechamento é da próxima
    ano_f, mes_f = _soma_mes(dia.year, dia.month, 1 if ja_fechou else 0)
    vence_no_mes_do_fechamento = (dia_vencimento or DIA_VENCIMENTO_PADRAO) > dia_fechamento
    return _chave(*_soma_mes(ano_f, mes_f, 0 if vence_no_mes_do_fechamento else 1))


def periodo(referencia: str, dia_fechamento: int | None, dia_vencimento: int | None = None) -> tuple[date, date]:
    """(primeiro dia, dia do fechamento) da fatura: cobre as compras de `inicio` até antes de `fim_exclusivo`."""
    ano, mes = _ano_mes(referencia)
    ano_f, mes_f = _mes_de_fechamento(ano, mes, dia_fechamento, dia_vencimento)
    dia = dia_fechamento or 1
    ano_i, mes_i = _soma_mes(ano_f, mes_f, -1)
    return data_no_mes(ano_i, mes_i, dia), data_no_mes(ano_f, mes_f, dia)


def vencimento(referencia: str, dia_vencimento: int | None = None) -> date:
    """Dia em que a fatura vence: o dia V (padrão 10) do mês da referência."""
    ano, mes = _ano_mes(referencia)
    return data_no_mes(ano, mes, dia_vencimento or DIA_VENCIMENTO_PADRAO)


# --- Faturas de um cartão ----------------------------------------------------------------------


@dataclass
class Fatura:
    """Uma fatura. `total_centavos` = compras − estornos (positivo = a pagar); `lancamentos`: compras dela."""

    cartao_id: int
    referencia: str
    inicio: date
    fim_exclusivo: date
    vencimento: date
    total_centavos: int
    situacao: str  # aberta | fechada | parcial (pagou parte) | paga | futura
    pago_centavos: int = 0  # soma de todos os pagamentos da fatura
    data_pagamento: date | None = None  # do último pagamento
    conta_origem_id: int | None = None  # do último pagamento
    pagamentos: list[dict] = field(default_factory=list)  # id, valor_centavos, data (date), conta_origem_id; do mais antigo ao mais novo
    lancamentos: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=_COLUNAS))

    @property
    def fechamento(self) -> date:
        """Dia em que a fatura fecha (a compra desse dia já vai pra próxima)."""
        return self.fim_exclusivo

    @property
    def paga(self) -> bool:
        return self.situacao == "paga"

    @property
    def restante_centavos(self) -> int:
        """Quanto falta pagar (0 se já pagou tudo ou mais)."""
        return max(self.total_centavos - self.pago_centavos, 0)

    @property
    def a_mais_centavos(self) -> int:
        """Quanto foi pago acima do total (0 se não passou)."""
        return max(self.pago_centavos - self.total_centavos, 0)


def _cartao(conn: sqlite3.Connection, cartao_id: int) -> sqlite3.Row:
    linha = conn.execute("SELECT * FROM contas WHERE id = ?", (cartao_id,)).fetchone()
    if linha is None or linha["tipo"] != "cartao":
        raise ValueError(f"Cartão {cartao_id} não existe.")
    return linha


def _pagamentos(conn: sqlite3.Connection, cartao_id: int) -> dict[str, list[sqlite3.Row]]:
    """Pagamentos do cartão por referência, do mais antigo ao mais novo (a fatura pode ter vários).

    Pagamento cujos lançamentos foram apagados à mão não conta.
    """
    linhas = conn.execute(
        """
        SELECT * FROM faturas_pagas fp
        WHERE fp.cartao_id = ?
          AND EXISTS (SELECT 1 FROM lancamentos l WHERE l.transferencia_id = fp.transferencia_id)
        ORDER BY fp.data, fp.id
        """,
        (cartao_id,),
    )
    por_ref: dict[str, list[sqlite3.Row]] = {}
    for r in linhas:
        por_ref.setdefault(r["referencia"], []).append(r)
    return por_ref


def faturas_do_cartao(conn: sqlite3.Connection, cartao_id: int, hoje: date) -> list[Fatura]:
    """Todas as faturas do cartão, da mais antiga pra mais nova.

    Entram as que têm compras (inclusive futuras: parcelas e previstos), as pagas e a fatura aberta de hoje
    (mesmo vazia). O lançamento do pagamento (transferência) não é compra. Estorno (entrada) abate.
    """
    cartao = _cartao(conn, cartao_id)
    fechamento, venc = cartao["dia_fechamento"], cartao["dia_vencimento"]
    compras = pd.read_sql_query(
        """
        SELECT l.id, l.data, l.descricao, l.valor_centavos, c.nome AS categoria, l.parcela_atual, l.parcela_total
        FROM lancamentos l LEFT JOIN categorias c ON c.id = l.categoria_id
        WHERE l.conta_id = ?
          AND COALESCE(c.tipo, '') <> 'transferencia' AND COALESCE(l.metodo, '') <> 'transferencia'
        ORDER BY l.data DESC, l.id DESC
        """,
        conn,
        params=(cartao_id,),
        parse_dates=["data"],
    )
    compras["referencia"] = [referencia_da_compra(d.date(), fechamento, venc) for d in compras["data"]]
    pagas = _pagamentos(conn, cartao_id)

    referencias = set(compras["referencia"]) | set(pagas) | {referencia_da_compra(hoje, fechamento, venc)}
    faturas = []
    for referencia in sorted(referencias):
        inicio, fim = periodo(referencia, fechamento, venc)
        itens = compras[compras["referencia"] == referencia][_COLUNAS].reset_index(drop=True)
        pagamentos = pagas.get(referencia, [])
        total = -int(itens["valor_centavos"].sum())
        pago = sum(p["valor_centavos"] for p in pagamentos)
        if pagamentos and pago >= total:
            situacao = "paga"
        elif pagamentos:
            situacao = "parcial"
        elif hoje >= fim:
            situacao = "fechada"
        elif hoje >= inicio:
            situacao = "aberta"
        else:
            situacao = "futura"
        faturas.append(
            Fatura(
                cartao_id=cartao_id,
                referencia=referencia,
                inicio=inicio,
                fim_exclusivo=fim,
                vencimento=vencimento(referencia, venc),
                total_centavos=total,
                situacao=situacao,
                pago_centavos=pago,
                data_pagamento=date.fromisoformat(pagamentos[-1]["data"]) if pagamentos else None,
                conta_origem_id=pagamentos[-1]["conta_origem_id"] if pagamentos else None,
                pagamentos=[
                    {"id": p["id"], "valor_centavos": p["valor_centavos"], "data": date.fromisoformat(p["data"]),
                     "conta_origem_id": p["conta_origem_id"]}
                    for p in pagamentos
                ],
                lancamentos=itens,
            )
        )
    return faturas


def gastos_por_categoria(conn: sqlite3.Connection, cartao_id: int, referencia: str, n: int = 5) -> pd.DataFrame:
    """Pra onde foi a fatura: as `n` maiores categorias + "Outros", no formato de `relatorios.gastos_top_categorias`.

    Conta só as compras da fatura (parcelas que caem nela inclusive); estorno e o pagamento não entram.
    Sem categoria = "A categorizar". Colunas: categoria, centavos, percentual (0 a 100).
    """
    _ano_mes(referencia)
    cartao = _cartao(conn, cartao_id)
    fechamento, venc = cartao["dia_fechamento"], cartao["dia_vencimento"]
    inicio, fim = periodo(referencia, fechamento, venc)
    compras = pd.read_sql_query(
        """
        SELECT COALESCE(c.nome, 'A categorizar') AS categoria, -l.valor_centavos AS centavos
        FROM lancamentos l LEFT JOIN categorias c ON c.id = l.categoria_id
        WHERE l.conta_id = ? AND l.valor_centavos < 0
          AND COALESCE(c.tipo, '') <> 'transferencia' AND COALESCE(l.metodo, '') <> 'transferencia'
          AND l.data >= ? AND l.data < ?
        """,
        conn,
        params=(cartao_id, inicio.isoformat(), fim.isoformat()),
    )
    return top_com_outros(compras.groupby("categoria", as_index=False)["centavos"].sum(), n)


# --- Pagar e desfazer --------------------------------------------------------------------------


def _categoria_pagamento(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM categorias WHERE nome = ?", (CATEGORIA_PAGAMENTO,)).fetchone()
    if linha is None:
        linha = conn.execute(
            "SELECT id FROM categorias WHERE tipo = 'transferencia' AND ativa = 1 ORDER BY id LIMIT 1"
        ).fetchone()
    if linha is None:
        raise ValueError(f"Falta a categoria '{CATEGORIA_PAGAMENTO}' (tipo transferência).")
    return linha["id"]


def pagar_fatura(
    conn: sqlite3.Connection,
    cartao_id: int,
    referencia: str,
    valor_centavos: int,
    conta_origem_id: int,
    data: date,
) -> int:
    """Registra um pagamento da fatura e devolve o id do registro em `faturas_pagas`.

    Cria uma transferência: saída na conta (categoria "Pagamento de fatura", método transferência) e entrada
    no cartão, as duas com o mesmo `transferencia_id`. Em conta e Devo caem juntos; gasto não duplica.
    `valor_centavos` (positivo) pode ser diferente do total: abaixo, a fatura fica "parcial" (falta o restante,
    que se paga depois); acima, fica paga com "pago a mais". Uma fatura pode ter vários pagamentos.
    """
    cartao = _cartao(conn, cartao_id)
    _ano_mes(referencia)
    if valor_centavos <= 0:
        raise ValueError("O valor pago precisa ser maior que zero.")
    origem = conn.execute("SELECT * FROM contas WHERE id = ?", (conta_origem_id,)).fetchone()
    if origem is None or origem["tipo"] != "corrente" or not origem["ativa"]:
        raise ValueError("Escolha uma conta corrente ativa pra pagar a fatura.")
    atual = next((f for f in faturas_do_cartao(conn, cartao_id, data) if f.referencia == referencia), None)
    if atual is not None and atual.paga:
        raise ValueError("Esta fatura já está paga.")

    categoria_id = _categoria_pagamento(conn)
    descricao = f"Pagamento da fatura {nome_com_final(cartao['nome'], cartao['final'])} ({referencia[5:]}/{referencia[:4]})"
    transferencia_id = uuid.uuid4().hex
    with conn:
        for conta_id, valor in ((conta_origem_id, -valor_centavos), (cartao_id, valor_centavos)):
            conn.execute(
                """
                INSERT INTO lancamentos
                    (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem, transferencia_id)
                VALUES (?, ?, ?, ?, ?, 'transferencia', 'manual', ?)
                """,
                (conta_id, data.isoformat(), valor, descricao, categoria_id, transferencia_id),
            )
        return conn.execute(
            """
            INSERT INTO faturas_pagas (cartao_id, referencia, valor_centavos, data, conta_origem_id, transferencia_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cartao_id, referencia, valor_centavos, data.isoformat(), conta_origem_id, transferencia_id),
        ).lastrowid


def desfazer_pagamento(conn: sqlite3.Connection, cartao_id: int, referencia: str, pagamento_id: int | None = None) -> None:
    """Desfaz UM pagamento (o `pagamento_id`, ou o mais recente): apaga os dois lados da transferência e o registro.

    Se era o único, a fatura volta a "fechada" (ou "aberta"); se havia outros, vira (ou segue) "parcial".
    """
    pagamentos = _pagamentos(conn, cartao_id).get(referencia, [])
    linha = next((p for p in reversed(pagamentos) if pagamento_id in (None, p["id"])), None)
    if linha is None:
        raise ValueError("Esta fatura não está paga.")
    with conn:
        conn.execute("DELETE FROM lancamentos WHERE transferencia_id = ?", (linha["transferencia_id"],))
        conn.execute("DELETE FROM faturas_pagas WHERE id = ?", (linha["id"],))
