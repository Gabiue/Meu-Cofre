"""Importação de extrato OFX (#14). Os extratos são FALSOS (tests/fixtures/extrato_ofx_falso.py)."""

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import id_cat, id_conta

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import extrato_ofx_falso as falso  # noqa: E402

from meu_cofre import cadastros  # noqa: E402
from meu_cofre import importar_ofx as ofx  # noqa: E402
from meu_cofre import lancamentos as lanc  # noqa: E402

HOJE = date(2026, 10, 20)
FIM = date(2026, 10, 10)


@pytest.fixture
def inter(tmp_path):
    return falso.inter(tmp_path / "inter.ofx")


@pytest.fixture
def caixa(tmp_path):
    return falso.caixa(tmp_path / "caixa.ofx")


def _prever(conn, caminho, conta="Inter"):
    return ofx.prever(conn, ofx.ler(caminho), id_conta(conn, conta), HOJE)


def _manual(conn, conta, dia, valor, descricao="Lançado à mão"):
    return lanc.registrar(conn, entrada=valor > 0, valor_centavos=abs(valor), descricao=descricao, categoria_id=None,
                          metodo="pix", conta_id=id_conta(conn, conta), data=dia)[0]


def _situacoes(previa):
    return [i.situacao for i in previa.itens]


# --- Leitura -----------------------------------------------------------------------------------------------


def test_le_o_inter_ignorando_o_cabecalho_que_mente(inter):
    e = ofx.ler(inter)
    assert (e.fid, e.banco, e.inicio, e.fim) == ("077", "Banco Ficticio S/A", date(2026, 10, 1), date(2026, 10, 10))
    assert (e.saldo_centavos, e.saldo_data) == (230_410, FIM)
    assert [t.valor_centavos for t in e.transacoes] == [150_000, -4590, -12_000, -3000]  # PAYMENT é saída
    assert e.transacoes[1].data == date(2026, 10, 3)  # 20261003120000[-3:BRT]
    assert e.transacoes[3].descricao == "Café Modelo"  # UTF-8 mesmo com cabeçalho cp1252; NAME = MEMO não repete
    assert e.transacoes[0].descricao == 'Pix recebido: Empresa Ficticia Ltda'  # NAME dentro do MEMO, sem as aspas


def test_le_a_caixa_com_o_horario_separado(caixa):
    e = ofx.ler(caixa)
    assert e.fid == "104" and e.saldo_centavos == 239_950
    assert [(t.descricao, t.hora) for t in e.transacoes[:2]] == [
        ("CREDITO SALARIO FICTICIO", "08:00:00"), ("COMPRA DEBITO CAFE EXEMPLO", "08:15:00")]
    assert e.transacoes[1].descricao_original.startswith("- 08:15:00")


def test_tolera_cp1252_tags_sem_fechamento_e_valor_com_virgula(tmp_path):
    texto = ("OFXHEADER:100\n\n<OFX><BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261002<TRNAMT>-1.234,50\n"
             "<FITID>1<MEMO>Padaria Pão &amp; Cia\n</STMTTRN></BANKTRANLIST>\n<LEDGERBAL><BALAMT>10.00<DTASOF>20261002\n</LEDGERBAL></OFX>")
    f = tmp_path / "x.ofx"
    f.write_bytes(texto.encode("cp1252"))
    e = ofx.ler(f)
    assert (e.transacoes[0].valor_centavos, e.transacoes[0].descricao) == (-123_450, "Padaria Pão & Cia")
    assert (e.saldo_centavos, e.saldo_data) == (1000, date(2026, 10, 2))


def test_arquivo_que_nao_e_ofx(tmp_path):
    f = tmp_path / "x.ofx"
    f.write_text("data,valor\n2026-10-01,10")
    with pytest.raises(ofx.ImportacaoErro, match="OFX"):
        ofx.ler(f)
    f.write_text("<OFX></OFX>")
    with pytest.raises(ofx.ImportacaoErro, match="transações"):
        ofx.ler(f)


def test_fitid_confiavel_so_pro_banco(inter, caixa):
    assert ofx.fitid_confiavel(ofx.ler(inter)) is True
    assert ofx.fitid_confiavel(ofx.ler(caixa)) is False


def test_conta_sugerida_pelo_fid(conn, inter, caixa):
    assert ofx.conta_sugerida(conn, ofx.ler(inter)) == id_conta(conn, "Inter")
    assert ofx.conta_sugerida(conn, ofx.ler(caixa)) == id_conta(conn, "Caixa")


# --- Prévia e confirmação -----------------------------------------------------------------------------------


def test_previa_nao_grava_nada(conn, inter):
    p = _prever(conn, inter)
    assert len(p.novas) == 4
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM importacoes").fetchone()[0] == 0


