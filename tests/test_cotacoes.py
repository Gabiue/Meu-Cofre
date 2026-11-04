"""Cotações e proventos por cota (F9): interpretação das respostas, fallback, gravação e status sem rede.
Nunca usa a internet: `_obter_json` é trocado por respostas falsas e o buscador é injetado."""

import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest

from meu_cofre import cotacoes, db
from meu_cofre import investimentos as inv

HOJE = date(2026, 10, 30)
GMT = -10800  # horário de Brasília


def _ts(ano, mes, dia):
    """Meio-dia de Brasília como epoch (cai no dia certo mesmo com o deslocamento)."""
    return int(datetime(ano, mes, dia, 15, tzinfo=timezone.utc).timestamp())


def _yahoo(preco=38.456, fechamentos=None, dividendos=None, tempo=None):
    fechamentos = fechamentos if fechamentos is not None else [(date(2026, 10, 28), 37.9), (date(2026, 10, 29), None), (date(2026, 10, 30), 38.4)]
    meta = {"gmtoffset": GMT}
    if preco is not None:
        meta["regularMarketPrice"] = preco
        meta["regularMarketTime"] = tempo or _ts(2026, 10, 30)
    resultado = {
        "meta": meta,
        "timestamp": [_ts(d.year, d.month, d.day) for d, _c in fechamentos],
        "indicators": {"quote": [{"close": [c for _d, c in fechamentos]}]},
    }
    if dividendos is not None:
        resultado["events"] = {"dividends": {str(_ts(d.year, d.month, d.day)): {"amount": v, "date": _ts(d.year, d.month, d.day)} for d, v in dividendos}}
    return {"chart": {"result": [resultado], "error": None}}


def _criar(conn, ticker="PETR4", tipo="acao", **kw):
    return inv.criar(conn, HOJE, nome=f"Ativo {ticker}", tipo=tipo, ticker=ticker, **kw)


# --- Yahoo ---------------------------------------------------------------------------------------------


def test_yahoo_preco_do_meta_fechamentos_e_proventos():
    d = cotacoes.interpretar_yahoo(_yahoo(dividendos=[(date(2026, 5, 15), 0.5), (date(2026, 2, 14), 0.25), (date(2026, 3, 1), 0)]))
    assert (d.preco_centavos, d.data) == (3846, date(2026, 10, 30))
    assert d.fechamentos == [(date(2026, 10, 28), 3790), (date(2026, 10, 30), 3840)]  # o dia sem negócio (null) some
    assert d.proventos == [(date(2026, 2, 14), 0.25), (date(2026, 5, 15), 0.5)]  # ordenados; valor zero ignorado


def test_yahoo_sem_preco_no_meta_usa_o_ultimo_fechamento():
    d = cotacoes.interpretar_yahoo(_yahoo(preco=None))
    assert (d.preco_centavos, d.data) == (3840, date(2026, 10, 30))


def test_yahoo_sem_nada_levanta():
    with pytest.raises(ValueError):
        cotacoes.interpretar_yahoo(_yahoo(preco=None, fechamentos=[]))
    with pytest.raises(ValueError):
        cotacoes.interpretar_yahoo({"chart": {"result": None, "error": {"code": "Not Found"}}})
    with pytest.raises(ValueError):
        cotacoes.interpretar_yahoo(_yahoo(preco=0))


def test_yahoo_ignora_evento_fora_do_formato():
    resp = _yahoo(dividendos=[(date(2026, 5, 15), 0.5)])
    resp["chart"]["result"][0]["events"]["dividends"]["x"] = {"amount": "abc"}
    assert cotacoes.interpretar_yahoo(resp).proventos == [(date(2026, 5, 15), 0.5)]


# --- B3, brapi e o caminho de rede ------------------------------------------------------------------------

