"""Taxas públicas do Banco Central (SGS) que movem os investimentos: CDI, Selic e IPCA.

Só o número da série sai do PC (nunca valores do usuário). Tudo é gravado em `indicadores` (uma linha por data) e
buscado de forma **incremental**: só o que falta. Sem internet nada quebra: `atualizar_indicadores` nunca levanta,
devolve o status e o app segue com o último valor guardado.

- `cdi` e `selic`: % ao dia, uma linha por dia útil (SGS 12 e 11);
- `ipca`: % ao mês, na data do dia 1 do mês de referência (SGS 433).

A busca de rede (`buscar`) é injetável: os testes passam um buscador falso e nunca acessam a internet.
O app roda a busca numa thread (ver `ui/atualizador.py`); por isso ela se divide em três passos que só o último
mexe no banco: `plano_de_busca` (o que falta), `buscar_plano` (rede) e `gravar`.
"""

import json
import os
import sqlite3
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta

from meu_cofre.lancamentos import somar_meses

SERIES = {"cdi": 12, "selic": 11, "ipca": 433}
URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json&dataInicial={inicio}&dataFinal={fim}"
TIMEOUT = 10  # segundos; a busca roda em segundo plano, então a tela nunca espera (o SGS às vezes demora)
DIAS_UTEIS_ANO = 252
MESES_DO_IPCA = 12  # a projeção do IPCA usa a média dos últimos 12 meses
VAR_SEM_REDE = "MEU_COFRE_SEM_REDE"

Buscador = Callable[[str, date, date], list[tuple[date, float]]]


def rede_desligada() -> bool:
    """MEU_COFRE_SEM_REDE=1 desliga qualquer busca (testes e screenshots)."""
    return os.environ.get(VAR_SEM_REDE, "") not in ("", "0")


def _buscar_http(serie: str, inicio: date, fim: date) -> list[tuple[date, float]]:
    """Busca a série no SGS do Banco Central. Levanta em qualquer falha (rede, resposta estranha)."""
    url = URL.format(codigo=SERIES[serie], inicio=inicio.strftime("%d/%m/%Y"), fim=fim.strftime("%d/%m/%Y"))
    pedido = urllib.request.Request(url, headers={"User-Agent": "meu-cofre", "Accept": "application/json"})
    with urllib.request.urlopen(pedido, timeout=TIMEOUT) as resposta:  # noqa: S310 (URL fixa, https)
        dados = json.load(resposta)
    pontos = []
    for linha in dados:
        try:
            dia, mes, ano = linha["data"].split("/")
            pontos.append((date(int(ano), int(mes), int(dia)), float(linha["valor"])))
        except (KeyError, ValueError, TypeError):
            continue  # linha fora do formato: ignora
    return pontos


# --- Leitura ---------------------------------------------------------------------------------


def carregar(conn: sqlite3.Connection, serie: str) -> list[tuple[date, float]]:
    """A série inteira, em ordem de data."""
    return [
        (date.fromisoformat(r["data"]), r["valor"])
        for r in conn.execute("SELECT data, valor FROM indicadores WHERE serie = ? ORDER BY data", (serie,))
    ]


def ultima_data(conn: sqlite3.Connection, serie: str = "cdi") -> date | None:
    """Data do último valor guardado da série (None se não há nenhum)."""
    texto = conn.execute("SELECT MAX(data) FROM indicadores WHERE serie = ?", (serie,)).fetchone()[0]
    return date.fromisoformat(texto) if texto else None


def ipca_mensal_medio(valores_pct: list[float]) -> float:
    """Média geométrica mensal (fração, 0.004 = 0,4%) dos últimos 12 valores da lista; 0 se vazia."""
    ultimos = valores_pct[-MESES_DO_IPCA:]
    if not ultimos:
        return 0.0
    produto = 1.0
    for v in ultimos:
        produto *= 1 + v / 100
    return produto ** (1 / len(ultimos)) - 1