def test_confirmar_grava_as_novas_com_origem_e_fitid(conn, inter):
    r = ofx.confirmar(conn, _prever(conn, inter))
    assert (r["novas"], r["conciliadas"], r["ja_importadas"]) == (4, 0, 0)
    linhas = conn.execute("SELECT * FROM lancamentos ORDER BY data").fetchall()
    assert [(l["origem"], l["conferido"], l["status"]) for l in linhas] == [("importacao", 1, "realizado")] * 4
    assert linhas[0]["fitid"] == "480000000011" and linhas[0]["chave_dedup"] and linhas[0]["importacao_id"]
    assert linhas[0]["descricao_original"] == 'Empresa Ficticia Ltda | Pix recebido: "Empresa Ficticia Ltda"'
    imp = conn.execute("SELECT * FROM importacoes").fetchone()
    assert (imp["formato"], imp["banco"], imp["linhas_lidas"], imp["linhas_novas"]) == ("ofx", "Banco Ficticio S/A", 4, 4)


@pytest.mark.parametrize("arquivo,conta", [("inter", "Inter"), ("caixa", "Caixa")])
def test_reimportar_nao_duplica(conn, inter, caixa, arquivo, conta):
    caminho = {"inter": inter, "caixa": caixa}[arquivo]
    ofx.confirmar(conn, _prever(conn, caminho, conta))
    p = _prever(conn, caminho, conta)
    assert _situacoes(p) == ["ja"] * 4
    r = ofx.confirmar(conn, p)
    assert (r["novas"], r["ja_importadas"]) == (0, 4)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 4


def test_caixa_dedup_por_chave_e_compras_identicas_viram_duas(conn, caixa):
    p = _prever(conn, caixa, "Caixa")
    assert not p.confiavel and len(p.novas) == 4
    assert p.itens[1].chave != p.itens[2].chave  # duas compras iguais no mesmo dia
    ofx.confirmar(conn, p)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE fitid IS NOT NULL").fetchone()[0] == 0  # FITID inventado não é gravado
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'COMPRA DEBITO CAFE EXEMPLO'").fetchone()[0] == 2


def test_importar_na_outra_conta_nao_conta_como_ja_importada(conn, caixa):
    ofx.confirmar(conn, _prever(conn, caixa, "Caixa"))
    assert len(_prever(conn, caixa, "Inter").novas) == 4


# --- Conciliação -------------------------------------------------------------------------------------------


def test_concilia_com_lancamento_manual_proximo(conn, inter):
    manual = _manual(conn, "Inter", date(2026, 10, 5), -4590, "Mercado")  # 2 dias depois do extrato
    p = _prever(conn, inter)
    assert _situacoes(p) == ["nova", "conciliada", "nova", "nova"] and p.itens[1].lancamento_id == manual
    r = ofx.confirmar(conn, p)
    assert (r["novas"], r["conciliadas"]) == (3, 1)
    linha = conn.execute("SELECT * FROM lancamentos WHERE id = ?", (manual,)).fetchone()
    assert (linha["conferido"], linha["fitid"], linha["origem"], linha["descricao"]) == (1, "480000000022", "manual", "Mercado")
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 4
    assert _situacoes(_prever(conn, inter)) == ["ja"] * 4


def test_fora_da_janela_de_3_dias_valor_ou_conta_diferente_nao_concilia(conn, inter):
    _manual(conn, "Inter", date(2026, 10, 7), -4590)  # 4 dias
    _manual(conn, "Inter", date(2026, 10, 3), -4591)  # valor
    _manual(conn, "Caixa", date(2026, 10, 3), -4590)  # conta
    assert _situacoes(_prever(conn, inter)) == ["nova"] * 4


def test_manual_ja_conferido_ou_importado_nao_concilia(conn, inter):
    ofx.confirmar(conn, _prever(conn, inter))
    outra = falso.inter(inter.parent / "outro.ofx")
    texto = outra.read_bytes().replace(b"480000000022", b"480000000099")  # mesma transação, FITID novo
    outra.write_bytes(texto)
    assert _situacoes(_prever(conn, outra)) == ["ja", "nova", "ja", "ja"]  # o lançamento importado não é "manual"


def test_um_manual_so_concilia_uma_vez(conn, caixa):
    manual = _manual(conn, "Caixa", date(2026, 10, 2), -1000)
    p = _prever(conn, caixa, "Caixa")
    assert _situacoes(p) == ["nova", "conciliada", "nova", "nova"] and p.itens[1].lancamento_id == manual


def test_o_mais_proximo_ganha_quando_nao_ha_empate(conn, inter):
    _manual(conn, "Inter", date(2026, 10, 6), -12000)
    perto = _manual(conn, "Inter", date(2026, 10, 5), -12000)
    p = _prever(conn, inter)
    assert p.itens[2].situacao == "conciliada" and p.itens[2].lancamento_id == perto


