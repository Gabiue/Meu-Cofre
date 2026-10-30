"""Metas (F7): contas de data, sobra média, viabilidade, guardar/retirar, reserva e simulador. Sem Qt."""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import lancamentos as lanc
from meu_cofre import metas, relatorios

HOJE = date(2026, 10, 20)


def _lancar(conn, entrada, centavos, data, cat, conta="Inter", metodo="pix", descricao="x"):
    lanc.registrar(
        conn, entrada=entrada, valor_centavos=centavos, descricao=descricao, categoria_id=id_cat(conn, cat),
        metodo=metodo, conta_id=id_conta(conn, conta), data=data,
    )


def _historico(conn, meses=(7, 8, 9), salario=500_000, essencial=200_000, flexivel=100_000):
    """Cada mês: salário, gasto essencial (Moradia) e gasto flexível (Lazer). Sobra = salário − essencial − flexível."""
    for m in meses:
        _lancar(conn, True, salario, date(2026, m, 5), "Salário")
        _lancar(conn, False, essencial, date(2026, m, 10), "Moradia")
        _lancar(conn, False, flexivel, date(2026, m, 15), "Lazer")


def _meta(conn, nome="Cadeira", valor=150_000, modo="prazo", prazo=date(2027, 3, 1), mensal=None, reserva=False):
    return metas.criar(
        conn, HOJE, nome=nome, valor_alvo_centavos=valor, modo=modo, prazo=prazo if modo == "prazo" else None,
        mensal_centavos=mensal, reserva=reserva,
    )


def _guardar(conn, meta_id, centavos, data=HOJE, conta="Inter"):
    return metas.guardar(conn, meta_id, centavos, id_conta(conn, conta), data)


# --- Contas de data -----------------------------------------------------------------------------------------


def test_mensal_para_prazo_conta_o_mes_atual_e_arredonda_pra_cima():
    # out/2026 até mar/2027 = 6 meses
    assert metas.mensal_para_prazo(150_000, HOJE, date(2027, 3, 1)) == 25_000
    assert metas.mensal_para_prazo(150_001, HOJE, date(2027, 3, 31)) == 25_001
    assert metas.mensal_para_prazo(100, HOJE, date(2026, 10, 31)) == 100  # prazo no mês atual: 1 mês
    assert metas.mensal_para_prazo(0, HOJE, date(2027, 3, 1)) == 0 and metas.mensal_para_prazo(-5, HOJE, date(2027, 3, 1)) == 0


def test_mensal_para_prazo_com_prazo_no_passado_pede_tudo_agora():
    assert metas.mensal_para_prazo(90_000, HOJE, date(2026, 6, 1)) == 90_000


def test_data_para_mensal_conta_o_mes_atual_como_o_primeiro():
    assert metas.data_para_mensal(150_000, HOJE, 50_000) == date(2026, 12, 1)  # out, nov, dez
    assert metas.data_para_mensal(150_001, HOJE, 50_000) == date(2027, 1, 1)
    assert metas.data_para_mensal(10_000, HOJE, 50_000) == date(2026, 10, 1)
    assert metas.data_para_mensal(0, HOJE, 50_000) == date(2026, 10, 1)  # já cumprida
    assert metas.data_para_mensal(1_200_000, HOJE, 100_000) == date(2027, 9, 1)  # vira o ano


def test_mensal_zero_ou_negativo_e_erro():
    with pytest.raises(ValueError, match="por mês"):
        metas.data_para_mensal(1000, HOJE, 0)
    with pytest.raises(ValueError):
        metas.meses_para_mensal(1000, -1)


# --- Sobra média --------------------------------------------------------------------------------------------


def test_sobra_media_dos_tres_meses_completos(conn):
    _historico(conn)
    assert metas.sobra_media(conn, HOJE) == (200_000, 3, False)


def test_sobra_media_ignora_meses_sem_dados_e_pega_so_os_tres_ultimos(conn):
    _historico(conn, meses=(5, 8, 9))  # maio fica fora da janela de 3 meses; julho não tem dados
    assert metas.sobra_media(conn, HOJE) == (200_000, 2, False)


def test_sobra_media_com_mes_ruim_pode_ser_negativa(conn):
    _historico(conn, meses=(8, 9), salario=100_000)
    assert metas.sobra_media(conn, HOJE) == (-200_000, 2, False)


def test_sobra_media_sem_mes_completo_usa_o_mes_atual_ate_hoje_como_estimativa(conn):
    _lancar(conn, True, 300_000, date(2026, 10, 5), "Salário")
    _lancar(conn, False, 100_000, date(2026, 10, 10), "Moradia")
    _lancar(conn, False, 999_999, date(2026, 10, 25), "Lazer")  # depois de hoje: não conta
    assert metas.sobra_media(conn, HOJE) == (200_000, 1, True)


