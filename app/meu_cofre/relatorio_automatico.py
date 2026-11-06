"""Relatório mensal gravado em arquivo, sem Qt: `meu-cofre relatorio` (timer do systemd) e a abertura do app.

Os arquivos ficam em `relatorios/AAAA-MM.md`, ao lado do banco. O app nunca envia nada: o que fazer com o arquivo
(ex.: mandar por email) é um passo do usuário, fora daqui.
"""

import argparse
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from meu_cofre import db, relatorio_mensal

PREF_CONTEXTO = "contexto_relatorio"


def mes_anterior(hoje: date) -> tuple[int, int]:
    return (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)


def tem_lancamentos(conn: sqlite3.Connection, ano: int, mes: int) -> bool:
    return conn.execute("SELECT 1 FROM lancamentos WHERE data LIKE ? LIMIT 1", (f"{ano:04d}-{mes:02d}-%",)).fetchone() is not None


def caminho_do_mes(conn: sqlite3.Connection, ano: int, mes: int) -> Path:
    """`<pasta do banco>/relatorios/AAAA-MM.md`."""
    arquivo = conn.execute("PRAGMA database_list").fetchone()["file"]
    return Path(arquivo).parent / "relatorios" / f"{ano:04d}-{mes:02d}.md"


def texto_do_mes(conn: sqlite3.Connection, ano: int, mes: int, hoje: date) -> str:
    return relatorio_mensal.gerar(conn, ano, mes, hoje, db.ler_preferencia(conn, PREF_CONTEXTO, "") or "")


def gerar_arquivo(conn: sqlite3.Connection, ano: int, mes: int, hoje: date, forcar: bool = False, destino: Path | None = None) -> Path | None:
    """Grava o relatório do mês e devolve o caminho. Não reescreve um arquivo que já existe (a menos que `forcar`)
    e não gera nada se o mês não tem lançamentos (devolve None)."""
    destino = destino or caminho_do_mes(conn, ano, mes)
    if destino.exists() and not forcar:
        return destino
    if not tem_lancamentos(conn, ano, mes):
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto_do_mes(conn, ano, mes, hoje), encoding="utf-8")
    return destino


def _mes_arg(texto: str) -> tuple[int, int]:
    achou = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", texto)
    if not achou:
        raise argparse.ArgumentTypeError(f"mês inválido: {texto!r} (use AAAA-MM, por exemplo 2026-10)")
    return int(achou[1]), int(achou[2])


def main(argv: list[str], hoje: date | None = None) -> int:
    """`meu-cofre relatorio [--mes AAAA-MM] [--saida CAMINHO|-] [--forcar]`. Imprime só o caminho gerado (uma linha)."""
    analisador = argparse.ArgumentParser(prog="meu-cofre relatorio", description="Gera o relatório do mês em markdown, sem abrir janela.")
    analisador.add_argument("--mes", type=_mes_arg, help="mês do relatório, AAAA-MM (padrão: o mês anterior)")
    analisador.add_argument("--saida", help="arquivo de saída, ou - pra escrever na saída padrão (padrão: relatorios/AAAA-MM.md ao lado do banco)")
    analisador.add_argument("--forcar", action="store_true", help="refaz o arquivo mesmo que ele já exista")
    args = analisador.parse_args(argv)

    hoje = hoje or date.today()
    ano, mes = args.mes or mes_anterior(hoje)
    conn = db.conectar()  # respeita MEU_COFRE_DB
    try:
        if args.saida == "-":  # pra pipe: só o markdown na saída padrão, nenhum arquivo
            if not tem_lancamentos(conn, ano, mes):
                caminho = None
            else:
                sys.stdout.write(texto_do_mes(conn, ano, mes, hoje))
                return 0
        else:
            caminho = gerar_arquivo(conn, ano, mes, hoje, args.forcar, Path(args.saida) if args.saida else None)
    finally:
        conn.close()
    if caminho is None:
        print(f"Sem lançamentos em {ano:04d}-{mes:02d}: nada a gerar.", file=sys.stderr)
        return 0
    print(caminho)
    return 0
