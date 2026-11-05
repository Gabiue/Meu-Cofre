"""Gera um banco de DEMONSTRAÇÃO do Meu Cofre, com dados FALSOS (nada real).

Cobre todas as abas: lançamentos, cartão com fatura paga, recorrentes, metas e investimentos (renda fixa, Tesouro,
ações e FII). Serve pra testar a interface e tirar os prints do README sem mexer no banco de verdade.
Tudo passa pelas funções do pacote; só as taxas e cotações (caches que o app busca na internet) são inseridas direto.

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
from datetime import date, timedelta
from pathlib import Path

from meu_cofre import cadastros, faturas, indicadores, investimentos, metas, recorrentes, renda_variavel
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
        # Salário, aluguel, internet e assinaturas vêm das recorrentes (ver `gravar_recorrentes`)
        _adicionar(
            planos, hoje, _dia(mes, 12),
            entrada=False, valor=rng.randint(14_000, 22_000), descricao="Conta de luz",
            categoria="Contas da casa", metodo="boleto", conta="Inter", parcelas=1,
        )
        _adicionar(
            planos, hoje, _dia(mes, 20),
            entrada=False, valor=4_990, descricao="Celular",
            categoria="Contas da casa", metodo="debito", conta="Inter", parcelas=1,
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
                categoria=categoria, metodo=(metodo := rng.choice(metodos)),
                conta="Cartão de crédito" if metodo == "cartao" else "Inter", parcelas=1,
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


def _mapas(conn) -> tuple[dict[str, int], dict[str, int]]:
    contas = {linha["nome"]: linha["id"] for linha in listar_contas(conn)}
    categorias = {linha["nome"]: linha["id"] for linha in listar_categorias(conn)}
    return contas, categorias


def gravar_cartao_e_recorrentes(conn, hoje: date) -> None:
    """Dá datas ao cartão, cria as recorrentes e gera os lançamentos de cada mês."""
    contas, categorias = _mapas(conn)
    cadastros.definir_saldo_inicial(conn, contas["Inter"], 4_000_000)
    cadastros.editar_cartao(
        conn, contas["Cartão de crédito"], banco="Banco Exemplo", dia_fechamento=20, dia_vencimento=27, final="4321"
    )
    inicio = somar_meses(hoje.replace(day=1), -3)
    # (descrição, valor com sinal, dia, categoria, método, assinatura)
    for descricao, valor, dia, categoria, metodo, assinatura in [
        ("Salário", 452_000, 5, "Salário", "pix", False),
        ("Aluguel", -185_000, 10, "Moradia", "boleto", False),
        ("Internet", -10_990, 15, "Contas da casa", "debito", False),
        ("Streaming de vídeo", -3_990, 7, "Assinaturas", "cartao", True),
        ("Streaming de música", -2_190, 7, "Assinaturas", "cartao", True),
        ("Academia", -9_990, 3, "Saúde", "debito", True),
    ]:
        recorrentes.criar(
            conn, descricao=descricao, valor_centavos=valor, conta_id=contas["Inter"],
            categoria_id=categorias[categoria], metodo=metodo, dia_do_mes=dia, inicio=inicio, assinatura=assinatura,
        )
    recorrentes.gerar_pendentes(conn, hoje)


def pagar_faturas_vencidas(conn, hoje: date) -> None:
    """Paga as faturas do cartão que já venceram (transferência da conta pro cartão)."""
    contas, _ = _mapas(conn)
    cartao = contas["Cartão de crédito"]
    for fatura in faturas.faturas_do_cartao(conn, cartao, hoje):
        if fatura.vencimento <= hoje and fatura.total_centavos > 0 and not fatura.paga:
            faturas.pagar_fatura(conn, cartao, fatura.referencia, fatura.restante_centavos, contas["Inter"], fatura.vencimento)


def gravar_metas(conn, hoje: date) -> None:
    contas, _ = _mapas(conn)
    reserva = metas.criar(
        conn, hoje, nome="Reserva de emergência", valor_alvo_centavos=2_400_000, modo="mensal",
        mensal_centavos=80_000, reserva=True,
    )
    viagem = metas.criar(
        conn, hoje, nome="Viagem de fim de ano", valor_alvo_centavos=480_000, modo="prazo",
        prazo=somar_meses(hoje.replace(day=1), 8),
    )
    for meses_atras in (3, 2, 1):
        dia = min(somar_meses(hoje.replace(day=1), -meses_atras) + timedelta(days=8), hoje)
        metas.guardar(conn, reserva, 80_000, contas["Inter"], dia)
    metas.guardar(conn, viagem, 60_000, contas["Inter"], hoje - timedelta(days=40))
    metas.guardar(conn, viagem, 40_000, contas["Inter"], hoje - timedelta(days=12))


def _serie_de_taxas(hoje: date, inicio: date, anual_pct: float, rng: random.Random) -> list[tuple[date, float]]:
    """Taxa diária (% ao dia útil) equivalente a uma taxa anual que oscila um pouco."""
    pontos, dia = [], inicio
    while dia <= hoje:
        if investimentos.dia_util(dia):
            anual = anual_pct + rng.uniform(-0.02, 0.02)
            pontos.append((dia, round(((1 + anual / 100) ** (1 / 252) - 1) * 100, 6)))
        dia += timedelta(days=1)
    return pontos


def gravar_mercado(conn, hoje: date, rng: random.Random) -> None:
    """Taxas (CDI, Selic, IPCA) e cotações inventadas. Direto no SQL: são caches que o app só enche pela rede."""
    inicio = hoje - timedelta(days=200)
    ipca, mes = [], somar_meses(inicio.replace(day=1), -12)
    while mes <= hoje.replace(day=1):
        ipca.append((mes, round(rng.uniform(0.25, 0.55), 2)))
        mes = somar_meses(mes, 1)
    indicadores.gravar(conn, {
        "cdi": _serie_de_taxas(hoje, inicio, 14.4, rng),
        "selic": _serie_de_taxas(hoje, inicio, 14.5, rng),
        "ipca": ipca,
    })
    base = {"PETR4": 3_400, "ITUB4": 3_300, "HGLG11": 15_800}  # centavos
    with conn:
        for ticker, preco in base.items():
            dia = hoje - timedelta(days=180)
            while dia <= hoje:
                preco = max(500, round(preco * (1 + rng.uniform(-0.03, 0.035))))
                conn.execute("INSERT OR REPLACE INTO cotacoes (ticker, data, preco_centavos) VALUES (?, ?, ?)", (ticker, dia.isoformat(), preco))
                dia += timedelta(days=7)
            conn.execute("INSERT OR REPLACE INTO cotacoes (ticker, data, preco_centavos) VALUES (?, ?, ?)", (ticker, hoje.isoformat(), preco))
        # histórico de proventos por cota (base da estimativa dos próximos 12 meses)
        for ticker, valor, passo in (("HGLG11", 1.10, 1), ("PETR4", 0.85, 3), ("ITUB4", 0.40, 3)):
            mes = somar_meses(hoje.replace(day=1), -11)
            while mes <= hoje.replace(day=1):
                if (mes.month % passo == 0 or passo == 1) and _dia(mes, 15) <= hoje:
                    conn.execute(
                        "INSERT OR REPLACE INTO proventos_ref (ticker, data, valor_por_cota) VALUES (?, ?, ?)",
                        (ticker, _dia(mes, 15).isoformat(), valor),
                    )
                mes = somar_meses(mes, 1)


def gravar_investimentos(conn, hoje: date) -> None:
    contas, _ = _mapas(conn)
    inter = contas["Inter"]
    cdb = investimentos.criar(
        conn, hoje - timedelta(days=150), nome="CDB 110% do CDI", tipo="renda_fixa", instituicao="Banco Exemplo",
        indexador="cdi", taxa=110, vencimento=hoje + timedelta(days=700),
    )
    tesouro = investimentos.criar(
        conn, hoje - timedelta(days=150), nome="Tesouro IPCA+ 2035", tipo="tesouro", instituicao="Tesouro Direto",
        indexador="ipca", taxa=6.2, vencimento=date(hoje.year + 9, 5, 15),
    )
    for dias, valor in ((140, 300_000), (80, 150_000), (20, 150_000)):
        investimentos.aportar(conn, cdb, valor, inter, hoje - timedelta(days=dias))
    for dias, valor in ((130, 200_000), (45, 100_000)):
        investimentos.aportar(conn, tesouro, valor, inter, hoje - timedelta(days=dias))
    # aportes no dia 1 do mês atual: o "Aporte do mês" nunca sai zerado no print, em qualquer dia
    investimentos.aportar(conn, cdb, 70_000, inter, hoje.replace(day=1))
    investimentos.aportar(conn, tesouro, 50_000, inter, hoje.replace(day=1))

    acoes = {
        "PETR4": ("acao", [(120, 100, 3_300), (50, 50, 3_550)]),
        "ITUB4": ("acao", [(110, 80, 3_250)]),
        "HGLG11": ("fii", [(100, 10, 15_600), (30, 5, 16_000)]),
    }
    for ticker, (tipo, compras) in acoes.items():
        ativo = investimentos.criar(conn, hoje - timedelta(days=150), nome=ticker, tipo=tipo, ticker=ticker)
        for dias, quantidade, preco in compras:
            renda_variavel.comprar(conn, ativo, hoje - timedelta(days=dias), quantidade, preco, taxas=100, conta_id=inter)
        if ticker == "HGLG11":
            renda_variavel.registrar_provento(conn, ativo, "rendimento", hoje - timedelta(days=20), 1_650, conta_id=inter)
    investimentos.definir_meta_aporte(conn, 150_000)


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
        gravar_cartao_e_recorrentes(conn, hoje)
        gravar(conn, planos)
        pagar_faturas_vencidas(conn, hoje)
        gravar_metas(conn, hoje)
        gravar_mercado(conn, hoje, rng)
        gravar_investimentos(conn, hoje)
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