def test_sobra_media_banco_sem_historico(conn):
    assert metas.sobra_media(conn, HOJE) == (0, 0, True)


def test_sobra_media_nao_conta_transferencias_nem_o_que_foi_guardado(conn):
    _historico(conn)
    meta = _meta(conn)
    _guardar(conn, meta, 50_000, date(2026, 9, 20))
    assert metas.sobra_media(conn, HOJE) == (200_000, 3, False)


# --- Reserva sugerida ---------------------------------------------------------------------------------------


def test_reserva_sugerida_e_seis_vezes_o_essencial_medio(conn):
    _historico(conn)
    assert metas.reserva_sugerida(conn, HOJE) == (1_200_000, 200_000, 3)


def test_reserva_sugerida_so_conta_natureza_essencial(conn):
    _historico(conn, essencial=150_000, flexivel=400_000)
    assert metas.reserva_sugerida(conn, HOJE) == (900_000, 150_000, 3)


def test_reserva_sugerida_usa_o_que_houver_e_zera_sem_historico(conn):
    assert metas.reserva_sugerida(conn, HOJE) == (0, 0, 0)
    _lancar(conn, False, 80_000, date(2026, 10, 3), "Moradia")
    assert metas.reserva_sugerida(conn, HOJE) == (480_000, 80_000, 1)


# --- Criar, editar, organizar -------------------------------------------------------------------------------


def test_criar_e_listar_em_ordem_de_prioridade(conn):
    a = _meta(conn, "A")
    b = _meta(conn, "B", modo="mensal", mensal=30_000)
    lista = metas.listar(conn, HOJE)
    assert [m.nome for m in lista] == ["A", "B"]
    assert [m.id for m in lista] == [a, b]
    assert lista[1].modo == "mensal" and lista[1].mensal_centavos == 30_000 and lista[1].prazo is None
    assert lista[0].prazo == date(2027, 3, 1) and lista[0].mensal_centavos is None
    assert lista[0].guardado == 0 and lista[0].progresso == 0 and lista[0].estado == "ativa"
    assert lista[0].criada_em == HOJE


def test_criar_valida(conn):
    with pytest.raises(ValueError, match="nome"):
        _meta(conn, nome="  ")
    with pytest.raises(ValueError, match="maior que zero"):
        _meta(conn, valor=0)
    with pytest.raises(ValueError, match="prazo"):
        metas.criar(conn, HOJE, nome="X", valor_alvo_centavos=100, modo="prazo")
    with pytest.raises(ValueError, match="já passou"):
        _meta(conn, prazo=date(2026, 9, 30))
    _meta(conn, prazo=date(2026, 10, 1))  # o mês atual vale, mesmo que o dia já tenha passado
    with pytest.raises(ValueError, match="por mês"):
        metas.criar(conn, HOJE, nome="X", valor_alvo_centavos=100, modo="mensal")
    with pytest.raises(ValueError, match="Modo"):
        metas.criar(conn, HOJE, nome="X", valor_alvo_centavos=100, modo="outro")


def test_reserva_entra_no_topo_e_so_pode_haver_uma(conn):
    _meta(conn, "A")
    _meta(conn, "B")
    r = _meta(conn, "Reserva de emergência", valor=1_000_000, reserva=True)
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["Reserva de emergência", "A", "B"]
    assert metas.tem_reserva(conn)
    with pytest.raises(ValueError, match="reserva"):
        _meta(conn, "Outra reserva", reserva=True)
    metas.arquivar(conn, r)
    assert not metas.tem_reserva(conn)
    _meta(conn, "Reserva nova", reserva=True)  # a arquivada não conta


def test_editar_troca_dados_e_limpa_o_campo_do_outro_modo(conn):
    m = _meta(conn, "Cadeira")
    metas.editar(conn, HOJE, m, nome="Cadeira gamer", valor_alvo_centavos=200_000, modo="mensal", mensal_centavos=40_000)
    meta = metas.obter(conn, m)
    assert (meta.nome, meta.valor_alvo_centavos, meta.modo, meta.prazo, meta.mensal_centavos) == (
        "Cadeira gamer", 200_000, "mensal", None, 40_000)
    metas.editar(conn, HOJE, m, nome="Cadeira gamer", valor_alvo_centavos=200_000, modo="prazo", prazo=date(2027, 5, 1))
    meta = metas.obter(conn, m)
    assert (meta.modo, meta.prazo, meta.mensal_centavos) == ("prazo", date(2027, 5, 1), None)


def test_editar_aceita_prazo_antigo_se_nao_mudou_mas_nao_um_novo_no_passado(conn):
    m = _meta(conn, prazo=date(2026, 10, 1))
    depois = date(2026, 12, 20)  # o prazo passa
    metas.editar(conn, depois, m, nome="Cadeira", valor_alvo_centavos=160_000, modo="prazo", prazo=date(2026, 10, 1))
    with pytest.raises(ValueError, match="já passou"):
        metas.editar(conn, depois, m, nome="Cadeira", valor_alvo_centavos=160_000, modo="prazo", prazo=date(2026, 11, 1))


