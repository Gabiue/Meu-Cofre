"""Indicadores do Banco Central (F8): plano incremental, gravação, status sem rede e taxa anual. Nunca usa a internet."""

import io
import json
from datetime import date

import pytest

from meu_cofre import indicadores

HOJE = date(2026, 10, 30)


class Buscador:
    """Buscador falso: devolve pontos prontos por série e guarda cada pedido (série, de, até)."""

    def __init__(self, dados=None, falha=None):
        self.dados = dados or {}
        self.falha = falha
        self.pedidos = []

    def __call__(self, serie, de, ate):
        self.pedidos.append((serie, de, ate))
        if self.falha:
            raise self.falha
        return [(d, v) for d, v in self.dados.get(serie, []) if de <= d <= ate]


def _dados():
    return {
        "cdi": [(date(2026, 10, 28), 0.04), (date(2026, 10, 29), 0.0401)],
        "selic": [(date(2026, 10, 29), 0.0399)],
        "ipca": [(date(2026, 8, 1), 0.2), (date(2026, 9, 1), 0.5)],
    }


def test_primeira_atualizacao_busca_tudo_e_grava(conn):
    b = Buscador(_dados())
    status = indicadores.atualizar_indicadores(conn, HOJE, b)
    assert status["ok"] and not status["sem_rede"] and status["novos"] == 5 and status["ate"] == date(2026, 10, 29)
    assert {p[0] for p in b.pedidos} == {"cdi", "selic", "ipca"}
    assert indicadores.carregar(conn, "cdi") == [(date(2026, 10, 28), 0.04), (date(2026, 10, 29), 0.0401)]
    cdi = next(p for p in b.pedidos if p[0] == "cdi")
    ipca = next(p for p in b.pedidos if p[0] == "ipca")
    assert cdi[1:] == (date(2026, 1, 1), HOJE)  # por padrão desde 1º de janeiro
    assert ipca[1] == date(2024, 12, 1)  # o IPCA recua 13 meses pra ter a média dos últimos 12


def _completos():
    """Dados desde 1º de outubro, no mesmo ritmo do Banco Central: CDI e Selic diários, IPCA mensal."""
    dias = [date(2026, 10, d) for d in range(1, 30)]
    return {
        "cdi": [(d, 0.04) for d in dias],
        "selic": [(d, 0.04) for d in dias],
        "ipca": [(date(2025, m, 1), 0.4) for m in range(9, 13)] + [(date(2026, m, 1), 0.4) for m in range(1, 10)],
    }


def test_busca_incremental_so_pede_o_que_falta(conn):
    indicadores.atualizar_indicadores(conn, HOJE, Buscador(_completos()), desde=date(2026, 10, 1))
    b = Buscador()
    indicadores.atualizar_indicadores(conn, date(2026, 11, 3), b, desde=date(2026, 10, 1))
    assert sorted(b.pedidos) == [
        ("cdi", date(2026, 10, 30), date(2026, 11, 3)),
        ("ipca", date(2026, 9, 2), date(2026, 11, 3)),  # o IPCA de setembro é o último; outubro ainda não saiu
        ("selic", date(2026, 10, 30), date(2026, 11, 3)),
    ]


def test_tudo_em_dia_nao_pede_nada(conn):
    indicadores.atualizar_indicadores(conn, HOJE, Buscador(_completos()), desde=date(2026, 10, 1))
    b = Buscador()
    indicadores.atualizar_indicadores(conn, HOJE, b, desde=date(2026, 10, 1))  # CDI até ontem, IPCA até o mês passado
    assert b.pedidos == []


def test_busca_o_comeco_que_falta_quando_desde_recua(conn):
    indicadores.atualizar_indicadores(conn, HOJE, Buscador(_completos()), desde=date(2026, 10, 1))
    b = Buscador()
    indicadores.atualizar_indicadores(conn, HOJE, b, desde=date(2026, 6, 1))
    assert ("cdi", date(2026, 6, 1), date(2026, 9, 30)) in b.pedidos


@pytest.mark.parametrize("erro", [OSError("sem rede"), TimeoutError("lento"), ValueError("resposta estranha"), RuntimeError("x")])
def test_sem_rede_nao_levanta_e_devolve_o_status(conn, erro):
    indicadores.gravar(conn, {"cdi": [(date(2026, 10, 20), 0.04)]})
    status = indicadores.atualizar_indicadores(conn, HOJE, Buscador(falha=erro))
    assert not status["ok"] and status["sem_rede"] and status["novos"] == 0
    assert set(status["erros"]) == {"cdi", "selic", "ipca"}
    assert status["ate"] == date(2026, 10, 20)  # o último valor guardado continua valendo
    assert indicadores.carregar(conn, "cdi") == [(date(2026, 10, 20), 0.04)]


def test_falha_de_uma_serie_nao_impede_as_outras(conn):
    def buscar(serie, de, ate):
        if serie == "selic":
            raise OSError("fora do ar")
        return _dados()[serie]

    status = indicadores.atualizar_indicadores(conn, HOJE, buscar)
    assert set(status["erros"]) == {"selic"} and status["novos"] == 4


def test_gravar_substitui_a_mesma_data(conn):
    indicadores.gravar(conn, {"cdi": [(date(2026, 10, 1), 0.04)]})
    indicadores.gravar(conn, {"cdi": [(date(2026, 10, 1), 0.05)]})
    assert indicadores.carregar(conn, "cdi") == [(date(2026, 10, 1), 0.05)]


def test_taxa_anual_atual(conn):
    assert indicadores.taxa_anual_atual(conn, "cdi") is None
    indicadores.gravar(conn, {"cdi": [(date(2026, 10, 28), 0.03), (date(2026, 10, 29), 0.04)]})
    assert indicadores.taxa_anual_atual(conn, "cdi") == pytest.approx((1.0004**252 - 1) * 100)
    indicadores.gravar(conn, {"ipca": [(date(2026, m, 1), 0.5) for m in range(1, 13)]})
    assert indicadores.taxa_anual_atual(conn, "ipca") == pytest.approx((1.005**12 - 1) * 100)


def test_ipca_mensal_medio_usa_os_ultimos_12():
    assert indicadores.ipca_mensal_medio([]) == 0.0
    assert indicadores.ipca_mensal_medio([9.0] * 5 + [0.5] * 12) == pytest.approx(0.005)


def test_sem_rede_por_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("MEU_COFRE_SEM_REDE", "1")
    assert indicadores.rede_desligada()
    monkeypatch.setenv("MEU_COFRE_SEM_REDE", "0")
    assert not indicadores.rede_desligada()
    monkeypatch.delenv("MEU_COFRE_SEM_REDE")
    assert not indicadores.rede_desligada()


def test_buscar_http_le_o_json_do_sgs(monkeypatch):
    corpo = json.dumps([{"data": "29/10/2026", "valor": "0.040168"}, {"data": "lixo", "valor": "x"}, {"data": "30/10/2026"}])
    pedidos = []

    def falso(pedido, timeout):
        pedidos.append((pedido.full_url, timeout))
        return io.BytesIO(corpo.encode())

    monkeypatch.setattr(indicadores.urllib.request, "urlopen", falso)
    pontos = indicadores._buscar_http("cdi", date(2026, 10, 1), date(2026, 10, 30))
    assert pontos == [(date(2026, 10, 29), 0.040168)]
    url, timeout = pedidos[0]
    assert url == "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json&dataInicial=01/10/2026&dataFinal=30/10/2026"
    assert timeout == indicadores.TIMEOUT
