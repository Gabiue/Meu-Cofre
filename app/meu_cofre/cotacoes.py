"""Cotações e proventos por cota de ações e FIIs (F9), de fontes públicas.

Só o ticker sai do PC (nunca quantidades ou valores). Fontes, em ordem:

1. **B3** (endpoint público de cotação, sem token): o preço atual de qualquer ticker;
2. **brapi**, só com token grátis (configurado no app ou em `MEU_COFRE_BRAPI_TOKEN`, que tem prioridade): preço + 1 ano de fechamentos + dividendos;
3. **Yahoo Finance** (chart API): fechamentos + dividendos (hoje costuma responder 429; fica por garantia).

Cada fonte que falha não impede as outras: o preço vem da primeira que responder, o histórico (fechamentos e proventos
por cota) da primeira que tiver. Sem preço de nenhuma, o ticker vira erro. Tudo é gravado em `cotacoes` (fechamento por data) e
`proventos_ref` (proventos por cota, base da estimativa). Sem internet nada quebra: `atualizar_cotacoes` nunca
levanta, devolve o status e o app segue com a última cotação guardada.

A busca de rede (`buscar`) é injetável: os testes passam um buscador falso e nunca acessam a internet. Como em
`indicadores.py`, o app roda a busca numa thread (ver `ui/atualizador.py`) e só `gravar` mexe no banco.
"""

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from meu_cofre.db import gravar_preferencia, ler_preferencia

from meu_cofre.indicadores import rede_desligada  # noqa: F401 (reexportado: MEU_COFRE_SEM_REDE vale pra tudo)

URL_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.SA?range=1y&interval=1d&events=div"
URL_B3 = "https://cotacao.b3.com.br/mds/api/v1/instrumentQuotation/{ticker}"
URL_BRAPI = "https://brapi.dev/api/quote/{ticker}?range=1y&interval=1d&dividends=true"
VAR_BRAPI = "MEU_COFRE_BRAPI_TOKEN"
PREF_TOKEN = "brapi_token"
PREF_YAHOO = "yahoo_pausado_ate"
PAUSA_YAHOO = timedelta(hours=6)  # depois de um 429 o Yahoo fica em paz: insistir só piora
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 10  # segundos; a busca roda em segundo plano
PADRAO_TICKER = re.compile(r"^[A-Z]{4}\d{1,2}$")


@dataclass(frozen=True)
class Dados:
    """O que a fonte devolve de um ticker: a cotação mais recente e, se a fonte tiver, o histórico."""

    preco_centavos: int
    data: date
    fechamentos: list[tuple[date, int]] = field(default_factory=list)  # (data, centavos)
    proventos: list[tuple[date, float]] = field(default_factory=list)  # (data, reais por cota)
    fonte: str = ""  # de onde veio o preço: "b3", "brapi" ou "yahoo"
    fonte_historico: str = ""  # de onde vieram fechamentos e proventos ("" = ninguém tinha)
    yahoo_429: bool = False  # o Yahoo respondeu 429 nesta busca (quem grava a pausa é a thread principal)


Buscador = Callable[[str], Dados]


def normalizar_ticker(texto: str | None) -> str:
    """" petr4 " -> "PETR4". Não valida."""
    return (texto or "").strip().upper()


def ticker_valido(ticker: str) -> bool:
    """Quatro letras e um ou dois dígitos: PETR4, VALE3, HGLG11."""
    return bool(PADRAO_TICKER.match(ticker))


def _obter_json(url: str, cabecalhos: dict[str, str] | None = None) -> dict:
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(cabecalhos or {})})
    with urllib.request.urlopen(pedido, timeout=TIMEOUT) as resposta:  # noqa: S310 (URL fixa, https)
        return json.load(resposta)


def _centavos(reais: float) -> int:
    return round(float(reais) * 100)


def _dia(epoch: float, deslocamento_s: int = 0) -> date:
    """Segundos desde 1970 -> a data no fuso da bolsa (`deslocamento_s` = gmtoffset do Yahoo)."""
    return (datetime.fromtimestamp(0, timezone.utc) + timedelta(seconds=epoch + deslocamento_s)).date()


# --- Interpretação das respostas ------------------------------------------------------------------


