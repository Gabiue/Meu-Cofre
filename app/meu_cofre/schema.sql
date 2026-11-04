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
    final                  TEXT,                       -- 4 últimos dígitos do cartão (opcional)
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

-- Meses que cada recorrente já gerou ('AAAA-MM'). Apagar o lançamento gerado não faz o mês voltar a ser gerado
-- (lancamento_id vira NULL); meses pulados numa pausa também ficam aqui, com lancamento_id NULL.
CREATE TABLE IF NOT EXISTS recorrentes_meses (
    recorrente_id INTEGER NOT NULL REFERENCES recorrentes (id),
    mes           TEXT    NOT NULL,
    lancamento_id INTEGER REFERENCES lancamentos (id) ON DELETE SET NULL,
    PRIMARY KEY (recorrente_id, mes)
);

-- Pagamentos de fatura de cartão (uma fatura pode ter vários: pagamento parcial). `referencia` = mês do
-- vencimento ('AAAA-MM'); `transferencia_id` liga à transferência conta -> cartão que pagou (apagar os dois
-- lados desfaz o pagamento).
CREATE TABLE IF NOT EXISTS faturas_pagas (
    id               INTEGER PRIMARY KEY,
    cartao_id        INTEGER NOT NULL REFERENCES contas (id),
    referencia       TEXT    NOT NULL,
    valor_centavos   INTEGER NOT NULL,
    data             TEXT    NOT NULL,
    conta_origem_id  INTEGER NOT NULL REFERENCES contas (id),
    transferencia_id TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_faturas_pagas_fatura ON faturas_pagas (cartao_id, referencia);

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

-- Metas (F7). `modo` 'prazo' = valor + data (o app calcula o mensal); 'mensal' = valor + quanto guarda por mês
-- (o app calcula a data). `prioridade` menor = mais importante. O dinheiro guardado fica na conta "Caixinhas"
-- (tipo investimento): cada guardar/retirar é uma transferência com `lancamentos.meta_id` nos dois lados.
CREATE TABLE IF NOT EXISTS metas (
    id                  INTEGER PRIMARY KEY,
    nome                TEXT    NOT NULL,
    valor_alvo_centavos INTEGER NOT NULL CHECK (valor_alvo_centavos > 0),
    modo                TEXT    NOT NULL CHECK (modo IN ('prazo', 'mensal')),
    prazo               TEXT,                          -- ISO; só no modo 'prazo'
    mensal_centavos     INTEGER,                       -- só no modo 'mensal'
    prioridade          INTEGER NOT NULL DEFAULT 0,
    reserva             INTEGER NOT NULL DEFAULT 0,    -- 1 = reserva de emergência
    criada_em           TEXT    NOT NULL DEFAULT (date('now', 'localtime')),
    concluida_em        TEXT,
    arquivada           INTEGER NOT NULL DEFAULT 0
);

-- Investimentos (F8). `indexador` + `taxa`: cdi/selic = % do índice (100 = 100% do CDI); ipca = taxa real somada ao
-- IPCA (6.2 = IPCA + 6,2%); pre = % ao ano. `caixinhas` = 1 marca o ativo que faz render o dinheiro das metas.
CREATE TABLE IF NOT EXISTS ativos (
    id          INTEGER PRIMARY KEY,
    nome        TEXT    NOT NULL,
    tipo        TEXT    NOT NULL CHECK (tipo IN ('renda_fixa', 'tesouro', 'acao', 'fii')),
    instituicao TEXT,
    indexador   TEXT    CHECK (indexador IN ('cdi', 'selic', 'ipca', 'pre')),
    taxa        REAL,
    vencimento  TEXT,
    caixinhas   INTEGER NOT NULL DEFAULT 0,
    ativo       INTEGER NOT NULL DEFAULT 1,
    criado_em   TEXT    NOT NULL DEFAULT (date('now', 'localtime')),
    ticker      TEXT
);

-- Renda variável (F9): ações e FIIs têm ticker (único) em vez de indexador e taxa.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ativos_ticker ON ativos (ticker) WHERE ticker IS NOT NULL;

-- aporte/resgate ligam-se à transferência conta <-> "Investimentos"; saldo_real é o saldo que o banco mostra.
CREATE TABLE IF NOT EXISTS movimentacoes_ativos (
    id               INTEGER PRIMARY KEY,
    ativo_id         INTEGER NOT NULL REFERENCES ativos (id),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('aporte', 'resgate', 'saldo_real')),
    data             TEXT    NOT NULL,
    valor_centavos   INTEGER NOT NULL,
    transferencia_id TEXT
);

