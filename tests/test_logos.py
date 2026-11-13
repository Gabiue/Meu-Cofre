"""Logos das assinaturas (#61): casamento pelo nome e desenho do ícone, tudo offline."""

import pytest
from PySide6.QtWidgets import QApplication

from meu_cofre.ui import logos, tema


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    tema.aplicar_tema(a)
    return a


@pytest.mark.parametrize("descricao, arquivo", [
    ("Netflix Premium", "netflix"),
    ("YouTube Music", "youtubemusic"),  # a mais específica antes de "youtube"
    ("youtube premium", "youtube"),
    ("Assinatura do Spotify", "spotify"),
    ("Conta de luz", "bolt"),
    ("Aluguel", "home"),
    ("Água e esgoto", "droplet"),  # sem acento no casamento
    ("Internet fibra", "wifi"),
    ("Academia", "barbell"),
    ("Plano de saúde", "heart-rate-monitor"),
])
def test_casa_pelo_nome(descricao, arquivo):
    caminho, _cor = logos.icone_para(descricao)
    assert caminho.stem == arquivo


@pytest.mark.parametrize("descricao", ["Streaming de vídeo", "Máximo", "Salário", "Ubernúcleo", "Leite"])
def test_sem_par_nao_inventa(descricao):
    assert logos.icone_para(descricao) is None  # só palavra inteira: "Ubernúcleo" não é Uber


def test_marca_leva_a_cor_e_generico_usa_a_do_texto():
    assert logos.icone_para("Netflix")[1] == "#E50914"
    assert logos.icone_para("Aluguel")[1] is None


def test_todo_arquivo_do_mapa_existe():
    for nome in logos.MARCAS:
        assert (logos.PASTA / "marcas" / f"{nome}.svg").is_file(), nome
    for nome in logos.GENERICOS:
        assert (logos.PASTA / "genericos" / f"{nome}.svg").is_file(), nome


def test_desenha_marca_preta_e_generico(app):
    for descricao in ("Uber One", "Netflix", "Aluguel"):  # Uber é preta: vira a cor do texto
        img = logos.pixmap_do_icone(*logos.icone_para(descricao), 18).toImage()
        assert any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))  # desenhou algo
