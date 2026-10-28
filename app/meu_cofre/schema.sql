-- meu-cofre: esquema ATUAL do banco (SQLite), aplicado só em banco novo.
-- Bancos existentes chegam aqui pelas migrações (migracoes.py): ao mudar este arquivo,
-- crie também a migração correspondente e suba ULTIMA_VERSAO.
-- Valores sempre em centavos (inteiro). Saída é negativa, entrada é positiva.

PRAGMA foreign_keys = ON;

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
    valor_centavos INTEGER NOT NULL,                  -- com sinal, como nos lançamentos (entrada > 0)
    conta_id       INTEGER NOT NULL REFERENCES contas (id),
    categoria_id   INTEGER REFERENCES categorias (id),
    metodo         TEXT,
    dia_do_mes     INTEGER NOT NULL CHECK (dia_do_mes BETWEEN 1 AND 31),
    inicio         TEXT    NOT NULL,
    fim            TEXT,
    ativa          INTEGER NOT NULL DEFAULT 1,
    assinatura     INTEGER NOT NULL DEFAULT 0,    -- 1 = assinatura; 0 = gasto fixo (aluguel, salário...)
    pausada        INTEGER NOT NULL DEFAULT 0     -- pausada não gera lançamentos; `ativa` = 0 é encerrada
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
CREATE INDEX IF NOT EXISTS ix_lancamentos_recorrente ON lancamentos (recorrente_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lancamentos_fitid ON lancamentos (conta_id, fitid) WHERE fitid IS NOT NULL;

CREATE TABLE IF NOT EXISTS regras_categorizacao (
    id           INTEGER PRIMARY KEY,
    padrao       TEXT    NOT NULL,
    tipo_match   TEXT    NOT NULL DEFAULT 'contem' CHECK (tipo_match IN ('contem', 'regex')),
    categoria_id INTEGER NOT NULL REFERENCES categorias (id),
    prioridade   INTEGER NOT NULL DEFAULT 100
);
