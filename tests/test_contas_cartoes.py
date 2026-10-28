"""Contas e cartões (F4): cadastro, regras de desativação, escolha do cartão no lançamento."""

from datetime import date

import pytest
from conftest import id_cat, id_conta

from meu_cofre import cadastros, relatorios
from meu_cofre import lancamentos as lanc

HOJE = date(2026, 10, 20)


def _compra(conn, conta_id, valor=10000, **kw):
    base = dict(entrada=False, valor_centavos=valor, descricao="Tênis", categoria_id=id_cat(conn, "Compras pessoais"),
                metodo="cartao", conta_id=conta_id, data=date(2026, 10, 5))
    base.update(kw)
    return lanc.registrar(conn, **base)[0]


def _conta_do_lancamento(conn, id_):
    return conn.execute("SELECT conta_id FROM lancamentos WHERE id = ?", (id_,)).fetchone()[0]


# --- Cadastro ---------------------------------------------------------------------


def test_contas_por_tipo(conn):
    assert [c["nome"] for c in cadastros.contas_por_tipo(conn, ("corrente", "dinheiro"))] == ["Inter", "Caixa", "Carteira"]
    assert [c["nome"] for c in cadastros.contas_por_tipo(conn, ("cartao",))] == ["Cartão de crédito"]
    cadastros.desativar_conta(conn, id_conta(conn, "Caixa"))
    assert [c["nome"] for c in cadastros.contas_por_tipo(conn, ("corrente",))] == ["Inter"]
    assert len(cadastros.contas_por_tipo(conn, ("corrente",), incluir_inativas=True)) == 2


def test_criar_cartao(conn):
    id_ = cadastros.criar_cartao(conn, "  Caixa Visa ", banco=" Caixa ", dia_fechamento=5, dia_vencimento=12)
    c = conn.execute("SELECT * FROM contas WHERE id = ?", (id_,)).fetchone()
    assert (c["nome"], c["tipo"], c["banco"], c["dia_fechamento"], c["dia_vencimento"], c["ativa"]) == (
        "Caixa Visa", "cartao", "Caixa", 5, 12, 1)
    simples = cadastros.criar_cartao(conn, "Inter Black")
    c = conn.execute("SELECT * FROM contas WHERE id = ?", (simples,)).fetchone()
    assert c["banco"] is None and c["dia_fechamento"] is None and c["dia_vencimento"] is None


def test_criar_cartao_invalido(conn):
    with pytest.raises(ValueError, match="nome"):
        cadastros.criar_cartao(conn, "   ")
    with pytest.raises(ValueError, match="Já existe"):
        cadastros.criar_cartao(conn, "cartão DE crédito")  # sem diferenciar maiúsculas
    with pytest.raises(ValueError, match="fechamento"):
        cadastros.criar_cartao(conn, "X", dia_fechamento=0)
    with pytest.raises(ValueError, match="vencimento"):
        cadastros.criar_cartao(conn, "X", dia_vencimento=32)
    assert conn.execute("SELECT COUNT(*) FROM contas WHERE nome = 'X'").fetchone()[0] == 0