B3_MXRF11 = {
    "BizSts": {"cd": "OK"},
    "Msg": {"dtTm": "2026-10-31 20:22:57"},
    "Trad": [{"scty": {"SctyQtn": {"opngPric": 8.94, "minPric": 8.84, "maxPric": 8.96, "avrgPric": 8.895, "curPrc": 8.9, "prcFlcn": -0.4474273},
                       "mkt": {"nm": "Vista"}, "symb": "MXRF11", "desc": "FII MAXI RENCI"}, "ttlQty": 82311}],
}
BRAPI = {
    "results": [{
        "symbol": "MXRF11", "regularMarketPrice": 8.95, "regularMarketTime": "2026-10-30T20:07:00.000Z",
        "historicalDataPrice": [{"date": _ts(2026, 10, 28), "close": 8.8}, {"date": _ts(2026, 10, 29), "close": None}, {"date": _ts(2026, 10, 30), "close": 8.95}],
        "dividendsData": {"cashDividends": [{"paymentDate": "2026-09-14T00:00:00.000Z", "rate": 0.10}, {"lastDatePrior": "2026-08-30T00:00:00.000Z", "rate": 0.09}, {"rate": 1}]},
    }]
}


def test_b3_curprc_e_data_da_mensagem():
    assert cotacoes.interpretar_b3(B3_MXRF11) == (890, date(2026, 10, 31))


@pytest.mark.parametrize(
    "resposta",
    [
        {"BizSts": {"cd": "OK"}, "Msg": {"dtTm": "2026-10-31 20:22:57"}, "Trad": []},  # ticker inexistente
        {"BizSts": {"cd": "OK"}, "Msg": {"dtTm": "2026-10-31 20:22:57"}},
        {**B3_MXRF11, "BizSts": {"cd": "ERRO"}},
        {"BizSts": {"cd": "OK"}, "Msg": {"dtTm": "ontem"}, "Trad": B3_MXRF11["Trad"]},
        {"Trad": "quebrado"},
        {},
    ],
)
def test_b3_sem_cotacao_levanta(resposta):
    with pytest.raises(ValueError):
        cotacoes.interpretar_b3(resposta)


def test_brapi_preco_historico_e_dividendos():
    d = cotacoes.interpretar_brapi(BRAPI)
    assert (d.preco_centavos, d.data, d.fonte) == (895, date(2026, 10, 30), "brapi")
    assert d.fechamentos == [(date(2026, 10, 28), 880), (date(2026, 10, 30), 895)]
    assert d.proventos == [(date(2026, 8, 30), 0.09), (date(2026, 9, 14), 0.10)]  # o sem data é ignorado
    simples = cotacoes.interpretar_brapi({"results": [{"regularMarketPrice": 38.45, "regularMarketTime": "2026-10-30T20:07:00.000Z"}]})
    assert (simples.preco_centavos, simples.fechamentos, simples.proventos) == (3845, [], [])
    with pytest.raises(ValueError):
        cotacoes.interpretar_brapi({"results": []})


class Rede:
    """`_obter_json` falso: responde por trecho da URL; um valor que é exceção é levantado."""

    def __init__(self, respostas):
        self.respostas, self.pedidos = respostas, []

    def __call__(self, url, cab=None):
        self.pedidos.append((url, cab))
        for trecho, resp in self.respostas.items():
            if trecho in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise OSError("sem rota")


def _rede(monkeypatch, respostas):
    rede = Rede(respostas)
    monkeypatch.setattr(cotacoes, "_obter_json", rede)
    return rede


def test_http_b3_e_a_fonte_do_preco_e_o_yahoo_429_nao_atrapalha(monkeypatch):
    rede = _rede(monkeypatch, {"b3.com.br": B3_MXRF11, "yahoo": OSError("HTTP Error 429")})
    d = cotacoes._buscar_http("MXRF11")
    assert (d.preco_centavos, d.data, d.fonte, d.fonte_historico, d.fechamentos, d.proventos) == (890, date(2026, 10, 31), "b3", "", [], [])
    assert rede.pedidos[0][0] == "https://cotacao.b3.com.br/mds/api/v1/instrumentQuotation/MXRF11"
    assert "yahoo" in rede.pedidos[1][0]  # sem token, tenta o Yahoo pelo histórico


