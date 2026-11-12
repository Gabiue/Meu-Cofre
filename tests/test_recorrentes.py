"""Assinaturas e fixos (F5): gerar, editar, pausar, encerrar, listar, resumo e o efeito no Livre."""

import sqlite3
from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import db, migracoes
from meu_cofre import lancamentos as lanc
from meu_cofre import recorrentes as rec
from meu_cofre import relatorios as rel

HOJE = date(2026, 10, 20)


def _criar(conn, descricao="Streaming", valor=-3990, conta="Cartão de crédito", metodo="cartao", dia=25,
           inicio=date(2026, 10, 1), assinatura=True, cat="Assinaturas"):
    return rec.criar(
        conn, descricao=descricao, valor_centavos=valor, conta_id=id_conta(conn, conta),
        categoria_id=id_cat(conn, cat), metodo=metodo, dia_do_mes=dia, inicio=inicio, assinatura=assinatura,
    )


def _gerados(conn, recorrente_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM lancamentos WHERE recorrente_id = ? ORDER BY data", (recorrente_id,))]


def _meses(conn, recorrente_id):
    return {r["mes"]: r["lancamento_id"] for r in conn.execute(
        "SELECT mes, lancamento_id FROM recorrentes_meses WHERE recorrente_id = ?", (recorrente_id,))}


# --- Criar --------------------------------------------------------------------------------


def test_criar_valida_os_campos(conn):
    with pytest.raises(ValueError, match="o que é"):
        _criar(conn, descricao="  ")
    with pytest.raises(ValueError, match="zero"):
        _criar(conn, valor=0)
    with pytest.raises(ValueError, match="1 a 31"):
        _criar(conn, dia=32)
    with pytest.raises(ValueError, match="Método"):
        _criar(conn, metodo="ted")


def test_criar_cartao_usa_o_cartao_e_dinheiro_a_carteira(conn):
    a = _criar(conn, conta="Inter", metodo="cartao")  # cartão escolhido errado -> primeiro cartão ativo
    b = _criar(conn, descricao="Feira", valor=-5000, conta="Inter", metodo="dinheiro", assinatura=False)
    assert rec.obter(conn, a)["conta"] == "Cartão de crédito"
    assert rec.obter(conn, b)["conta"] == "Carteira"


def test_criar_a_partir_de_lancamento(conn):
    (lid,) = lanc.registrar(conn, entrada=False, valor_centavos=5590, descricao="Música", metodo="cartao",
                            categoria_id=id_cat(conn, "Assinaturas"), conta_id=id_conta(conn, "Cartão de crédito"),
                            data=date(2026, 10, 7))
    rid = rec.criar_a_partir_de_lancamento(conn, lid, assinatura=True)
    r = rec.obter(conn, rid)
    assert (r["dia_do_mes"], r["inicio"], r["valor_centavos"], r["assinatura"]) == (7, "2026-10-07", -5590, 1)
    assert (r["conta"], r["categoria"], r["metodo"]) == ("Cartão de crédito", "Assinaturas", "cartao")
    assert _meses(conn, rid) == {"2026-10": lid}
    assert lanc.obter(conn, lid)["recorrente_id"] == rid
    assert rec.gerar_pendentes(conn, HOJE) == 0  # o mês do lançamento já está feito: sem duplicata
    assert len(_gerados(conn, rid)) == 1


def test_criar_a_partir_de_lancamento_recusa_casos_invalidos(conn):
    (lid,) = lanc.registrar(conn, entrada=False, valor_centavos=100, descricao="x", metodo="pix",
                            categoria_id=None, conta_id=id_conta(conn, "Inter"), data=HOJE)
    rec.criar_a_partir_de_lancamento(conn, lid, assinatura=False)
    with pytest.raises(ValueError, match="já repete"):
        rec.criar_a_partir_de_lancamento(conn, lid, assinatura=False)
    parcelas = lanc.registrar(conn, entrada=False, valor_centavos=3000, descricao="TV", metodo="cartao",
                              categoria_id=None, conta_id=id_conta(conn, "Cartão de crédito"), data=HOJE, parcelas=3)
    with pytest.raises(ValueError, match="parcelada"):
        rec.criar_a_partir_de_lancamento(conn, parcelas[0], assinatura=False)
    with pytest.raises(ValueError, match="não existe"):
        rec.criar_a_partir_de_lancamento(conn, 9999, assinatura=False)


# --- Gerar --------------------------------------------------------------------------------


def test_gera_desde_o_inicio_com_previsto_e_realizado(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 8, 1))
    assert rec.gerar_pendentes(conn, HOJE) == 3  # ago, set (realizados) e out (previsto: dia 25 > 20)
    ls = _gerados(conn, rid)
    assert [(l["data"], l["status"]) for l in ls] == [
        ("2026-08-25", "realizado"), ("2026-09-25", "realizado"), ("2026-10-25", "previsto")]
    assert all(l["origem"] == "recorrente" and l["valor_centavos"] == -3990 for l in ls)
    assert (ls[0]["conta_id"], ls[0]["metodo"], ls[0]["descricao"]) == (id_conta(conn, "Cartão de crédito"), "cartao", "Streaming")


