"""Importação da planilha de movimentações da B3 (F9). A planilha é FALSA (tests/fixtures/planilha_b3_falsa.py)."""

import sys
from datetime import date
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import planilha_b3_falsa as falsa  # noqa: E402

from meu_cofre import importar_b3 as b3  # noqa: E402
from meu_cofre import investimentos as inv  # noqa: E402
from meu_cofre import renda_variavel as rv  # noqa: E402

HOJE = date(2026, 10, 30)


@pytest.fixture
def planilha(tmp_path):
    return falsa.gerar(tmp_path / "movimentacao.xlsx")


def _id(conn, ticker):
    return conn.execute("SELECT id FROM ativos WHERE ticker = ?", (ticker,)).fetchone()["id"]


def test_previa_separa_novas_ignoradas_e_ativos_novos(conn, planilha):
    p = b3.prever(conn, planilha)
    assert len(p.novas) == 10 and p.duplicadas == []
    assert [t.tipo for t in p.novas].count("compra") == 6 and [t.tipo for t in p.novas].count("venda") == 1
    assert [(i.linha, i.motivo) for i in p.ignoradas] == [
        (12, "Troca de corretora: a posição não muda"),
        (13, "Direito de subscrição ainda não exercido: só entra quando for exercido"),
        (14, "O produto não é ação nem FII"),
        (15, "Data inválida"),
    ]
    assert "Tesouro Selic 2029" in p.ignoradas[2].resumo
    assert [(a.ticker, a.tipo) for a in p.ativos_novos] == [("PETR4", "acao"), ("VALE3", "acao"), ("HGLG11", "fii"), ("MXRF11", "fii")]
    assert p.ativos_novos[0].nome == "Petroleo Brasileiro S.A. Petrobras"