def test_editar_para_reserva_leva_pro_topo_e_respeita_a_unica(conn):
    _meta(conn, "A")
    b = _meta(conn, "B")
    metas.editar(conn, HOJE, b, nome="B", valor_alvo_centavos=150_000, modo="prazo", prazo=date(2027, 3, 1), reserva=True)
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["B", "A"]
    a = metas.listar(conn, HOJE)[1].id
    with pytest.raises(ValueError, match="reserva"):
        metas.editar(conn, HOJE, a, nome="A", valor_alvo_centavos=150_000, modo="prazo", prazo=date(2027, 3, 1), reserva=True)


def test_mover_prioridade_troca_com_o_vizinho_e_para_nos_limites(conn):
    a, b, c = _meta(conn, "A"), _meta(conn, "B"), _meta(conn, "C")
    nomes = lambda: [m.nome for m in metas.listar(conn, HOJE)]  # noqa: E731
    metas.mover_prioridade(conn, c, -1)
    assert nomes() == ["A", "C", "B"]
    metas.mover_prioridade(conn, a, -1)  # já é a primeira
    assert nomes() == ["A", "C", "B"]
    metas.mover_prioridade(conn, a, +1)
    assert nomes() == ["C", "A", "B"]
    metas.mover_prioridade(conn, b, +1)  # já é a última
    assert nomes() == ["C", "A", "B"]
    assert [m.prioridade for m in metas.listar(conn, HOJE)] == [0, 1, 2]
    with pytest.raises(ValueError):
        metas.mover_prioridade(conn, a, 2)


def test_mover_prioridade_ignora_metas_encerradas(conn):
    a, b, c = _meta(conn, "A"), _meta(conn, "B"), _meta(conn, "C")
    metas.arquivar(conn, b)
    metas.mover_prioridade(conn, c, -1)  # pula a arquivada
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["C", "A"]


def test_concluir_arquivar_e_reativar(conn):
    a, b = _meta(conn, "A"), _meta(conn, "B")
    metas.concluir(conn, HOJE, a)
    metas.arquivar(conn, b)
    assert [m.nome for m in metas.listar(conn, HOJE)] == ["A"]
    todas = metas.listar(conn, HOJE, incluir_arquivadas=True)
    assert [(m.nome, m.estado) for m in todas] == [("A", "concluida"), ("B", "arquivada")]
    assert todas[0].concluida_em == HOJE and todas[0].viabilidade is None
    assert metas.viabilidade(conn, HOJE) == []
    metas.reativar(conn, a)
    assert [(m.nome, m.estado) for m in metas.listar(conn, HOJE)] == [("A", "ativa")]
    with pytest.raises(ValueError, match="não existe"):
        metas.obter(conn, 999)


# --- Guardar e retirar --------------------------------------------------------------------------------------


def test_guardar_move_da_conta_pras_caixinhas_e_retirar_devolve(conn):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    m = _meta(conn, "Cadeira")
    assert relatorios.em_conta(conn, HOJE) == 100_000
    _guardar(conn, m, 30_000)
    assert relatorios.em_conta(conn, HOJE) == 70_000
    assert metas.guardado(conn, m) == 30_000
    assert metas.guardado_total(conn) == 30_000
    assert conn.execute("SELECT SUM(valor_centavos) FROM lancamentos WHERE conta_id = ?", (metas.conta_caixinhas(conn),)).fetchone()[0] == 30_000
    metas.retirar(conn, m, 10_000, id_conta(conn, "Inter"), HOJE)
    assert relatorios.em_conta(conn, HOJE) == 80_000
    assert metas.guardado(conn, m) == 20_000
    assert metas.obter(conn, m).progresso == pytest.approx(20_000 / 150_000)


def test_guardar_cria_duas_pernas_ligadas_a_meta_e_com_o_mesmo_transferencia_id(conn):
    m = _meta(conn, "Cadeira")
    tid = _guardar(conn, m, 30_000, date(2026, 10, 18))
    linhas = conn.execute(
        """SELECT l.valor_centavos, l.metodo, l.meta_id, l.data, l.descricao, c.nome AS conta, k.nome AS cat, k.tipo AS cat_tipo
           FROM lancamentos l JOIN contas c ON c.id = l.conta_id JOIN categorias k ON k.id = l.categoria_id
           WHERE l.transferencia_id = ? ORDER BY l.valor_centavos""",
        (tid,),
    ).fetchall()
    assert [(r["valor_centavos"], r["conta"]) for r in linhas] == [(-30_000, "Inter"), (30_000, "Caixinhas")]
    for r in linhas:
        assert (r["metodo"], r["meta_id"], r["data"], r["descricao"], r["cat"], r["cat_tipo"]) == (
            "transferencia", m, "2026-10-18", "Guardado: Cadeira", "Guardar em meta", "transferencia")
    tid2 = metas.retirar(conn, m, 5_000, id_conta(conn, "Carteira"), HOJE)
    assert {r["descricao"] for r in conn.execute("SELECT descricao FROM lancamentos WHERE transferencia_id = ?", (tid2,))} == {"Retirado: Cadeira"}
    assert tid2 != tid


