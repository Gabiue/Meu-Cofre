"""Relatório mensal (issue #9): números de cada seção, histórico, mês parcial e privacidade. Sem Qt, dados falsos.

Cenário: hoje = 20/10/2026, relatório de setembro (base = jun, jul e ago). Os centavos são escolhidos pra
dar contas redondas.
"""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import faturas, investimentos, metas, recorrentes
from meu_cofre import lancamentos as lanc
from meu_cofre.relatorio_mensal import descricao_publica, gerar

HOJE = date(2026, 10, 20)


def _l(conn, entrada, centavos, dia, cat, descricao="x", metodo="pix", conta="Inter"):
    lanc.registrar(
        conn, entrada=entrada, valor_centavos=centavos, descricao=descricao, categoria_id=id_cat(conn, cat),
        metodo=metodo, conta_id=id_conta(conn, conta), data=dia,
    )


def _base(conn, mercado=30_000, lazer=10_000, delivery=2_000, cafes=2):
    """jun, jul e ago iguais: salário R$ 5.000, e os gastos pedidos (Mercado, Lazer, Delivery e `cafes` cafés de R$ 5)."""
    for m in (6, 7, 8):
        _l(conn, True, 500_000, date(2026, m, 5), "Salário", "Salário")
        _l(conn, False, mercado, date(2026, m, 10), "Mercado", "Feira")
        _l(conn, False, lazer, date(2026, m, 12), "Lazer", "Cinema")
        _l(conn, False, delivery, date(2026, m, 14), "Delivery", "iFood")
        for i in range(cafes):
            _l(conn, False, 500, date(2026, m, 15 + i), "Alimentação fora", "Café")


def _setembro(conn, mercado=40_000, lazer=10_000, delivery=2_000, cafes=2):
    _l(conn, True, 500_000, date(2026, 9, 5), "Salário", "Salário")  # sábado
    _l(conn, False, mercado, date(2026, 9, 12), "Mercado", "Feira")  # sábado
    _l(conn, False, lazer, date(2026, 9, 3), "Lazer", "Cinema")  # quinta
    _l(conn, False, delivery, date(2026, 9, 14), "Delivery", "iFood")  # segunda
    for i in range(cafes):
        _l(conn, False, 500, date(2026, 9, 15 + i), "Alimentação fora", "Café")


def _relatorio(conn, contexto=""):
    return gerar(conn, 2026, 9, HOJE, contexto)


def _linha_com(texto, trecho):
    return next(linha for linha in texto.splitlines() if trecho in linha)


# --- Resumo ------------------------------------------------------------------------------------------------------------


def test_resumo_poupanca_e_fixos(conn):
    _base(conn)
    _setembro(conn)
    inter = id_conta(conn, "Inter")
    recorrentes.criar(conn, descricao="Aluguel", valor_centavos=-120_000, conta_id=inter, categoria_id=id_cat(conn, "Moradia"),
                      metodo="boleto", dia_do_mes=10, inicio=date(2026, 1, 1), assinatura=False)
    recorrentes.criar(conn, descricao="Salário", valor_centavos=500_000, conta_id=inter, categoria_id=id_cat(conn, "Salário"),
                      metodo="pix", dia_do_mes=5, inicio=date(2026, 1, 1), assinatura=False)  # entrada não é fixo
    recorrentes.criar(conn, descricao="Academia", valor_centavos=-50_000, conta_id=inter, categoria_id=id_cat(conn, "Lazer"),
                      metodo="boleto", dia_do_mes=1, inicio=date(2026, 10, 1), assinatura=True)  # começa depois de setembro
    t = _relatorio(conn)
    assert "- Entrou: R$ 5.000,00" in t
    assert "- Saiu: R$ 530,00" in t  # 400 + 100 + 20 + 2 cafés de 5
    assert "- Sobrou: R$ 4.470,00" in t
    assert "- Taxa de poupança: 89%" in t  # 4.470 / 5.000 = 89,4%
    assert "R$ 1.200,00 = 24% da renda" in t