def test_gerar_e_idempotente(conn):
    _criar(conn, inicio=date(2026, 8, 1))
    assert rec.gerar_pendentes(conn, HOJE) == 3
    assert rec.gerar_pendentes(conn, HOJE) == 0
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 3


def test_dia_31_cai_no_ultimo_dia_do_mes(conn):
    rid = _criar(conn, dia=31, inicio=date(2026, 1, 31))
    rec.gerar_pendentes(conn, date(2026, 3, 31))
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    rid2 = _criar(conn, descricao="Bissexto", dia=30, inicio=date(2028, 1, 1))
    rec.gerar_pendentes(conn, date(2028, 2, 10))
    assert [l["data"] for l in _gerados(conn, rid2)] == ["2028-01-30", "2028-02-29"]


def test_primeiro_mes_so_vale_se_o_dia_nao_passou_do_inicio(conn):
    rid = _criar(conn, dia=5, inicio=date(2026, 10, 12))
    rec.gerar_pendentes(conn, HOJE)
    assert _gerados(conn, rid) == []  # 5/out é antes do início (12/out)
    rec.gerar_pendentes(conn, date(2026, 11, 20))
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-11-05"]


def test_previsto_vira_realizado_quando_o_dia_chega(conn):
    rid = _criar(conn, dia=25)
    rec.gerar_pendentes(conn, HOJE)
    assert _gerados(conn, rid)[0]["status"] == "previsto"
    rec.gerar_pendentes(conn, date(2026, 10, 24))
    assert _gerados(conn, rid)[0]["status"] == "previsto"
    rec.gerar_pendentes(conn, date(2026, 10, 25))  # no próprio dia
    assert [l["status"] for l in _gerados(conn, rid)] == ["realizado"]


def test_promocao_nao_mexe_em_previsto_de_outra_origem(conn):
    lanc.registrar(conn, entrada=False, valor_centavos=100, descricao="x", metodo="pix",
                   categoria_id=None, conta_id=id_conta(conn, "Inter"), data=date(2026, 10, 1))
    conn.execute("UPDATE lancamentos SET status = 'previsto'")
    rec.gerar_pendentes(conn, HOJE)
    assert conn.execute("SELECT status FROM lancamentos").fetchone()[0] == "previsto"


def test_apagar_o_lancamento_gerado_nao_recria(conn):
    rid = _criar(conn, dia=5, inicio=date(2026, 8, 1))
    rec.gerar_pendentes(conn, HOJE)
    setembro = [l for l in _gerados(conn, rid) if l["data"] == "2026-09-05"][0]
    lanc.excluir(conn, [setembro["id"]])
    assert rec.gerar_pendentes(conn, HOJE) == 0
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-08-05", "2026-10-05"]
    assert _meses(conn, rid)["2026-09"] is None
    # e o mês seguinte continua sendo gerado normalmente
    assert rec.gerar_pendentes(conn, date(2026, 11, 6)) == 1


