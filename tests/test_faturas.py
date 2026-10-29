"""Faturas de cartão (F6): regra da fatura, totais, parcelas, pagar e desfazer, gastei no mês, comprometido."""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import cadastros, faturas, recorrentes, relatorios
from meu_cofre import lancamentos as lanc

HOJE = date(2026, 10, 29)


def _compra(conn, cartao, valor, dia, descricao="Compra", parcelas=1, categoria="Compras pessoais"):
    return lanc.registrar(
        conn, entrada=False, valor_centavos=valor, descricao=descricao, categoria_id=id_cat(conn, categoria),
        metodo="cartao", conta_id=cartao, data=dia, parcelas=parcelas,
    )


def _visa(conn, fechamento=1, vencimento=10, final="1234"):
    return cadastros.criar_cartao(conn, "Caixa Visa", "Caixa", fechamento, vencimento, final)


def _por_ref(lista):
    return {f.referencia: f for f in lista}


# --- Regra da fatura (sem banco) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "dia, esperado",
    [
        (date(2026, 10, 1), "2026-11"),   # dia 1 é o primeiro dia da fatura de novembro
        (date(2026, 10, 15), "2026-11"),
        (date(2026, 10, 31), "2026-11"),
        (date(2026, 11, 1), "2026-12"),   # a compra do dia do fechamento já é da fatura seguinte
        (date(2026, 9, 30), "2026-10"),
        (date(2026, 12, 31), "2027-01"),
    ],
)
def test_referencia_fecha_dia_1_vence_dia_10(dia, esperado):
    assert faturas.referencia_da_compra(dia, 1, 10) == esperado


def test_periodo_e_vencimento_fecha_1_vence_10():
    assert faturas.periodo("2026-11", 1, 10) == (date(2026, 10, 1), date(2026, 11, 1))
    assert faturas.vencimento("2026-11", 10) == date(2026, 11, 10)
    assert faturas.periodo("2027-01", 1, 10) == (date(2026, 12, 1), date(2027, 1, 1))


@pytest.mark.parametrize(
    "dia, esperado",
    [
        (date(2026, 10, 24), "2026-11"),  # antes do fechamento (25/10): vence 05/11
        (date(2026, 10, 25), "2026-12"),  # no dia do fechamento já é da próxima
        (date(2026, 9, 25), "2026-11"),
        (date(2026, 9, 24), "2026-10"),
        (date(2026, 12, 26), "2027-02"),
    ],
)
def test_referencia_fecha_25_vence_5_vira_o_mes(dia, esperado):
    assert faturas.referencia_da_compra(dia, 25, 5) == esperado


def test_periodo_e_vencimento_fecha_25_vence_5():
    assert faturas.periodo("2026-11", 25, 5) == (date(2026, 9, 25), date(2026, 10, 25))
    assert faturas.vencimento("2026-11", 5) == date(2026, 11, 5)
    assert faturas.periodo("2027-01", 25, 5) == (date(2026, 11, 25), date(2026, 12, 25))


def test_fecha_dia_10_vence_dia_20_mesmo_mes():
    # V > F: vence no mês do fechamento. Compra de 15/10 fecha em 10/11 e vence 20/11.
    assert faturas.referencia_da_compra(date(2026, 10, 15), 10, 20) == "2026-11"
    assert faturas.referencia_da_compra(date(2026, 10, 9), 10, 20) == "2026-10"
    assert faturas.periodo("2026-11", 10, 20) == (date(2026, 10, 10), date(2026, 11, 10))
    assert faturas.vencimento("2026-11", 20) == date(2026, 11, 20)


def test_sem_fechamento_a_fatura_e_o_mes_do_calendario():
    assert faturas.referencia_da_compra(date(2026, 10, 3), None) == "2026-11"
    assert faturas.referencia_da_compra(date(2026, 10, 31), None, 15) == "2026-11"
    assert faturas.periodo("2026-11", None) == (date(2026, 10, 1), date(2026, 11, 1))
    assert faturas.vencimento("2026-11", None) == date(2026, 11, 10)  # padrão: dia 10
    assert faturas.vencimento("2026-11", 15) == date(2026, 11, 15)


