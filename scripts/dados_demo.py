"""Gera um banco de DEMONSTRAÇÃO do Meu Cofre, com lançamentos FALSOS (nada real).

Serve pra testar os gráficos e o autocompletar sem mexer no banco de verdade.

Uso (na raiz do repositório):

    uv run python scripts/dados_demo.py
    uv run python scripts/dados_demo.py --db /tmp/demo.db --seed 7

Cobre os 3 meses anteriores ao mês atual, mais o mês atual até hoje.
Mesmo --seed e mesmo dia de execução geram exatamente os mesmos dados.

Para abrir o demo no app, use o comando que o próprio script imprime, algo como:

    MEU_COFRE_DB=<caminho> uv run meu-cofre

ATENÇÃO: se o arquivo informado em --db já existir, ele é APAGADO e recriado.
O script recusa o nome meu-cofre.db para nunca apagar o banco real.
"""

import argparse
import calendar
import random
import sys
from datetime import date
from pathlib import Path

from meu_cofre.db import conectar
from meu_cofre.lancamentos import listar_categorias, listar_contas, registrar, somar_meses

RAIZ = Path(__file__).resolve().parents[1]
DB_PADRAO = RAIZ / "data" / "demo.db"
NOME_BANCO_REAL = "meu-cofre.db"

# Gastos do dia a dia: (descrição, categoria, métodos possíveis, faixa em centavos).
# Descrições repetidas de propósito, pro autocompletar fazer sentido.
GASTOS_VARIAVEIS = [
    ("Supermercado", "Mercado", ["debito", "pix", "cartao"], (8_000, 35_000)),
    ("Feira livre", "Mercado", ["pix", "dinheiro"], (3_000, 9_000)),
    ("Padaria", "Mercado", ["dinheiro", "debito"], (800, 3_500)),
    ("Almoço", "Alimentação fora", ["pix", "debito", "dinheiro"], (2_200, 4_500)),
    ("Café", "Alimentação fora", ["pix", "dinheiro"], (500, 1_200)),
    ("Lanche", "Alimentação fora", ["pix", "dinheiro"], (1_200, 2_500)),
    ("iFood", "Delivery", ["cartao"], (3_000, 7_500)),
    ("Uber", "Transporte", ["pix", "cartao"], (1_200, 4_000)),
    ("Combustível", "Transporte", ["debito", "cartao"], (15_000, 28_000)),
    ("Cinema", "Lazer", ["cartao", "debito"], (3_000, 5_000)),
    ("Bar com amigos", "Lazer", ["cartao", "pix"], (4_000, 9_000)),
    ("Corte de cabelo", "Cuidados pessoais", ["pix", "debito"], (3_500, 6_000)),
    ("Farmácia", "Saúde", ["debito", "pix"], (2_000, 12_000)),
    ("Roupas", "Compras pessoais", ["cartao"], (6_000, 25_000)),
    ("Livraria", "Compras pessoais", ["cartao", "pix"], (3_000, 12_000)),
]


def _dia(mes: date, dia: int) -> date:
    """Dia do mês; se o mês não tem esse dia, usa o último."""
    ultimo = calendar.monthrange(mes.year, mes.month)[1]
    return date(mes.year, mes.month, min(dia, ultimo))


def _adicionar(planos: list[dict], hoje: date, data: date, **campos) -> dict | None:
    """Guarda um lançamento planejado, se a data já chegou."""
    if data > hoje:
        return None
    plano = {"data": data, **campos}
    planos.append(plano)
    return plano


