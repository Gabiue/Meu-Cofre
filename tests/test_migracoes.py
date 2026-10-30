"""Migrações do banco: versão em PRAGMA user_version, backup antes de migrar, banco novo direto na última."""

import sqlite3

import pytest

from meu_cofre import db, migracoes

# Esquema dos bancos que já existiam (user_version 0), congelado aqui de propósito.
SCHEMA_V0 = """
CREATE TABLE IF NOT EXISTS contas (
    id                     INTEGER PRIMARY KEY,
    nome                   TEXT    NOT NULL UNIQUE,
    tipo                   TEXT    NOT NULL CHECK (tipo IN ('corrente', 'cartao', 'dinheiro', 'investimento')),
    banco                  TEXT,
    saldo_inicial_centavos INTEGER NOT NULL DEFAULT 0,
    dia_fechamento         INTEGER,
    dia_vencimento         INTEGER,
    ativa                  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categorias (
    id       INTEGER PRIMARY KEY,
    nome     TEXT    NOT NULL UNIQUE,
    tipo     TEXT    NOT NULL CHECK (tipo IN ('receita', 'despesa', 'transferencia')),
    natureza TEXT    CHECK (natureza IN ('essencial', 'flexivel', 'superfluo')),
    pai_id   INTEGER REFERENCES categorias (id),
    ativa    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recorrentes (
    id             INTEGER PRIMARY KEY,
    descricao      TEXT    NOT NULL,
    valor_centavos INTEGER NOT NULL,
    conta_id       INTEGER NOT NULL REFERENCES contas (id),
    categoria_id   INTEGER REFERENCES categorias (id),
    metodo         TEXT,
    dia_do_mes     INTEGER NOT NULL CHECK (dia_do_mes BETWEEN 1 AND 31),
    inicio         TEXT    NOT NULL,
    fim            TEXT,
    ativa          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS importacoes (
    id                INTEGER PRIMARY KEY,
    arquivo           TEXT    NOT NULL,
    banco             TEXT,
    formato           TEXT    NOT NULL CHECK (formato IN ('csv', 'ofx')),
    conta_id          INTEGER REFERENCES contas (id),
    importado_em      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    linhas_lidas      INTEGER NOT NULL DEFAULT 0,
    linhas_novas      INTEGER NOT NULL DEFAULT 0,
    linhas_duplicadas INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lancamentos (
    id                 INTEGER PRIMARY KEY,
    conta_id           INTEGER NOT NULL REFERENCES contas (id),
    data               TEXT    NOT NULL,              -- ISO: AAAA-MM-DD
    valor_centavos     INTEGER NOT NULL,
    descricao_original TEXT,                          -- como veio do banco, nunca alterada
    descricao          TEXT    NOT NULL,
    categoria_id       INTEGER REFERENCES categorias (id),
    metodo             TEXT    CHECK (metodo IN ('pix', 'debito', 'cartao', 'dinheiro', 'boleto', 'transferencia')),
    status             TEXT    NOT NULL DEFAULT 'realizado' CHECK (status IN ('previsto', 'realizado')),
    origem             TEXT    NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'importacao', 'recorrente')),
    recorrente_id      INTEGER REFERENCES recorrentes (id),
    importacao_id      INTEGER REFERENCES importacoes (id),
    transferencia_id   TEXT,                          -- liga os dois lados de uma transferência
    grupo_parcelas     TEXT,                          -- liga as parcelas de uma mesma compra
    parcela_atual      INTEGER,
    parcela_total      INTEGER,
    conferido          INTEGER NOT NULL DEFAULT 0,    -- já bateu com uma linha de extrato/fatura?
    fitid              TEXT,                          -- id da transação no OFX, quando conciliado
    chave_dedup        TEXT,
    criado_em          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS ix_lancamentos_data ON lancamentos (data);
CREATE INDEX IF NOT EXISTS ix_lancamentos_conta ON lancamentos (conta_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lancamentos_fitid ON lancamentos (conta_id, fitid) WHERE fitid IS NOT NULL;

CREATE TABLE IF NOT EXISTS regras_categorizacao (
    id           INTEGER PRIMARY KEY,
    padrao       TEXT    NOT NULL,
    tipo_match   TEXT    NOT NULL DEFAULT 'contem' CHECK (tipo_match IN ('contem', 'regex')),
    categoria_id INTEGER NOT NULL REFERENCES categorias (id),
    prioridade   INTEGER NOT NULL DEFAULT 100
);
"""


def _banco_antigo(caminho):
    """Banco v0 com o esquema antigo, o seed de contas e dois lançamentos."""
    c = sqlite3.connect(caminho)
    c.executescript(SCHEMA_V0)
    c.execute("INSERT INTO contas (nome, tipo) VALUES ('Inter', 'corrente'), ('Cartão de crédito', 'cartao')")
    c.execute("INSERT INTO categorias (nome, tipo) VALUES ('Mercado', 'despesa')")
    c.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao) VALUES (1, '2026-10-01', -1500, 'Pão')")
    c.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao) VALUES (2, '2026-10-02', -9900, 'Tênis')")
    c.execute("INSERT INTO recorrentes (descricao, valor_centavos, conta_id, dia_do_mes, inicio) VALUES ('Netflix', -4490, 2, 7, '2026-01-01')")
    c.commit()
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    c.close()