def test_dia_que_nao_existe_no_mes_vira_o_ultimo():
    # fecha 31, vence 10 (V <= F: vence no mês seguinte ao fechamento)
    assert faturas.periodo("2026-03", 31, 10) == (date(2026, 1, 31), date(2026, 2, 28))
    assert faturas.referencia_da_compra(date(2026, 2, 27), 31, 10) == "2026-03"  # fecha 28/02
    assert faturas.referencia_da_compra(date(2026, 2, 28), 31, 10) == "2026-04"  # fecha 28/02, essa é da próxima
    assert faturas.periodo("2026-04", 31, 10) == (date(2026, 2, 28), date(2026, 3, 31))
    assert faturas.vencimento("2026-02", 31) == date(2026, 2, 28)


def test_referencia_e_periodo_sao_coerentes():
    """Toda data cai no período da fatura que `referencia_da_compra` indica (e só nele)."""
    for fechamento, venc in ((1, 10), (25, 5), (31, 10), (10, 20), (None, None), (28, 28)):
        dia = date(2026, 1, 1)
        while dia < date(2027, 3, 1):
            ref = faturas.referencia_da_compra(dia, fechamento, venc)
            inicio, fim = faturas.periodo(ref, fechamento, venc)
            assert inicio <= dia < fim, (fechamento, venc, dia, ref)
            dia = date.fromordinal(dia.toordinal() + 1)


def test_referencia_invalida():
    with pytest.raises(ValueError, match="AAAA-MM"):
        faturas.periodo("2026-13", 1, 10)


# --- Faturas de um cartão ----------------------------------------------------------------------


def test_cartao_inexistente_ou_que_nao_e_cartao(conn):
    with pytest.raises(ValueError):
        faturas.faturas_do_cartao(conn, 999, HOJE)
    with pytest.raises(ValueError):
        faturas.faturas_do_cartao(conn, id_conta(conn, "Inter"), HOJE)


def test_sempre_ha_a_fatura_aberta_mesmo_vazia(conn):
    cartao = _visa(conn)
    lista = faturas.faturas_do_cartao(conn, cartao, HOJE)
    assert [(f.referencia, f.situacao, f.total_centavos) for f in lista] == [("2026-11", "aberta", 0)]
    assert lista[0].lancamentos.empty
    assert lista[0].vencimento == date(2026, 11, 10)
    assert lista[0].fechamento == date(2026, 11, 1)


def test_situacoes_e_totais(conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 10000, date(2026, 9, 20), "Setembro")
    _compra(conn, cartao, 30000, date(2026, 10, 15), "Outubro")
    _compra(conn, cartao, 5000, date(2026, 11, 3), "Novembro")
    por_ref = _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))
    assert {r: f.situacao for r, f in por_ref.items()} == {"2026-10": "fechada", "2026-11": "aberta", "2026-12": "futura"}
    assert por_ref["2026-10"].total_centavos == 10000
    assert por_ref["2026-11"].total_centavos == 30000
    assert por_ref["2026-12"].total_centavos == 5000
    assert list(por_ref["2026-11"].lancamentos["descricao"]) == ["Outubro"]


def test_fatura_fecha_no_dia_do_fechamento(conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 10000, date(2026, 10, 20))
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, date(2026, 10, 31)))["2026-11"].situacao == "aberta"
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, date(2026, 11, 1)))["2026-11"].situacao == "fechada"


def test_estorno_abate_e_ordem_das_compras(conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 10000, date(2026, 10, 3), "Tênis")
    _compra(conn, cartao, 4000, date(2026, 10, 9), "Camisa")
    lanc.registrar(conn, entrada=True, valor_centavos=2500, descricao="Estorno", categoria_id=id_cat(conn, "Reembolsos"),
                   metodo="cartao", conta_id=cartao, data=date(2026, 10, 10))
    f = _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-11"]
    assert f.total_centavos == 11500
    assert list(f.lancamentos["descricao"]) == ["Estorno", "Camisa", "Tênis"]  # mais recente primeiro