def test_transferencias_fatura_e_aportes_ficam_fora(conn):
    _base(conn)
    _setembro(conn)
    inter = id_conta(conn, "Inter")
    cartao = id_conta(conn, "Cartão de crédito")
    _l(conn, False, 20_000, date(2026, 9, 8), "Mercado", "Compra", metodo="cartao", conta="Cartão de crédito")
    faturas.pagar_fatura(conn, cartao, "2026-09", 20_000, inter, date(2026, 9, 10))
    lanc.transferir(conn, origem_id=inter, destino_id=id_conta(conn, "Caixa"), valor_centavos=70_000, data=date(2026, 9, 11))
    meta = metas.criar(conn, HOJE, nome="Viagem", valor_alvo_centavos=300_000, modo="mensal", mensal_centavos=50_000)
    metas.guardar(conn, meta, 30_000, inter, date(2026, 9, 12))
    ativo = investimentos.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100)
    investimentos.aportar(conn, ativo, 40_000, inter, date(2026, 9, 13))
    t = _relatorio(conn)
    assert "- Entrou: R$ 5.000,00" in t
    assert "- Saiu: R$ 730,00" in t  # os R$ 530 + só a compra no cartão (R$ 200)
    assert "| Mercado | R$ 600,00 |" in t


# --- Categorias -------------------------------------------------------------------------------------------------------


def test_categorias_com_variacao_e_ordem(conn):
    _base(conn)
    _setembro(conn)
    t = _relatorio(conn)
    assert "Base de comparação: média de 3 meses completos (ago/2026, jul/2026, jun/2026)." in t
    assert "| Mercado | R$ 400,00 | R$ 300,00 | +33% |" in t
    assert "| Lazer | R$ 100,00 | R$ 100,00 | +0% |" in t
    assert "| Alimentação fora | R$ 10,00 | R$ 10,00 | +0% |" in t
    assert t.index("| Mercado |") < t.index("| Lazer |") < t.index("| Delivery |")


def test_sem_historico_nao_inventa_numero(conn):
    _setembro(conn)  # nenhum mês antes
    t = _relatorio(conn)
    assert "Base de comparação: ainda sem histórico." in t
    assert "| Mercado | R$ 400,00 | — | — |" in t
    assert "Categorias e frequência acima da média: ainda sem histórico." in t
    assert "Nada disparou" not in t
    assert "+" not in "".join(linha for linha in t.splitlines() if linha.startswith("|"))


def test_primeiro_mes_de_uso_tardio_nao_entra_na_base(conn):
    _l(conn, False, 20_000, date(2026, 6, 20), "Mercado")  # 1º lançamento, depois do dia 5: junho fica fora
    _l(conn, False, 30_000, date(2026, 7, 3), "Mercado")
    _l(conn, False, 10_000, date(2026, 8, 28), "Mercado")
    _setembro(conn)
    assert "média de 2 meses completos (ago/2026, jul/2026)" in _relatorio(conn)


# --- Fora do padrão ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mercado", "dispara"),
    [(44_999, False), (45_000, True)],  # 1,5x de R$ 300 = R$ 450; R$ 150 acima, folgado
)
def test_categoria_dispara_em_1_5_vezes(conn, mercado, dispara):
    _base(conn)
    _setembro(conn, mercado=mercado)
    assert ("Categorias bem acima da média:" in _relatorio(conn)) is dispara


@pytest.mark.parametrize(
    ("delivery", "dispara"),
    [(6_999, False), (7_000, True)],  # média R$ 20: 1,5x = R$ 30, mas precisa de R$ 50 acima = R$ 70
)
def test_categoria_dispara_so_com_50_reais_acima(conn, delivery, dispara):
    _base(conn)
    _setembro(conn, delivery=delivery)
    t = _relatorio(conn)
    assert ("- Delivery: " in t) is dispara
    if dispara:
        assert "- Delivery: R$ 70,00 (média R$ 20,00, +250%)" in t


@pytest.mark.parametrize(
    ("cafes_base", "cafes_mes", "dispara"),
    [(2, 4, True), (2, 3, False), (3, 5, False), (3, 6, True), (0, 4, True), (0, 3, False)],
)
def test_frequencia_dispara_em_2_vezes_e_4_no_mes(conn, cafes_base, cafes_mes, dispara):
    _base(conn, cafes=cafes_base)
    _setembro(conn, cafes=cafes_mes)
    t = _relatorio(conn)
    assert ("Compras que dispararam em frequência:" in t) is dispara
    if dispara:
        assert f"- Café: {cafes_mes} vezes no mês (média {cafes_base},0)" in t


def test_assinatura_nova_do_mes(conn):
    _base(conn)
    _setembro(conn)
    inter = id_conta(conn, "Inter")
    recorrentes.criar(conn, descricao="Streaming", valor_centavos=-3_990, conta_id=inter, categoria_id=id_cat(conn, "Assinaturas"),
                      metodo="boleto", dia_do_mes=15, inicio=date(2026, 9, 15), assinatura=True)
    recorrentes.criar(conn, descricao="Plano antigo", valor_centavos=-2_000, conta_id=inter, categoria_id=id_cat(conn, "Assinaturas"),
                      metodo="boleto", dia_do_mes=15, inicio=date(2026, 5, 15), assinatura=True)
    t = _relatorio(conn)
    assert "Assinaturas e fixos novos:" in t
    assert "- Streaming: R$ 39,90/mês (Assinaturas)" in t
    assert "Plano antigo" not in _linha_com(t, "Streaming") and "- Plano antigo" not in t


