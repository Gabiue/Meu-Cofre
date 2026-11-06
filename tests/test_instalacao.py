"""Issue #42: caminho do banco e scripts de instalação, só com HOME/PATH temporários e `uv` falso."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from meu_cofre import config

RAIZ = Path(__file__).resolve().parents[1]
SCRIPTS = RAIZ / "scripts"
INSTALAR = SCRIPTS / "instalar.sh"
SO_LINUX = pytest.mark.skipif(sys.platform == "win32", reason="scripts .sh e git worktree: Linux (Windows tem os .ps1)")


# ---- config.caminho_banco ----

@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.delenv("MEU_COFRE_DB", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "RAIZ", tmp_path / "projeto")
    return tmp_path


def test_env_vence_tudo(ambiente, monkeypatch):
    (ambiente / "projeto" / "data").mkdir(parents=True)
    (ambiente / "projeto" / "data" / "meu-cofre.db").touch()
    monkeypatch.setenv("MEU_COFRE_DB", str(ambiente / "x.db"))
    assert config.caminho_banco() == ambiente / "x.db"


def test_legado_existente_vence_xdg(ambiente):
    (ambiente / "projeto" / "data").mkdir(parents=True)
    (ambiente / "projeto" / "data" / "meu-cofre.db").touch()
    assert config.caminho_banco() == ambiente / "projeto" / "data" / "meu-cofre.db"


@pytest.mark.skipif(sys.platform == "win32", reason="no Windows o padrão é %APPDATA%")
def test_sem_legado_usa_xdg(ambiente, monkeypatch):
    assert config.caminho_banco() == ambiente / "home" / ".local/share/meu-cofre/meu-cofre.db"
    monkeypatch.setenv("XDG_DATA_HOME", str(ambiente / "xdg"))
    assert config.caminho_banco() == ambiente / "xdg" / "meu-cofre" / "meu-cofre.db"


def test_sem_legado_no_windows_usa_appdata(ambiente, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(ambiente / "roaming"))
    assert config.caminho_banco() == ambiente / "roaming" / "meu-cofre" / "meu-cofre.db"
    monkeypatch.delenv("APPDATA")
    assert config.caminho_banco() == ambiente / "home" / "AppData" / "Roaming" / "meu-cofre" / "meu-cofre.db"


# ---- scripts ----

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd)})


def _esqueleto(pasta):
    """Cópia mínima do projeto: o que o instalador lê (ícone, scripts, pyproject)."""
    (pasta / "app/meu_cofre/ui").mkdir(parents=True)
    shutil.copy(RAIZ / "app/meu_cofre/ui/icone.svg", pasta / "app/meu_cofre/ui/icone.svg")
    shutil.copytree(SCRIPTS, pasta / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (pasta / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (pasta / "uv.lock").write_text("", encoding="utf-8")
    return pasta


@pytest.fixture
def casa(tmp_path):
    """HOME temporário e PATH com um `uv` falso que aceita qualquer comando."""
    (tmp_path / "bin").mkdir()
    uv = tmp_path / "bin" / "uv"
    uv.write_text("#!/bin/sh\n", encoding="utf-8")
    uv.chmod(0o755)
    (tmp_path / "home").mkdir()
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "MEU_COFRE_SEM_CACHE": "1"}
    return tmp_path, env


def _instalar(projeto, env, *args):
    return subprocess.run(["sh", str(projeto / "scripts/instalar.sh"), *args], env=env, capture_output=True, text=True)


def _desktop(tmp_path):
    return (tmp_path / "home/.local/share/applications/meu-cofre.desktop").read_text(encoding="utf-8")


def _clone_git(tmp_path):
    """Projeto git com `origin` (bare local) e `main`, sem rede."""
    origem = tmp_path / "origem.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origem)], check=True)
    projeto = _esqueleto(tmp_path / "projeto")
    _git("init", "-q", "-b", "main", cwd=projeto)
    _git("add", ".", cwd=projeto)
    _git("commit", "-q", "-m", "inicial", cwd=projeto)
    _git("remote", "add", "origin", str(origem), cwd=projeto)
    _git("push", "-q", "origin", "main", cwd=projeto)
    return projeto


@SO_LINUX
def test_sintaxe_dos_scripts():
    for nome in ("instalar.sh", "instalar-atalho.sh", "instalar-relatorio-automatico.sh"):
        assert subprocess.run(["sh", "-n", str(SCRIPTS / nome)]).returncode == 0


@SO_LINUX
def test_sem_uv_sai_com_erro_e_explica(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    if shutil.which("uv", path=env["PATH"]):
        pytest.skip("uv instalado em /usr/bin")
    r = _instalar(RAIZ, env)
    assert r.returncode == 1 and "uv" in r.stderr and "docs.astral.sh/uv" in r.stderr
    assert not (tmp_path / ".local").exists()


@SO_LINUX
def test_clone_git_usa_copia_estavel(casa):
    tmp, env = casa
    projeto = _clone_git(tmp)
    estavel = tmp / "estavel"
    r = _instalar(projeto, {**env, "MEU_COFRE_ESTAVEL": str(estavel)})
    assert r.returncode == 0, r.stderr
    d = _desktop(tmp)
    print(d)
    assert f"Path={estavel}" in d and (estavel / ".git").exists()
    assert "MEU_COFRE_DB" not in d and f"Exec={tmp}/bin/uv run meu-cofre\n" in d
    assert (tmp / "home/.local/share/icons/hicolor/scalable/apps/meu-cofre.svg").exists()


@SO_LINUX
def test_zip_sem_git_roda_a_propria_pasta(casa):
    tmp, env = casa
    projeto = _esqueleto(tmp / "zip")
    r = _instalar(projeto, env)
    assert r.returncode == 0 and "própria pasta" in r.stdout
    d = _desktop(tmp)
    print(d)
    assert f"Path={projeto}\n" in d and "MEU_COFRE_DB" not in d
    for campo in ("Type=Application", "Name=", "GenericName=", "Comment=", "Exec=", "Icon=meu-cofre", "Terminal=false",
                  "Categories=Office;Finance;", "Keywords=", "StartupWMClass=meu-cofre"):
        assert campo in d
    assert str(tmp / "home/.local/share/meu-cofre/meu-cofre.db") in r.stdout


@SO_LINUX
def test_zip_dentro_de_outro_repo_nao_usa_o_repo_de_fora(casa):
    tmp, env = casa
    de_fora = _clone_git(tmp)
    projeto = _esqueleto(de_fora / "baixado")
    r = _instalar(projeto, {**env, "MEU_COFRE_ESTAVEL": str(tmp / "estavel")})
    assert r.returncode == 0, r.stderr
    assert f"Path={projeto}\n" in _desktop(tmp) and not (tmp / "estavel").exists()


@SO_LINUX
def test_exec_fixa_banco_so_com_legado_ou_variavel(casa):
    tmp, env = casa
    projeto = _esqueleto(tmp / "zip")
    (projeto / "data").mkdir()
    (projeto / "data/meu-cofre.db").touch()
    assert _instalar(projeto, env).returncode == 0
    assert f"Exec=env MEU_COFRE_DB={projeto}/data/meu-cofre.db {tmp}/bin/uv run meu-cofre\n" in _desktop(tmp)
    assert _instalar(projeto, {**env, "MEU_COFRE_DB": "/dados/x.db"}).returncode == 0
    assert "Exec=env MEU_COFRE_DB=/dados/x.db " in _desktop(tmp)
    (projeto / "data/meu-cofre.db").unlink()
    assert _instalar(projeto, env).returncode == 0
    assert "MEU_COFRE_DB" not in _desktop(tmp)


@SO_LINUX
def test_remover_apaga_atalho_e_icone_e_preserva_dados(casa):
    tmp, env = casa
    projeto = _esqueleto(tmp / "zip")
    banco = tmp / "home/.local/share/meu-cofre/meu-cofre.db"
    banco.parent.mkdir(parents=True)
    banco.touch()
    assert _instalar(projeto, env).returncode == 0
    r = _instalar(projeto, env, "--remover")
    assert r.returncode == 0
    assert not (tmp / "home/.local/share/applications/meu-cofre.desktop").exists()
    assert not (tmp / "home/.local/share/icons/hicolor/scalable/apps/meu-cofre.svg").exists()
    assert banco.exists()


@SO_LINUX
def test_apelido_antigo_repassa_ao_instalador(casa):
    tmp, env = casa
    projeto = _esqueleto(tmp / "zip")
    r = subprocess.run(["sh", str(projeto / "scripts/instalar-atalho.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and (tmp / "home/.local/share/applications/meu-cofre.desktop").exists()


@SO_LINUX
def test_relatorio_sem_git_roda_a_propria_pasta(casa):
    tmp, env = casa
    projeto = _esqueleto(tmp / "zip")
    env = {**env, "XDG_CONFIG_HOME": str(tmp / "cfg"), "MEU_COFRE_SEM_SYSTEMCTL": "1", "MEU_COFRE_ESTAVEL": str(tmp / "nao-existe")}
    r = subprocess.run(["sh", str(projeto / "scripts/instalar-relatorio-automatico.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"WorkingDirectory={projeto}" in (tmp / "cfg/systemd/user/meu-cofre-relatorio.service").read_text(encoding="utf-8")
    assert "MEU_COFRE_DB" not in (tmp / "cfg/systemd/user/meu-cofre-relatorio.sh").read_text(encoding="utf-8")
