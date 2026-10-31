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
        (12, "Movimentação não importada: Transferência"),
        (13, "Movimentação não importada: Bonificação em Ativos"),
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
    texto.write_text("isto não é uma planilha")
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
