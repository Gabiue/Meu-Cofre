"""Dados iniciais: contas e categorias. Só entram num banco novo (tabela vazia),
pra não ressuscitar o que você apagou ou renomeou."""

import sqlite3

CONTAS = [
    # nome, tipo, banco
    ("Inter", "corrente", "Inter"),
    ("Caixa", "corrente", "Caixa"),
    ("Cartão de crédito", "cartao", None),
    ("Carteira", "dinheiro", None),
    ("Caixinhas", "investimento", None),  # onde ficam os valores guardados das metas
    ("Investimentos", "investimento", None),  # onde ficam os aportes em renda fixa e Tesouro
]

CATEGORIAS = [
    # nome, tipo, natureza
    ("Moradia", "despesa", "essencial"),
    ("Contas da casa", "despesa", "essencial"),
    ("Mercado", "despesa", "essencial"),
    ("Transporte", "despesa", "essencial"),
    ("Saúde", "despesa", "essencial"),
    ("Educação", "despesa", "essencial"),
    ("Impostos e tarifas", "despesa", "essencial"),
    ("Alimentação fora", "despesa", "flexivel"),
    ("Compras pessoais", "despesa", "flexivel"),
    ("Cuidados pessoais", "despesa", "flexivel"),
    ("Presentes e doações", "despesa", "flexivel"),
    ("Lazer", "despesa", "flexivel"),
    ("Delivery", "despesa", "superfluo"),
    ("Assinaturas", "despesa", "superfluo"),
    ("Outros", "despesa", "flexivel"),
    ("Salário", "receita", None),
    ("Renda extra", "receita", None),
    ("Reembolsos", "receita", None),
    ("Rendimentos", "receita", None),
    ("Transferência entre contas", "transferencia", None),
    ("Pagamento de fatura", "transferencia", None),
    ("Guardar em meta", "transferencia", None),
    ("A categorizar", "despesa", None),
]


def aplicar(conn: sqlite3.Connection) -> None:
    with conn:
        if not conn.execute("SELECT 1 FROM contas LIMIT 1").fetchone():
            conn.executemany("INSERT INTO contas (nome, tipo, banco) VALUES (?, ?, ?)", CONTAS)
        if not conn.execute("SELECT 1 FROM categorias LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO categorias (nome, tipo, natureza) VALUES (?, ?, ?)", CATEGORIAS
            )