def test_guardar_nao_vira_gasto_nem_renda_nem_muda_os_graficos(conn):
    _historico(conn)
    _lancar(conn, False, 20_000, date(2026, 10, 3), "Mercado")
    antes = (relatorios.resumo_mes(conn, 2026, 10), relatorios.gastei_no_mes(conn, 2026, 10, ate=HOJE),
             relatorios.gastos_por_categoria(conn, 2026, 10).to_dict("records"),
             relatorios.gastos_por_dia(conn, 2026, 10, HOJE)["centavos"].tolist(),
             relatorios.historico_mensal(conn, HOJE)["saidas"].tolist(), relatorios.saldo_em(conn, HOJE))
    m = _meta(conn)
    _guardar(conn, m, 50_000, date(2026, 10, 10))
    metas.retirar(conn, m, 10_000, id_conta(conn, "Inter"), date(2026, 10, 12))
    depois = (relatorios.resumo_mes(conn, 2026, 10), relatorios.gastei_no_mes(conn, 2026, 10, ate=HOJE),
              relatorios.gastos_por_categoria(conn, 2026, 10).to_dict("records"),
              relatorios.gastos_por_dia(conn, 2026, 10, HOJE)["centavos"].tolist(),
              relatorios.historico_mensal(conn, HOJE)["saidas"].tolist(), relatorios.saldo_em(conn, HOJE))
    assert depois == antes  # o patrimônio total também não muda: o dinheiro só trocou de conta


def test_livre_nao_conta_o_guardado(conn):
    _lancar(conn, True, 100_000, date(2026, 10, 1), "Salário")
    livre = relatorios.livre_ate_fim_do_mes(conn, HOJE)
    m = _meta(conn)
    _guardar(conn, m, 40_000)
    assert relatorios.livre_ate_fim_do_mes(conn, HOJE) == livre - 40_000
    metas.retirar(conn, m, 40_000, id_conta(conn, "Inter"), HOJE)
    assert relatorios.livre_ate_fim_do_mes(conn, HOJE) == livre


def test_retirar_nao_passa_do_guardado(conn):
    m = _meta(conn)
    with pytest.raises(ValueError, match="nada guardado"):
        metas.retirar(conn, m, 1, id_conta(conn, "Inter"), HOJE)
    _guardar(conn, m, 10_000)
    with pytest.raises(ValueError, match="R\\$ 100,00"):
        metas.retirar(conn, m, 10_001, id_conta(conn, "Inter"), HOJE)
    metas.retirar(conn, m, 10_000, id_conta(conn, "Inter"), HOJE)
    assert metas.guardado(conn, m) == 0
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 4


def test_guardar_e_retirar_validam_valor_e_conta(conn):
    m = _meta(conn)
    for valor in (0, -5):
        with pytest.raises(ValueError, match="maior que zero"):
            _guardar(conn, m, valor)
    with pytest.raises(ValueError, match="conta corrente"):
        metas.guardar(conn, m, 100, id_conta(conn, "Cartão de crédito"), HOJE)
    with pytest.raises(ValueError, match="conta corrente"):
        metas.guardar(conn, m, 100, metas.conta_caixinhas(conn), HOJE)
    _guardar(conn, m, 1_000)
    with pytest.raises(ValueError, match="conta corrente"):
        metas.retirar(conn, m, 100, id_conta(conn, "Cartão de crédito"), HOJE)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2  # nada pela metade


def test_so_meta_ativa_recebe_dinheiro_mas_a_encerrada_ainda_devolve(conn):
    m = _meta(conn)
    _guardar(conn, m, 10_000)
    metas.concluir(conn, HOJE, m)
    with pytest.raises(ValueError, match="encerrada"):
        _guardar(conn, m, 100)
    metas.retirar(conn, m, 10_000, id_conta(conn, "Inter"), HOJE)
    assert metas.guardado(conn, m) == 0
    metas.arquivar(conn, m)
    with pytest.raises(ValueError, match="encerrada"):
        _guardar(conn, m, 100)


def test_guardado_por_meta_e_total(conn):
    a, b = _meta(conn, "A"), _meta(conn, "B")
    _guardar(conn, a, 10_000)
    _guardar(conn, b, 5_000)
    _guardar(conn, a, 2_500)
    assert (metas.guardado(conn, a), metas.guardado(conn, b), metas.guardado_total(conn)) == (12_500, 5_000, 17_500)