def _colunas(conn, tabela):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}


def _tirar_v5(conn):
    """O banco de teste já nasce na última versão: desfaz o que é da v5 (metas, meta_id, Caixinhas)."""
    conn.execute("DROP INDEX ix_lancamentos_meta")
    conn.execute("ALTER TABLE lancamentos DROP COLUMN meta_id")
    conn.execute("DROP TABLE metas")
    conn.execute("DELETE FROM contas WHERE nome = 'Caixinhas'")
    conn.execute("DELETE FROM categorias WHERE nome = 'Guardar em meta'")


def _tirar_v6(conn):
    """Desfaz o que é da v6 (investimentos, indicadores e a conta Investimentos)."""
    conn.execute("DROP TABLE movimentacoes_ativos")
    conn.execute("DROP TABLE ativos")
    conn.execute("DROP TABLE indicadores")
    conn.execute("DELETE FROM contas WHERE nome = 'Investimentos'")


def test_banco_novo_nasce_na_ultima_versao_sem_backup(tmp_path):
    caminho = tmp_path / "novo.db"
    conn = db.conectar(caminho)
    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert {"assinatura", "pausada"} <= _colunas(conn, "recorrentes")
    assert "final" in _colunas(conn, "contas")
    assert {"cartao_id", "referencia", "transferencia_id"} <= _colunas(conn, "faturas_pagas")
    assert {"valor_alvo_centavos", "modo", "prioridade", "reserva", "arquivada"} <= _colunas(conn, "metas")
    assert "meta_id" in _colunas(conn, "lancamentos")
    assert conn.execute("SELECT tipo FROM contas WHERE nome = 'Caixinhas'").fetchone()[0] == "investimento"
    assert {"nome", "tipo", "instituicao", "indexador", "taxa", "vencimento", "caixinhas", "ativo"} <= _colunas(conn, "ativos")
    assert {"ativo_id", "tipo", "data", "valor_centavos", "transferencia_id"} <= _colunas(conn, "movimentacoes_ativos")
    assert {"serie", "data", "valor"} == _colunas(conn, "indicadores")
    assert conn.execute("SELECT tipo FROM contas WHERE nome = 'Investimentos'").fetchone()[0] == "investimento"
    assert not (tmp_path / "backups").exists()


def test_banco_antigo_migra_preservando_dados_e_gera_backup(tmp_path):
    caminho = tmp_path / "meu.db"
    _banco_antigo(caminho)
    conn = db.conectar(caminho)

    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert [r["descricao"] for r in conn.execute("SELECT descricao FROM lancamentos ORDER BY id")] == ["Pão", "Tênis"]
    assert conn.execute("SELECT SUM(valor_centavos) FROM lancamentos").fetchone()[0] == -11400
    rec = conn.execute("SELECT * FROM recorrentes").fetchone()
    assert (rec["descricao"], rec["valor_centavos"], rec["assinatura"], rec["pausada"]) == ("Netflix", -4490, 0, 0)
    indices = {r["name"] for r in conn.execute("PRAGMA index_list(lancamentos)")}
    assert "ix_lancamentos_recorrente" in indices

    backups = list((tmp_path / "backups").glob("meu-*-v0.db"))
    assert len(backups) == 1
    copia = sqlite3.connect(backups[0])
    assert copia.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2
    assert copia.execute("PRAGMA user_version").fetchone()[0] == 0  # o backup é o banco como estava
    assert "assinatura" not in {r[1] for r in copia.execute("PRAGMA table_info(recorrentes)")}
    copia.close()


def test_v3_para_v4_acrescenta_final_e_faturas_pagas_sem_perder_dados(tmp_path):
    caminho = tmp_path / "meu.db"
    conn = db.conectar(caminho)
    conn.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao) VALUES (1, '2026-10-01', -1500, 'Pão')")
    conn.execute("DROP TABLE faturas_pagas")
    conn.execute("ALTER TABLE contas DROP COLUMN final")
    _tirar_v5(conn)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()

    conn = db.conectar(caminho)
    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert "final" in _colunas(conn, "contas")
    assert conn.execute("SELECT final FROM contas").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 0
    assert conn.execute("SELECT descricao FROM lancamentos").fetchone()[0] == "Pão"
    assert len(list((tmp_path / "backups").glob("meu-*-v3.db"))) == 1