def test_respeita_o_fim(conn):
    rid = _criar(conn, dia=10, inicio=date(2026, 7, 1))
    conn.execute("UPDATE recorrentes SET fim = '2026-09-15' WHERE id = ?", (rid,))
    rec.gerar_pendentes(conn, date(2026, 12, 1))
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-07-10", "2026-08-10", "2026-09-10"]


def test_nao_gera_de_pausada_nem_de_encerrada(conn):
    a = _criar(conn, descricao="Pausada")
    b = _criar(conn, descricao="Encerrada")
    conn.execute("UPDATE recorrentes SET pausada = 1 WHERE id = ?", (a,))
    conn.execute("UPDATE recorrentes SET ativa = 0 WHERE id = ?", (b,))
    assert rec.gerar_pendentes(conn, HOJE) == 0


def test_entrada_gera_lancamento_positivo(conn):
    rid = _criar(conn, descricao="Salário", valor=520000, conta="Inter", metodo="pix", dia=5, assinatura=False,
                 cat="Salário")
    rec.gerar_pendentes(conn, HOJE)
    (l,) = _gerados(conn, rid)
    assert l["valor_centavos"] == 520000 and l["status"] == "realizado"


# --- Editar -------------------------------------------------------------------------------


def test_editar_so_afeta_o_futuro(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 9, 1))
    rec.gerar_pendentes(conn, HOJE)  # set realizado, out previsto
    rec.editar(conn, rid, HOJE, valor_centavos=-4990, descricao="Streaming Plus", dia_do_mes=28)
    setembro, outubro = _gerados(conn, rid)
    assert (setembro["valor_centavos"], setembro["descricao"], setembro["data"]) == (-3990, "Streaming", "2026-09-25")
    assert (outubro["valor_centavos"], outubro["descricao"], outubro["data"]) == (-4990, "Streaming Plus", "2026-10-28")
    assert rec.obter(conn, rid)["dia_do_mes"] == 28
    rec.gerar_pendentes(conn, date(2026, 11, 1))  # os próximos meses saem com o molde novo
    assert _gerados(conn, rid)[-1]["valor_centavos"] == -4990


def test_editar_nao_mexe_no_ja_cobrado_do_mes(conn):
    rid = _criar(conn, dia=5, inicio=date(2026, 10, 1))
    rec.gerar_pendentes(conn, HOJE)  # 5/out já realizado
    rec.editar(conn, rid, HOJE, valor_centavos=-9900)
    assert _gerados(conn, rid)[0]["valor_centavos"] == -3990
    rec.gerar_pendentes(conn, date(2026, 11, 20))
    assert _gerados(conn, rid)[1]["valor_centavos"] == -9900


def test_editar_dia_que_cairia_no_passado_mantem_a_data_do_previsto(conn):
    rid = _criar(conn, dia=25)
    rec.gerar_pendentes(conn, HOJE)
    rec.editar(conn, rid, HOJE, dia_do_mes=10, valor_centavos=-1000)
    (l,) = _gerados(conn, rid)
    assert (l["data"], l["status"], l["valor_centavos"]) == ("2026-10-25", "previsto", -1000)
    rec.gerar_pendentes(conn, date(2026, 11, 12))
    assert _gerados(conn, rid)[-1]["data"] == "2026-11-10"


def test_editar_troca_sinal_conta_e_assinatura(conn):
    rid = _criar(conn, dia=25)
    rec.gerar_pendentes(conn, HOJE)
    rec.editar(conn, rid, HOJE, valor_centavos=2000, metodo="pix", conta_id=id_conta(conn, "Inter"), assinatura=False)
    r = rec.obter(conn, rid)
    assert (r["assinatura"], r["metodo"], r["conta"]) == (0, "pix", "Inter")
    (l,) = _gerados(conn, rid)
    assert (l["valor_centavos"], l["metodo"], l["conta_id"]) == (2000, "pix", id_conta(conn, "Inter"))