def test_http_com_token_o_brapi_traz_historico_e_o_preco_continua_da_b3(monkeypatch):
    rede = _rede(monkeypatch, {"b3.com.br": B3_MXRF11, "brapi.dev": BRAPI})
    d = cotacoes._buscar_http("MXRF11", token="segredo")
    assert (d.preco_centavos, d.fonte, d.fonte_historico) == (890, "b3", "brapi")
    assert len(d.fechamentos) == 2 and len(d.proventos) == 2
    url, cab = rede.pedidos[1]
    assert url.startswith("https://brapi.dev/api/quote/MXRF11?") and cab == {"Authorization": "Bearer segredo"} and "segredo" not in url
    assert not any("yahoo" in u for u, _c in rede.pedidos)  # o brapi já deu o histórico


def test_http_b3_fora_do_ar_o_brapi_da_o_preco(monkeypatch):
    _rede(monkeypatch, {"b3.com.br": OSError("fora"), "brapi.dev": BRAPI})
    d = cotacoes._buscar_http("MXRF11", token="t")
    assert (d.preco_centavos, d.fonte, d.fonte_historico) == (895, "brapi", "brapi")


def test_http_so_o_yahoo_responde(monkeypatch):
    _rede(monkeypatch, {"b3.com.br": {"BizSts": {"cd": "OK"}, "Msg": {"dtTm": "2026-10-31 10:00:00"}, "Trad": []}, "yahoo": _yahoo(dividendos=[(date(2026, 5, 15), 0.5)])})
    d = cotacoes._buscar_http("PETR4")
    assert (d.fonte, d.fonte_historico, d.preco_centavos) == ("yahoo", "yahoo", 3846) and d.proventos == [(date(2026, 5, 15), 0.5)]


@pytest.mark.parametrize("b3", [OSError("HTTP Error 429"), ValueError("JSON quebrado"), {"BizSts": {"cd": "ERRO"}}])
def test_http_sem_nenhuma_fonte_levanta_com_o_motivo(monkeypatch, b3):
    _rede(monkeypatch, {"b3.com.br": b3, "yahoo": OSError("HTTP Error 429")})
    with pytest.raises(RuntimeError, match="b3: .*yahoo: OSError: HTTP Error 429"):
        cotacoes._buscar_http("PETR4")


def test_http_recusa_ticker_estranho_sem_pedir_nada(monkeypatch):
    monkeypatch.setattr(cotacoes, "_obter_json", lambda *a, **k: pytest.fail("não devia ir pra rede"))
    with pytest.raises(ValueError):
        cotacoes._buscar_http("PETR4.SA/../x")


def test_lote_com_fonte_que_falha_vira_erro_sem_levantar(monkeypatch):
    _rede(monkeypatch, {"instrumentQuotation/MXRF11": B3_MXRF11})
    dados, erros = cotacoes.buscar_lote(["MXRF11", "XXXX3"])  # o segundo: a B3 devolve "sem rota" -> erro
    assert list(dados) == ["MXRF11"] and "XXXX3" in erros
    status = cotacoes.montar_status(1, erros, dados)
    assert status["fontes"] == {"MXRF11": "b3"} and not status["sem_rede"]  # um ticker veio: há internet


def test_sem_fonte_de_historico(conn, monkeypatch):
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    assert not cotacoes.sem_fonte_de_historico(conn)  # sem ativos
    _criar(conn, "PETR4")
    assert cotacoes.sem_fonte_de_historico(conn)
    monkeypatch.setenv(cotacoes.VAR_BRAPI, "t")
    assert not cotacoes.sem_fonte_de_historico(conn)
    monkeypatch.delenv(cotacoes.VAR_BRAPI)
    cotacoes.gravar(conn, {"PETR4": cotacoes.Dados(3300, date(2026, 10, 29), [], [(date(2026, 5, 15), 0.5)])})
    assert not cotacoes.sem_fonte_de_historico(conn)


def test_ticker_normaliza_e_valida():
    assert cotacoes.normalizar_ticker(" petr4 ") == "PETR4" and cotacoes.normalizar_ticker(None) == ""
    assert all(cotacoes.ticker_valido(t) for t in ("PETR4", "VALE3", "HGLG11", "MXRF11"))
    assert not any(cotacoes.ticker_valido(t) for t in ("", "PETR", "petr4", "PETR444", "PE4", "PETR4F", "PETR4.SA"))


