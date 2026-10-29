"""Migrações do banco, controladas por `PRAGMA user_version`.

- Banco novo (sem tabelas): aplica `schema.sql` (sempre o esquema mais recente) e marca `ULTIMA_VERSAO`.
- Banco existente com versão menor: faz backup do arquivo e aplica as migrações pendentes em ordem,
  cada uma numa transação (se falhar, volta tudo e a versão não muda).

Para mudar o banco: edite `schema.sql`, acrescente uma migração em `MIGRACOES` com a mesma mudança
(versão = ULTIMA_VERSAO + 1) e o teste de migração em `tests/test_migracoes.py`.
"""

import sqlite3
from datetime import datetime
from importlib.resources import files
from pathlib import Path

# Cada migração é uma lista de comandos SQL (um por item, sem ponto e vírgula no fim).
MIGRACOES: dict[int, list[str]] = {
    # v1: baseline. Os bancos que já existiam (user_version 0) têm exatamente o esquema da v1.
    1: [],
    # v2: contas e cartões (F4) e recorrentes (F5).
    #  - assinatura / pausada: a aba "Assinaturas/fixos". O sinal (entrada ou saída) já vem no
    #    `valor_centavos` da recorrente, como nos lançamentos, então não há coluna `entrada`.
    #  - índice em lancamentos.recorrente_id: achar rápido o que uma recorrente já gerou.
    2: [
        "ALTER TABLE recorrentes ADD COLUMN assinatura INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE recorrentes ADD COLUMN pausada INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS ix_lancamentos_recorrente ON lancamentos (recorrente_id)",
    ],
    # v3: assinaturas/fixos (F5). `recorrentes_meses` guarda os meses que cada recorrente já gerou.
    #  Assim, se o lançamento de um mês for apagado, ele não é recriado (o lançamento some, a linha fica
    #  com lancamento_id NULL). Também marca os meses pulados durante uma pausa.
    3: [
        """CREATE TABLE IF NOT EXISTS recorrentes_meses (
    recorrente_id INTEGER NOT NULL REFERENCES recorrentes (id),
    mes           TEXT    NOT NULL,
    lancamento_id INTEGER REFERENCES lancamentos (id) ON DELETE SET NULL,
    PRIMARY KEY (recorrente_id, mes)
)""",
    ],
    # v4: cartões e faturas (F6).
    #  - contas.final: os 4 últimos dígitos do cartão ("Caixa Visa •••• 1234"), opcional.
    #  - faturas_pagas: uma linha por fatura paga ('AAAA-MM' = mês do vencimento). Liga a fatura à
    #    transferência conta -> cartão que a pagou (`transferencia_id`), pra poder desfazer.
    4: [
        "ALTER TABLE contas ADD COLUMN final TEXT",
        """CREATE TABLE IF NOT EXISTS faturas_pagas (
    id               INTEGER PRIMARY KEY,
    cartao_id        INTEGER NOT NULL REFERENCES contas (id),
    referencia       TEXT    NOT NULL,
    valor_centavos   INTEGER NOT NULL,
    data             TEXT    NOT NULL,
    conta_origem_id  INTEGER NOT NULL REFERENCES contas (id),
    transferencia_id TEXT    NOT NULL,
    UNIQUE (cartao_id, referencia)
)""",
    ],
}
ULTIMA_VERSAO = max(MIGRACOES)


def versao(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _banco_vazio(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone() is None


def _arquivo_do_banco(conn: sqlite3.Connection) -> Path | None:
    """Caminho do arquivo do banco; None se for em memória."""
    arquivo = conn.execute("PRAGMA database_list").fetchone()["file"]
    return Path(arquivo) if arquivo else None


def fazer_backup(conn: sqlite3.Connection, versao_antiga: int) -> Path | None:
    """Copia o banco para `<pasta>/backups/<nome>-AAAAMMDD-HHMMSS-v<versão>.db` (API de backup do SQLite).

    Devolve o caminho criado, ou None se o banco é em memória.
    """
    origem = _arquivo_do_banco(conn)
    if origem is None:
        return None
    pasta = origem.parent / "backups"
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = pasta / f"{origem.stem}-{carimbo}-v{versao_antiga}.db"
    copia = sqlite3.connect(destino)
    try:
        conn.backup(copia)
    finally:
        copia.close()
    return destino


def _aplicar(conn: sqlite3.Connection, numero: int) -> None:
    """Uma migração inteira numa transação: tudo ou nada."""
    conn.commit()
    conn.execute("BEGIN")
    try:
        for comando in MIGRACOES[numero]:
            conn.execute(comando)
        conn.execute(f"PRAGMA user_version = {numero}")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def preparar(conn: sqlite3.Connection) -> None:
    """Cria o banco novo ou leva o existente até a última versão. Não faz nada se já está nela."""
    atual = versao(conn)
    if _banco_vazio(conn):
        schema = files("meu_cofre").joinpath("schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema + f"\nPRAGMA user_version = {ULTIMA_VERSAO};")
        return
    if atual > ULTIMA_VERSAO:
        raise RuntimeError(
            f"Este banco é da versão {atual}, mais nova que a deste app ({ULTIMA_VERSAO}). Atualize o meu-cofre."
        )
    if atual == ULTIMA_VERSAO:
        return
    fazer_backup(conn, atual)
    for numero in range(atual + 1, ULTIMA_VERSAO + 1):
        _aplicar(conn, numero)