def test_conta_caixinhas_e_criada_se_faltar(conn):
    assert conn.execute("SELECT tipo FROM contas WHERE id = ?", (metas.conta_caixinhas(conn),)).fetchone()[0] == "investimento"
    conn.execute("DELETE FROM contas WHERE nome = 'Caixinhas'")
    conn.commit()
    novo = metas.conta_caixinhas(conn)
    assert conn.execute("SELECT nome FROM contas WHERE id = ?", (novo,)).fetchone()[0] == "Caixinhas"
    assert metas.conta_caixinhas(conn) == novo


# --- Viabilidade --------------------------------------------------------------------------------------------


def test_viabilidade_cabe_quando_a_sobra_cobre_o_mensal(conn):
    _historico(conn)  # sobra 2000/mês
    _meta(conn, "Cadeira", valor=150_000, prazo=date(2027, 3, 1))  # 250/mês
    (v,) = metas.viabilidade(conn, HOJE)
    assert (v.nome, v.situacao, v.necessario, v.alocado) == ("Cadeira", "cabe", 25_000, 25_000)
    assert v.pedida == date(2027, 3, 1) and v.prevista == date(2027, 3, 1) and v.atraso_meses == 0


def test_viabilidade_reparte_a_sobra_por_prioridade_cabe_parcial_nao_cabe(conn):
    _historico(conn)  # sobra 2000/mês
    _meta(conn, "Reserva", valor=1_200_000, modo="mensal", mensal=120_000, reserva=True)  # pede 1200
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=150_000)  # pede 1500, sobram 800
    _meta(conn, "Cadeira", valor=100_000, modo="mensal", mensal=10_000)  # pede 100, sobra nada
    r, v, c = metas.viabilidade(conn, HOJE)
    assert (r.situacao, r.alocado, r.atraso_meses) == ("cabe", 120_000, 0)
    assert (v.situacao, v.necessario, v.alocado) == ("parcial", 150_000, 80_000)
    assert v.pedida == date(2027, 1, 1)  # 4 meses a 1500
    assert v.prevista == date(2027, 5, 1)  # 8 meses a 800 (7,5 pra cima)
    assert v.atraso_meses == 4
    assert (c.situacao, c.alocado, c.prevista, c.atraso_meses) == ("nao_cabe", 0, None, None)


def test_recebendo_um_pouco_menos_mas_pronta_no_mes_pedido_ainda_cabe(conn):
    _historico(conn, salario=414_423)  # sobra 1144,23
    _meta(conn, "Viagem", valor=440_000, modo="mensal", mensal=120_000)  # pede 1200: 4 meses (até jan/2027)
    (v,) = metas.viabilidade(conn, HOJE)
    assert (v.necessario, v.alocado) == (120_000, 114_423)
    assert (v.situacao, v.pedida, v.prevista, v.atraso_meses) == ("cabe", date(2027, 1, 1), date(2027, 1, 1), 0)


def test_viabilidade_com_prazo_parcial_atrasa_em_relacao_ao_prazo(conn):
    _historico(conn, salario=330_000)  # sobra 300/mês
    _meta(conn, "Cadeira", valor=150_000, prazo=date(2027, 3, 1))  # pede 250: cabe
    _meta(conn, "Tela", valor=90_000, prazo=date(2027, 3, 1))  # pede 150, sobram 50
    cadeira, tela = metas.viabilidade(conn, HOJE)
    assert cadeira.situacao == "cabe"
    assert (tela.situacao, tela.alocado, tela.prevista, tela.atraso_meses) == ("parcial", 5_000, date(2028, 3, 1), 12)


def test_viabilidade_sobra_zero_ou_negativa_nada_cabe(conn):
    _historico(conn, salario=300_000)  # sobra 0
    _meta(conn, "A")
    assert [v.situacao for v in metas.viabilidade(conn, HOJE)] == ["nao_cabe"]


def test_viabilidade_com_sobra_negativa_nada_cabe_e_o_livre_zera(conn):
    _historico(conn, salario=100_000)  # sobra −2000
    _meta(conn, "A")
    assert metas.sobra_media(conn, HOJE)[0] == -200_000
    (v,) = metas.viabilidade(conn, HOJE)
    assert (v.situacao, v.alocado) == ("nao_cabe", 0)
    r = metas.resumo(conn, HOJE)
    assert (r.sobra, r.livre) == (-200_000, 0)


def test_viabilidade_banco_sem_historico_nada_cabe_e_nao_quebra(conn):
    _meta(conn, "A")
    (v,) = metas.viabilidade(conn, HOJE)
    assert (v.situacao, v.alocado, v.prevista) == ("nao_cabe", 0, None)