def test_duvida_quando_ha_empate_e_o_usuario_escolhe(conn, inter):
    a = _manual(conn, "Inter", date(2026, 10, 8), -3000, "A")
    b = _manual(conn, "Inter", date(2026, 10, 10), -3000, "B")
    p = _prever(conn, inter)
    item = p.itens[3]
    assert item.situacao == "duvida" and {c["id"] for c in item.candidatos} == {a, b} and item.lancamento_id in (a, b)
    assert len(p.duvidas) == 1 and p.a_gravar == 3
    r = ofx.confirmar(conn, p, escolhas={3: b})
    assert (r["novas"], r["conciliadas"]) == (3, 1)
    assert conn.execute("SELECT conferido FROM lancamentos WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT conferido FROM lancamentos WHERE id = ?", (a,)).fetchone()[0] == 0


def test_duvida_importada_como_nova(conn, inter):
    _manual(conn, "Inter", date(2026, 10, 8), -3000)
    _manual(conn, "Inter", date(2026, 10, 10), -3000)
    p = _prever(conn, inter)
    r = ofx.confirmar(conn, p, escolhas={3: None})
    assert (r["novas"], r["conciliadas"]) == (4, 0)


def test_escolha_invalida_e_repetida(conn, inter):
    _manual(conn, "Inter", date(2026, 10, 8), -3000)
    _manual(conn, "Inter", date(2026, 10, 10), -3000)
    with pytest.raises(ofx.ImportacaoErro):
        ofx.confirmar(conn, _prever(conn, inter), escolhas={3: 9999})
    with pytest.raises(ofx.ImportacaoErro):
        ofx.confirmar(conn, _prever(conn, inter), escolhas={0: None})


# --- Categoria ---------------------------------------------------------------------------------------------


def test_sugere_categoria_pelo_historico_e_deixa_pix_sem(conn, inter):
    lanc.registrar(conn, entrada=False, valor_centavos=500, descricao="Café Modelo", categoria_id=id_cat(conn, "Alimentação fora"),
                   metodo="debito", conta_id=id_conta(conn, "Inter"), data=date(2026, 9, 1))
    lanc.registrar(conn, entrada=False, valor_centavos=500, descricao='Pix enviado: "Fulano de Tal"', categoria_id=id_cat(conn, "Lazer"),
                   metodo="pix", conta_id=id_conta(conn, "Inter"), data=date(2026, 9, 1))
    p = _prever(conn, inter)
    assert [i.categoria_id for i in p.itens] == [None, None, None, id_cat(conn, "Alimentação fora")]  # Pix não adivinha


# --- Saldo -------------------------------------------------------------------------------------------------


def test_saldo_em_conta_bate_com_o_extrato(conn, inter):
    conta = id_conta(conn, "Inter")
    p = _prever(conn, inter)
    assert ofx.diferenca_saldo(conn, p) == 100_000  # o extrato tinha R$ 1.000 antes do primeiro lançamento
    r = ofx.confirmar(conn, p)
    assert r["diferenca_saldo"] == 100_000
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE id = ?", (conta,)).fetchone()[0] == 100_000
    assert cadastros.saldo_da_conta(conn, conta, FIM) == 230_410


def test_saldo_bate_importando_periodo_passado_com_lancamentos_posteriores(conn, inter):
    conta = id_conta(conn, "Inter")
    cadastros.definir_saldo_inicial(conn, conta, 5000)
    _manual(conn, "Inter", date(2026, 9, 20), -700)  # antes do período
    _manual(conn, "Inter", date(2026, 10, 15), -2500)  # depois do extrato
    em_conta_hoje = cadastros.saldo_da_conta(conn, conta, HOJE)
    p = _prever(conn, inter)
    esperado = ofx.diferenca_saldo(conn, p)
    r = ofx.confirmar(conn, p)
    assert r["diferenca_saldo"] == esperado
    assert cadastros.saldo_da_conta(conn, conta, FIM) == 230_410
    assert cadastros.saldo_da_conta(conn, conta, HOJE) == 230_410 - 2500  # o lançamento posterior continua valendo
    assert em_conta_hoje != cadastros.saldo_da_conta(conn, conta, HOJE)


def test_saldo_com_conciliada_sem_ajuste_e_depois_com_ajuste(conn, inter):
    conta = id_conta(conn, "Inter")
    _manual(conn, "Inter", date(2026, 10, 3), -4590)
    r = ofx.confirmar(conn, _prever(conn, inter), ajustar_saldo=False)
    assert r["diferenca_saldo"] == 0 and cadastros.saldo_da_conta(conn, conta, FIM) == 130_410
    p = _prever(conn, inter)  # tudo já importado: ainda dá pra acertar o saldo depois
    assert ofx.diferenca_saldo(conn, p) == 100_000
    assert ofx.confirmar(conn, p)["diferenca_saldo"] == 100_000 and cadastros.saldo_da_conta(conn, conta, FIM) == 230_410


def test_descricao_sem_aspas_do_pix():
    from meu_cofre.importar_ofx import _descricao

    assert _descricao("Pix enviado", 'Pix enviado: "Fulano de Tal"') == "Pix enviado: Fulano de Tal"