def test_nada_disparou(conn):
    _base(conn)
    _setembro(conn, mercado=30_000)
    t = _relatorio(conn)
    assert "Nada disparou" in t
    assert "Os 3 maiores gastos do mês (fora os fixos):" in t


def test_tres_maiores_gastos(conn):
    _base(conn)
    _setembro(conn)
    _l(conn, False, 90_000, date(2026, 9, 20), "Outros", "Notebook")
    _l(conn, False, 80_000, date(2026, 9, 21), "Outros", "Geladeira")
    t = _relatorio(conn).split("Os 3 maiores gastos do mês (fora os fixos):")[1].split("##")[0]
    assert t.strip().splitlines() == [
        "1. Notebook: R$ 900,00 (Outros, 20/09)",
        "2. Geladeira: R$ 800,00 (Outros, 21/09)",
        "3. Feira: R$ 400,00 (Mercado, 12/09)",
    ]


# --- Onde e quando ----------------------------------------------------------------------------------------------------


def test_top_5_descricoes_dia_da_semana_e_frase(conn):
    _base(conn)
    _setembro(conn)
    _l(conn, False, 30_000, date(2026, 9, 26), "Mercado", "Feira")  # sábado, 2ª vez: Feira = R$ 700
    t = _relatorio(conn)
    topo = t.split("Onde mais gastou (por descrição, fora os fixos):")[1].split("Dia da semana")[0].strip().splitlines()
    assert topo == [
        "1. Feira: R$ 700,00 em 2 vezes",
        "2. Cinema: R$ 100,00 em 1 vez",
        "3. iFood: R$ 20,00 em 1 vez",
        "4. Café: R$ 10,00 em 2 vezes",
    ]
    assert "| Dia | Mercado | Lazer | Delivery |" in t
    assert "| Sábado | R$ 700,00 | R$ 0,00 | R$ 0,00 |" in t
    assert "| Quinta | R$ 0,00 | R$ 100,00 | R$ 0,00 |" in t
    assert "| Segunda | R$ 0,00 | R$ 0,00 | R$ 20,00 |" in t
    assert "Sábado concentra 100% de Mercado." in t


def test_frase_do_dia_com_fatia_parcial(conn):
    _base(conn)
    _setembro(conn, mercado=30_000)  # sábado 12/09
    _l(conn, False, 10_000, date(2026, 9, 16), "Mercado", "Feira")  # quarta
    assert "Sábado concentra 75% de Mercado." in _relatorio(conn)


# --- Metas e investimentos --------------------------------------------------------------------------------------------


def test_metas_aporte_do_mes_e_patrimonio(conn):
    _base(conn)
    _setembro(conn)
    inter = id_conta(conn, "Inter")
    meta = metas.criar(conn, HOJE, nome="Viagem", valor_alvo_centavos=300_000, modo="mensal", mensal_centavos=50_000)
    metas.guardar(conn, meta, 30_000, inter, date(2026, 9, 12))
    ativo = investimentos.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100)
    investimentos.aportar(conn, ativo, 40_000, inter, date(2026, 9, 13))
    investimentos.aportar(conn, ativo, 90_000, inter, date(2026, 10, 2))  # outro mês: não conta em setembro
    investimentos.definir_meta_aporte(conn, 100_000)
    t = _relatorio(conn)
    assert "- Viagem: R$ 300,00 de R$ 3.000,00 (10%)" in t
    assert "Aportes no mês: R$ 700,00 para uma meta de aporte de R$ 1.000,00 (faltaram R$ 300,00)" in t
    assert "- CDB: R$ 400,00" in t
    assert "- Caixinhas (metas): R$ 300,00" in t
    assert f"Patrimônio guardado hoje: R$ {investimentos.patrimonio(conn, HOJE)['total'] // 100:,}".replace(",", ".") in t
    assert "Previsão para 31/12/2026" in t


def test_sem_metas_nem_aportes(conn):
    _base(conn)
    _setembro(conn)
    t = _relatorio(conn)
    assert "Nenhuma meta ativa." in t
    assert "Aportes no mês: R$ 0,00 (sem meta de aporte definida)" in t


# --- Mês parcial, contexto e pedido ----------------------------------------------------------------------------------


