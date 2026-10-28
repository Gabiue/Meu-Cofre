"""Conexão com o SQLite e criação do banco."""

import sqlite3
from importlib.resources import files
from pathlib import Path

from meu_cofre import seed
from meu_cofre.config import caminho_banco


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Abre o banco, criando o arquivo, as tabelas e o seed na primeira vez."""
    caminho = Path(caminho) if caminho else caminho_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(caminho, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(files("meu_cofre").joinpath("schema.sql").read_text(encoding="utf-8"))
    seed.aplicar(conn)
    return conn
