"""Histórico, correção e exclusão de operações de investimento (issue #6). Sem Qt. Nada pode ficar órfão."""

from datetime import date

import pytest
from conftest import id_conta

from meu_cofre import cadastros
from meu_cofre import investimentos as inv
from meu_cofre import renda_variavel as rv

HOJE = date(2026, 10, 30)


def _inter(conn):
    return id_conta(conn, "Inter")


def _saldo(conn):
    return cadastros.saldo_da_conta(conn, _inter(conn), HOJE)


def _sem_orfaos(conn):
    """Toda transferência tem os dois lados e dono; todo vínculo aponta pra algo que existe."""
    for r in conn.execute("SELECT transferencia_id, COUNT(*) n FROM lancamentos WHERE transferencia_id IS NOT NULL GROUP BY 1"):
        assert r["n"] == 2
    dono = {r[0] for r in conn.execute(
        "SELECT transferencia_id FROM movimentacoes_ativos WHERE transferencia_id IS NOT NULL "
        "UNION SELECT transferencia_id FROM operacoes_rv WHERE transferencia_id IS NOT NULL")}
    assert {r[0] for r in conn.execute("SELECT DISTINCT transferencia_id FROM lancamentos WHERE transferencia_id IS NOT NULL")} == dono
    assert conn.execute(
        "SELECT COUNT(*) FROM operacoes_rv o WHERE o.afeta_conta = 1 AND o.transferencia_id IS NULL AND o.lancamento_id IS NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM operacoes_rv WHERE lancamento_id IS NOT NULL AND lancamento_id NOT IN (SELECT id FROM lancamentos)"
    ).fetchone()[0] == 0


def _cdb(conn):
    return inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="pre", taxa=10.0)


def _acao(conn, ticker="PETR4"):
    return inv.criar(conn, HOJE, nome=ticker, tipo="acao", ticker=ticker)


def _lados(conn, tid):
    return sorted(r[0] for r in conn.execute("SELECT valor_centavos FROM lancamentos WHERE transferencia_id = ?", (tid,)))


# --- Renda fixa ---------------------------------------------------------------------------------------


def test_editar_aporte_acerta_os_dois_lados_e_o_saldo(conn):
    a = _cdb(conn)
    base = _saldo(conn)
    tid = inv.aportar(conn, a, 100_000, _inter(conn), date(2026, 9, 1))
    mov = conn.execute("SELECT id FROM movimentacoes_ativos WHERE transferencia_id = ?", (tid,)).fetchone()[0]
    inv.editar_movimentacao(conn, mov, valor=80_000, data=date(2026, 9, 5))
    assert _lados(conn, tid) == [-80_000, 80_000]
    assert {r[0] for r in conn.execute("SELECT data FROM lancamentos WHERE transferencia_id = ?", (tid,))} == {"2026-09-05"}
    assert _saldo(conn) == base - 80_000
    assert inv.historico(conn, a)[0]["valor"] == 80_000
    _sem_orfaos(conn)
    with pytest.raises(ValueError, match="maior que zero"):
        inv.editar_movimentacao(conn, mov, valor=0, data=HOJE)


def test_excluir_aporte_some_com_os_dois_lados(conn):
    a = _cdb(conn)
    base = _saldo(conn)
    tid = inv.aportar(conn, a, 100_000, _inter(conn), date(2026, 9, 1))
    inv.excluir_movimentacao(conn, conn.execute("SELECT id FROM movimentacoes_ativos").fetchone()[0])
    assert _saldo(conn) == base and inv.historico(conn, a) == []
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE transferencia_id = ?", (tid,)).fetchone()[0] == 0
    assert inv.valor_atual(conn, a, HOJE)[0] == 0
    _sem_orfaos(conn)