def test_editar_valida(conn):
    rid = _criar(conn)
    with pytest.raises(ValueError, match="não editáveis"):
        rec.editar(conn, rid, HOJE, ativa=0)
    with pytest.raises(ValueError, match="zero"):
        rec.editar(conn, rid, HOJE, valor_centavos=0)
    with pytest.raises(ValueError, match="não existe"):
        rec.editar(conn, 999, HOJE, descricao="x")


# --- Pausar, retomar, encerrar ----------------------------------------------------------------


def test_pausar_apaga_previstos_futuros_e_para_de_gerar(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 9, 1))
    rec.gerar_pendentes(conn, HOJE)
    rec.pausar(conn, rid, HOJE)
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-09-25"]  # o previsto de out sumiu
    assert rec.gerar_pendentes(conn, date(2026, 12, 1)) == 0
    assert rec.obter(conn, rid)["pausada"] == 1


def test_pausar_fecha_as_contas_ate_hoje_antes_de_parar(conn):
    rid = _criar(conn, dia=5, inicio=date(2026, 8, 1))  # nunca gerou (app fechado)
    rec.pausar(conn, rid, HOJE)
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-08-05", "2026-09-05", "2026-10-05"]


def test_retomar_nao_gera_retroativos(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 8, 1))
    rec.gerar_pendentes(conn, date(2026, 8, 30))  # ago realizado
    rec.pausar(conn, rid, date(2026, 8, 30))
    rec.retomar(conn, rid, date(2026, 11, 3))  # parada em set e out
    assert rec.obter(conn, rid)["pausada"] == 0
    assert _meses(conn, rid) == {"2026-08": _gerados(conn, rid)[0]["id"], "2026-09": None, "2026-10": None}
    assert rec.gerar_pendentes(conn, date(2026, 11, 3)) == 1
    assert [(l["data"], l["status"]) for l in _gerados(conn, rid)] == [
        ("2026-08-25", "realizado"), ("2026-11-25", "previsto")]


def test_pausar_e_retomar_no_mesmo_mes_gera_o_previsto_de_novo(conn):
    rid = _criar(conn, dia=25)
    rec.gerar_pendentes(conn, HOJE)
    rec.pausar(conn, rid, HOJE)
    assert _gerados(conn, rid) == []
    rec.retomar(conn, rid, date(2026, 10, 22))
    rec.gerar_pendentes(conn, date(2026, 10, 22))
    assert [(l["data"], l["status"]) for l in _gerados(conn, rid)] == [("2026-10-25", "previsto")]


def test_encerrar(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 9, 1))
    rec.gerar_pendentes(conn, HOJE)
    rec.encerrar(conn, rid, HOJE)
    r = rec.obter(conn, rid)
    assert (r["ativa"], r["fim"]) == (0, "2026-10-20")
    assert [l["data"] for l in _gerados(conn, rid)] == ["2026-09-25"]
    assert rec.gerar_pendentes(conn, date(2027, 1, 1)) == 0
    rec.encerrar(conn, rid, date(2027, 1, 1))  # de novo: não muda nada
    assert rec.obter(conn, rid)["fim"] == "2026-10-20"


def test_encerrar_pausada_e_no_dia_da_cobranca_mantem_o_cobrado(conn):
    a = _criar(conn, descricao="A", dia=20)
    rec.gerar_pendentes(conn, HOJE)  # cobrada hoje
    rec.encerrar(conn, a, HOJE)
    assert len(_gerados(conn, a)) == 1
    b = _criar(conn, descricao="B", dia=25)
    rec.pausar(conn, b, HOJE)
    rec.encerrar(conn, b, HOJE)
    r = rec.obter(conn, b)
    assert (r["ativa"], r["pausada"]) == (0, 0)


# --- Listar e resumo ------------------------------------------------------------------------------