def test_viabilidade_usa_o_restante_depois_de_guardar(conn):
    _historico(conn)
    m = _meta(conn, "Cadeira", valor=150_000, prazo=date(2027, 3, 1))
    _guardar(conn, m, 60_000)
    (v,) = metas.viabilidade(conn, HOJE)
    assert (v.restante, v.necessario) == (90_000, 15_000)


def test_meta_ja_cumprida_nao_pede_mensal_e_libera_a_sobra(conn):
    _historico(conn, salario=330_000)  # sobra 300/mês
    pronta = _meta(conn, "Pronta", valor=50_000, modo="mensal", mensal=20_000)
    _meta(conn, "Outra", valor=100_000, modo="mensal", mensal=30_000)
    _guardar(conn, pronta, 50_000)
    v_pronta, v_outra = metas.viabilidade(conn, HOJE)
    assert (v_pronta.situacao, v_pronta.necessario, v_pronta.alocado, v_pronta.atraso_meses) == ("cumprida", 0, 0, 0)
    assert v_outra.situacao == "cabe"
    _guardar(conn, pronta, 10_000)  # passou do alvo
    meta = metas.obter(conn, pronta, HOJE)
    assert meta.restante == 0 and meta.progresso == 1.0 and meta.viabilidade.situacao == "cumprida"


def test_prazo_no_passado_de_meta_existente_pede_tudo_agora(conn):
    _historico(conn)
    m = _meta(conn, "Cadeira", valor=150_000, prazo=date(2026, 10, 1))
    metas.editar(conn, date(2026, 12, 5), m, nome="Cadeira", valor_alvo_centavos=150_000, modo="prazo", prazo=date(2026, 10, 1))
    (v,) = metas.calcular_viabilidade([metas.obter(conn, m).entrada()], date(2026, 12, 5), 200_000)
    assert (v.necessario, v.alocado, v.situacao) == (150_000, 150_000, "cabe")
    assert v.pedida == date(2026, 12, 1)


def test_resumo_do_topo(conn):
    _historico(conn)  # sobra 2000
    a = _meta(conn, "A", valor=150_000, prazo=date(2027, 3, 1))  # 250
    _meta(conn, "B", valor=1_000_000, modo="mensal", mensal=50_000)  # 500
    _guardar(conn, a, 30_000)
    r = metas.resumo(conn, HOJE)
    assert (r.guardado, r.sobra, r.meses_usados, r.estimativa) == (30_000, 200_000, 3, False)
    assert r.comprometido == 20_000 + 50_000  # A: restante 1200 / 6 = 200
    assert r.livre == 200_000 - 70_000


def test_resumo_livre_nunca_negativo(conn):
    _historico(conn, salario=400_000)  # sobra 1000
    _meta(conn, "A", valor=1_000_000, modo="mensal", mensal=50_000)
    assert metas.resumo(conn, HOJE).livre == 50_000
    _meta(conn, "B", valor=1_000_000, modo="mensal", mensal=90_000)
    assert metas.resumo(conn, HOJE).livre == 0


# --- Prévias ------------------------------------------------------------------------------------------------


def _nova(conn, **campos):
    base = dict(nome="Nova", valor_alvo_centavos=600_000, modo="mensal", mensal_centavos=100_000)
    return metas.simular_nova_meta(conn, HOJE, **{**base, **campos})


def test_simular_nova_meta_mostra_a_viabilidade_dela_sem_gravar(conn):
    _historico(conn)  # sobra 2000
    p = _nova(conn)
    assert (p.meta.situacao, p.meta.necessario, p.meta.prevista, p.atrasos) == ("cabe", 100_000, date(2027, 3, 1), [])
    assert p.sobra == 200_000 and len(p.todas) == 1
    assert metas.listar(conn, HOJE) == []


def test_simular_nova_meta_diz_quem_atrasa(conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)  # 1000, pronta em mar/2027
    _meta(conn, "Cadeira", valor=300_000, modo="mensal", mensal=60_000)  # 600
    p = _nova(conn, nome="Moto", valor_alvo_centavos=1_000_000, mensal_centavos=100_000)  # 1000: ninguém cobre os 3
    assert p.meta.situacao == "parcial" and p.meta.alocado == 40_000  # a nova entra no fim: só 400 sobram
    assert p.atrasos == []  # as de cima não sentem
    p = _nova(conn, nome="Reserva", valor_alvo_centavos=1_000_000, mensal_centavos=50_000, reserva=True)
    assert p.meta.situacao == "cabe" and p.todas[0].nome == "Reserva"  # a reserva passa na frente
    assert [(a.nome, a.meses) for a in p.atrasos] == [("Cadeira", 1)]  # 600 -> 500 por mês: 5 meses viram 6


def test_simular_nova_meta_que_tira_tudo_de_outra_marca_deixa_de_caber(conn):
    _historico(conn)
    _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)
    p = _nova(conn, nome="Reserva", valor_alvo_centavos=9_000_000, mensal_centavos=300_000, reserva=True)
    assert [(a.nome, a.meses) for a in p.atrasos] == [("Viagem", None)]


