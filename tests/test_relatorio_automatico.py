"""Relatório automático (issue #38): comando, geração ao abrir o app e script do timer. Só bancos temporários."""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import id_cat, id_conta
from PySide6.QtWidgets import QApplication

from meu_cofre import db, relatorio_automatico
from meu_cofre import lancamentos as lanc
from meu_cofre.ui import tema
from meu_cofre.ui.janela import JanelaPrincipal

HOJE = date(2026, 11, 3)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "instalar-relatorio-automatico.sh"


def _gasto(conn, dia):
    lanc.registrar(conn, entrada=False, valor_centavos=5_000, descricao="Feira", categoria_id=id_cat(conn, "Mercado"),
                   metodo="pix", conta_id=id_conta(conn, "Inter"), data=dia)


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Banco temporário com um gasto em outubro/2026, apontado por MEU_COFRE_DB."""
    caminho = tmp_path / "teste.db"
    monkeypatch.setenv("MEU_COFRE_DB", str(caminho))
    c = db.conectar(caminho)
    _gasto(c, date(2026, 10, 10))
    c.close()
    return caminho


def _rodar(args):
    return relatorio_automatico.main(args, hoje=HOJE)


def test_mes_padrao_e_o_anterior_e_grava_ao_lado_do_banco(banco, capsys):
    assert _rodar([]) == 0
    esperado = banco.parent / "relatorios" / "2026-10.md"
    assert capsys.readouterr().out == f"{esperado}\n"
    assert esperado.read_text(encoding="utf-8").startswith("# Relatório financeiro: Outubro de 2026")


def test_mes_escolhido_e_nao_reescreve_sem_forcar(banco, capsys):
    arquivo = banco.parent / "relatorios" / "2026-10.md"
    assert _rodar(["--mes", "2026-10"]) == 0
    arquivo.write_text("editado", encoding="utf-8")
    assert _rodar(["--mes", "2026-10"]) == 0
    assert arquivo.read_text(encoding="utf-8") == "editado" and capsys.readouterr().out.count(str(arquivo)) == 2
    assert _rodar(["--mes", "2026-10", "--forcar"]) == 0
    assert arquivo.read_text(encoding="utf-8").startswith("# Relatório")


@pytest.mark.parametrize("mes", ["2026-13", "10/2026", "2026-1", "abc"])
def test_formato_invalido_sai_com_2(banco, mes, capsys):
    with pytest.raises(SystemExit) as e:
        _rodar(["--mes", mes])
    assert e.value.code == 2 and "AAAA-MM" in capsys.readouterr().err


def test_saida_traco_escreve_o_markdown_e_nao_cria_arquivo(banco, capsys):
    assert _rodar(["--saida", "-"]) == 0
    assert capsys.readouterr().out.startswith("# Relatório financeiro: Outubro de 2026")
    assert not (banco.parent / "relatorios").exists()


def test_saida_em_arquivo_escolhido(banco, tmp_path, capsys):
    destino = tmp_path / "outra" / "r.md"
    assert _rodar(["--saida", str(destino)]) == 0
    assert capsys.readouterr().out == f"{destino}\n" and destino.exists()


def test_mes_vazio_nao_cria_e_sai_com_0(banco, capsys):
    assert _rodar(["--mes", "2026-08"]) == 0
    saida = capsys.readouterr()
    assert saida.out == "" and "Sem lançamentos" in saida.err
    assert _rodar(["--mes", "2026-08", "--saida", "-"]) == 0
    assert not (banco.parent / "relatorios").exists()


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


def test_abrir_o_app_gera_uma_vez_e_avisa(app, conn):
    _gasto(conn, date(2026, 10, 10))
    arquivo = relatorio_automatico.caminho_do_mes(conn, 2026, 10)
    janela = JanelaPrincipal(conn, HOJE)
    assert arquivo.exists() and not janela.pilula_relatorio.isHidden()
    assert janela.pilula_relatorio.text() == "Relatório de outubro pronto"
    arquivo.write_text("editado", encoding="utf-8")
    segunda = JanelaPrincipal(conn, HOJE)
    assert arquivo.read_text(encoding="utf-8") == "editado" and segunda.pilula_relatorio.isHidden()


def test_abrir_o_app_com_mes_anterior_vazio_nao_gera(app, conn):
    janela = JanelaPrincipal(conn, HOJE)
    assert not relatorio_automatico.caminho_do_mes(conn, 2026, 10).exists() and janela.pilula_relatorio.isHidden()


@pytest.mark.skipif(sys.platform == "win32", reason="script .sh: Linux")
def test_script_instala_e_remove(tmp_path):
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "MEU_COFRE_SEM_SYSTEMCTL": "1", "MEU_COFRE_DB": "/dados/falso.db", "MEU_COFRE_ESTAVEL": str(tmp_path / "estavel")}
    unidades = tmp_path / "cfg" / "systemd" / "user"
    assert subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True).returncode == 1  # sem a cópia estável
    (tmp_path / "estavel").mkdir()
    # uv precisa existir no PATH do script; um falso basta
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "uv").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "bin" / "uv").chmod(0o755)
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    subprocess.run(["sh", str(SCRIPT)], env=env, check=True)
    servico = (unidades / "meu-cofre-relatorio.service").read_text(encoding="utf-8")
    timer = (unidades / "meu-cofre-relatorio.timer").read_text(encoding="utf-8")
    wrapper = (unidades / "meu-cofre-relatorio.sh").read_text(encoding="utf-8")
    print(servico, timer, wrapper, sep="\n---\n")
    assert "Type=oneshot" in servico and f"WorkingDirectory={tmp_path / 'estavel'}" in servico
    assert f"ExecStart=/bin/sh {unidades}/meu-cofre-relatorio.sh" in servico
    assert "OnCalendar=*-*-01 08:00:00" in timer and "Persistent=true" in timer
    assert "MEU_COFRE_DB=\"/dados/falso.db\"" in wrapper and "run meu-cofre relatorio" in wrapper
    assert f"{tmp_path}/cfg/meu-cofre/depois-do-relatorio" in wrapper
    subprocess.run(["sh", str(SCRIPT), "--remover"], env=env, check=True)
    assert not list(unidades.glob("meu-cofre-relatorio.*"))