def test_parcelas_6x_caem_uma_por_fatura(conn):
    cartao = _visa(conn)
    _compra(conn, cartao, 60000, date(2026, 10, 15), "Cadeira presidente", parcelas=6)
    lista = faturas.faturas_do_cartao(conn, cartao, HOJE)
    assert [f.referencia for f in lista] == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
    for n, f in enumerate(lista, start=1):
        assert f.total_centavos == 10000
        linha = f.lancamentos.iloc[0]
        assert (linha["parcela_atual"], linha["parcela_total"]) == (n, 6)
    assert [f.situacao for f in lista] == ["aberta"] + ["futura"] * 5


def test_parcelas_dia_31_nao_pulam_nem_repetem_fatura(conn):
    cartao = _visa(conn, fechamento=1, vencimento=10)
    _compra(conn, cartao, 30000, date(2026, 12, 31), "Geladeira", parcelas=3)  # 31/12, 31/01, 28/02
    lista = faturas.faturas_do_cartao(conn, cartao, HOJE)
    assert [(f.referencia, f.total_centavos) for f in lista if f.total_centavos] == [
        ("2027-01", 10000), ("2027-02", 10000), ("2027-03", 10000)]


def test_cartao_sem_fechamento_usa_o_mes_do_calendario(conn):
    cartao = cadastros.criar_cartao(conn, "Inter Black")
    _compra(conn, cartao, 7000, date(2026, 10, 2))
    _compra(conn, cartao, 3000, date(2026, 10, 30))
    f = _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-11"]
    assert f.total_centavos == 10000
    assert (f.inicio, f.fim_exclusivo, f.vencimento) == (date(2026, 10, 1), date(2026, 11, 1), date(2026, 11, 10))
    assert f.situacao == "aberta"


# --- Pagar e desfazer --------------------------------------------------------------------------


def _estado(conn, hoje=HOJE):
    resumo = relatorios.resumo_mes(conn, 2026, 10)
    return {
        "em_conta": relatorios.em_conta(conn, hoje),
        "devo": relatorios.devo(conn),
        "entradas": resumo.entradas,
        "saidas": resumo.saidas,
        "categorias": relatorios.gastos_por_categoria(conn, 2026, 10).to_dict("records"),
    }


def test_pagar_fatura_move_dinheiro_sem_virar_gasto(conn):
    inter = id_conta(conn, "Inter")
    lanc.registrar(conn, entrada=True, valor_centavos=500000, descricao="Salário", categoria_id=id_cat(conn, "Salário"),
                   metodo="pix", conta_id=inter, data=date(2026, 10, 5))
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15), "Compra de setembro")
    antes = _estado(conn)
    assert antes["em_conta"] == 500000 and antes["devo"] == 30000

    pago_id = faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 10))
    assert isinstance(pago_id, int)

    depois = _estado(conn)
    assert depois["em_conta"] == 470000 and depois["devo"] == 0  # Em conta e Devo caem juntos
    assert depois["entradas"] == antes["entradas"] and depois["saidas"] == antes["saidas"]  # resumo do mês não muda
    assert depois["categorias"] == antes["categorias"]  # gastos por categoria também não

    lados = conn.execute("SELECT * FROM lancamentos WHERE transferencia_id IS NOT NULL ORDER BY valor_centavos").fetchall()
    assert [(l["conta_id"], l["valor_centavos"], l["metodo"], l["data"]) for l in lados] == [
        (inter, -30000, "transferencia", "2026-10-10"), (cartao, 30000, "transferencia", "2026-10-10")]
    assert lados[0]["transferencia_id"] == lados[1]["transferencia_id"]
    assert conn.execute("SELECT nome FROM categorias WHERE id = ?", (lados[0]["categoria_id"],)).fetchone()[0] == "Pagamento de fatura"
    assert "1234" in lados[0]["descricao"] and "10/2026" in lados[0]["descricao"]