def test_mes_atual_sai_parcial_e_corta_depois_de_hoje(conn):
    _base(conn)
    _l(conn, True, 500_000, date(2026, 10, 5), "Salário")
    _l(conn, False, 10_000, date(2026, 10, 20), "Mercado")
    _l(conn, False, 99_900, date(2026, 10, 21), "Mercado", "Amanhã")  # depois de hoje
    t = gerar(conn, 2026, 10, HOJE)
    assert t.startswith("# Relatório financeiro: Outubro de 2026 (parcial, até 20/10)")
    assert "- Saiu: R$ 100,00" in t
    assert "(parcial" not in _relatorio(conn)


def test_contexto_vai_no_relatorio_e_some_se_vazio(conn):
    _base(conn)
    _setembro(conn)
    assert "## Sobre mim\nTrabalho em casa.\nMoro com a família." in _relatorio(conn, "  Trabalho em casa.\nMoro com a família.  ")
    assert "Sobre mim" not in _relatorio(conn, "   ")


def test_pedido_pra_ia_no_fim(conn):
    _base(conn)
    _setembro(conn)
    fim = _relatorio(conn).split("## Pedido\n")[1]
    assert "padrões" in fim and "perguntas" in fim and "sermão" in fim and "parcial" in fim


# --- Privacidade ------------------------------------------------------------------------------------------------------


def test_pix_pra_pessoa_nunca_mostra_o_nome(conn):
    _base(conn)
    _setembro(conn)
    _l(conn, False, 95_000, date(2026, 9, 20), "Outros", "Pix enviado: Cp :123-FULANO DE TAL")
    _l(conn, False, 85_000, date(2026, 9, 21), "Presentes e doações", "Churrasco do Joãozinho")
    for dia in (22, 23, 24, 25):  # frequência: 4 vezes, nada na base
        _l(conn, False, 100, date(2026, 9, dia), "Outros", "Pix enviado: Cp :999-BELTRANA SILVA")
    t = _relatorio(conn)
    for nome in ("FULANO", "Joãozinho", "BELTRANA", "Churrasco", "Cp :"):
        assert nome not in t
    assert "1. Pix enviado: R$ 950,00 (Outros, 20/09)" in t
    assert "2. Pix enviado: R$ 850,00 (Presentes e doações, 21/09)" in t
    assert "- Pix enviado: 6 vezes no mês (média 0,0)" in t


@pytest.mark.parametrize(
    ("descricao", "categoria", "tipo", "metodo", "centavos", "esperado"),
    [
        ("Pix enviado: Cp :123-FULANO DE TAL", "Outros", "despesa", "pix", -100, "Pix enviado"),
        ("PIX ENVIADO: fulano", "Outros", "despesa", None, -100, "Pix enviado"),
        ("Pix recebido: Empresa Ficticia Ltda", "Salário", "receita", "pix", 100, "Pix recebido"),
        ("Rifa", "Dinheiro recebido", "receita", "pix", 100, "Pix recebido"),
        ("Ajuda do tio", "Renda extra", "receita", "pix", 100, "Pix recebido"),
        ("Conta do bar", "Reembolsos", "receita", "pix", 100, "Pix recebido"),
        ("Presente da Ana", "Presentes e doações", "despesa", "pix", -100, "Pix enviado"),
        ("Presente da Ana", "Presentes e doações", "despesa", "cartao", -100, "Presente da Ana"),  # não é pix
        ("Empresa X", "Salário", "receita", "pix", 100, "Empresa X"),
        ("Aplicação", "Rendimentos", "receita", "pix", 100, "Aplicação"),
        ("Pix pro mercado", "Mercado", "despesa", "pix", -100, "Pix pro mercado"),
        ("Supermercado", "Mercado", "despesa", "pix", -100, "Supermercado"),
    ],
)
def test_descricao_publica(descricao, categoria, tipo, metodo, centavos, esperado):
    assert descricao_publica(descricao, categoria, tipo, metodo, centavos) == esperado


def test_fixos_ficam_fora_de_onde_e_quando_e_dos_maiores(conn):
    _base(conn)
    _setembro(conn)
    rec = recorrentes.criar(conn, descricao="Aluguel", valor_centavos=-150_000, conta_id=id_conta(conn, "Inter"),
                            categoria_id=id_cat(conn, "Moradia"), metodo="boleto", dia_do_mes=10,
                            inicio=date(2026, 6, 1), assinatura=False)
    recorrentes.gerar_pendentes(conn, HOJE)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE recorrente_id = ?", (rec,)).fetchone()[0]
    t = _relatorio(conn)
    resto = t.split("## Fora do padrão")[1].split("## Metas")[0]
    assert "Aluguel" not in resto and "concentra 100% de Moradia" not in t
