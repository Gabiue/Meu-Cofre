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
