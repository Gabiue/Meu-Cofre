"""Gera o executável com PyInstaller: .exe (Windows) ou onedir + AppDir (Linux).

    uv run --group build python scripts/build.py [--fumaca] [--appimagetool CAMINHO]

Linux: monta build/AppDir; o .AppImage só sai se o appimagetool for passado (a CI baixa o oficial).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
NOME = "meu-cofre"
ENTRADA = RAIZ / "scripts" / "entrada_executavel.py"
SVG = RAIZ / "app" / "meu_cofre" / "ui" / "icone.svg"
ICONES = RAIZ / "build" / "icone"
APPDIR = RAIZ / "build" / "AppDir"
# O app só usa QtCore/Gui/Widgets/Charts (+ Svg pro ícone); o resto do PySide6 só incha o pacote.
EXCLUIDOS = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "Qt3DCore", "QtQuick", "QtQml", "QtMultimedia", "QtPdf", "QtDesigner", "QtSql", "QtTest"]

DESKTOP = f"""[Desktop Entry]
Type=Application
Name=Meu Cofre
Comment=Finanças pessoais locais
Exec={NOME}
Icon={NOME}
Categories=Office;Finance;
Terminal=false
"""


def argumentos_pyinstaller(plataforma: str) -> list[str]:
    """Args do PyInstaller por SO: Windows em arquivo único com ícone; Linux em pasta (abre mais rápido)."""
    args = [
        str(ENTRADA), "--name", NOME, "--noconfirm", "--windowed",
        "--collect-data", "meu_cofre",  # schema.sql, icone.svg e a fonte embutida
        "--hidden-import", "PySide6.QtSvg",  # plugin do ícone SVG; nenhum import direto no app
        "--distpath", str(RAIZ / "dist"), "--workpath", str(RAIZ / "build" / "pyinstaller"), "--specpath", str(RAIZ / "build"),
    ]
    for modulo in EXCLUIDOS:
        args += ["--exclude-module", f"PySide6.{modulo}"]
    if plataforma == "win32":
        args += ["--onefile", "--icon", str(ICONES / f"{NOME}.ico")]
    else:
        args += ["--onedir"]
    return args


def gerar_icones() -> None:
    """.ico (Windows) e .png 256 (Linux) a partir do SVG, com o próprio Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication([])
    ICONES.mkdir(parents=True, exist_ok=True)
    imagem = QImage(256, 256, QImage.Format.Format_ARGB32)
    imagem.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(imagem)
    QSvgRenderer(str(SVG)).render(pintor)
    pintor.end()
    for destino in (ICONES / f"{NOME}.png", ICONES / f"{NOME}.ico"):
        if not imagem.save(str(destino)):
            raise SystemExit(f"não consegui gerar {destino}")


def montar_appdir(onedir: Path, icone: Path, destino: Path = APPDIR) -> Path:
    """AppDir freedesktop: usr/bin = o onedir, AppRun, .desktop e o ícone na raiz."""
    shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(onedir, destino / "usr" / "bin")
    run = destino / "AppRun"
    run.write_text(f'#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/usr/bin/{NOME}" "$@"\n')
    run.chmod(0o755)
    (destino / f"{NOME}.desktop").write_text(DESKTOP, encoding="utf-8")
    shutil.copy(icone, destino / f"{NOME}.png")
    return destino


def fumaca(executavel: Path, plataforma: str) -> None:
    """Abre o executável offscreen: tem que salvar o PNG e criar o banco. No Linux, também roda a linha de comando."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "MEU_COFRE_SEM_REDE": "1", "MEU_COFRE_DB": str(base / "fumaca.db"), "MEU_COFRE_SCREENSHOT": str(base / "fumaca.png")}
        subprocess.run([str(executavel)], env=env, timeout=120, check=True)
        png = base / "fumaca.png"
        if not png.exists() or png.stat().st_size == 0:
            raise SystemExit("fumaça falhou: o screenshot não foi gerado")
        if not (base / "fumaca.db").exists():
            raise SystemExit("fumaça falhou: o banco não foi criado")
        if plataforma == "win32":  # o exe windowed não tem console: não há saída pra conferir
            return
        env.pop("MEU_COFRE_SCREENSHOT")
        # banco vazio: sem lançamentos, o relatório não gera nada e sai com 0
        subprocess.run([str(executavel), "relatorio", "--saida", "-"], env=env, timeout=120, check=True)


def main() -> int:
    analisador = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("--fumaca", action="store_true", help="roda o executável gerado offscreen e confere screenshot e banco")
    analisador.add_argument("--appimagetool", help="caminho do appimagetool: gera meu-cofre-x86_64.AppImage (só Linux)")
    args = analisador.parse_args()

    from PyInstaller.__main__ import run

    gerar_icones()
    run(argumentos_pyinstaller(sys.platform))
    dist = RAIZ / "dist"
    if sys.platform == "win32":
        executavel = dist / f"{NOME}.exe"
    else:
        executavel = dist / NOME / NOME
        appdir = montar_appdir(dist / NOME, ICONES / f"{NOME}.png")
        print(f"AppDir: {appdir}")
        if args.appimagetool:
            saida = dist / f"{NOME}-x86_64.AppImage"
            subprocess.run([args.appimagetool, str(appdir), str(saida)], check=True, env={**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})
            print(f"AppImage: {saida}")
            executavel = saida  # a fumaça roda no arquivo que vai pra página de Releases, não só no onedir
    if args.fumaca:
        fumaca(executavel, sys.platform)
        print(f"fumaça ok: {executavel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