def test_previa_nao_grava_nada(conn, planilha):
    b3.prever(conn, planilha)
    assert conn.execute("SELECT COUNT(*) FROM ativos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 0


def test_valores_em_texto_e_em_numero_viram_centavos(conn, planilha):
    p = b3.prever(conn, planilha)
    por_linha = {o.linha: o for o in p.novas}
    assert (por_linha[2].quantidade, por_linha[2].preco_centavos, por_linha[2].valor_centavos) == (100, 3500, 350_000)  # "R$ 3.500,00"
    assert (por_linha[4].data, por_linha[4].preco_centavos, por_linha[4].valor_centavos) == (date(2026, 4, 15), 3650, 182_500)  # numérico
    assert por_linha[6].tipo == "dividendo" and por_linha[6].valor_centavos == 4550 and por_linha[6].preco_centavos is None
    assert {o.tipo for o in p.novas} == {"compra", "venda", "dividendo", "jcp", "rendimento"}


def test_confirmar_grava_sem_afetar_conta_e_monta_a_posicao(conn, planilha):
    r = b3.confirmar(conn, b3.prever(conn, planilha), hoje=HOJE)
    assert r == {"operacoes": 10, "ativos": 4}
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert conn.execute("SELECT DISTINCT afeta_conta || origem FROM operacoes_rv").fetchall()[0][0] == "0b3"
    assert inv.obter(conn, _id(conn, "HGLG11")).tipo == "fii" and inv.obter(conn, _id(conn, "PETR4")).criado_em == HOJE
    p = rv.posicao(conn, _id(conn, "PETR4"), HOJE)
    # 100 + 100 + 50 comprados a 3500, 3500, 3650; vendeu 60
    assert p["quantidade"] == 190 and p["proventos_total"] == 4550
    assert p["preco_medio"] == pytest.approx((350_000 * 2 + 182_500) / 250)
    assert rv.posicao(conn, _id(conn, "VALE3"), HOJE)["proventos_total"] == 2210
    assert rv.posicao(conn, _id(conn, "HGLG11"), HOJE)["proventos_total"] == 1120


def test_reimportar_nao_duplica(conn, planilha):
    b3.confirmar(conn, b3.prever(conn, planilha), hoje=HOJE)
    p = b3.prever(conn, planilha)
    assert p.novas == [] and len(p.duplicadas) == 10 and p.ativos_novos == []
    assert b3.confirmar(conn, p, hoje=HOJE) == {"operacoes": 0, "ativos": 0}
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 10


def test_planilha_com_linhas_novas_so_traz_as_novas(conn, tmp_path):
    b3.confirmar(conn, b3.prever(conn, falsa.gerar(tmp_path / "a.xlsx")), hoje=HOJE)
    nova = ["Credito", "20/10/2026", "Dividendo", falsa.HGLG, falsa.CORRETORA, 10, "R$ 1,10", "R$ 11,00"]
    p = b3.prever(conn, falsa.gerar(tmp_path / "b.xlsx", falsa.LINHAS + [nova]))
    assert len(p.novas) == 1 and p.novas[0].ticker == "HGLG11" and len(p.duplicadas) == 10 and p.ativos_novos == []


def test_duas_linhas_identicas_no_mesmo_arquivo_sao_duas_operacoes(conn, planilha):
    b3.confirmar(conn, b3.prever(conn, planilha), hoje=HOJE)
    compras = conn.execute("SELECT COUNT(*) FROM operacoes_rv WHERE tipo = 'compra' AND data = '2026-03-02'").fetchone()[0]
    assert compras == 2


def test_ativo_ja_cadastrado_nao_vira_ativo_novo(conn, planilha):
    existente = inv.criar(conn, HOJE, nome="Minha Petrobras", tipo="acao", ticker="PETR4")
    p = b3.prever(conn, planilha)
    assert "PETR4" not in [a.ticker for a in p.ativos_novos]
    b3.confirmar(conn, p, hoje=HOJE)
    assert conn.execute("SELECT COUNT(*) FROM ativos WHERE ticker = 'PETR4'").fetchone()[0] == 1
    assert rv.posicao(conn, existente, HOJE)["quantidade"] == 190 and inv.obter(conn, existente).nome == "Minha Petrobras"


def test_tipo_do_ativo_novo_e_editavel_antes_de_confirmar(conn, planilha):
    p = b3.prever(conn, planilha)
    next(a for a in p.ativos_novos if a.ticker == "MXRF11").tipo = "acao"
    b3.confirmar(conn, p, hoje=HOJE)
    assert inv.obter(conn, _id(conn, "MXRF11")).tipo == "acao"
    p2 = b3.prever(conn, falsa.gerar(planilha.parent / "x.xlsx"))
    p2.ativos_novos.append(b3.AtivoNovo("ABCD3", "Teste", "cripto"))
    with pytest.raises(b3.ImportacaoErro):
        b3.confirmar(conn, p2, hoje=HOJE)


def test_inferencia_de_tipo():
    assert b3.inferir_tipo("HGLG11", "") == "fii" and b3.inferir_tipo("BPAC11", "BANCO BTG PACTUAL UNITS") == "fii"
    assert b3.inferir_tipo("VALE3", "VALE S.A.") == "acao"
    assert b3.inferir_tipo("ABCD3", "ALGUM FDO INV IMOB") == "fii"


def test_cabecalhos_com_outro_formato_e_linhas_em_branco(conn, tmp_path):
    livro = openpyxl.Workbook()
    aba = livro.active
    aba.append(["Extrato de movimentações", None])  # título antes do cabeçalho
    aba.append([])
    aba.append(["  ENTRADA / SAÍDA ", "data", "MOVIMENTAÇÃO", "produto", "Instituicao", "quantidade", "PREÇO UNITÁRIO", "valor da operação"])
    aba.append(["Credito", "05/10/2026", "Transferência - Liquidação", "PETR4F - PETROLEO", "X", "10", "R$ 38,00", "R$ 380,00"])
    aba.append([None] * 8)
    aba.append(["Debito", "06/10/2026", "Transferência - Liquidação", "PETR4F - PETROLEO", "X", "4", "R$ 39,00", "R$ 156,00"])
    caminho = tmp_path / "outra.xlsx"
    livro.save(caminho)
    p = b3.prever(conn, caminho)
    assert [(o.ticker, o.tipo, o.valor_centavos) for o in p.novas] == [("PETR4", "compra", 38_000), ("PETR4", "venda", 15_600)]
    assert p.ignoradas == []


def test_preco_ou_valor_que_faltam_sao_deduzidos(conn, tmp_path):
    linhas = [
        ["Credito", "05/10/2026", "Transferência - Liquidação", falsa.PETR, "X", 10, "R$ 38,00", "-"],
        ["Credito", "06/10/2026", "Transferência - Liquidação", falsa.PETR, "X", 10, "-", "R$ 380,00"],
        ["Credito", "07/10/2026", "Transferência - Liquidação", falsa.PETR, "X", "-", "R$ 38,00", "R$ 380,00"],
        ["Credito", "08/10/2026", "Dividendo", falsa.PETR, "X", 10, "-", "-"],
        ["", "09/10/2026", "Transferência - Liquidação", falsa.PETR, "X", 10, "R$ 38,00", "R$ 380,00"],
    ]
    p = b3.prever(conn, falsa.gerar(tmp_path / "f.xlsx", linhas))
    assert [(o.preco_centavos, o.valor_centavos) for o in p.novas] == [(3800, 38_000), (3800, 38_000)]
    assert [i.motivo for i in p.ignoradas] == ["Sem quantidade", "Sem valor", "Não deu pra saber se foi compra ou venda (coluna Entrada/Saída)"]


def test_arquivo_que_nao_serve_da_erro_claro(conn, tmp_path):
    with pytest.raises(b3.ImportacaoErro, match="abrir"):
        b3.prever(conn, tmp_path / "nao-existe.xlsx")
    texto = tmp_path / "x.xlsx"
    texto.write_text("isto não é uma planilha", encoding="utf-8")
    with pytest.raises(b3.ImportacaoErro, match="abrir"):
        b3.prever(conn, texto)
    livro = openpyxl.Workbook()
    livro.active.append(["Nome", "Valor"])
    livro.save(tmp_path / "outra.xlsx")
    with pytest.raises(b3.ImportacaoErro, match="colunas"):
        b3.prever(conn, tmp_path / "outra.xlsx")


def test_confirmar_e_tudo_ou_nada(conn, planilha):
    p = b3.prever(conn, planilha)
    p.novas[-1].ticker = "ZZZZ9"  # operação de um ativo que não está na lista de novos
    with pytest.raises(b3.ImportacaoErro):
        b3.confirmar(conn, p, hoje=HOJE)
    assert conn.execute("SELECT COUNT(*) FROM ativos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM operacoes_rv").fetchone()[0] == 0


def test_a_importacao_entra_no_patrimonio(conn, planilha):
    b3.confirmar(conn, b3.prever(conn, planilha), hoje=HOJE)
    assert inv.patrimonio(conn, HOJE)["renda_variavel"] > 0  # sem cotação, pelo custo


# --- Eventos corporativos --------------------------------------------------------------------------------

ITSA = "ITSA4 - ITAUSA S.A."
C = falsa.CORRETORA


def _importar(conn, tmp_path, linhas, nome="e.xlsx"):
    p = b3.prever(conn, falsa.gerar(tmp_path / nome, linhas))
    b3.confirmar(conn, p, hoje=HOJE)
    return p


def _pos(conn, ticker="ITSA4"):
    return rv.posicao(conn, _id(conn, ticker), HOJE)


EVENTOS_ITSA = [
    ["Credito", "02/03/2026", "Transferência - Liquidação", ITSA, C, 100, "R$ 10,00", "R$ 1.000,00"],
    ["Credito", "10/03/2026", "Bonificação em Ativos", ITSA, C, 10, "R$ 5,00", "R$ 50,00"],
    ["Credito", "20/03/2026", "Desdobro", ITSA, C, 110, "-", "-"],
    ["Debito", "30/03/2026", "Grupamento", ITSA, C, 198, "-", "-"],
]


def test_bonificacao_desdobro_e_grupamento_batem_com_a_corretora(conn, tmp_path):
    p = _importar(conn, tmp_path, EVENTOS_ITSA)
    assert [(o.tipo, o.quantidade, o.valor_centavos, o.preco_centavos) for o in p.novas[1:]] == [
        ("bonificacao", 10, 5000, None), ("desdobramento", 110, 0, None), ("grupamento", -198, 0, None),
    ]
    assert conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert conn.execute("SELECT DISTINCT afeta_conta FROM operacoes_rv").fetchall()[0][0] == 0
    ate = lambda d: rv.posicao(conn, _id(conn, "ITSA4"), d)  # noqa: E731
    a = ate(date(2026, 3, 10))  # 100 + 10 bonificadas, custo 100.000 + 5.000 c
    assert (a["quantidade"], a["custo"]) == (110, 105_000) and a["preco_medio"] == pytest.approx(105_000 / 110)  # 954,54...
    a = ate(date(2026, 3, 20))  # 1 pra 2
    assert (a["quantidade"], a["custo"]) == (220, 105_000) and a["preco_medio"] == pytest.approx(105_000 / 220)
    a = ate(date(2026, 3, 30))  # 10 pra 1
    assert (a["quantidade"], a["custo"], a["realizado"]) == (22, 105_000, 0) and a["preco_medio"] == pytest.approx(105_000 / 22)


def test_bonificacao_sem_custo_derruba_o_preco_medio(conn, tmp_path):
    _importar(conn, tmp_path, [EVENTOS_ITSA[0], ["Credito", "10/03/2026", "Bonificação em Ativos", ITSA, C, 10, "-", "-"]])
    p = _pos(conn)
    assert (p["quantidade"], p["custo"]) == (110, 100_000) and p["preco_medio"] == pytest.approx(100_000 / 110)


def test_fracao_sai_ao_preco_medio_sem_lucro(conn, tmp_path):
    linhas = [
        ["Credito", "02/03/2026", "Transferência - Liquidação", ITSA, C, 10, "R$ 10,00", "R$ 100,00"],
        ["Credito", "10/03/2026", "Bonificação em Ativos", ITSA, C, "0,5", "-", "-"],
        ["Debito", "11/03/2026", "Fração em Ativos", ITSA, C, "0,5", "-", "-"],
        ["Credito", "12/03/2026", "Leilão de Fração", ITSA, C, "-", "-", "R$ 4,00"],
    ]
    p = _importar(conn, tmp_path, linhas)
    assert [i.motivo for i in p.ignoradas] == ["Valor das frações vendidas em leilão: lance como entrada na conta se quiser"]
    pos = _pos(conn)
    assert pos["quantidade"] == 10 and pos["realizado"] == 0 and pos["custo"] == 9524  # 10.000 × 10/10,5: a fração saiu ao PM
    assert pos["preco_medio"] == pytest.approx(10_000 / 10.5)  # o PM não muda com a fração
    antes = rv.posicao(conn, _id(conn, "ITSA4"), date(2026, 3, 10))
    assert (antes["quantidade"], antes["custo"]) == (10.5, 10_000)


def test_evento_no_mesmo_dia_vale_antes_da_venda(conn, tmp_path):
    linhas = [
        EVENTOS_ITSA[0],
        ["Credito", "20/03/2026", "Desdobramento", ITSA, C, 100, "-", "-"],
        ["Debito", "20/03/2026", "Transferência - Liquidação", ITSA, C, 150, "R$ 6,00", "R$ 900,00"],
    ]
    _importar(conn, tmp_path, linhas)
    p = _pos(conn)
    assert p["quantidade"] == 50 and p["custo"] == 25_000 and p["realizado"] == 90_000 - 75_000


def test_subscricao_exercida_vira_compra_do_ticker_base(conn, tmp_path):
    hglg13 = "HGLG13 - CSHG LOGISTICA - FDO INV IMOB - FII"
    linhas = [
        ["Credito", "02/03/2026", "Transferência - Liquidação", falsa.HGLG, C, 10, "R$ 160,00", "R$ 1.600,00"],
        ["Credito", "10/03/2026", "Direitos de Subscrição - Exercido", hglg13, C, 5, "R$ 150,00", "R$ 750,00"],
        ["Credito", "11/03/2026", "Recibo de Subscrição", "ITSA10 - ITAUSA S.A.", C, 20, "R$ 9,00", "R$ 180,00"],
        ["Credito", "12/03/2026", "Subscrição", "ITSA9 - ITAUSA S.A.", C, 10, "R$ 8,00", "R$ 80,00"],
        ["Credito", "15/03/2026", "Atualização", "HGLG11 - CSHG LOGISTICA - FDO INV IMOB - FII", C, 5, "-", "-"],
        ["Credito", "09/03/2026", "Direito de Subscrição", "HGLG12 - CSHG LOGISTICA - FDO INV IMOB - FII", C, 5, "-", "-"],
        ["Credito", "09/03/2026", "Cessão de Direitos", "HGLG12 - CSHG LOGISTICA - FDO INV IMOB - FII", C, 5, "-", "-"],
        ["Credito", "16/03/2026", "Transferência", "HGLG11 - CSHG LOGISTICA - FDO INV IMOB - FII", C, 5, "-", "-"],
    ]
    p = _importar(conn, tmp_path, linhas)
    assert [(o.ticker, o.tipo, o.quantidade) for o in p.novas] == [
        ("HGLG11", "compra", 10), ("HGLG11", "compra", 5), ("ITSA4", "compra", 20), ("ITSA3", "compra", 10),
    ]
    assert [i.motivo for i in p.ignoradas][:2] == ["Conversão do recibo: já contado na subscrição", "Direito de subscrição ainda não exercido: só entra quando for exercido"]
    assert len(p.ignoradas) == 4 and p.ignoradas[-1].motivo == "Troca de corretora: a posição não muda"
    h = _pos(conn, "HGLG11")
    assert h["quantidade"] == 15 and h["custo"] == 160_000 + 75_000
    assert {a.ticker for a in p.ativos_novos} == {"HGLG11", "ITSA4", "ITSA3"}


def test_ticker_base_da_subscricao():
    assert [b3._ticker_base(t) for t in ("HGLG12", "HGLG13", "HGLG14", "HGLG15", "ITSA1", "ITSA9", "ITSA2", "ITSA10", "VALE3")] == [
        "HGLG11", "HGLG11", "HGLG11", "HGLG11", "ITSA3", "ITSA3", "ITSA4", "ITSA4", "VALE3",
    ]


def test_reimportar_planilha_com_eventos_nao_duplica(conn, tmp_path):
    _importar(conn, tmp_path, EVENTOS_ITSA)
    p = b3.prever(conn, tmp_path / "e.xlsx")
    assert p.novas == [] and len(p.duplicadas) == 4
    assert b3.confirmar(conn, p, hoje=HOJE) == {"operacoes": 0, "ativos": 0}
    assert _pos(conn)["quantidade"] == 22


def test_evento_sem_quantidade_ou_sentido_e_ignorado(conn, tmp_path):
    linhas = [
        ["Credito", "10/03/2026", "Bonificação em Ativos", ITSA, C, "-", "-", "R$ 5,00"],
        ["", "10/03/2026", "Desdobro", ITSA, C, 5, "-", "-"],
        ["Debito", "10/03/2026", "Bonificação em Ativos", ITSA, C, 5, "-", "-"],
    ]
    p = b3.prever(conn, falsa.gerar(tmp_path / "x.xlsx", linhas))
    assert p.novas == [] and len(p.ignoradas) == 3


def test_eventos_nao_entram_em_aportes_do_mes(conn, tmp_path):
    _importar(conn, tmp_path, EVENTOS_ITSA)
    assert inv.aportes_do_mes(conn, 2026, 3) == []  # histórico importado não conta
    conn.execute("UPDATE operacoes_rv SET afeta_conta = 1")  # mesmo se afetassem a conta, só compra/venda entram
    assert inv.aportes_do_mes(conn, 2026, 3) == [("Itausa S.A.", 100_000)]


def test_historico_mostra_eventos_e_exclusao_confere_a_posicao(conn, tmp_path):
    _importar(conn, tmp_path, EVENTOS_ITSA)
    ativo = _id(conn, "ITSA4")
    assert [i["tipo"] for i in inv.historico(conn, ativo)] == ["grupamento", "desdobramento", "bonificacao", "compra"]
    desdobro = next(i for i in inv.historico(conn, ativo) if i["tipo"] == "desdobramento")
    with pytest.raises(ValueError, match="não se edita"):
        rv.editar_operacao(conn, desdobro["id"], data=HOJE, quantidade=1, preco=100)
    with pytest.raises(ValueError, match="vendendo mais"):  # sem o desdobro, o grupamento de 198 estoura
        rv.excluir_operacao(conn, desdobro["id"])
    grup = next(i for i in inv.historico(conn, ativo) if i["tipo"] == "grupamento")
    rv.excluir_operacao(conn, grup["id"])
    rv.excluir_operacao(conn, desdobro["id"])
    assert _pos(conn)["quantidade"] == 110
