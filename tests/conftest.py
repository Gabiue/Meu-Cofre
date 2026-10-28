import os

# Os testes de interface nunca podem abrir janelas de verdade no desktop (o ambiente pode
# trazer QT_QPA_PLATFORM=wayland): força o modo offscreen antes de qualquer import do Qt.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest

from meu_cofre import db


@pytest.fixture
def conn(tmp_path):
    """Banco temporário com o seed padrão (nunca toca em data/)."""
    c = db.conectar(tmp_path / "teste.db")
    yield c
    c.close()


def id_conta(conn, nome):
    return conn.execute("SELECT id FROM contas WHERE nome = ?", (nome,)).fetchone()["id"]


def id_cat(conn, nome):
    return conn.execute("SELECT id FROM categorias WHERE nome = ?", (nome,)).fetchone()["id"]
