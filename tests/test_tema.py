"""Tema claro e escuro (#57): troca dos tokens, preferência gravada e troca com o app aberto."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from meu_cofre import db
from meu_cofre.ui import tema
from meu_cofre.ui.janela import JanelaPrincipal


@pytest.fixture
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    yield a
    tema.aplicar_tema(a, "claro")  # o tema é global: os outros testes rodam no claro


def test_escuro_troca_os_tokens_e_volta(app):
    tema.aplicar_tema(app, "escuro")
    assert tema.modo_atual == "escuro" and tema.FUNDO == "#07080a" and tema.BOTAO_FUNDO == "#e8ecf1"
    assert "#07080a" in app.styleSheet()  # o QSS foi refeito com as cores novas
    tema.aplicar_tema(app, "claro")
    assert tema.modo_atual == "claro" and tema.FUNDO == "#eef2f4" and tema.BOTAO_FUNDO == tema.VERDE


def test_todo_token_do_escuro_existe_no_claro():
    assert set(tema.ESCURO) == set(tema.CLARO)  # um token esquecido ficaria com a cor do claro no escuro


def test_sistema_segue_o_modo_do_sistema(app, monkeypatch):
    monkeypatch.setattr(tema, "sistema_escuro", lambda _app: True)
    tema.aplicar_tema(app, "sistema")
    assert tema.modo_atual == "escuro"
    monkeypatch.setattr(tema, "sistema_escuro", lambda _app: False)
    tema.aplicar_tema(app, "sistema")
    assert tema.modo_atual == "claro"


def test_escolher_tema_na_janela_grava_e_troca_na_hora(app, conn):
    j = JanelaPrincipal(conn, hoje=date(2026, 10, 20))
    assert j.acoes_tema["sistema"].isChecked()  # sem preferência gravada: segue o sistema
    j.acoes_tema["escuro"].trigger()
    assert db.ler_preferencia(conn, "tema") == "escuro" and tema.modo_atual == "escuro"
    assert j.acoes_tema["escuro"].isChecked() and not j.acoes_tema["sistema"].isChecked()
    j.acoes_tema["claro"].trigger()
    assert db.ler_preferencia(conn, "tema") == "claro" and tema.FUNDO == tema.CLARO["FUNDO"]