def test_fatura_paga_deixa_de_ser_fechada(conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-10"].situacao == "fechada"

    faturas.pagar_fatura(conn, cartao, "2026-10", 28000, inter, date(2026, 10, 10))
    f = _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-10"]
    assert (f.situacao, f.paga, f.total_centavos, f.pago_centavos) == ("paga", True, 30000, 28000)
    assert (f.data_pagamento, f.conta_origem_id) == (date(2026, 10, 10), inter)
    assert len(f.lancamentos) == 1  # o pagamento (transferência) não é compra


def test_pagar_nao_conta_o_pagamento_na_fatura_aberta(conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    _compra(conn, cartao, 5000, date(2026, 10, 12))
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 29))  # entrada no cartão em 29/10
    por_ref = _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))
    assert por_ref["2026-11"].total_centavos == 5000
    assert por_ref["2026-10"].situacao == "paga"


def test_desfazer_pagamento_restaura_tudo(conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    antes = _estado(conn)
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 10))
    faturas.desfazer_pagamento(conn, cartao, "2026-10")
    assert _estado(conn) == antes
    assert conn.execute("SELECT COUNT(*) FROM faturas_pagas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE transferencia_id IS NOT NULL").fetchone()[0] == 0
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-10"].situacao == "fechada"
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 11))  # pode pagar de novo


def test_pagar_valida(conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    with pytest.raises(ValueError, match="maior que zero"):
        faturas.pagar_fatura(conn, cartao, "2026-10", 0, inter, HOJE)
    with pytest.raises(ValueError, match="conta corrente"):
        faturas.pagar_fatura(conn, cartao, "2026-10", 100, id_conta(conn, "Carteira"), HOJE)
    with pytest.raises(ValueError, match="conta corrente"):
        faturas.pagar_fatura(conn, cartao, "2026-10", 100, cartao, HOJE)
    cadastros.desativar_conta(conn, id_conta(conn, "Caixa"))
    with pytest.raises(ValueError, match="conta corrente"):
        faturas.pagar_fatura(conn, cartao, "2026-10", 100, id_conta(conn, "Caixa"), HOJE)
    with pytest.raises(ValueError, match="Cartão"):
        faturas.pagar_fatura(conn, inter, "2026-10", 100, inter, HOJE)
    with pytest.raises(ValueError, match="AAAA-MM"):
        faturas.pagar_fatura(conn, cartao, "10/2026", 100, inter, HOJE)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0

    faturas.pagar_fatura(conn, cartao, "2026-10", 100, inter, HOJE)
    with pytest.raises(ValueError, match="já está paga"):
        faturas.pagar_fatura(conn, cartao, "2026-10", 100, inter, HOJE)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2


def test_desfazer_fatura_que_nao_esta_paga(conn):
    with pytest.raises(ValueError, match="não está paga"):
        faturas.desfazer_pagamento(conn, _visa(conn), "2026-10")


def test_pagamento_apagado_a_mao_nao_deixa_a_fatura_presa(conn):
    inter = id_conta(conn, "Inter")
    cartao = _visa(conn)
    _compra(conn, cartao, 30000, date(2026, 9, 15))
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 10))
    ids = [r[0] for r in conn.execute("SELECT id FROM lancamentos WHERE transferencia_id IS NOT NULL")]
    lanc.excluir(conn, ids)  # o dono apagou os lançamentos do pagamento
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-10"].situacao == "fechada"
    faturas.pagar_fatura(conn, cartao, "2026-10", 30000, inter, date(2026, 10, 12))
    assert _por_ref(faturas.faturas_do_cartao(conn, cartao, HOJE))["2026-10"].situacao == "paga"


# --- Gastei no mês -----------------------------------------------------------------------------


