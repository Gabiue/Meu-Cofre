"""Importação de extrato OFX (#14). Os extratos são FALSOS (tests/fixtures/extrato_ofx_falso.py)."""

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import id_cat, id_conta

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import extrato_ofx_falso as falso  # noqa: E402

from meu_cofre import cadastros  # noqa: E402
from meu_cofre import investimentos as inv  # noqa: E402
from meu_cofre import renda_variavel as rv  # noqa: E402
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


def _inicio(conn, conta="Inter"):
    """Marca o início do uso da conta antes do extrato (lançamento de valor zero, não mexe em saldo nem concilia)."""
    conn.execute("INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao, origem) VALUES (?, '2026-09-01', 0, 'Início', 'manual')",
                 (id_conta(conn, conta),))


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
    f.write_text("data,valor\n2026-10-01,10", encoding="utf-8")
    with pytest.raises(ofx.ImportacaoErro, match="OFX"):
        ofx.ler(f)
    f.write_text("<OFX></OFX>", encoding="utf-8")
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
    _inicio(conn)
    manual = _manual(conn, "Inter", date(2026, 10, 5), -4590, "Mercado")  # 2 dias depois do extrato
    p = _prever(conn, inter)
    assert _situacoes(p) == ["nova", "conciliada", "nova", "nova"] and p.itens[1].lancamento_id == manual
    r = ofx.confirmar(conn, p)
    assert (r["novas"], r["conciliadas"]) == (3, 1)
    linha = conn.execute("SELECT * FROM lancamentos WHERE id = ?", (manual,)).fetchone()
    assert (linha["conferido"], linha["fitid"], linha["origem"], linha["descricao"]) == (1, "480000000022", "manual", "Mercado")
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 5
    assert _situacoes(_prever(conn, inter)) == ["ja"] * 4


def test_fora_da_janela_de_3_dias_valor_ou_conta_diferente_nao_concilia(conn, inter):
    _inicio(conn)
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
    _inicio(conn, "Caixa")
    manual = _manual(conn, "Caixa", date(2026, 10, 2), -1000)
    p = _prever(conn, caixa, "Caixa")
    assert _situacoes(p) == ["nova", "conciliada", "nova", "nova"] and p.itens[1].lancamento_id == manual


def test_o_mais_proximo_ganha_quando_nao_ha_empate(conn, inter):
    _manual(conn, "Inter", date(2026, 10, 6), -12000)
    perto = _manual(conn, "Inter", date(2026, 10, 5), -12000)
    p = _prever(conn, inter)
    assert p.itens[2].situacao == "conciliada" and p.itens[2].lancamento_id == perto


def test_duvida_quando_ha_empate_e_o_usuario_escolhe(conn, inter):
    _inicio(conn)
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
    _inicio(conn)
    _manual(conn, "Inter", date(2026, 10, 8), -3000)
    _manual(conn, "Inter", date(2026, 10, 10), -3000)
    p = _prever(conn, inter)
    r = ofx.confirmar(conn, p, escolhas={3: None})
    assert (r["novas"], r["conciliadas"]) == (4, 0)


def test_escolha_invalida_e_repetida(conn, inter):
    _inicio(conn)
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
    _inicio(conn)
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


# --- Investimento com centavos de diferença (#53) -----------------------------------------------------------


def _ofx(tmp_path, linhas, saldo=None):
    """Extrato mínimo: linhas = [(AAAAMMDD, valor, descrição)]; saldo = (valor, AAAAMMDD)."""
    trn = "".join(f"<STMTTRN><TRNTYPE>OTHER<DTPOSTED>{d}<TRNAMT>{v / 100:.2f}<FITID>F{i}<MEMO>{m}</STMTTRN>"
                  for i, (d, v, m) in enumerate(linhas))
    bal = f"<LEDGERBAL><BALAMT>{saldo[0] / 100:.2f}<DTASOF>{saldo[1]}</LEDGERBAL>" if saldo else ""
    f = tmp_path / "x.ofx"
    f.write_text(f"<OFX><FID>077<BANKTRANLIST>{trn}</BANKTRANLIST>{bal}</OFX>", encoding="utf-8")
    return f


