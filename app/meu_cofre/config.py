"""Onde ficam os dados. Por padrão, em `data/` na raiz do repositório (fora do git)."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def caminho_banco() -> Path:
    """Caminho do arquivo SQLite. Pode ser trocado pela variável MEU_COFRE_DB."""
    if env := os.environ.get("MEU_COFRE_DB"):
        return Path(env).expanduser()
    return RAIZ / "data" / "meu-cofre.db"