def test_simular_nova_meta_valida(conn):
    with pytest.raises(ValueError, match="nome"):
        _nova(conn, nome="")
    with pytest.raises(ValueError, match="prazo"):
        _nova(conn, modo="prazo", prazo=None)


def test_simular_edicao_compara_com_a_meta_como_estava(conn):
    _historico(conn)  # sobra 2000
    a = _meta(conn, "Viagem", valor=600_000, modo="mensal", mensal=100_000)  # 1000
    b = _meta(conn, "Cadeira", valor=300_000, modo="mensal", mensal=60_000)  # 600
    c = _meta(conn, "Tela", valor=200_000, modo="mensal", mensal=40_000)  # 400: cabe tudo (2000)
    p = metas.simular_edicao(conn, HOJE, a, nome="Viagem", valor_alvo_centavos=600_000, modo="mensal", mensal_centavos=150_000)
    assert p.meta.alocado == 150_000
    # sobram 500 pra Cadeira (era 600: 5 meses viram 6) e nada pra Tela (deixa de caber)
    assert [(x.nome, x.meses) for x in p.atrasos] == [("Cadeira", 1), ("Tela", None)]
    assert metas.obter(conn, a).mensal_centavos == 100_000  # nada gravado
    p = metas.simular_edicao(conn, HOJE, a, nome="Viagem", valor_alvo_centavos=600_000, modo="mensal", mensal_centavos=50_000)
    assert p.atrasos == []  # pedir menos não atrasa ninguém


def test_simular_edicao_mantem_o_guardado(conn):
    _historico(conn)
    m = _meta(conn, "Cadeira", valor=150_000, prazo=date(2027, 3, 1))
    _guardar(conn, m, 90_000)
    p = metas.simular_edicao(conn, HOJE, m, nome="Cadeira", valor_alvo_centavos=300_000, modo="prazo", prazo=date(2027, 3, 1))
    assert p.meta.restante == 210_000 and p.meta.necessario == 35_000


# --- Comprar agora ou parcelar? -----------------------------------------------------------------------------


def test_simular_compra_a_vista_cabe_no_livre_agora(conn):
    _lancar(conn, True, 500_000, date(2026, 10, 1), "Salário")
    s = metas.simular_compra(conn, HOJE, 100_000, 1)
    assert s["a_vista"]["cabe_agora"] is True
    assert s["a_vista"]["livre_agora"] == relatorios.livre_ate_fim_do_mes(conn, HOJE) == 500_000