CREATE INDEX IF NOT EXISTS ix_movimentacoes_ativo ON movimentacoes_ativos (ativo_id);

-- Séries públicas do Banco Central: cdi e selic (% ao dia, dias úteis) e ipca (% ao mês, dia 1 do mês).
CREATE TABLE IF NOT EXISTS indicadores (
    serie TEXT NOT NULL,
    data  TEXT NOT NULL,
    valor REAL NOT NULL,
    PRIMARY KEY (serie, data)
);

-- Compras, vendas e proventos de ações e FIIs. `afeta_conta` = 1: mexeu numa conta (transferência com a conta
-- "Investimentos" ou entrada de provento, ligadas por `transferencia_id` / `lancamento_id`); 0: só a posição (histórico
-- importado). `valor_centavos` = quantidade × preço (compra/venda, sem as taxas) ou o total recebido (provento).
-- `chave_dedup` evita gravar duas vezes a mesma linha da planilha da B3 (NULL nas operações manuais).
CREATE TABLE IF NOT EXISTS operacoes_rv (
    id               INTEGER PRIMARY KEY,
    ativo_id         INTEGER NOT NULL REFERENCES ativos (id),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('compra', 'venda', 'dividendo', 'jcp', 'rendimento')),
    data             TEXT    NOT NULL,
    quantidade       REAL,
    preco_centavos   INTEGER,
    valor_centavos   INTEGER NOT NULL,
    taxas_centavos   INTEGER NOT NULL DEFAULT 0,
    afeta_conta      INTEGER NOT NULL DEFAULT 0,
    transferencia_id TEXT,
    lancamento_id    INTEGER REFERENCES lancamentos (id) ON DELETE SET NULL,
    origem           TEXT    NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'b3')),
    chave_dedup      TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_operacoes_rv_ativo ON operacoes_rv (ativo_id);

-- Cotações (fechamento diário) de ações e FIIs, buscadas em fonte pública, e o histórico público de proventos por cota
-- (base da estimativa de proventos futuros).
CREATE TABLE IF NOT EXISTS cotacoes (
    ticker         TEXT    NOT NULL,
    data           TEXT    NOT NULL,
    preco_centavos INTEGER NOT NULL,
    PRIMARY KEY (ticker, data)
);

CREATE TABLE IF NOT EXISTS proventos_ref (
    ticker         TEXT NOT NULL,
    data           TEXT NOT NULL,
    valor_por_cota REAL NOT NULL,
    PRIMARY KEY (ticker, data)
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
    meta_id            INTEGER REFERENCES metas (id), -- guardar/retirar de uma meta (os dois lados)
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
CREATE INDEX IF NOT EXISTS ix_lancamentos_meta ON lancamentos (meta_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lancamentos_fitid ON lancamentos (conta_id, fitid) WHERE fitid IS NOT NULL;

CREATE TABLE IF NOT EXISTS regras_categorizacao (
    id           INTEGER PRIMARY KEY,
    padrao       TEXT    NOT NULL,
    tipo_match   TEXT    NOT NULL DEFAULT 'contem' CHECK (tipo_match IN ('contem', 'regex')),
    categoria_id INTEGER NOT NULL REFERENCES categorias (id),
    prioridade   INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS preferencias (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