@pytest.fixture
def varias(conn):
    ids = {
        "salario": _criar(conn, descricao="Salário", valor=520000, conta="Inter", metodo="pix", dia=5,
                          assinatura=False, cat="Salário", inicio=date(2026, 8, 1)),
        "aluguel": _criar(conn, descricao="Aluguel", valor=-150000, conta="Inter", metodo="boleto", dia=25,
                          assinatura=False, cat="Moradia", inicio=date(2026, 8, 1)),
        "video": _criar(conn, descricao="Vídeo", valor=-3990, dia=7, inicio=date(2026, 8, 1)),
        "musica": _criar(conn, descricao="Música", valor=-2190, dia=28, inicio=date(2026, 9, 1)),
        "pausada": _criar(conn, descricao="Revista", valor=-1990, dia=3, inicio=date(2026, 8, 1)),
        "velha": _criar(conn, descricao="Antiga", valor=-999, dia=2, inicio=date(2026, 6, 1)),
    }
    rec.gerar_pendentes(conn, HOJE)
    rec.pausar(conn, ids["pausada"], HOJE)
    rec.encerrar(conn, ids["velha"], HOJE)
    return ids


def test_listar_situacao_dias_e_totais(conn, varias):
    df = rec.listar(conn, HOJE).set_index("descricao")
    assert set(df.index) == {"Salário", "Aluguel", "Vídeo", "Música", "Revista"}  # sem a encerrada
    assert df.loc["Vídeo", "situacao_mes"] == "cobrada" and df.loc["Vídeo", "dias_para_cobrar"] is None
    assert df.loc["Aluguel", "situacao_mes"] == "prevista" and df.loc["Aluguel", "dias_para_cobrar"] == 5
    assert df.loc["Música", "dias_para_cobrar"] == 8
    assert df.loc["Revista", "situacao_mes"] == "pausada" and df.loc["Revista", "pausada"]
    # total até hoje: ago, set e out cobrados (3 × 39,90); desde o primeiro cobrado
    assert df.loc["Vídeo", "total_ate_hoje"] == 3 * 3990
    assert df.loc["Vídeo", "desde"] == date(2026, 8, 7)
    assert df.loc["Aluguel", "total_ate_hoje"] == 2 * 150000  # out ainda é previsto
    assert df.loc["Salário", "total_ate_hoje"] == 3 * 520000  # entrada também vem positiva
    assert df.loc["Vídeo", "conta"] == "Cartão de crédito" and df.loc["Vídeo", "conta_tipo"] == "cartao"
    assert df.loc["Vídeo", "assinatura"] and not df.loc["Aluguel", "assinatura"]
    assert df.loc["Salário", "valor_centavos"] == 520000 and df.loc["Vídeo", "dia_do_mes"] == 7
    assert {"id", "metodo", "categoria", "ativa", "inicio"} <= set(df.reset_index().columns)


def test_listar_filtros_e_encerradas(conn, varias):
    assert set(rec.listar(conn, HOJE, "assinaturas")["descricao"]) == {"Vídeo", "Música", "Revista"}
    assert set(rec.listar(conn, HOJE, "fixos")["descricao"]) == {"Salário", "Aluguel"}
    todos = rec.listar(conn, HOJE, "ambos", incluir_encerradas=True).set_index("descricao")
    assert todos.loc["Antiga", "situacao_mes"] == "encerrada" and not todos.loc["Antiga", "ativa"]
    assert todos.loc["Antiga", "total_ate_hoje"] == 5 * 999  # jun a out (o dia 2 de out já passou quando encerrou)
    with pytest.raises(ValueError):
        rec.listar(conn, HOJE, "outros")


def test_listar_vazio_tem_as_colunas(conn):
    df = rec.listar(conn, HOJE)
    assert df.empty and list(df.columns) == rec.COLUNAS_LISTA + ["dias_para_cobrar"]


def test_lancamento_apagado_vira_sem_cobranca(conn):
    rid = _criar(conn, dia=5)
    rec.gerar_pendentes(conn, HOJE)
    lanc.excluir(conn, [_gerados(conn, rid)[0]["id"]])
    assert rec.listar(conn, HOJE).iloc[0]["situacao_mes"] == "sem_cobranca"