def gerar_planos(rng: random.Random, hoje: date) -> list[dict]:
    """Monta a lista de lançamentos (sem gravar nada). Valores em centavos, positivos."""
    planos: list[dict] = []
    variaveis: list[dict] = []
    mes_atual = hoje.replace(day=1)
    meses = [somar_meses(mes_atual, deslocamento) for deslocamento in range(-3, 1)]

    for indice, mes in enumerate(meses):
        # Fixos do mês
        _adicionar(
            planos, hoje, _dia(mes, 5),
            entrada=True, valor=rng.randint(440_000, 465_000), descricao="Salário",
            categoria="Salário", metodo="pix", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 10),
            entrada=False, valor=185_000, descricao="Aluguel",
            categoria="Moradia", metodo="boleto", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 12),
            entrada=False, valor=rng.randint(14_000, 22_000), descricao="Conta de luz",
            categoria="Contas da casa", metodo="boleto", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 15),
            entrada=False, valor=10_990, descricao="Internet",
            categoria="Contas da casa", metodo="debito", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 20),
            entrada=False, valor=4_990, descricao="Celular",
            categoria="Contas da casa", metodo="debito", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 7),
            entrada=False, valor=3_990, descricao="Streaming de vídeo",
            categoria="Assinaturas", metodo="cartao", conta="Cartão de crédito", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 7),
            entrada=False, valor=2_190, descricao="Streaming de música",
            categoria="Assinaturas", metodo="cartao", conta="Cartão de crédito", parcelas=1,
        )

        # Renda extra, de vez em quando
        if rng.random() < 0.4:
            dia_extra = rng.randint(1, _ultimo_dia_util(mes, hoje))
            _adicionar(
                planos, hoje, date(mes.year, mes.month, dia_extra),
                entrada=True, valor=rng.randint(50_000, 180_000), descricao="Freelance",
                categoria="Renda extra", metodo="pix", conta="Inter", parcelas=1,
            )

        # Gastos variados
        for _ in range(rng.randint(12, 20)):
            descricao, categoria, metodos, (minimo, maximo) = rng.choice(GASTOS_VARIAVEIS)
            dia = rng.randint(1, _ultimo_dia_util(mes, hoje))
            plano = _adicionar(
                planos, hoje, date(mes.year, mes.month, dia),
                entrada=False, valor=rng.randint(minimo, maximo), descricao=descricao,
                categoria=categoria, metodo=rng.choice(metodos), conta="Inter", parcelas=1,
            )
            if plano:
                variaveis.append(plano)

        # Compra parcelada no cartão, só no primeiro mês
        if indice == 0:
            _adicionar(
                planos, hoje, _dia(mes, 14),
                entrada=False, valor=60_000, descricao="Tênis",
                categoria="Compras pessoais", metodo="cartao", conta="Cartão de crédito",
                parcelas=6,
            )

    # Dois ou três lançamentos sem categoria, de propósito
    for plano in rng.sample(variaveis, k=min(3, len(variaveis))):
        plano["categoria"] = None

    return sorted(planos, key=lambda p: p["data"])


def _ultimo_dia_util(mes: date, hoje: date) -> int:
    """Último dia do mês que já aconteceu (no mês atual, é hoje)."""
    if (mes.year, mes.month) == (hoje.year, hoje.month):
        return hoje.day
    return calendar.monthrange(mes.year, mes.month)[1]


def gravar(conn, planos: list[dict]) -> None:
    contas = {linha["nome"]: linha["id"] for linha in listar_contas(conn)}
    categorias = {linha["nome"]: linha["id"] for linha in listar_categorias(conn)}
    for p in planos:
        registrar(
            conn,
            entrada=p["entrada"],
            valor_centavos=p["valor"],
            descricao=p["descricao"],
            categoria_id=categorias[p["categoria"]] if p["categoria"] else None,
            metodo=p["metodo"],
            conta_id=contas[p["conta"]],
            data=p["data"],
            parcelas=p["parcelas"],
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gera um banco de demonstração com dados falsos.")
    parser.add_argument("--db", type=Path, default=DB_PADRAO, help="Arquivo do banco (será recriado).")
    parser.add_argument("--seed", type=int, default=42, help="Semente do gerador aleatório.")
    args = parser.parse_args(argv)

    caminho = args.db.expanduser().resolve()
    if caminho.name == NOME_BANCO_REAL:
        sys.exit(f"Recusado: '{NOME_BANCO_REAL}' é o banco real. Escolha outro --db.")

    if caminho.exists():
        caminho.unlink()

    hoje = date.today()
    rng = random.Random(args.seed)
    planos = gerar_planos(rng, hoje)

    conn = conectar(caminho)
    try:
        gravar(conn, planos)
        total = conn.execute("SELECT COUNT(*) AS n FROM lancamentos").fetchone()["n"]
    finally:
        conn.close()

    inicio = planos[0]["data"] if planos else hoje
    print(f"Banco de demonstração gerado: {caminho}")
    print(f"Lançamentos: {total} (de {inicio.isoformat()} até {hoje.isoformat()})")
    print("Para abrir no app:")
    print(f"  MEU_COFRE_DB={caminho} uv run meu-cofre")


if __name__ == "__main__":
    main()