def test_v4_para_v5_cria_metas_meta_id_e_caixinhas_sem_perder_dados(tmp_path):
    caminho = tmp_path / "meu.db"
    conn = db.conectar(caminho)
    conn.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao) VALUES (1, '2026-10-01', -1500, 'Pão')")
    _tirar_v5(conn)
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    conn = db.conectar(caminho)
    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert conn.execute("SELECT descricao, meta_id FROM lancamentos").fetchone()[:] == ("Pão", None)
    assert conn.execute("SELECT COUNT(*) FROM metas").fetchone()[0] == 0
    assert conn.execute("SELECT nome, tipo, ativa FROM contas WHERE nome = 'Caixinhas'").fetchone()[:] == ("Caixinhas", "investimento", 1)
    assert conn.execute("SELECT tipo FROM categorias WHERE nome = 'Guardar em meta'").fetchone()[0] == "transferencia"
    assert "ix_lancamentos_meta" in {r["name"] for r in conn.execute("PRAGMA index_list(lancamentos)")}
    assert len(list((tmp_path / "backups").glob("meu-*-v4.db"))) == 1


def test_v5_para_v6_cria_investimentos_sem_perder_dados(tmp_path):
    caminho = tmp_path / "meu.db"
    conn = db.conectar(caminho)
    conn.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao) VALUES (1, '2026-10-01', -1500, 'Pão')")
    conn.execute("INSERT INTO metas (nome, valor_alvo_centavos, modo, prazo) VALUES ('Viagem', 100000, 'prazo', '2027-01-01')")
    n_contas = conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
    _tirar_v6(conn)
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()

    conn = db.conectar(caminho)
    assert migracoes.versao(conn) == 6
    assert conn.execute("SELECT descricao FROM lancamentos").fetchone()[0] == "Pão"
    assert conn.execute("SELECT nome FROM metas").fetchone()[0] == "Viagem"
    assert conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == n_contas
    assert conn.execute("SELECT nome, tipo, ativa FROM contas WHERE nome = 'Investimentos'").fetchone()[:] == ("Investimentos", "investimento", 1)
    assert conn.execute("SELECT COUNT(*) FROM ativos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM movimentacoes_ativos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM indicadores").fetchone()[0] == 0
    conn.execute("INSERT INTO ativos (nome, tipo, indexador, taxa) VALUES ('CDB', 'renda_fixa', 'cdi', 110)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ativos (nome, tipo) VALUES ('X', 'cripto')")
    assert len(list((tmp_path / "backups").glob("meu-*-v5.db"))) == 1


def test_v6_nao_duplica_a_conta_investimentos_que_ja_existe(tmp_path):
    caminho = tmp_path / "meu.db"
    conn = db.conectar(caminho)
    n = conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
    for tabela in ("movimentacoes_ativos", "ativos", "indicadores"):
        conn.execute(f"DROP TABLE {tabela}")  # a conta fica
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    conn = db.conectar(caminho)
    assert conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == n


def test_v5_nao_duplica_caixinhas_que_ja_existe(tmp_path):
    caminho = tmp_path / "meu.db"
    conn = db.conectar(caminho)
    n = conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
    conn.execute("DROP INDEX ix_lancamentos_meta")
    conn.execute("ALTER TABLE lancamentos DROP COLUMN meta_id")
    conn.execute("DROP TABLE metas")  # Caixinhas e a categoria ficam
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    conn = db.conectar(caminho)
    assert conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == n
    assert conn.execute("SELECT COUNT(*) FROM categorias WHERE nome = 'Guardar em meta'").fetchone()[0] == 1


def test_conectar_de_novo_nao_reaplica_nem_refaz_backup(tmp_path):
    caminho = tmp_path / "meu.db"
    _banco_antigo(caminho)
    db.conectar(caminho).close()
    db.conectar(caminho).close()
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_migracao_que_falha_volta_tudo_e_mantem_a_versao(tmp_path, monkeypatch):
    caminho = tmp_path / "meu.db"
    _banco_antigo(caminho)
    quebrada = {1: [], 2: ["ALTER TABLE recorrentes ADD COLUMN assinatura INTEGER NOT NULL DEFAULT 0", "COMANDO INVALIDO"]}
    monkeypatch.setattr(migracoes, "MIGRACOES", quebrada)
    monkeypatch.setattr(migracoes, "ULTIMA_VERSAO", 2)
    with pytest.raises(sqlite3.OperationalError):
        db.conectar(caminho)
    c = sqlite3.connect(caminho)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1  # a v1 (baseline) valeu; a v2 voltou inteira
    assert "assinatura" not in {r[1] for r in c.execute("PRAGMA table_info(recorrentes)")}
    assert c.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2


def test_banco_de_versao_mais_nova_e_recusado(tmp_path):
    caminho = tmp_path / "futuro.db"
    db.conectar(caminho).close()
    c = sqlite3.connect(caminho)
    c.execute(f"PRAGMA user_version = {migracoes.ULTIMA_VERSAO + 1}")
    c.close()
    with pytest.raises(RuntimeError, match="mais nova"):
        db.conectar(caminho)


def test_banco_em_memoria_nao_faz_backup():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migracoes.preparar(conn)
    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert migracoes.fazer_backup(conn, 0) is None
