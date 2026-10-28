"""Números do dashboard. Transferências entre contas nunca contam como gasto nem receita."""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

# Lançamentos que contam como renda/gasto: tudo que não é transferência.
_SEM_TRANSFERENCIA = """
    FROM lancamentos l
    LEFT JOIN categorias c ON c.id = l.categoria_id
    WHERE COALESCE(c.tipo, '') <> 'transferencia'
      AND COALESCE(l.metodo, '') <> 'transferencia'
"""


def limites_do_mes(ano: int, mes: int) -> tuple[date, date]:
    return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])


@dataclass
class ResumoMes:
    entradas: int
    saidas: int  # positivo

    @property
    def sobra(self) -> int:
        return self.entradas - self.saidas

    @property
    def taxa_poupanca(self) -> float | None:
        """Quanto da renda sobrou (0.2 = 20%). None se não houve renda."""
        return self.sobra / self.entradas if self.entradas else None


def resumo_mes(conn: sqlite3.Connection, ano: int, mes: int) -> ResumoMes:
    inicio, fim = limites_do_mes(ano, mes)
    linha = conn.execute(
        f"""
        SELECT COALESCE(SUM(CASE WHEN l.valor_centavos > 0 THEN l.valor_centavos END), 0) AS entradas,
               COALESCE(SUM(CASE WHEN l.valor_centavos < 0 THEN -l.valor_centavos END), 0) AS saidas
        {_SEM_TRANSFERENCIA}
          AND l.data BETWEEN ? AND ?
        """,
        (inicio.isoformat(), fim.isoformat()),
    ).fetchone()
    return ResumoMes(entradas=linha["entradas"], saidas=linha["saidas"])


def gastos_por_categoria(conn: sqlite3.Connection, ano: int, mes: int) -> pd.DataFrame:
    """Colunas: categoria, natureza, centavos (positivo). Maior gasto primeiro."""
    inicio, fim = limites_do_mes(ano, mes)
    return pd.read_sql_query(
        f"""
        SELECT COALESCE(c.nome, 'A categorizar') AS categoria,
               c.natureza,
               -SUM(l.valor_centavos) AS centavos
        {_SEM_TRANSFERENCIA}
          AND l.valor_centavos < 0
          AND l.data BETWEEN ? AND ?
        GROUP BY 1, 2
        ORDER BY centavos DESC
        """,
        conn,
        params=(inicio.isoformat(), fim.isoformat()),
    )


def gastos_por_natureza(conn: sqlite3.Connection, ano: int, mes: int) -> pd.DataFrame:
    """Colunas: natureza (essencial/flexivel/superfluo/sem natureza), centavos."""
    df = gastos_por_categoria(conn, ano, mes)
    df["natureza"] = df["natureza"].fillna("sem natureza")
    ordem = ["essencial", "flexivel", "superfluo", "sem natureza"]
    return (
        df.groupby("natureza", as_index=False)["centavos"].sum()
        .set_index("natureza").reindex(ordem).dropna().reset_index()
    )


def historico_mensal(conn: sqlite3.Connection, ate: date, meses: int = 6) -> pd.DataFrame:
    """Entradas e saídas dos últimos `meses` meses até `ate`. Colunas: mes, entradas, saidas."""
    linhas = []
    ano, mes = ate.year, ate.month
    for _ in range(meses):
        r = resumo_mes(conn, ano, mes)
        linhas.append({"mes": f"{ano}-{mes:02d}", "entradas": r.entradas, "saidas": r.saidas})
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    return pd.DataFrame(linhas[::-1])


def saldo_em(conn: sqlite3.Connection, dia: date) -> int:
    """Saldo somado de todas as contas no fim de `dia` (inclui saldos iniciais).

    O cartão entra negativo (é dívida), então isso é o saldo líquido.
    """
    inicial = conn.execute(
        "SELECT COALESCE(SUM(saldo_inicial_centavos), 0) FROM contas WHERE ativa = 1"
    ).fetchone()[0]
    movimentos = conn.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos WHERE data <= ?",
        (dia.isoformat(),),
    ).fetchone()[0]
    return inicial + movimentos


def projecao_fim_do_mes(conn: sqlite3.Connection, hoje: date) -> int:
    """Saldo de hoje + tudo que já está lançado até o fim do mês (parcelas, previstos)."""
    _, fim = limites_do_mes(hoje.year, hoje.month)
    return saldo_em(conn, fim)


def saldo_diario(conn: sqlite3.Connection, inicio: date, fim: date) -> pd.DataFrame:
    """Saldo líquido no fim de cada dia entre `inicio` e `fim`. Colunas: data, centavos."""
    base = saldo_em(conn, date.fromordinal(inicio.toordinal() - 1))
    mov = pd.read_sql_query(
        """
        SELECT data, SUM(valor_centavos) AS centavos
        FROM lancamentos WHERE data BETWEEN ? AND ?
        GROUP BY data
        """,
        conn,
        params=(inicio.isoformat(), fim.isoformat()),
        parse_dates=["data"],
    ).set_index("data")["centavos"]
    dias = pd.date_range(inicio, fim, freq="D")
    saldo = mov.reindex(dias, fill_value=0).cumsum() + base
    return pd.DataFrame({"data": dias, "centavos": saldo.to_numpy()})


# --- Tela inicial (F2) --------------------------------------------------------

_CONTAS_DO_DIA_A_DIA = ("corrente", "dinheiro")


