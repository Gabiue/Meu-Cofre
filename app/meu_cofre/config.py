"""Onde ficam os dados: MEU_COFRE_DB, o `data/` do projeto (instalação antiga) ou a pasta de dados do usuário (XDG; %APPDATA% no Windows)."""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def caminho_banco() -> Path:
    """Caminho do arquivo SQLite. A pasta é criada por db.conectar; nenhum banco é movido ou copiado."""
    if env := os.environ.get("MEU_COFRE_DB"):
        return Path(env).expanduser()
    legado = RAIZ / "data" / "meu-cofre.db"
    if legado.exists():
        return legado
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "meu-cofre" / "meu-cofre.db"
    dados = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return dados / "meu-cofre" / "meu-cofre.db"