def test_resumo(conn, varias):
    r = rec.resumo(conn, HOJE, "ambos")
    # saídas fixas (sem a pausada e a encerrada): aluguel + vídeo + música
    assert r["saidas_por_mes"] == 150000 + 3990 + 2190
    assert r["saidas_por_ano"] == 12 * r["saidas_por_mes"]
    assert r["entradas_por_mes"] == 520000
    assert r["este_mes_cobradas"] == (1, 3990)
    assert r["este_mes_a_cobrar"] == (2, 150000 + 2190)  # a entrada (salário) não entra nas contagens
    assinaturas = rec.resumo(conn, HOJE, "assinaturas")
    assert assinaturas["saidas_por_mes"] == 3990 + 2190 and assinaturas["entradas_por_mes"] == 0
    assert rec.resumo(conn, HOJE, "fixos")["saidas_por_mes"] == 150000


def test_economia_so_conta_saida_ativa(conn, varias):
    e = rec.economia(conn, HOJE, {varias["video"], varias["musica"], varias["pausada"], varias["salario"], varias["velha"]})
    assert e["nomes"] == ["Música", "Vídeo"]  # a pausada, a entrada e a encerrada não entram
    assert (e["mensal"], e["anual"]) == (3990 + 2190, 12 * (3990 + 2190))
    assert e["pct_entradas"] == pytest.approx((3990 + 2190) / 520000 * 100)


def test_economia_vazia_e_sem_entrada_fixa(conn):
    rid = _criar(conn)
    assert rec.economia(conn, HOJE, set())["mensal"] == 0
    assert rec.economia(conn, HOJE, {rid})["pct_entradas"] is None  # sem salário fixo, sem percentual


def test_resumo_vazio(conn):
    assert rec.resumo(conn, HOJE) == {
        "saidas_por_mes": 0, "saidas_por_ano": 0, "entradas_por_mes": 0,
        "este_mes_cobradas": (0, 0), "este_mes_a_cobrar": (0, 0),
    }


# --- Efeito nos números da tela inicial ----------------------------------------------------------------


def test_livre_desconta_previstos_de_saida_e_ignora_entrada_futura(conn):
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 1000000 WHERE nome = 'Inter'")
    antes = rel.livre_ate_fim_do_mes(conn, HOJE)
    _criar(conn, descricao="Streaming", valor=-3990, dia=25)  # prevista no cartão
    _criar(conn, descricao="Aluguel", valor=-150000, conta="Inter", metodo="boleto", dia=28, assinatura=False,
           cat="Moradia")  # prevista no Inter
    _criar(conn, descricao="Salário", valor=520000, conta="Inter", metodo="pix", dia=30, assinatura=False,
           cat="Salário")  # entrada futura: não conta
    rec.gerar_pendentes(conn, HOJE)
    assert rel.livre_ate_fim_do_mes(conn, HOJE) == antes - 3990 - 150000
    assert rel.devo(conn) == 0  # prevista ainda não usa o limite do cartão (o Livre já a desconta, acima)
    assert rel.em_conta(conn, HOJE) == 1000000  # nada saiu ainda


def test_previsto_realizado_no_dia_move_o_em_conta(conn):
    conn.execute("UPDATE contas SET saldo_inicial_centavos = 1000000 WHERE nome = 'Inter'")
    _criar(conn, descricao="Aluguel", valor=-150000, conta="Inter", metodo="boleto", dia=25, assinatura=False)
    rec.gerar_pendentes(conn, HOJE)
    livre = rel.livre_ate_fim_do_mes(conn, HOJE)
    dia = date(2026, 10, 25)
    rec.gerar_pendentes(conn, dia)
    assert rel.em_conta(conn, dia) == 850000
    assert rel.livre_ate_fim_do_mes(conn, dia) == livre  # o Livre não muda: já estava descontado


# --- Lançamento vindo de recorrente -----------------------------------------------------------------------