def taxa_anual_atual(conn: sqlite3.Connection, serie: str) -> float | None:
    """Taxa anual (%) que a série vale hoje, pra projeções; None se não há dado.

    CDI e Selic: o último valor diário composto por 252 dias úteis. IPCA: média dos últimos 12 meses composta em 12.
    """
    valores = [v for _d, v in carregar(conn, serie)]
    if not valores:
        return None
    if serie == "ipca":
        return ((1 + ipca_mensal_medio(valores)) ** 12 - 1) * 100
    return ((1 + valores[-1] / 100) ** DIAS_UTEIS_ANO - 1) * 100


# --- Atualização: plano (banco) -> busca (rede) -> gravação (banco) ------------------------------


def _mes(d: date) -> int:
    return d.year * 12 + d.month


def plano_de_busca(conn: sqlite3.Connection, hoje: date, desde: date | None = None) -> list[tuple[str, date, date]]:
    """O que falta buscar: lista de (série, de, até). Vazia se está tudo em dia.

    `desde`: a data mais antiga que importa (padrão: 1º de janeiro do ano). O IPCA recua 13 meses, pra ter a média
    dos últimos 12. Busca o começo que falta e o fim (do último valor guardado até hoje).
    """
    desde = desde or date(hoje.year, 1, 1)
    plano = []
    for serie in SERIES:
        inicio = somar_meses(desde.replace(day=1), -13) if serie == "ipca" else desde
        linha = conn.execute("SELECT MIN(data), MAX(data) FROM indicadores WHERE serie = ?", (serie,)).fetchone()
        if linha[0] is None:
            plano.append((serie, inicio, hoje))
            continue
        primeira, ultima = date.fromisoformat(linha[0]), date.fromisoformat(linha[1])
        folga = 40 if serie == "ipca" else 7  # fim de semana, feriado, mês ainda sem divulgação
        if primeira > inicio + timedelta(days=folga):
            plano.append((serie, inicio, primeira - timedelta(days=1)))
        defasado = _mes(hoje) - _mes(ultima) >= 2 if serie == "ipca" else ultima < hoje - timedelta(days=1)
        if defasado:
            plano.append((serie, ultima + timedelta(days=1), hoje))
    return plano


def buscar_plano(
    plano: list[tuple[str, date, date]], buscar: Buscador = _buscar_http
) -> tuple[dict[str, list[tuple[date, float]]], dict[str, str]]:
    """Executa a busca de rede do plano (não mexe no banco). Devolve (pontos por série, erro por série).

    Nunca levanta: cada série que falha vira uma mensagem em `erros`. Dá pra rodar fora da thread principal.
    """
    pontos: dict[str, list[tuple[date, float]]] = {}
    erros: dict[str, str] = {}
    for serie, de, ate in plano:
        if serie in erros:  # a rede já falhou nessa série: não insiste
            continue
        try:
            pontos.setdefault(serie, []).extend(buscar(serie, de, ate))
        except Exception as erro:  # noqa: BLE001 (qualquer falha vira status; o app não pode cair por isso)
            erros[serie] = f"{type(erro).__name__}: {erro}"
    return pontos, erros


def gravar(conn: sqlite3.Connection, pontos: dict[str, list[tuple[date, float]]]) -> int:
    """Grava os pontos (substitui a data que já existe). Devolve quantos."""
    linhas = [(serie, d.isoformat(), v) for serie, lista in pontos.items() for d, v in lista]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO indicadores (serie, data, valor) VALUES (?, ?, ?)", linhas)
    return len(linhas)


def montar_status(conn: sqlite3.Connection, novos: int, erros: dict[str, str]) -> dict:
    """O resultado de uma atualização: `ok` (nada falhou), `sem_rede` (alguma série falhou), `novos` (valores
    gravados), `ate` (data do último CDI guardado, que o app mostra no selo) e `erros` (por série)."""
    return {"ok": not erros, "sem_rede": bool(erros), "novos": novos, "ate": ultima_data(conn, "cdi"), "erros": erros}


def atualizar_indicadores(
    conn: sqlite3.Connection, hoje: date, buscar: Buscador = _buscar_http, desde: date | None = None
) -> dict:
    """Busca o que falta do CDI, da Selic e do IPCA e grava. **Nunca levanta** por falta de rede: devolve o status
    (ver `montar_status`)."""
    pontos, erros = buscar_plano(plano_de_busca(conn, hoje, desde), buscar)
    return montar_status(conn, gravar(conn, pontos), erros)
