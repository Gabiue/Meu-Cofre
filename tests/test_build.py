"""scripts/build.py sem rodar o PyInstaller: args por SO, AppDir e o caminho do banco no executável."""

import importlib.util
import stat
import sys
from pathlib import Path

from meu_cofre import config

_spec = importlib.util.spec_from_file_location("build_exe", Path(__file__).parents[1] / "scripts" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)  # só define funções: PyInstaller é importado dentro de main()


def test_args_windows_arquivo_unico_com_icone():
    args = build.argumentos_pyinstaller("win32")
    assert "--onefile" in args and "--onedir" not in args
    assert args[args.index("--icon") + 1].endswith("meu-cofre.ico")
    assert {"--windowed", "--noconfirm"} <= set(args)
    assert args[args.index("--collect-data") + 1] == "meu_cofre"


def test_args_linux_pasta_sem_icone():
    args = build.argumentos_pyinstaller("linux")
    assert "--onedir" in args and "--onefile" not in args and "--icon" not in args
    assert "PySide6.QtWebEngineCore" in args


def test_appdir(tmp_path):
    onedir = tmp_path / "meu-cofre"
    onedir.mkdir()
    (onedir / "meu-cofre").write_text("binario")
    png = tmp_path / "i.png"
    png.write_bytes(b"png")
    appdir = build.montar_appdir(onedir, png, tmp_path / "AppDir")
    assert (appdir / "usr" / "bin" / "meu-cofre").read_text() == "binario"
    assert (appdir / "meu-cofre.png").read_bytes() == b"png"
    assert (appdir / "AppRun").stat().st_mode & stat.S_IXUSR
    desktop = (appdir / "meu-cofre.desktop").read_text(encoding="utf-8")
    assert "Icon=meu-cofre" in desktop and "Categories=Office;Finance;" in desktop and "Exec=meu-cofre" in desktop


def test_executavel_ignora_data_legado_ao_lado(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "meu-cofre.db").write_bytes(b"")
    monkeypatch.delenv("MEU_COFRE_DB", raising=False)
    monkeypatch.setattr(config, "RAIZ", tmp_path)
    assert config.caminho_banco() == tmp_path / "data" / "meu-cofre.db"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config.caminho_banco() != tmp_path / "data" / "meu-cofre.db"