def test_simular_compra_a_vista_quanto_tempo_guardando_pela_sobra_livre(conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Viagem", valor=1_200_000, modo="mensal", mensal=100_000)  # compromete 1000 -> livre 1000
    s = metas.simular_compra(conn, HOJE, 900_000, 1)  # o Livre de hoje é 6000
    v = s["a_vista"]
    assert v["cabe_agora"] is False and v["livre_agora"] == 600_000 and v["guardando_por_mes"] == 100_000
    assert (v["meses"], v["mes_previsto"]) == (9, date(2027, 6, 1))


def test_simular_compra_sem_sobra_livre_nao_tem_previsao(conn):
    s = metas.simular_compra(conn, HOJE, 450_000, 1)
    assert s["a_vista"]["meses"] is None and s["a_vista"]["mes_previsto"] is None and s["estimativa"] is True
    _historico(conn)
    _meta(conn, "Viagem", valor=9_000_000, modo="mensal", mensal=250_000)  # pede mais que a sobra
    assert metas.simular_compra(conn, HOJE, 450_000, 1)["a_vista"]["meses"] is None


def test_simular_compra_parcelada_valor_mes_final_e_sobra(conn):
    _historico(conn)  # sobra 2000
    s = metas.simular_compra(conn, HOJE, 150_000, 10)
    p = s["parcelado"]
    assert (p["parcela_centavos"], p["total_centavos"], p["acrescimo_centavos"]) == (15_000, 150_000, 0)
    assert p["mes_final"] == date(2027, 7, 1)  # out/2026 + 9 meses
    assert (p["sobra_antes"], p["sobra_depois"], p["atrasos"]) == (200_000, 185_000, [])


def test_simular_compra_parcelada_com_acrescimo(conn):
    _historico(conn)
    p = metas.simular_compra(conn, HOJE, 150_000, 10, acrescimo_centavos=30_000)["parcelado"]
    assert (p["parcela_centavos"], p["total_centavos"], p["acrescimo_centavos"]) == (18_000, 180_000, 30_000)
    assert p["sobra_depois"] == 182_000


def test_simular_compra_parcelada_em_uma_vez_termina_no_mes_atual(conn):
    p = metas.simular_compra(conn, HOJE, 10_000, 1)["parcelado"]
    assert p["mes_final"] == date(2026, 10, 1) and p["parcela_centavos"] == 10_000


def test_simular_compra_parcelada_diz_quais_metas_atrasam_e_quanto(conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Viagem", valor=1_200_000, modo="mensal", mensal=100_000)  # 12 meses
    _meta(conn, "Cadeira", valor=300_000, modo="mensal", mensal=100_000)  # 3 meses
    # 10 x 1500: a sobra cai de 2000 pra 500 durante 10 meses. A Viagem (1000) passa a receber 500 e a Cadeira nada.
    s = metas.simular_compra(conn, HOJE, 1_500_000, 10)
    assert s["parcelado"]["sobra_depois"] == 50_000
    # Viagem: 12 meses -> 10 meses a 500 (5000) + 7000 faltantes a 1000 = 17 (+5). Cadeira: 3 -> 10 + 3 = 13 (+10)
    assert [(a.nome, a.meses) for a in s["parcelado"]["atrasos"]] == [("Viagem", 5), ("Cadeira", 10)]


def test_simular_compra_parcela_que_cabe_na_sobra_livre_nao_atrasa_ninguem(conn):
    _historico(conn)
    _meta(conn, "Viagem", valor=1_200_000, modo="mensal", mensal=100_000)
    p = metas.simular_compra(conn, HOJE, 800_000, 10)["parcelado"]  # parcela de 800 < livre de 1000
    assert p["atrasos"] == [] and p["sobra_depois"] == 120_000


def test_simular_compra_meta_que_acaba_dentro_do_parcelamento(conn):
    _historico(conn)  # sobra 2000
    _meta(conn, "Cadeira", valor=300_000, modo="mensal", mensal=100_000)  # 3 meses
    _meta(conn, "Tela", valor=200_000, modo="mensal", mensal=100_000)  # 2 meses
    # parcela de 1500 -> sobra 500. Cadeira recebe 500 e termina dentro do parcelamento (6 meses, +3);
    # Tela fica sem nada até as parcelas acabarem: 10 + 2 = 12 meses (+10)
    p = metas.simular_compra(conn, HOJE, 1_500_000, 10)["parcelado"]
    assert [(a.nome, a.meses) for a in p["atrasos"]] == [("Cadeira", 3), ("Tela", 10)]


def test_simular_compra_valida(conn):
    for args in ((0, 1), (-1, 1), (1000, 0), (1000, 25)):
        with pytest.raises(ValueError):
            metas.simular_compra(conn, HOJE, *args)
    with pytest.raises(ValueError, match="acréscimo"):
        metas.simular_compra(conn, HOJE, 1000, 2, acrescimo_centavos=-1)


# --- Recentes -----------------------------------------------------------------------------------------------


def test_recentes_mostra_guardar_e_retirar_uma_vez_so_pelo_lado_da_conta(conn):
    _lancar(conn, False, 5_000, date(2026, 10, 18), "Mercado", descricao="Feira")
    m = _meta(conn, "Cadeira")
    _guardar(conn, m, 30_000, date(2026, 10, 19))
    metas.retirar(conn, m, 10_000, id_conta(conn, "Inter"), date(2026, 10, 20))
    df = lanc.recentes(conn, HOJE, 10)
    assert df["descricao"].tolist() == ["Retirado: Cadeira", "Guardado: Cadeira", "Feira"]
    assert df["conta"].tolist() == ["Inter", "Inter", "Inter"]
    assert df["valor_centavos"].tolist() == [10_000, -30_000, -5_000]
    assert df["meta_id"].isna().tolist() == [False, False, True]
    assert lanc.recentes(conn, HOJE, 10, "saidas")["descricao"].tolist() == ["Feira"]


def test_mes_em_que_o_uso_comecou_tarde_nao_entra_na_media(conn):
    """Começou a usar no dia 27: aquele mês tem só 5 dias de gastos e não pode virar 'mês completo'."""
    _lancar(conn, True, 500_000, date(2026, 10, 27), "Salário")  # salário cheio, poucos gastos
    _lancar(conn, False, 10_000, date(2026, 10, 28), "Mercado")
    _lancar(conn, True, 500_000, date(2026, 11, 5), "Salário")
    _lancar(conn, False, 300_000, date(2026, 11, 10), "Mercado")
    valor, meses, estimativa = metas.sobra_media(conn, date(2026, 12, 3))
    assert (valor, meses, estimativa) == (200_000, 1, False)  # só novembro conta


def test_mes_em_que_o_uso_comecou_cedo_entra_na_media(conn):
    _lancar(conn, True, 500_000, date(2026, 10, 3), "Salário")
    _lancar(conn, False, 100_000, date(2026, 10, 20), "Mercado")
    valor, meses, _ = metas.sobra_media(conn, date(2026, 11, 10))
    assert (valor, meses) == (400_000, 1)