def test_gastei_no_mes_exemplo_do_dono(conn):
    inter = id_conta(conn, "Inter")
    cartao = id_conta(conn, "Cartão de crédito")
    for valor, metodo in ((10000, "pix"), (15000, "debito")):
        lanc.registrar(conn, entrada=False, valor_centavos=valor, descricao="x", categoria_id=id_cat(conn, "Mercado"),
                       metodo=metodo, conta_id=inter, data=date(2026, 10, 5))
    _compra(conn, cartao, 30000, date(2026, 10, 6))
    lanc.registrar(conn, entrada=True, valor_centavos=999900, descricao="Salário", categoria_id=id_cat(conn, "Salário"),
                   metodo="pix", conta_id=inter, data=date(2026, 10, 5))
    assert relatorios.gastei_no_mes(conn, 2026, 10) == (55000, 25000, 30000)
    faturas.pagar_fatura(conn, cartao, "2026-11", 30000, inter, date(2026, 10, 20))  # pagar não duplica o gasto
    assert relatorios.gastei_no_mes(conn, 2026, 10) == (55000, 25000, 30000)
    assert relatorios.gastei_no_mes(conn, 2026, 9) == (0, 0, 0)


def test_gastei_no_mes_ate_ignora_o_que_ainda_nao_aconteceu(conn):
    cartao = id_conta(conn, "Cartão de crédito")
    _compra(conn, cartao, 6000, date(2026, 10, 10), parcelas=1)
    _compra(conn, cartao, 9000, date(2026, 10, 25))
    assert relatorios.gastei_no_mes(conn, 2026, 10, ate=date(2026, 10, 20)) == (6000, 0, 6000)
    assert relatorios.gastei_no_mes(conn, 2026, 10) == (15000, 0, 15000)


# --- Comprometido nos próximos meses -------------------------------------------------------------


def test_comprometido_com_parcelas_assinatura_e_aluguel(conn):
    inter = id_conta(conn, "Inter")
    cartao = id_conta(conn, "Cartão de crédito")
    _compra(conn, cartao, 60000, date(2026, 10, 15), "Cadeira", parcelas=6)  # 6 x 100, de out a mar
    recorrentes.criar(conn, descricao="Streaming", valor_centavos=-4000, conta_id=cartao, categoria_id=id_cat(conn, "Assinaturas"),
                      metodo="cartao", dia_do_mes=7, inicio=date(2026, 9, 7), assinatura=True)
    recorrentes.criar(conn, descricao="Aluguel", valor_centavos=-120000, conta_id=inter, categoria_id=id_cat(conn, "Moradia"),
                      metodo="boleto", dia_do_mes=5, inicio=date(2026, 9, 5), assinatura=False)
    recorrentes.gerar_pendentes(conn, HOJE)  # gera até out; nov em diante só existe como molde

    df = relatorios.comprometido_proximos_meses(conn, HOJE)
    assert list(df.columns) == ["mes", "cartao", "contas", "total"]
    assert list(df["mes"]) == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
    assert list(df["cartao"]) == [14000, 14000, 14000, 14000, 14000, 4000]  # parcela 100 + streaming 40; abril só streaming
    assert list(df["contas"]) == [120000] * 6
    assert list(df["total"]) == [134000] * 5 + [124000]


def test_comprometido_nao_conta_duas_vezes_nem_o_que_foi_apagado_ou_pausado(conn):
    inter = id_conta(conn, "Inter")
    aluguel = recorrentes.criar(conn, descricao="Aluguel", valor_centavos=-100000, conta_id=inter, categoria_id=id_cat(conn, "Moradia"),
                                metodo="boleto", dia_do_mes=5, inicio=date(2026, 9, 5), assinatura=False)
    pausada = recorrentes.criar(conn, descricao="Revista", valor_centavos=-3000, conta_id=inter, categoria_id=None,
                                metodo="boleto", dia_do_mes=5, inicio=date(2026, 9, 5), assinatura=True)
    entrada = recorrentes.criar(conn, descricao="Salário", valor_centavos=500000, conta_id=inter, categoria_id=id_cat(conn, "Salário"),
                                metodo="pix", dia_do_mes=5, inicio=date(2026, 9, 5), assinatura=False)
    recorrentes.gerar_pendentes(conn, HOJE)
    recorrentes.pausar(conn, pausada, HOJE)
    # um previsto de dezembro já gerado e um mês projetado (jan) que o dono apagou: não deve voltar
    conn.execute("INSERT INTO recorrentes_meses (recorrente_id, mes, lancamento_id) VALUES (?, '2027-01', NULL)", (aluguel,))
    conn.execute(
        "INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao, status, origem, recorrente_id)"
        " VALUES (?, '2026-12-05', -100000, 'Aluguel', 'previsto', 'recorrente', ?)", (inter, aluguel))
    conn.execute("INSERT INTO recorrentes_meses (recorrente_id, mes, lancamento_id) VALUES (?, '2026-12', last_insert_rowid())", (aluguel,))
    conn.commit()
    assert entrada

    df = relatorios.comprometido_proximos_meses(conn, HOJE, meses=3)
    assert list(df["mes"]) == ["2026-11", "2026-12", "2027-01"]
    assert list(df["contas"]) == [100000, 100000, 0]  # dez uma vez só (já lançado); jan apagado; entradas e pausadas fora