def test_resgate_editar_e_excluir_respeitam_o_valor_disponivel(conn):
    a = _cdb(conn)
    base = _saldo(conn)
    inv.aportar(conn, a, 100_000, _inter(conn), date(2026, 9, 1))
    tid = inv.resgatar(conn, a, 20_000, _inter(conn), date(2026, 9, 10))
    mov = conn.execute("SELECT id FROM movimentacoes_ativos WHERE transferencia_id = ?", (tid,)).fetchone()[0]
    with pytest.raises(ValueError, match="Só há"):
        inv.editar_movimentacao(conn, mov, valor=500_000, data=date(2026, 9, 10))
    assert inv.historico(conn, a)[0]["valor"] == 20_000  # nada mudou
    inv.editar_movimentacao(conn, mov, valor=30_000, data=date(2026, 9, 12))
    assert _lados(conn, tid) == [-30_000, 30_000]
    assert _saldo(conn) == base - 100_000 + 30_000
    inv.excluir_movimentacao(conn, mov)
    assert _saldo(conn) == base - 100_000
    assert inv.valor_atual(conn, a, HOJE)[0] > 100_000  # o resgate deixou de abater
    _sem_orfaos(conn)


def test_saldo_real_editar_e_excluir_sem_transferencia(conn):
    a = _cdb(conn)
    inv.aportar(conn, a, 100_000, _inter(conn), date(2026, 9, 1))
    inv.registrar_saldo_real(conn, a, 105_000, date(2026, 10, 1))
    inv.registrar_saldo_real(conn, a, 90_000, date(2026, 10, 5))
    saldos = [m for m in inv.historico(conn, a) if m["tipo"] == "saldo_real"]
    inv.editar_movimentacao(conn, saldos[0]["id"], valor=0, data=date(2026, 10, 1))  # o de 05/10 vira o de 01/10: um só por dia
    assert [(m["data"], m["valor"]) for m in inv.historico(conn, a) if m["tipo"] == "saldo_real"] == [(date(2026, 10, 1), 0)]
    inv.excluir_movimentacao(conn, saldos[0]["id"])
    assert all(m["tipo"] == "aporte" for m in inv.historico(conn, a))
    _sem_orfaos(conn)
    with pytest.raises(ValueError, match="não existe"):
        inv.excluir_movimentacao(conn, saldos[0]["id"])


def test_historico_mais_recente_primeiro(conn):
    a = _cdb(conn)
    inv.aportar(conn, a, 1000, _inter(conn), date(2026, 8, 1))
    inv.aportar(conn, a, 2000, _inter(conn), date(2026, 9, 1))
    assert [h["data"] for h in inv.historico(conn, a)] == [date(2026, 9, 1), date(2026, 8, 1)]


# --- Renda variável ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("com_conta", [True, False])
def test_compra_editar_e_excluir(conn, com_conta):
    a = _acao(conn)
    conta = _inter(conn) if com_conta else None
    base = _saldo(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000, taxas=500, conta_id=conta)
    op = rv.comprar(conn, a, date(2026, 4, 2), 50, 3600, conta_id=conta)
    rv.editar_operacao(conn, op, data=date(2026, 4, 3), quantidade=40, preco=3500, taxas=100)
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 140 and p["custo"] == 300_500 + 140_000 + 100
    assert p["preco_medio"] == pytest.approx(p["custo"] / 140)
    if com_conta:
        tid = conn.execute("SELECT transferencia_id FROM operacoes_rv WHERE id = ?", (op,)).fetchone()[0]
        assert _lados(conn, tid) == [-140_100, 140_100]
        assert _saldo(conn) == base - 300_500 - 140_100
        assert conn.execute("SELECT descricao FROM lancamentos WHERE transferencia_id = ?", (tid,)).fetchone()[0] == "Compra: PETR4 (40)"
    rv.excluir_operacao(conn, op)
    p = rv.posicao(conn, a, HOJE)
    assert p["quantidade"] == 100 and p["custo"] == 300_500
    assert _saldo(conn) == base - (300_500 if com_conta else 0)
    _sem_orfaos(conn)


@pytest.mark.parametrize("com_conta", [True, False])
def test_venda_editar_e_excluir(conn, com_conta):
    a = _acao(conn)
    conta = _inter(conn) if com_conta else None
    rv.comprar(conn, a, date(2026, 3, 2), 100, 3000)
    base = _saldo(conn)
    op = rv.vender(conn, a, date(2026, 5, 2), 30, 4000, taxas=200, conta_id=conta)
    rv.editar_operacao(conn, op, data=date(2026, 5, 3), quantidade=40, preco=4100, taxas=300)
    assert rv.posicao(conn, a, HOJE)["quantidade"] == 60
    if com_conta:
        assert _saldo(conn) == base + 40 * 4100 - 300
    rv.excluir_operacao(conn, op)
    assert rv.posicao(conn, a, HOJE)["quantidade"] == 100 and rv.posicao(conn, a, HOJE)["realizado"] == 0
    assert _saldo(conn) == base
    _sem_orfaos(conn)