def test_obter_expoe_dados_da_recorrencia(conn):
    rid = _criar(conn, dia=7, assinatura=True)
    rec.gerar_pendentes(conn, HOJE)
    l = lanc.obter(conn, _gerados(conn, rid)[0]["id"])
    assert (l["recorrente_id"], l["recorrente_dia"], l["recorrente_assinatura"], l["recorrente_ativa"]) == (rid, 7, 1, 1)
    (avulso,) = lanc.registrar(conn, entrada=False, valor_centavos=100, descricao="x", metodo="pix",
                               categoria_id=None, conta_id=id_conta(conn, "Inter"), data=HOJE)
    assert lanc.obter(conn, avulso)["recorrente_dia"] is None


# --- Migração v2 -> v3 --------------------------------------------------------------------------------------


def test_migracao_v2_para_v3_preserva_dados_e_cria_backup(tmp_path):
    caminho = tmp_path / "meu.db"
    db.conectar(caminho).close()
    c = sqlite3.connect(caminho)
    c.execute("DROP TABLE recorrentes_meses")
    c.execute("DROP TABLE operacoes_rv")  # tira também o que é da v7
    c.execute("DROP INDEX ux_ativos_ticker")
    c.execute("ALTER TABLE ativos DROP COLUMN ticker")
    c.execute("DROP TABLE faturas_pagas")  # o banco de teste já nasce na última versão: tira o que é da v4 e da v5
    c.execute("DROP INDEX ix_lancamentos_meta")
    c.execute("ALTER TABLE lancamentos DROP COLUMN meta_id")
    c.execute("DROP TABLE metas")
    c.execute("ALTER TABLE contas DROP COLUMN final")
    c.execute("PRAGMA user_version = 2")
    c.execute("INSERT INTO recorrentes (descricao, valor_centavos, conta_id, dia_do_mes, inicio, assinatura) "
              "VALUES ('Netflix', -4490, 3, 7, '2026-01-01', 1)")
    c.commit()
    c.close()

    conn = db.conectar(caminho)
    assert migracoes.versao(conn) == migracoes.ULTIMA_VERSAO
    assert conn.execute("SELECT descricao, valor_centavos FROM recorrentes").fetchone()[:] == ("Netflix", -4490)
    colunas = {r["name"] for r in conn.execute("PRAGMA table_info(recorrentes_meses)")}
    assert colunas == {"recorrente_id", "mes", "lancamento_id"}
    assert len(list((tmp_path / "backups").glob("meu-*-v2.db"))) == 1
    assert rec.gerar_pendentes(conn, date(2026, 3, 10)) == 3  # jan, fev, mar
    conn.close()


def test_reativar_encerrada_nao_gera_retroativos(conn):
    rid = _criar(conn, dia=25, inicio=date(2026, 8, 1))
    rec.gerar_pendentes(conn, date(2026, 8, 30))  # ago realizado
    rec.encerrar(conn, rid, date(2026, 8, 30))
    rec.reativar(conn, rid, date(2026, 11, 3))  # parada em set e out
    r = rec.obter(conn, rid)
    assert (r["ativa"], r["pausada"], r["fim"]) == (1, 0, None)
    assert rec.gerar_pendentes(conn, date(2026, 11, 3)) == 1
    assert [(l["data"], l["status"]) for l in _gerados(conn, rid)] == [
        ("2026-08-25", "realizado"), ("2026-11-25", "previsto")]
    rec.reativar(conn, rid, date(2026, 11, 3))  # já ativa: não muda nada
    assert rec.obter(conn, rid)["ativa"] == 1


def test_reativar_no_mesmo_mes_gera_o_previsto_de_novo(conn):
    rid = _criar(conn, dia=25)
    rec.gerar_pendentes(conn, HOJE)
    rec.encerrar(conn, rid, HOJE)
    assert _gerados(conn, rid) == []
    rec.reativar(conn, rid, date(2026, 10, 22))
    rec.gerar_pendentes(conn, date(2026, 10, 22))
    assert [(l["data"], l["status"]) for l in _gerados(conn, rid)] == [("2026-10-25", "previsto")]
