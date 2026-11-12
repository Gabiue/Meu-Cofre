"""Logos das assinaturas e ícones das contas fixas, embutidos no app (nada é buscado na internet: o nome das
assinaturas não sai do PC).

Marcas: Simple Icons (CC0), com a cor oficial de cada uma. Genéricos (aluguel, luz, internet...): Tabler Icons (MIT).
`icone_para(descricao)` casa pelo nome, sem diferenciar maiúsculas e acentos e só por palavra inteira ("Máximo" não
vira "Max"); marca antes de genérico. Sem par: None, e quem chama mostra a inicial.
"""

import re
import unicodedata
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from meu_cofre.ui import tema

PASTA = Path(__file__).with_name("logos")

# arquivo em logos/marcas: (cor oficial, palavras que casam)
MARCAS = {
    "netflix": ("E50914", ("netflix",)),
    "spotify": ("1ED760", ("spotify",)),
    "youtubemusic": ("FF0000", ("youtube music",)),
    "youtube": ("FF0000", ("youtube",)),
    "hbomax": ("000000", ("hbo",)),
    "paramountplus": ("0064FF", ("paramount",)),
    "crunchyroll": ("FF5E00", ("crunchyroll",)),
    "applemusic": ("FA243C", ("apple music",)),
    "appletv": ("000000", ("apple tv",)),
    "icloud": ("3693F3", ("icloud",)),
    "apple": ("000000", ("apple",)),
    "googledrive": ("4285F4", ("google drive",)),
    "googleplay": ("414141", ("google play",)),
    "google": ("4285F4", ("google",)),
    "deezer": ("A238FF", ("deezer",)),
    "tidal": ("000000", ("tidal",)),
    "soundcloud": ("FF5500", ("soundcloud",)),
    "audible": ("F8991C", ("audible",)),
    "twitch": ("9146FF", ("twitch",)),
    "playstation": ("0070D1", ("playstation", "ps plus", "psn")),
    "steam": ("000000", ("steam",)),
    "epicgames": ("313131", ("epic games",)),
    "discord": ("5865F2", ("discord",)),
    "claude": ("D97757", ("claude", "anthropic")),
    "notion": ("000000", ("notion",)),
    "figma": ("F24E1E", ("figma",)),
    "trello": ("0052CC", ("trello",)),
    "evernote": ("00A82D", ("evernote",)),
    "dropbox": ("0061FF", ("dropbox",)),
    "github": ("181717", ("github",)),
    "zoom": ("0B5CFF", ("zoom",)),
    "duolingo": ("58CC02", ("duolingo",)),
    "1password": ("145FE4", ("1password",)),
    "bitwarden": ("175DDC", ("bitwarden",)),
    "nordvpn": ("4687FF", ("nordvpn",)),
    "expressvpn": ("DA3940", ("expressvpn",)),
    "proton": ("6D4AFF", ("proton",)),
    "patreon": ("000000", ("patreon",)),
    "telegram": ("26A5E4", ("telegram",)),
    "uber": ("000000", ("uber",)),
    "ifood": ("EA1D2C", ("ifood",)),
    "nubank": ("820AD1", ("nubank",)),
    "picpay": ("21C25E", ("picpay",)),
    "mercadopago": ("00B1EA", ("mercado pago",)),
    "vivo": ("415FFF", ("vivo",)),
}

# arquivo em logos/genericos: palavras que casam (o ícone usa a cor do texto)
GENERICOS = {
    "home": ("aluguel", "condominio", "moradia", "iptu"),
    "bolt": ("luz", "energia", "eletrica", "enel", "cemig", "copel", "light"),
    "wifi": ("internet", "wifi", "fibra", "banda larga"),
    "droplet": ("agua", "saneamento", "sabesp", "copasa", "esgoto"),
    "flame": ("gas", "comgas"),
    "device-mobile": ("celular", "telefone", "claro", "tim", "oi"),
    "barbell": ("academia", "gym", "smart fit", "smartfit", "gympass", "wellhub", "crossfit", "pilates"),
    "school": ("escola", "faculdade", "curso", "mensalidade escolar", "ingles", "idiomas"),
    "heart-rate-monitor": ("plano de saude", "saude", "unimed", "amil", "bradesco saude", "terapia", "psicologo"),
    "shield-check": ("seguro",),
    "car": ("carro", "ipva", "estacionamento", "combustivel", "gasolina", "financiamento do carro"),
    "building-bank": ("tarifa", "anuidade", "cesta de servicos"),
}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def _casa(texto: str, palavra: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(palavra)}(?![a-z0-9])", texto) is not None


def icone_para(descricao: str) -> tuple[Path, str | None] | None:
    """(arquivo do ícone, cor da marca ou None pra usar a cor do texto) do que a descrição nomeia; None sem par."""
    texto = _normalizar(descricao)
    for nome, (cor, palavras) in MARCAS.items():  # na ordem: "youtube music" antes de "youtube"
        if any(_casa(texto, p) for p in palavras):
            return PASTA / "marcas" / f"{nome}.svg", f"#{cor}"
    for nome, palavras in GENERICOS.items():
        if any(_casa(texto, _normalizar(p)) for p in palavras):
            return PASTA / "genericos" / f"{nome}.svg", None
    return None


def _escura(cor: str) -> bool:
    c = QColor(cor)
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF() < 0.12


@cache
def pixmap_do_icone(arquivo: Path, cor: str | None, lado: int, escala: float = 2.0) -> QPixmap:
    """O SVG pintado na cor (marca preta ou sem cor: a cor do texto, pra aparecer também no tema escuro)."""
    tinta = tema.TEXTO if cor is None or _escura(cor) else cor
    svg = arquivo.read_text()
    svg = svg.replace("currentColor", tinta).replace("<svg ", f'<svg fill="{tinta}" ', 1) if cor else svg.replace("currentColor", tinta)
    renderizador = QSvgRenderer(QByteArray(svg.encode()))
    px = QPixmap(int(lado * escala), int(lado * escala))
    px.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(px)
    renderizador.render(pintor, QRectF(0, 0, px.width(), px.height()))
    pintor.end()
    px.setDevicePixelRatio(escala)
    return px
