"""O banco de demonstração cobre todas as abas (dados falsos, banco em tmp_path)."""

import importlib.util
from pathlib import Path

from meu_cofre import db

CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "dados_demo.py"


def _carregar():
    spec = importlib.util.spec_from_file_location("dados_demo", CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_demo_completo_gera_uma_coisa_de_cada(tmp_path):
    caminho = tmp_path / "demo.db"
    _carregar().main(["--db", str(caminho), "--seed", "1"])
    conn = db.conectar(caminho)
    try:
        def n(sql):
            return conn.execute(sql).fetchone()[0]

        assert n("SELECT COUNT(*) FROM lancamentos l JOIN contas c ON c.id = l.conta_id WHERE c.tipo = 'cartao' AND l.valor_centavos < 0") > 0
        assert n("SELECT COUNT(*) FROM faturas_pagas") > 0
        assert n("SELECT COUNT(*) FROM recorrentes") >= 3
        assert n("SELECT COUNT(*) FROM metas") == 2
        assert n("SELECT COUNT(*) FROM ativos WHERE tipo IN ('renda_fixa', 'tesouro')") >= 2
        assert n("SELECT COUNT(*) FROM operacoes_rv WHERE tipo = 'compra'") > 0
        assert n("SELECT COUNT(*) FROM indicadores") > 0
    finally:
        conn.close()


def test_demo_recusa_banco_real(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        _carregar().main(["--db", str(tmp_path / "meu-cofre.db")])
