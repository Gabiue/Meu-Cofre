import os

# Os testes de interface nunca podem abrir janelas de verdade no desktop (o ambiente pode
# trazer QT_QPA_PLATFORM=wayland): força o modo offscreen antes de qualquer import do Qt.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# E nada de rede: a janela principal buscaria as taxas do Banco Central ao abrir (ver ui/atualizador.py).
os.environ["MEU_COFRE_SEM_REDE"] = "1"

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


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Sai logo depois do resumo, sem a finalização do Python: com widgets ainda vivos, desmontar o Qt na saída dá
    segfault (a CI terminava com código 139 depois de todos os testes passarem, no Linux e no Windows). O código de
    saída é o do pytest."""
    yield  # o resumo dos testes (e os outros sessionfinish) já rodou aqui
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(session.exitstatus))