# --- Lote, banco e status -----------------------------------------------------------------------------


class Buscador:
    def __init__(self, dados=None, falhas=()):
        self.dados, self.falhas, self.pedidos = dados or {}, set(falhas), []

    def __call__(self, ticker):
        self.pedidos.append(ticker)
        if ticker in self.falhas:
            raise OSError("sem rede")
        return self.dados[ticker]


def _dados(preco=3846):
    return cotacoes.Dados(preco, date(2026, 10, 30), [(date(2026, 10, 29), preco - 10)], [(date(2026, 5, 15), 0.5)])


def test_lote_nunca_levanta_e_separa_erros():
    dados, erros = cotacoes.buscar_lote(["PETR4", "VALE3"], Buscador({"PETR4": _dados()}, falhas=["VALE3"]))
    assert list(dados) == ["PETR4"] and list(erros) == ["VALE3"] and "OSError" in erros["VALE3"]


def test_gravar_guarda_cotacoes_e_proventos_e_substitui_a_data(conn):
    assert cotacoes.gravar(conn, {"PETR4": _dados(3846)}) == 1
    assert cotacoes.ultima(conn, "PETR4", HOJE) == (3846, date(2026, 10, 30))
    assert cotacoes.historico(conn, "PETR4") == [(date(2026, 10, 29), 3836), (date(2026, 10, 30), 3846)]
    assert cotacoes.proventos_por_cota(conn, "PETR4") == [(date(2026, 5, 15), 0.5)]
    cotacoes.gravar(conn, {"PETR4": _dados(3900)})
    assert cotacoes.ultima(conn, "PETR4", HOJE)[0] == 3900
    assert len(cotacoes.historico(conn, "PETR4")) == 2 and len(cotacoes.proventos_por_cota(conn, "PETR4")) == 1


def test_ultima_respeita_a_data_e_sem_cotacao_e_none(conn):
    cotacoes.gravar(conn, {"PETR4": _dados()})
    assert cotacoes.ultima(conn, "PETR4", date(2026, 10, 29)) == (3836, date(2026, 10, 29))
    assert cotacoes.ultima(conn, "PETR4", date(2026, 10, 1)) is None
    assert cotacoes.ultima(conn, "VALE3", HOJE) is None


def test_so_tickers_de_ativos_ativos(conn):
    _criar(conn, "PETR4")
    encerrado = _criar(conn, "VALE3")
    _criar(conn, "HGLG11", "fii")
    inv.criar(conn, HOJE, nome="CDB", tipo="renda_fixa", indexador="cdi", taxa=100.0)
    inv.encerrar(conn, HOJE, encerrado)
    assert cotacoes.tickers_ativos(conn) == ["HGLG11", "PETR4"]


def test_atualizar_grava_o_que_veio_e_conta_a_falha(conn):
    _criar(conn, "PETR4")
    _criar(conn, "VALE3")
    b = Buscador({"PETR4": _dados()}, falhas=["VALE3"])
    status = cotacoes.atualizar_cotacoes(conn, b)
    assert b.pedidos == ["PETR4", "VALE3"]
    assert status["novos"] == 1 and not status["sem_rede"] and not status["ok"] and list(status["erros"]) == ["VALE3"]
    assert cotacoes.ultima(conn, "PETR4", HOJE) is not None and cotacoes.ultima(conn, "VALE3", HOJE) is None


def test_sem_internet_mantem_a_ultima_cotacao_guardada(conn):
    _criar(conn, "PETR4")
    cotacoes.gravar(conn, {"PETR4": _dados(3846)})
    status = cotacoes.atualizar_cotacoes(conn, Buscador(falhas=["PETR4"]))
    assert status["sem_rede"] and cotacoes.ultima(conn, "PETR4", HOJE) == (3846, date(2026, 10, 30))