def interpretar_yahoo(resposta: dict) -> Dados:
    """Converte a resposta do chart do Yahoo. ValueError se não houver preço."""
    try:
        resultado = resposta["chart"]["result"][0]
        meta = resultado["meta"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("Resposta do Yahoo sem resultado") from None
    deslocamento = int(meta.get("gmtoffset") or 0)
    fechamentos: list[tuple[date, int]] = []
    fechos = ((resultado.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for ts, fecho in zip(resultado.get("timestamp") or [], fechos):
        if fecho is not None:  # dia sem negócio vem como null
            fechamentos.append((_dia(ts, deslocamento), _centavos(fecho)))
    preco = meta.get("regularMarketPrice")
    if preco is not None:
        data = _dia(meta["regularMarketTime"], deslocamento) if meta.get("regularMarketTime") else (fechamentos[-1][0] if fechamentos else date.today())
        preco_centavos = _centavos(preco)
    elif fechamentos:
        data, preco_centavos = fechamentos[-1]
    else:
        raise ValueError("Resposta do Yahoo sem preço")
    if preco_centavos <= 0:
        raise ValueError("Preço inválido")
    proventos = []
    for evento in ((resultado.get("events") or {}).get("dividends") or {}).values():
        try:
            valor = float(evento["amount"])
            if valor > 0:
                proventos.append((_dia(evento["date"], deslocamento), valor))
        except (KeyError, TypeError, ValueError):
            continue  # evento fora do formato: ignora
    return Dados(preco_centavos, data, sorted(fechamentos), sorted(proventos), fonte="yahoo")


def interpretar_b3(resposta: dict) -> tuple[int, date]:
    """(preço em centavos, data) da cotação pública da B3 (`curPrc`, data de `Msg.dtTm`). ValueError se não houver
    cotação (ticker inexistente, `BizSts` diferente de OK, resposta quebrada)."""
    try:
        if resposta["BizSts"]["cd"] != "OK":
            raise ValueError("A B3 não devolveu cotação")
        preco = float(resposta["Trad"][0]["scty"]["SctyQtn"]["curPrc"])
        dia = datetime.strptime(resposta["Msg"]["dtTm"][:10], "%Y-%m-%d").date()
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("A B3 não devolveu cotação") from None
    if preco <= 0:
        raise ValueError("Preço inválido")
    return _centavos(preco), dia


def _data_brapi(valor) -> date | None:
    if isinstance(valor, (int, float)):
        return _dia(valor, -3 * 3600)
    try:
        momento = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.hour == momento.minute == 0:  # só a data (pagamento de provento): não muda de dia com o fuso
        return momento.date()
    return momento.astimezone(timezone(timedelta(hours=-3))).date()


def interpretar_brapi(resposta: dict) -> Dados:
    """Converte a resposta do brapi: preço e, se vierem, fechamentos (`historicalDataPrice`) e dividendos
    (`dividendsData.cashDividends`). ValueError se não houver preço."""
    try:
        r = resposta["results"][0]
        preco = float(r["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Resposta do brapi sem preço") from None
    if preco <= 0:
        raise ValueError("Preço inválido")
    data = _data_brapi(r.get("regularMarketTime")) or date.today()
    fechamentos, proventos = [], []
    for linha in r.get("historicalDataPrice") or []:
        dia = _data_brapi(linha.get("date"))
        if dia and linha.get("close"):
            fechamentos.append((dia, _centavos(linha["close"])))
    for linha in (r.get("dividendsData") or {}).get("cashDividends") or []:
        dia = _data_brapi(linha.get("paymentDate") or linha.get("lastDatePrior"))
        try:
            valor = float(linha["rate"])
        except (KeyError, TypeError, ValueError):
            continue
        if dia and valor > 0:
            proventos.append((dia, valor))
    return Dados(_centavos(preco), data, sorted(fechamentos), sorted(proventos), fonte="brapi")


def token_brapi_do_ambiente() -> str:
    return os.environ.get(VAR_BRAPI, "").strip()


def token_brapi(conn: sqlite3.Connection) -> str:
    """O token da brapi: a variável de ambiente, se houver, senão o salvo no app ("" = sem token)."""
    return token_brapi_do_ambiente() or (ler_preferencia(conn, PREF_TOKEN) or "").strip()


def yahoo_pausado(conn: sqlite3.Connection, agora: datetime | None = None) -> bool:
    ate = ler_preferencia(conn, PREF_YAHOO)
    try:
        return bool(ate) and (agora or datetime.now()) < datetime.fromisoformat(ate)
    except ValueError:  # preferência estragada: como se não houvesse pausa
        return False


def houve_429_do_yahoo(dados: dict[str, Dados], erros: dict[str, str]) -> bool:
    """O Yahoo respondeu 429 em algum ticker (com o preço vindo da B3 ou com tudo falhando)?"""
    return any(d.yahoo_429 for d in dados.values()) or any("yahoo: HTTPError: HTTP Error 429" in e for e in erros.values())


def pausar_yahoo(conn: sqlite3.Connection, agora: datetime | None = None) -> None:
    gravar_preferencia(conn, PREF_YAHOO, ((agora or datetime.now()) + PAUSA_YAHOO).isoformat(timespec="seconds"))


def testar_token(token: str) -> str | None:
    """Uma chamada real ao brapi com o token. None se funcionou; senão uma mensagem curta."""
    try:
        interpretar_brapi(_obter_json(URL_BRAPI.format(ticker="PETR4"), {"Authorization": f"Bearer {token}"}))
    except urllib.error.HTTPError as erro:
        return "Token inválido ou sem permissão." if erro.code in (401, 403) else f"A brapi respondeu erro {erro.code}."
    except (urllib.error.URLError, TimeoutError, OSError):
        return "Sem internet ou a brapi não respondeu."
    except ValueError:
        return "A brapi respondeu algo inesperado."
    return None


def _buscar_http(ticker: str, token: str = "", yahoo_pausado: bool = False) -> Dados:
    """Busca um ticker nas fontes, em ordem (B3, brapi com token, Yahoo). Levanta só se nenhuma deu preço.

    Roda numa thread: token e pausa do Yahoo chegam prontos (a thread não toca no banco nem no ambiente)."""
    if not ticker_valido(ticker):
        raise ValueError(f"Ticker inválido: {ticker}")
    erros: list[str] = []
    preco: tuple[int, date, str] | None = None
    historico: Dados | None = None  # quem trouxe fechamentos ou proventos
    yahoo_429 = False

    def tentar(nome: str, buscar) -> Dados | None:
        try:
            return buscar()
        except Exception as erro:  # noqa: BLE001 (uma fonte fora do ar não derruba as outras)
            nonlocal yahoo_429
            yahoo_429 = yahoo_429 or (nome == "yahoo" and isinstance(erro, urllib.error.HTTPError) and erro.code == 429)
            erros.append(f"{nome}: {type(erro).__name__}: {erro}")
            return None

    def b3() -> Dados:
        centavos, dia = interpretar_b3(_obter_json(URL_B3.format(ticker=ticker)))
        return Dados(centavos, dia, fonte="b3")

    fontes = [("b3", b3)]
    if token:
        fontes.append(("brapi", lambda: interpretar_brapi(_obter_json(URL_BRAPI.format(ticker=ticker), {"Authorization": f"Bearer {token}"}))))
    for nome, buscar in fontes:
        d = tentar(nome, buscar)
        if d is None:
            continue
        preco = preco or (d.preco_centavos, d.data, nome)
        if (d.fechamentos or d.proventos) and historico is None:
            historico = d
    if historico is None and not yahoo_pausado:  # ninguém trouxe histórico (sem token, ou brapi fora): o Yahoo, se um dia voltar
        d = tentar("yahoo", lambda: interpretar_yahoo(_obter_json(URL_YAHOO.format(ticker=ticker))))
        if d is not None:
            historico = d
            preco = preco or (d.preco_centavos, d.data, "yahoo")
    if preco is None:
        raise RuntimeError("; ".join(erros) or "nenhuma fonte respondeu")
    return Dados(
        preco[0], preco[1],
        historico.fechamentos if historico else [], historico.proventos if historico else [],
        fonte=preco[2], fonte_historico=historico.fonte or "yahoo" if historico else "", yahoo_429=yahoo_429,
    )


# --- Banco ------------------------------------------------------------------------------------------


def tickers_ativos(conn: sqlite3.Connection) -> list[str]:
    """Os tickers dos ativos ativos (ações e FIIs), em ordem."""
    return [
        r["ticker"]
        for r in conn.execute("SELECT ticker FROM ativos WHERE ativo = 1 AND ticker IS NOT NULL ORDER BY ticker")
    ]


def ultima(conn: sqlite3.Connection, ticker: str, ate: date) -> tuple[int, date] | None:
    """(preço em centavos, data) da cotação mais recente até `ate`; None se não há."""
    r = conn.execute(
        "SELECT preco_centavos, data FROM cotacoes WHERE ticker = ? AND data <= ? ORDER BY data DESC LIMIT 1",
        (ticker, ate.isoformat()),
    ).fetchone()
    return (r["preco_centavos"], date.fromisoformat(r["data"])) if r else None


def historico(conn: sqlite3.Connection, ticker: str) -> list[tuple[date, int]]:
    """Todas as cotações guardadas do ticker, em ordem de data."""
    return [
        (date.fromisoformat(r["data"]), r["preco_centavos"])
        for r in conn.execute("SELECT data, preco_centavos FROM cotacoes WHERE ticker = ? ORDER BY data", (ticker,))
    ]


def proventos_por_cota(conn: sqlite3.Connection, ticker: str) -> list[tuple[date, float]]:
    """O histórico público de proventos por cota (reais), em ordem de data."""
    return [
        (date.fromisoformat(r["data"]), r["valor_por_cota"])
        for r in conn.execute("SELECT data, valor_por_cota FROM proventos_ref WHERE ticker = ? ORDER BY data", (ticker,))
    ]


def gravar(conn: sqlite3.Connection, dados: dict[str, Dados]) -> int:
    """Grava cotações e proventos (substitui a data que já existe). Devolve quantos tickers foram gravados."""
    with conn:
        for ticker, d in dados.items():
            conn.executemany(
                "INSERT OR REPLACE INTO cotacoes (ticker, data, preco_centavos) VALUES (?, ?, ?)",
                [(ticker, dia.isoformat(), c) for dia, c in d.fechamentos],
            )
            # o preço de agora vale mais que o fechamento do mesmo dia (é o último negociado)
            conn.execute(
                "INSERT OR REPLACE INTO cotacoes (ticker, data, preco_centavos) VALUES (?, ?, ?)",
                (ticker, d.data.isoformat(), d.preco_centavos),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO proventos_ref (ticker, data, valor_por_cota) VALUES (?, ?, ?)",
                [(ticker, dia.isoformat(), v) for dia, v in d.proventos],
            )
    return len(dados)


# --- Atualização: busca (rede) -> gravação (banco) ---------------------------------------------------


def buscar_lote(tickers: list[str], buscar: Buscador = _buscar_http) -> tuple[dict[str, Dados], dict[str, str]]:
    """Executa a busca de rede (não mexe no banco). Devolve (dados por ticker, erro por ticker).

    Nunca levanta: cada ticker que falha vira uma mensagem em `erros`. Dá pra rodar fora da thread principal.
    """
    dados: dict[str, Dados] = {}
    erros: dict[str, str] = {}
    for ticker in tickers:
        try:
            dados[ticker] = buscar(ticker)
        except Exception as erro:  # noqa: BLE001 (qualquer falha vira status; o app não pode cair por isso)
            erros[ticker] = f"{type(erro).__name__}: {erro}"
    return dados, erros


def montar_status(gravados: int, erros: dict[str, str], dados: dict[str, Dados] | None = None) -> dict:
    """O resultado de uma atualização de cotações: `ok`, `sem_rede` (todos os tickers falharam: um ticker inválido
    não é falta de internet), `novos` (tickers gravados), `erros` (por ticker) e `fontes` (origem do preço)."""
    return {
        "ok": not erros, "sem_rede": bool(erros) and not dados, "novos": gravados, "erros": erros,
        "fontes": {t: d.fonte for t, d in (dados or {}).items()},
    }


def sem_fonte_de_historico(conn: sqlite3.Connection) -> bool:
    """Há ação ou FII ativo sem nenhum provento por cota guardado e sem token do brapi pra buscá-lo?"""
    if token_brapi(conn):
        return False
    return any(not proventos_por_cota(conn, t) for t in tickers_ativos(conn))


def atualizar_cotacoes(conn: sqlite3.Connection, buscar: Buscador = _buscar_http) -> dict:
    """Busca e grava as cotações dos ativos ativos. **Nunca levanta** por falta de rede: devolve o status."""
    dados, erros = buscar_lote(tickers_ativos(conn), buscar)
    return montar_status(gravar(conn, dados), erros, dados)
