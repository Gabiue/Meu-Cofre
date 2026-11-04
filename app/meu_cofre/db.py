"""Conexão com o SQLite: cria o banco novo ou migra o existente (ver `migracoes.py`)."""

import sqlite3
from pathlib import Path

from meu_cofre import migracoes, seed
from meu_cofre.config import caminho_banco


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Abre o banco. Na primeira vez cria o arquivo, as tabelas e o seed; num banco de versão
    antiga, faz backup em `backups/` ao lado dele e migra."""
    caminho = Path(caminho) if caminho else caminho_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(caminho, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migracoes.preparar(conn)
    seed.aplicar(conn)
    return conn


def ler_preferencia(conn: sqlite3.Connection, chave: str, padrao: str | None = None) -> str | None:
    linha = conn.execute("SELECT valor FROM preferencias WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha else padrao


def gravar_preferencia(conn: sqlite3.Connection, chave: str, valor: str | None) -> None:
    """Grava a preferência; `valor=None` apaga."""
    if valor is None:
        conn.execute("DELETE FROM preferencias WHERE chave = ?", (chave,))
    else:
        conn.execute("INSERT INTO preferencias (chave, valor) VALUES (?, ?) ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor", (chave, valor))
    conn.commit()