def _aporte(conn, valor, dia):
    ativo = inv.criar(conn, HOJE, nome="Tesouro", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    inv.aportar(conn, ativo, valor, id_conta(conn, "Inter"), dia)
    return conn.execute("SELECT id FROM lancamentos WHERE conta_id = ? AND valor_centavos < 0", (id_conta(conn, "Inter"),)).fetchone()[0]


def _compra(conn, dia, quantidade=2, preco=11_313):
    ativo = inv.criar(conn, HOJE, nome="HGRU", tipo="fii", ticker="HGRU11")
    op = rv.comprar(conn, ativo, dia, quantidade, preco, 0, id_conta(conn, "Inter"))
    return op, conn.execute("SELECT id FROM lancamentos WHERE conta_id = ? AND valor_centavos < 0", (id_conta(conn, "Inter"),)).fetchone()[0]


def test_aporte_do_tesouro_com_centavos_de_diferenca_vira_duvida_e_confirmar_acerta_o_valor(conn, tmp_path):
    lid = _aporte(conn, 24_728, date(2026, 11, 4))
    p = ofx.prever(conn, ofx.ler(_ofx(tmp_path, [("20261104", -24_636, "Debito Tesouro Direto")])), id_conta(conn, "Inter"), HOJE)
    item = p.itens[0]
    assert item.situacao == "duvida" and item.lancamento_id == lid and [c["id"] for c in item.candidatos] == [lid]
    r = ofx.confirmar(conn, p, ajustar_saldo=False)
    assert (r["novas"], r["conciliadas"]) == (0, 1)
    assert conn.execute("SELECT valor_centavos FROM movimentacoes_ativos").fetchone()[0] == 24_636
    assert sorted(x[0] for x in conn.execute("SELECT valor_centavos FROM lancamentos")) == [-24_636, 24_636]
    assert conn.execute("SELECT conferido FROM lancamentos WHERE id = ?", (lid,)).fetchone()[0] == 1


def test_compra_da_b3_quatro_dias_depois_vira_duvida_e_a_diferenca_vai_pras_taxas(conn, tmp_path):
    op, lid = _compra(conn, date(2026, 11, 5))  # 2 x 113,13 = 226,26
    p = ofx.prever(conn, ofx.ler(_ofx(tmp_path, [("20261109", -22_632, "Debito B3: NOTA BOV")])), id_conta(conn, "Inter"), HOJE)
    assert p.itens[0].situacao == "duvida" and p.itens[0].lancamento_id == lid
    ofx.confirmar(conn, p, ajustar_saldo=False)
    o = conn.execute("SELECT * FROM operacoes_rv WHERE id = ?", (op,)).fetchone()
    assert (o["quantidade"], o["preco_centavos"], o["valor_centavos"], o["taxas_centavos"]) == (2, 11_313, 22_626, 6)
    assert conn.execute("SELECT valor_centavos FROM lancamentos WHERE id = ?", (lid,)).fetchone()[0] == -22_632


def test_compra_debitada_a_menos_ajusta_o_preco_e_nao_deixa_taxa_negativa(conn, tmp_path):
    op, _ = _compra(conn, date(2026, 11, 5))
    p = ofx.prever(conn, ofx.ler(_ofx(tmp_path, [("20261106", -22_600, "Debito B3")])), id_conta(conn, "Inter"), HOJE)
    ofx.confirmar(conn, p, ajustar_saldo=False)
    o = conn.execute("SELECT * FROM operacoes_rv WHERE id = ?", (op,)).fetchone()
    assert (o["preco_centavos"], o["taxas_centavos"], o["valor_centavos"]) == (11_300, 0, 22_600)


def test_diferenca_acima_do_limite_data_fora_da_janela_ou_nao_investimento_continua_nova(conn, tmp_path):
    _aporte(conn, 24_728, date(2026, 11, 4))
    _manual(conn, "Inter", date(2026, 11, 4), -10_000, "Mercado")
    _inicio(conn)
    conta = id_conta(conn, "Inter")

    def previa(*linhas):
        return ofx.prever(conn, ofx.ler(_ofx(tmp_path, list(linhas))), conta, HOJE)

    assert _situacoes(previa(("20261104", -24_000, "Tesouro"))) == ["nova"]  # 7,28 de diferença
    assert _situacoes(previa(("20261112", -24_636, "Tesouro"))) == ["nova"]  # 8 dias depois
    assert _situacoes(previa(("20261103", -24_636, "Tesouro"))) == ["nova"]  # antes do lançamento
    assert _situacoes(previa(("20261104", -10_050, "Mercado"))) == ["nova"]  # manual comum não tem tolerância
    assert _situacoes(previa(("20261104", -24_728, "Tesouro"))) == ["conciliada"]  # valor exato segue igual


# --- Linhas antes do início do uso (#53) --------------------------------------------------------------------


def test_linha_anterior_ao_primeiro_lancamento_vem_desmarcada_e_nao_mexe_no_saldo_inicial(conn, tmp_path):
    conta = id_conta(conn, "Inter")
    cadastros.definir_saldo_inicial(conn, conta, 10_000)  # já inclui o que veio antes do início do uso
    _manual(conn, "Inter", date(2026, 10, 5), -500, "Café")
    f = _ofx(tmp_path, [("20261001", -3000, "Antiga"), ("20261008", -1000, "Nova")], saldo=(5500, "20261008"))
    p = ofx.prever(conn, ofx.ler(f), conta, HOJE)
    assert _situacoes(p) == ["antes", "nova"] and p.itens[0].destino == "ja" and p.a_gravar == 1
    assert ofx.diferenca_saldo(conn, p) == 5500 - (10_000 - 500) + 1000
    r = ofx.confirmar(conn, p)
    assert r["novas"] == 1 and r["ja_importadas"] == 1
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'Antiga'").fetchone()[0] == 0
    assert conn.execute("SELECT saldo_inicial_centavos FROM contas WHERE id = ?", (conta,)).fetchone()[0] == 10_000 + 5500 - 8500


def test_linha_anterior_marcada_e_importada(conn, tmp_path):
    conta = id_conta(conn, "Inter")
    _manual(conn, "Inter", date(2026, 10, 5), -500)
    p = ofx.prever(conn, ofx.ler(_ofx(tmp_path, [("20261001", -3000, "Antiga")])), conta, HOJE)
    assert p.itens[0].situacao == "antes" and p.a_gravar == 0
    ofx.confirmar(conn, p, escolhas={0: True}, ajustar_saldo=False)
    assert conn.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'Antiga'").fetchone()[0] == 1


def test_conta_sem_lancamentos_nao_tem_inicio_do_uso(conn, tmp_path):
    p = ofx.prever(conn, ofx.ler(_ofx(tmp_path, [("20260101", -3000, "Antiga")])), id_conta(conn, "Inter"), HOJE)
    assert _situacoes(p) == ["nova"]
