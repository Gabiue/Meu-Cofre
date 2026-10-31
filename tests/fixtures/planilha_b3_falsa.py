"""Planilha de movimentações da B3 **falsa** (nada de dado real): gerada na hora pelos testes e pelos screenshots.

    uv run python tests/fixtures/planilha_b3_falsa.py /tmp/movimentacao-falsa.xlsx
"""

import sys
from datetime import datetime
from pathlib import Path

import openpyxl

CABECALHO = ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição", "Quantidade", "Preço unitário", "Valor da Operação"]
CORRETORA = "CORRETORA FICTICIA S/A"
PETR = "PETR4 - PETROLEO BRASILEIRO S.A. PETROBRAS"
VALE = "VALE3 - VALE S.A."
HGLG = "HGLG11 - CSHG LOGISTICA - FDO INV IMOB - FII"
MXRF = "MXRF11 - MAXI RENDA - FDO INV IMOB - FII"

LINHAS = [
    ["Credito", "02/03/2026", "Transferência - Liquidação", PETR, CORRETORA, 100, "R$ 35,00", "R$ 3.500,00"],
    ["Credito", "02/03/2026", "Transferência - Liquidação", PETR, CORRETORA, 100, "R$ 35,00", "R$ 3.500,00"],  # idêntica à de cima
    ["Credito", datetime(2026, 4, 15), "Transferência - Liquidação", PETR, CORRETORA, 50, 36.5, 1825.0],  # células numéricas
    ["Debito", "10/09/2026", "Transferência - Liquidação", PETR, CORRETORA, 60, "R$ 40,00", "R$ 2.400,00"],
    ["Credito", "20/05/2026", "Dividendo", PETR, CORRETORA, 250, "R$ 0,18", "R$ 45,50"],
    ["Credito", "03/03/2026", "Transferência - Liquidação", VALE, CORRETORA, 40, "R$ 60,00", "R$ 2.400,00"],
    ["Credito", "10/06/2026", "Juros Sobre Capital Próprio", VALE, CORRETORA, 40, "R$ 0,55", "R$ 22,10"],
    ["Credito", "05/03/2026", "Transferência - Liquidação", HGLG, CORRETORA, 10, "R$ 160,00", "R$ 1.600,00"],
    ["Credito", "14/04/2026", "Rendimento", HGLG, CORRETORA, 10, "R$ 1,12", "R$ 11,20"],
    ["Credito", "07/04/2026", "Transferência - Liquidação", MXRF, CORRETORA, 200, "R$ 10,00", "R$ 2.000,00"],
    # as que o app ignora
    ["Credito", "11/03/2026", "Transferência", PETR, CORRETORA, 10, "-", "-"],
    ["Credito", "12/03/2026", "Bonificação em Ativos", VALE, CORRETORA, 4, "R$ 0,00", "-"],
    ["Credito", "13/03/2026", "Transferência - Liquidação", "Tesouro Selic 2029", CORRETORA, 1, "R$ 14.000,00", "R$ 14.000,00"],
    ["Credito", "bla", "Dividendo", VALE, CORRETORA, 40, "R$ 0,40", "R$ 16,00"],
]


def gerar(caminho: Path | str, linhas: list[list] | None = None) -> Path:
    """Grava a planilha falsa (`linhas` padrão: `LINHAS`) e devolve o caminho."""
    livro = openpyxl.Workbook()
    aba = livro.active
    aba.title = "Movimentação"
    aba.append(CABECALHO)
    for linha in LINHAS if linhas is None else linhas:
        aba.append(linha)
    livro.save(caminho)
    return Path(caminho)


if __name__ == "__main__":
    print(gerar(sys.argv[1] if len(sys.argv) > 1 else "movimentacao-falsa.xlsx"))