def test_sem_ativos_nao_busca_nada(conn):
    b = Buscador()
    assert cotacoes.atualizar_cotacoes(conn, b) == {"ok": True, "sem_rede": False, "novos": 0, "erros": {}, "fontes": {}}
    assert b.pedidos == []


def test_o_ambiente_de_teste_tem_a_rede_desligada():
    assert cotacoes.rede_desligada()


def test_um_ticker_invalido_nao_e_falta_de_internet():
    ok = cotacoes.montar_status(1, {"XXXX3": "sem cotação"}, {"PETR4": cotacoes.Dados(5266, date(2026, 10, 31), fonte="b3")})
    assert ok["sem_rede"] is False and ok["ok"] is False
    tudo_falhou = cotacoes.montar_status(0, {"PETR4": "timeout"}, {})
    assert tudo_falhou["sem_rede"] is True


def _http_error(codigo):
    return urllib.error.HTTPError("https://x", codigo, "erro", {}, None)


def test_token_do_ambiente_vence_o_salvo_no_app(conn, monkeypatch):
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    assert cotacoes.token_brapi(conn) == ""
    db.gravar_preferencia(conn, cotacoes.PREF_TOKEN, " salvo ")
    assert cotacoes.token_brapi(conn) == "salvo"
    monkeypatch.setenv(cotacoes.VAR_BRAPI, "ambiente")
    assert cotacoes.token_brapi(conn) == "ambiente"


def test_sem_fonte_de_historico_considera_o_token_salvo(conn, monkeypatch):
    monkeypatch.delenv(cotacoes.VAR_BRAPI, raising=False)
    _criar(conn, "PETR4")
    assert cotacoes.sem_fonte_de_historico(conn)
    db.gravar_preferencia(conn, cotacoes.PREF_TOKEN, "t")
    assert not cotacoes.sem_fonte_de_historico(conn)


def test_testar_token(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cotacoes, "_obter_json", lambda url, cab=None: chamadas.append((url, cab)) or BRAPI)
    assert cotacoes.testar_token("abc") is None
    assert "PETR4" in chamadas[0][0] and chamadas[0][1] == {"Authorization": "Bearer abc"}

    def falha(erro):
        def _f(*_a, **_k):
            raise erro
        return _f

    monkeypatch.setattr(cotacoes, "_obter_json", falha(_http_error(401)))
    assert "inválido" in cotacoes.testar_token("x")
    monkeypatch.setattr(cotacoes, "_obter_json", falha(_http_error(500)))
    assert "500" in cotacoes.testar_token("x")
    monkeypatch.setattr(cotacoes, "_obter_json", falha(urllib.error.URLError("sem rota")))
    assert "internet" in cotacoes.testar_token("x")


def test_yahoo_429_e_marcado_e_pausado_pula_o_yahoo(monkeypatch):
    rede = _rede(monkeypatch, {"b3.com.br": B3_MXRF11, "yahoo": _http_error(429)})
    assert cotacoes._buscar_http("MXRF11").yahoo_429  # o preço veio da B3 e só o Yahoo falhou
    rede.pedidos.clear()
    d = cotacoes._buscar_http("MXRF11", yahoo_pausado=True)
    assert not d.yahoo_429 and not any("yahoo" in u for u, _c in rede.pedidos)


def test_429_do_yahoo_quando_tudo_falha_aparece_no_erro():
    assert cotacoes.houve_429_do_yahoo({}, {"X": "RuntimeError: b3: OSError: fora; yahoo: HTTPError: HTTP Error 429: Too Many Requests"})
    assert not cotacoes.houve_429_do_yahoo({}, {"X": "RuntimeError: b3: OSError: fora; yahoo: OSError: sem rota"})


def test_pausa_do_yahoo_dura_seis_horas(conn):
    agora = datetime(2026, 10, 30, 10, 0)
    assert not cotacoes.yahoo_pausado(conn, agora)
    cotacoes.pausar_yahoo(conn, agora)
    assert cotacoes.yahoo_pausado(conn, agora + timedelta(hours=5, minutes=59))
    assert not cotacoes.yahoo_pausado(conn, agora + timedelta(hours=6))