def test_comprometido_respeita_o_fim_da_recorrente_e_zera_meses_vazios(conn):
    inter = id_conta(conn, "Inter")
    rid = recorrentes.criar(conn, descricao="Curso", valor_centavos=-9000, conta_id=inter, categoria_id=None,
                            metodo="boleto", dia_do_mes=20, inicio=date(2026, 8, 20), assinatura=False)
    recorrentes.gerar_pendentes(conn, HOJE)
    recorrentes.encerrar(conn, rid, HOJE)
    df = relatorios.comprometido_proximos_meses(conn, HOJE)
    assert list(df["total"]) == [0] * 6 and len(df) == 6


def test_comprometido_vira_o_ano(conn):
    df = relatorios.comprometido_proximos_meses(conn, date(2026, 12, 15), meses=2)
    assert list(df["mes"]) == ["2027-01", "2027-02"]


# --- Cadastros: final e edição do cartão ---------------------------------------------------------


def test_final_do_cartao(conn):
    cartao = cadastros.criar_cartao(conn, "Caixa Visa", final=" 1234 ")
    assert conn.execute("SELECT final FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == "1234"
    cadastros.definir_final(conn, cartao, "")
    assert conn.execute("SELECT final FROM contas WHERE id = ?", (cartao,)).fetchone()[0] is None
    cadastros.definir_final(conn, cartao, "9876")
    for ruim in ("123", "12345", "12a4", "١٢٣٤"):
        with pytest.raises(ValueError, match="4 dígitos"):
            cadastros.definir_final(conn, cartao, ruim)
    with pytest.raises(ValueError, match="4 dígitos"):
        cadastros.criar_cartao(conn, "Outro", final="12")
    with pytest.raises(ValueError, match="não é um cartão"):
        cadastros.definir_final(conn, id_conta(conn, "Inter"), "1234")
    assert conn.execute("SELECT final FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == "9876"


def test_editar_cartao(conn):
    cartao = _visa(conn)
    cadastros.editar_cartao(conn, cartao, banco=" Inter ", dia_fechamento=25, dia_vencimento=5, final="4321")
    c = conn.execute("SELECT * FROM contas WHERE id = ?", (cartao,)).fetchone()
    assert (c["banco"], c["dia_fechamento"], c["dia_vencimento"], c["final"]) == ("Inter", 25, 5, "4321")
    cadastros.editar_cartao(conn, cartao, banco="", dia_fechamento=None, dia_vencimento=None, final=None)
    c = conn.execute("SELECT * FROM contas WHERE id = ?", (cartao,)).fetchone()
    assert (c["banco"], c["dia_fechamento"], c["dia_vencimento"], c["final"]) == (None, None, None, None)
    with pytest.raises(ValueError, match="fechamento"):
        cadastros.editar_cartao(conn, cartao, banco=None, dia_fechamento=40, dia_vencimento=None, final=None)
    with pytest.raises(ValueError, match="não é um cartão"):
        cadastros.editar_cartao(conn, id_conta(conn, "Inter"), banco=None, dia_fechamento=None, dia_vencimento=None, final=None)


def test_nome_com_final():
    assert lanc.nome_com_final("Caixa Visa", "1234") == "Caixa Visa •••• 1234"
    assert lanc.nome_com_final("Caixa Visa", None) == "Caixa Visa"
    assert lanc.nome_com_final("Caixa Visa", float("nan")) == "Caixa Visa"