def _saldo_das_contas(conn: sqlite3.Connection, tipos: tuple[str, ...], ate: date | None) -> int:
    """Saldo inicial + lançamentos (até `ate`, ou de todas as datas) das contas ativas dos `tipos`."""
    marcas = ", ".join("?" * len(tipos))
    filtro, params = ("AND l.data <= ?", [ate.isoformat()]) if ate else ("", [])
    return conn.execute(
        f"""
        SELECT COALESCE((SELECT SUM(saldo_inicial_centavos) FROM contas WHERE ativa = 1 AND tipo IN ({marcas})), 0)
             + COALESCE((SELECT SUM(l.valor_centavos) FROM lancamentos l JOIN contas ct ON ct.id = l.conta_id
                         WHERE ct.ativa = 1 AND ct.tipo IN ({marcas}) {filtro}), 0)
        """,
        [*tipos, *tipos, *params],
    ).fetchone()[0]


def em_conta(conn: sqlite3.Connection, hoje: date) -> int:
    """Dinheiro disponível hoje: saldo das contas corrente e dinheiro (saldo inicial + lançamentos até hoje)."""
    return _saldo_das_contas(conn, _CONTAS_DO_DIA_A_DIA, hoje)


def _divida_do_cartao(conn: sqlite3.Connection, ate: date | None) -> int:
    """Quanto está no cartão (positivo). `ate=None` conta todas as datas, inclusive parcelas futuras."""
    return max(0, -_saldo_das_contas(conn, ("cartao",), ate))


def devo(conn: sqlite3.Connection) -> int:
    """Tudo que está no cartão e ainda não foi pago, parcelas futuras incluídas (centavos, positivo)."""
    return _divida_do_cartao(conn, None)


def livre_ate_fim_do_mes(conn: sqlite3.Connection, hoje: date) -> int:
    """Em conta, menos a dívida do cartão até o fim do mês, menos as saídas já lançadas
    (contas corrente/dinheiro) de amanhã até o fim do mês. Pode ser negativo.

    Transferências ficam de fora das saídas futuras: pagar a fatura já está na dívida do cartão.
    """
    _, fim = limites_do_mes(hoje.year, hoje.month)
    amanha = date.fromordinal(hoje.toordinal() + 1)
    futuras = conn.execute(
        """
        SELECT COALESCE(-SUM(l.valor_centavos), 0)
        FROM lancamentos l
        JOIN contas ct ON ct.id = l.conta_id
        LEFT JOIN categorias c ON c.id = l.categoria_id
        WHERE ct.ativa = 1 AND ct.tipo IN ('corrente', 'dinheiro')
          AND l.valor_centavos < 0
          AND COALESCE(c.tipo, '') <> 'transferencia'
          AND COALESCE(l.metodo, '') <> 'transferencia'
          AND l.data BETWEEN ? AND ?
        """,
        (amanha.isoformat(), fim.isoformat()),
    ).fetchone()[0]
    return em_conta(conn, hoje) - _divida_do_cartao(conn, fim) - futuras


def por_dia_ate_fim_do_mes(conn: sqlite3.Connection, hoje: date) -> int:
    """Livre dividido pelos dias que faltam no mês, contando hoje (centavos, divisão inteira)."""
    _, fim = limites_do_mes(hoje.year, hoje.month)
    dias_restantes = fim.day - hoje.day + 1  # nunca zero: hoje conta
    return livre_ate_fim_do_mes(conn, hoje) // dias_restantes


def gastos_por_dia(conn: sqlite3.Connection, ano: int, mes: int, ate: date | None = None) -> pd.DataFrame:
    """Saídas (positivo) de cada dia do mês, de 1 até `ate` (padrão: fim do mês). Dias sem gasto = 0.

    Colunas: data, centavos.
    """
    inicio, fim = limites_do_mes(ano, mes)
    ultimo = min(ate, fim) if ate else fim
    dias = pd.date_range(inicio, ultimo, freq="D")
    if ultimo < inicio:
        return pd.DataFrame({"data": dias, "centavos": []}, dtype="int64")
    por_data = pd.read_sql_query(
        f"""
        SELECT l.data AS data, -SUM(l.valor_centavos) AS centavos
        {_SEM_TRANSFERENCIA}
          AND l.valor_centavos < 0
          AND l.data BETWEEN ? AND ?
        GROUP BY l.data
        """,
        conn,
        params=(inicio.isoformat(), ultimo.isoformat()),
        parse_dates=["data"],
    ).set_index("data")["centavos"]
    return pd.DataFrame({"data": dias, "centavos": por_data.reindex(dias, fill_value=0).to_numpy(dtype="int64")})


def gastos_top_categorias(conn: sqlite3.Connection, ano: int, mes: int, n: int = 5) -> pd.DataFrame:
    """As `n` maiores categorias de gasto + uma linha "Outros" com o resto (só se houver resto).

    A categoria "Outros" do seed entra sempre nessa última linha, pra não haver duas iguais.
    Colunas: categoria, centavos, percentual (0 a 100).
    """
    df = gastos_por_categoria(conn, ano, mes)[["categoria", "centavos"]]
    if df.empty:
        return pd.DataFrame({"categoria": [], "centavos": [], "percentual": []}).astype(
            {"categoria": "object", "centavos": "int64", "percentual": "float64"}
        )
    total = int(df["centavos"].sum())
    da_lista = df[df["categoria"] != "Outros"].sort_values("centavos", ascending=False, kind="stable")
    top = da_lista.head(n)
    resto = total - int(top["centavos"].sum())
    linhas = top.to_dict("records")
    if resto > 0:
        linhas.append({"categoria": "Outros", "centavos": resto})
    saida = pd.DataFrame(linhas)
    saida["centavos"] = saida["centavos"].astype("int64")
    saida["percentual"] = saida["centavos"] / total * 100
    return saida
