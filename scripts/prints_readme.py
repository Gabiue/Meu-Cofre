"""Gera os prints do README em `.github/readme/`, com o banco de DEMONSTRAÇÃO (dados falsos).

Roda o app como subprocesso no modo screenshot (offscreen, sem rede), nunca abre janela de verdade:

    uv run python scripts/prints_readme.py
    uv run python scripts/prints_readme.py --db /tmp/demo.db --tamanho 1400x900
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / ".github" / "readme"

# nome do arquivo -> variáveis do modo screenshot do app
PRINTS = {
    "inicio-fluxo": {"MEU_COFRE_ABA": "inicio", "MEU_COFRE_PAINEL": "fluxo"},
    "assinaturas": {"MEU_COFRE_ABA": "recorrentes"},
    "cartoes": {"MEU_COFRE_ABA": "cartoes"},
    "metas": {"MEU_COFRE_ABA": "metas"},
    "investimentos": {"MEU_COFRE_ABA": "investimentos"},
    "escuro": {"MEU_COFRE_ABA": "inicio", "MEU_COFRE_PAINEL": "rosca", "MEU_COFRE_TEMA": "escuro"},
    "novo-lancamento": {"MEU_COFRE_ABA": "inicio", "MEU_COFRE_DIALOGO": "cartao"},
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Tira os prints do README com o banco de demonstração.")
    parser.add_argument("--db", type=Path, help="Banco de demo a usar (padrão: um novo, em pasta temporária).")
    parser.add_argument("--tamanho", default="1400x900", help="LxA da janela.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as pasta:
        db = args.db or Path(pasta) / "demo.db"
        if not args.db:
            subprocess.run([sys.executable, str(RAIZ / "scripts" / "dados_demo.py"), "--db", str(db)], check=True)
        SAIDA.mkdir(parents=True, exist_ok=True)
        for nome, extras in PRINTS.items():
            ambiente = {
                **os.environ, "QT_QPA_PLATFORM": "offscreen", "MEU_COFRE_SEM_REDE": "1", "MEU_COFRE_DB": str(db),
                "MEU_COFRE_TAMANHO": args.tamanho, "MEU_COFRE_SCREENSHOT": str(SAIDA / f"{nome}.png"), **extras,
            }
            subprocess.run([sys.executable, "-m", "meu_cofre.ui.app"], env=ambiente, check=True)
            print(f"ok: {SAIDA / nome}.png")


if __name__ == "__main__":
    main()