@pytest.mark.parametrize("tipo", rv.TIPOS_PROVENTO)
@pytest.mark.parametrize("com_conta", [True, False])
def test_provento_editar_e_excluir(conn, tipo, com_conta):
    a = _acao(conn)
    base = _saldo(conn)
    op = rv.registrar_provento(conn, a, tipo, date(2026, 6, 1), 5000, conta_id=_inter(conn) if com_conta else None)
    rv.editar_operacao(conn, op, data=date(2026, 6, 2), valor=7000)
    assert rv.posicao(conn, a, HOJE)["proventos_total"] == 7000
    assert _saldo(conn) == base + (7000 if com_conta else 0)
    rv.excluir_operacao(conn, op)
    assert rv.posicao(conn, a, HOJE)["proventos_total"] == 0 and _saldo(conn) == base
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao LIKE '%PETR4%'").fetchone()[0] == 0
    _sem_orfaos(conn)


def test_bloqueia_venda_a_descoberto(conn):
    a = _acao(conn)
    compra = rv.comprar(conn, a, date(2026, 3, 2), 100, 3000, conta_id=_inter(conn))
    venda = rv.vender(conn, a, date(2026, 5, 2), 80, 4000, conta_id=_inter(conn))
    antes = _saldo(conn)
    with pytest.raises(ValueError, match="vendendo mais cotas"):
        rv.excluir_operacao(conn, compra)
    with pytest.raises(ValueError, match="vendendo mais cotas"):
        rv.editar_operacao(conn, compra, data=date(2026, 3, 2), quantidade=70, preco=3000)
    with pytest.raises(ValueError, match="vendendo mais cotas"):
        rv.editar_operacao(conn, venda, data=date(2026, 5, 2), quantidade=120, preco=4000)
    with pytest.raises(ValueError, match="vendendo mais cotas"):  # mover a compra pra depois da venda
        rv.editar_operacao(conn, compra, data=date(2026, 6, 1), quantidade=100, preco=3000)
    assert _saldo(conn) == antes and rv.posicao(conn, a, HOJE)["quantidade"] == 20
    rv.editar_operacao(conn, compra, data=date(2026, 3, 2), quantidade=90, preco=3000)  # ainda cobre a venda
    rv.excluir_operacao(conn, venda)
    rv.excluir_operacao(conn, compra)
    _sem_orfaos(conn)


def test_validacoes_iguais_as_de_criar(conn):
    a = _acao(conn)
    compra = rv.comprar(conn, a, date(2026, 3, 2), 10, 3000)
    prov = rv.registrar_provento(conn, a, "jcp", date(2026, 3, 3), 100)
    for kw in ({"quantidade": 0, "preco": 100}, {"quantidade": 1, "preco": 0}, {"quantidade": 1, "preco": 100, "taxas": -1}):
        with pytest.raises(ValueError):
            rv.editar_operacao(conn, compra, data=HOJE, **kw)
    with pytest.raises(ValueError, match="maior que zero"):
        rv.editar_operacao(conn, prov, data=HOJE, valor=0)
    with pytest.raises(ValueError, match="não existe"):
        rv.excluir_operacao(conn, 999)


def test_historico_junta_as_operacoes_da_acao(conn):
    a = _acao(conn)
    rv.comprar(conn, a, date(2026, 3, 2), 10, 3000, conta_id=_inter(conn))
    rv.registrar_provento(conn, a, "dividendo", date(2026, 4, 1), 500)
    h = inv.historico(conn, a)
    assert [(i["tipo"], i["afeta_conta"]) for i in h] == [("dividendo", False), ("compra", True)]
    assert h[1]["quantidade"] == 10 and h[1]["preco"] == 3000 and h[0]["fonte"] == "rv"