def test_renomear(conn):
    cartao = id_conta(conn, "Cartão de crédito")
    cadastros.renomear_conta(conn, cartao, " Caixa Elo ")
    assert conn.execute("SELECT nome FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == "Caixa Elo"
    cadastros.renomear_conta(conn, cartao, "caixa elo")  # o próprio nome, só mudando a caixa, é permitido
    with pytest.raises(ValueError, match="Já existe"):
        cadastros.renomear_conta(conn, cartao, "Inter")
    with pytest.raises(ValueError, match="nome"):
        cadastros.renomear_conta(conn, cartao, "")
    with pytest.raises(ValueError, match="não existe"):
        cadastros.renomear_conta(conn, 999, "Fantasma")


def test_saldo_inicial(conn):
    inter = id_conta(conn, "Inter")
    cadastros.definir_saldo_inicial(conn, inter, 123456)
    assert relatorios.em_conta(conn, HOJE) == 123456
    cadastros.definir_saldo_inicial(conn, id_conta(conn, "Carteira"), -500)
    assert relatorios.em_conta(conn, HOJE) == 122956
    with pytest.raises(ValueError, match="saldo inicial"):
        cadastros.definir_saldo_inicial(conn, id_conta(conn, "Cartão de crédito"), 100)


def test_saldo_da_conta_e_divida_do_cartao(conn):
    inter, cartao = id_conta(conn, "Inter"), id_conta(conn, "Cartão de crédito")
    cadastros.definir_saldo_inicial(conn, inter, 100000)
    lanc.registrar(conn, entrada=False, valor_centavos=2500, descricao="Pão", categoria_id=None,
                   metodo="pix", conta_id=inter, data=date(2026, 10, 1))
    lanc.registrar(conn, entrada=False, valor_centavos=2500, descricao="Futuro", categoria_id=None,
                   metodo="pix", conta_id=inter, data=date(2026, 11, 1))
    assert cadastros.saldo_da_conta(conn, inter, HOJE) == 97500
    assert cadastros.saldo_da_conta(conn, inter) == 95000
    _compra(conn, cartao, 30000, parcelas=3)
    assert cadastros.divida_do_cartao(conn, cartao) == 30000
    assert cadastros.saldo_da_conta(conn, cartao, HOJE) == -10000


# --- Desativar / reativar -------------------------------------------------------------


def test_desativar_cartao_mantem_historico_e_divida(conn):
    cartao = id_conta(conn, "Cartão de crédito")
    _compra(conn, cartao, 8000)
    cadastros.desativar_conta(conn, cartao)  # cartão pode ficar sem nenhum ativo
    assert conn.execute("SELECT ativa FROM contas WHERE id = ?", (cartao,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1
    assert relatorios.devo(conn) == 8000  # cancelado, mas a fatura continua a pagar
    assert lanc.cartoes_ativos(conn) == []
    cadastros.reativar_conta(conn, cartao)
    assert [c["id"] for c in lanc.cartoes_ativos(conn)] == [cartao]


def test_nao_desativa_ultima_conta_corrente_nem_carteira(conn):
    cadastros.desativar_conta(conn, id_conta(conn, "Caixa"))
    with pytest.raises(ValueError, match="conta corrente"):
        cadastros.desativar_conta(conn, id_conta(conn, "Inter"))
    with pytest.raises(ValueError, match="carteira"):
        cadastros.desativar_conta(conn, id_conta(conn, "Carteira"))
    assert conn.execute("SELECT COUNT(*) FROM contas WHERE ativa = 1").fetchone()[0] == 3
    cadastros.reativar_conta(conn, id_conta(conn, "Caixa"))
    cadastros.desativar_conta(conn, id_conta(conn, "Inter"))  # agora sobra a Caixa


# --- Cartão escolhido no lançamento ----------------------------------------------------


@pytest.fixture
def dois_cartoes(conn):
    elo = id_conta(conn, "Cartão de crédito")
    cadastros.renomear_conta(conn, elo, "Caixa Elo")
    visa = cadastros.criar_cartao(conn, "Caixa Visa")
    return elo, visa


def test_registrar_usa_o_cartao_escolhido(conn, dois_cartoes):
    elo, visa = dois_cartoes
    assert _conta_do_lancamento(conn, _compra(conn, visa)) == visa
    assert _conta_do_lancamento(conn, _compra(conn, elo)) == elo
    parcelas = lanc.registrar(conn, entrada=False, valor_centavos=9000, descricao="TV", categoria_id=None,
                              metodo="cartao", conta_id=visa, data=date(2026, 10, 5), parcelas=3)
    assert {_conta_do_lancamento(conn, i) for i in parcelas} == {visa}


def test_cartao_invalido_cai_no_primeiro_ativo(conn, dois_cartoes):
    elo, visa = dois_cartoes
    inter = id_conta(conn, "Inter")
    assert _conta_do_lancamento(conn, _compra(conn, inter)) == elo  # não é cartão
    assert _compra(conn, 0) and lanc.conta_do_metodo(conn, "cartao", 0) == elo
    cadastros.desativar_conta(conn, elo)
    assert lanc.conta_do_metodo(conn, "cartao", elo) == visa  # desativado: vai pro primeiro ativo


def test_sem_cartao_ativo_da_erro_amigavel(conn):
    cadastros.desativar_conta(conn, id_conta(conn, "Cartão de crédito"))
    with pytest.raises(ValueError, match="Adicione um"):
        _compra(conn, 0)


def test_atualizar_troca_de_cartao_e_de_metodo(conn, dois_cartoes):
    elo, visa = dois_cartoes
    id_ = _compra(conn, elo)
    lanc.atualizar(conn, id_, metodo="cartao", conta_id=visa)
    assert _conta_do_lancamento(conn, id_) == visa
    lanc.atualizar(conn, id_, metodo="pix", conta_id=id_conta(conn, "Inter"))
    assert _conta_do_lancamento(conn, id_) == id_conta(conn, "Inter")
    lanc.atualizar(conn, id_, metodo="cartao", conta_id=elo)
    assert _conta_do_lancamento(conn, id_) == elo


def test_editar_compra_de_cartao_desativado_nao_muda_de_cartao(conn, dois_cartoes):
    elo, visa = dois_cartoes
    id_ = _compra(conn, elo)
    cadastros.desativar_conta(conn, elo)
    lanc.atualizar(conn, id_, metodo="cartao", conta_id=elo, descricao="Tênis novo")
    assert _conta_do_lancamento(conn, id_) == elo
    lanc.atualizar(conn, id_, metodo="cartao", conta_id=visa)  # trocar de propósito vale
    assert _conta_do_lancamento(conn, id_) == visa


def test_ultimo_cartao_usado(conn, dois_cartoes):
    elo, visa = dois_cartoes
    assert lanc.ultimo_cartao_usado(conn) is None
    lanc.registrar(conn, entrada=False, valor_centavos=100, descricao="Pix", categoria_id=None,
                   metodo="pix", conta_id=id_conta(conn, "Inter"), data=HOJE)
    assert lanc.ultimo_cartao_usado(conn) is None  # só método cartão conta
    _compra(conn, visa)
    _compra(conn, elo)
    assert lanc.ultimo_cartao_usado(conn) == elo
    _compra(conn, visa)
    assert lanc.ultimo_cartao_usado(conn) == visa
    cadastros.desativar_conta(conn, visa)
    assert lanc.ultimo_cartao_usado(conn) == elo  # o Visa foi desativado: vale o mais recente entre os ativos
    cadastros.desativar_conta(conn, elo)
    assert lanc.ultimo_cartao_usado(conn) is None
