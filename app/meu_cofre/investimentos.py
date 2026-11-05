"""Investimentos (docs/design/telas.md, F8): renda fixa e Tesouro Direto, patrimônio e previsão de fim de ano.

**Renda variável (F9).** Ações e FIIs ficam em `renda_variavel.py`; aqui eles entram no patrimônio (valor atual pela
última cotação), na previsão de 31/12 (sem supor alta de preço: só somam os proventos estimados até lá) e no gráfico.
O rendimento do ano e o "% do CDI" continuam sendo só da renda fixa e do Tesouro.

**Onde o dinheiro fica.** Aportar R$ X é uma **transferência** da conta corrente (ou carteira) pra conta
"Investimentos" (tipo investimento), e resgatar é a volta; não é gasto nem renda e o dinheiro sai do "Em conta".
Cada movimento também vira uma linha em `movimentacoes_ativos`, ligada ao ativo pelo `transferencia_id`.

**Valor atual (estimado, bruto, sem IR/IOF).** Parte do último "saldo real" (o que o banco mostra, que recalibra a
estimativa) ou do zero e aplica, em ordem, aportes, resgates e o rendimento do indexador entre um evento e outro:

- CDI e Selic: produto dos fatores diários `1 + valor_do_dia/100 × taxa/100`, nos dias com dado da série;
- IPCA + taxa: IPCA do mês distribuído pelos dias úteis dele × `(1 + taxa/100)^(1/252)` por dia útil;
- prefixado: `(1 + taxa/100)^(1/252)` por dia útil (feriados nacionais incluídos no calendário).

Um aporte começa a render no dia seguinte. Dia depois do último dado da série (ou antes do primeiro) usa a última
(primeira) taxa conhecida; o IPCA de mês ainda não divulgado usa a média dos últimos 12 meses. Isso marca o valor como
`estimado` só quando o buraco é velho (ver `TOLERANCIA_*`): o dado de ontem ainda não sair é normal. Num dia com saldo
real e aportes, o saldo real vale por último. Depois do vencimento o ativo para de render.

**Caixinhas das metas** viram um ativo especial (`caixinhas = 1`, "Caixinhas (metas)", 100% do CDI por padrão, taxa
editável) cujos aportes e resgates são as transferências da conta "Caixinhas". `metas.guardado` não muda: o progresso
da meta continua sendo o que foi guardado; o rendimento só entra no patrimônio.

Tudo que depende do dia recebe `hoje` (nada de `date.today()` aqui). Dinheiro em centavos; taxas em %.
"""

import calendar
import sqlite3
import uuid
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property, lru_cache

import pandas as pd

from meu_cofre import cotacoes, db, indicadores, metas, renda_variavel
from meu_cofre.lancamentos import formatar_brl

NOME_CONTA = "Investimentos"
NOME_CAIXINHAS = "Caixinhas (metas)"
CATEGORIA_TRANSFERENCIA = "Transferência entre contas"
TIPOS = ("renda_fixa", "tesouro", "acao", "fii")
TIPOS_F8 = ("renda_fixa", "tesouro")
TIPOS_RV = ("acao", "fii")  # têm ticker em vez de indexador e taxa
INDEXADORES = ("cdi", "selic", "ipca", "pre")
NOMES_TIPO = {"renda_fixa": "Renda fixa", "tesouro": "Tesouro Direto", "acao": "Ações", "fii": "FIIs"}
DIAS_UTEIS_ANO = indicadores.DIAS_UTEIS_ANO
TOLERANCIA_DIARIA = 5  # dias sem CDI/Selic na ponta que ainda são normais (a divulgação atrasa)
TOLERANCIA_IPCA = 62  # dias sem o IPCA do mês (sai por volta do dia 10 do mês seguinte)
TAXA_MAXIMA = {"cdi": 400.0, "selic": 400.0, "ipca": 100.0, "pre": 100.0}


# --- Calendário de dias úteis --------------------------------------------------------------------


def _pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


@lru_cache(maxsize=None)
def feriados(ano: int) -> frozenset[date]:
    """Feriados nacionais em que o mercado não funciona (Carnaval, Sexta Santa e Corpus Christi incluídos)."""
    pascoa = _pascoa(ano)
    moveis = {pascoa + timedelta(days=n) for n in (-48, -47, -2, 60)}
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))  # Consciência Negra
    return frozenset(moveis | {date(ano, m, d) for m, d in fixos})


def dia_util(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados(d.year)


@lru_cache(maxsize=None)
def _dias_uteis_do_mes(ano: int, mes: int) -> int:
    return sum(dia_util(date(ano, mes, d)) for d in range(1, calendar.monthrange(ano, mes)[1] + 1))


# --- Texto da taxa -------------------------------------------------------------------------------


def _num(x: float) -> str:
    """110.0 -> "110"; 6.2 -> "6,2"; 12.55 -> "12,55"."""
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def descrever_taxa(indexador: str | None, taxa: float | None) -> str:
    """"110% do CDI", "100% da Selic", "IPCA + 6,2%", "12,5% a.a."."""
    if indexador is None or taxa is None:
        return ""
    return {
        "cdi": f"{_num(taxa)}% do CDI",
        "selic": f"{_num(taxa)}% da Selic",
        "ipca": f"IPCA + {_num(taxa)}%",
        "pre": f"{_num(taxa)}% a.a.",
    }[indexador]


# --- Mercado: séries e curvas de rendimento -----------------------------------------------------------


class _Serie:
    """Uma série do Banco Central em memória: valor do dia (ou o mais próximo conhecido)."""

    def __init__(self, pontos: list[tuple[date, float]]):
        self.datas = [d for d, _v in pontos]
        self.valores = [v for _d, v in pontos]
        self.por_data = dict(pontos)

    def __bool__(self) -> bool:
        return bool(self.datas)

    @property
    def primeira(self) -> date:
        return self.datas[0]

    @property
    def ultima(self) -> date:
        return self.datas[-1]

    def ate(self, d: date) -> list[float]:
        """Os valores com data até `d`, em ordem."""
        return self.valores[: bisect_right(self.datas, d)]


class _Curva:
    """Fator de rendimento acumulado dia a dia. `fator(a, b)` é o rendimento do que ficou aplicado de a (fora) até b
    (dentro); o segundo valor diz se algum dia do período teve a taxa suposta (dado velho em falta)."""

    def __init__(self, inicio: date, fatores: list[float], faltas: list[bool]):
        self.inicio = inicio
        self._produto: list[float] = []
        self._faltas: list[int] = []
        produto, falta = 1.0, 0
        for f, em_falta in zip(fatores, faltas):
            produto *= f
            falta += em_falta
            self._produto.append(produto)
            self._faltas.append(falta)

    def _acumulado(self, d: date) -> tuple[float, int]:
        i = min((d - self.inicio).days, len(self._produto) - 1)
        return (1.0, 0) if i < 0 else (self._produto[i], self._faltas[i])

    def fator(self, a: date, b: date) -> tuple[float, bool]:
        if b <= a:
            return 1.0, False
        pa, fa = self._acumulado(a)
        pb, fb = self._acumulado(b)
        return pb / pa, fb > fa


class _Mercado:
    """As séries e as curvas (por indexador e taxa) de um intervalo de datas, calculadas uma vez só."""

    def __init__(self, conn: sqlite3.Connection, hoje: date, inicio: date, fim: date):
        self.conn, self.hoje, self.inicio, self.fim = conn, hoje, inicio, fim
        self._series: dict[str, _Serie] = {}
        self._curvas: dict[tuple, _Curva] = {}
        self._ipca: dict[tuple[int, int], tuple[float, bool]] = {}

    def serie(self, nome: str) -> _Serie:
        if nome not in self._series:
            self._series[nome] = _Serie(indicadores.carregar(self.conn, nome))
        return self._series[nome]

    def curva(self, indexador: str | None, taxa: float | None, vencimento: date | None = None) -> _Curva:
        chave = (indexador, taxa, vencimento)
        if chave not in self._curvas:
            fatores, faltas = [], []
            for k in range((self.fim - self.inicio).days + 1):
                f, falta = self._fator_do_dia(self.inicio + timedelta(days=k), indexador, taxa or 0.0, vencimento)
                fatores.append(f)
                faltas.append(falta)
            self._curvas[chave] = _Curva(self.inicio, fatores, faltas)
        return self._curvas[chave]

    def _velho(self, d: date, tolerancia: int) -> bool:
        """O dia já passou há mais que a tolerância (um buraco recente ainda é normal)?"""
        return d <= self.hoje and (self.hoje - d).days > tolerancia

    def _fator_do_dia(self, d: date, indexador: str | None, taxa: float, vencimento: date | None) -> tuple[float, bool]:
        if vencimento is not None and d > vencimento:
            return 1.0, False
        if indexador in ("cdi", "selic"):
            return self._fator_diario(d, self.serie(indexador), taxa)
        if not dia_util(d):
            return 1.0, False
        if indexador == "pre":
            return (1 + taxa / 100) ** (1 / DIAS_UTEIS_ANO), False
        if indexador == "ipca":
            inflacao, falta = self._ipca_do_mes(d.year, d.month)
            real = (1 + taxa / 100) ** (1 / DIAS_UTEIS_ANO)
            return (1 + inflacao) ** (1 / _dias_uteis_do_mes(d.year, d.month)) * real, falta and self._velho(d, TOLERANCIA_IPCA)
        return 1.0, False

    def _fator_diario(self, d: date, serie: _Serie, taxa: float) -> tuple[float, bool]:
        """CDI ou Selic: o valor do dia; fora do alcance da série, a taxa conhecida mais próxima."""
        if not serie:
            return 1.0, dia_util(d) and self._velho(d, 0)
        if d in serie.por_data:
            return 1 + serie.por_data[d] / 100 * taxa / 100, False
        if serie.primeira < d < serie.ultima:
            return 1.0, False  # a série pulou o dia: sem expediente
        if not dia_util(d):
            return 1.0, False
        valor = serie.valores[-1] if d > serie.ultima else serie.valores[0]
        return 1 + valor / 100 * taxa / 100, self._velho(d, TOLERANCIA_DIARIA)

    def _ipca_do_mes(self, ano: int, mes: int) -> tuple[float, bool]:
        """(IPCA do mês como fração, é suposição?). Mês sem divulgação usa a média dos 12 meses anteriores."""
        if (ano, mes) not in self._ipca:
            serie = self.serie("ipca")
            primeiro_do_mes = date(ano, mes, 1)
            if primeiro_do_mes in serie.por_data:
                self._ipca[(ano, mes)] = (serie.por_data[primeiro_do_mes] / 100, False)
            else:
                anteriores = serie.ate(primeiro_do_mes - timedelta(days=1)) or serie.valores
                self._ipca[(ano, mes)] = (indicadores.ipca_mensal_medio(anteriores), True)
        return self._ipca[(ano, mes)]


# --- Ativos no banco -------------------------------------------------------------------------------


@dataclass
class Ativo:
    id: int
    nome: str
    tipo: str
    instituicao: str | None
    indexador: str | None
    taxa: float | None
    vencimento: date | None
    caixinhas: bool
    ativo: bool
    criado_em: date
    ticker: str | None = None

    @property
    def descricao_taxa(self) -> str:
        return descrever_taxa(self.indexador, self.taxa)


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def _ativo_da_linha(r: sqlite3.Row) -> Ativo:
    return Ativo(
        id=r["id"], nome=r["nome"], tipo=r["tipo"], instituicao=r["instituicao"], indexador=r["indexador"], taxa=r["taxa"],
        vencimento=_data(r["vencimento"]), caixinhas=bool(r["caixinhas"]), ativo=bool(r["ativo"]), criado_em=_data(r["criado_em"]),
        ticker=r["ticker"],
    )


def obter(conn: sqlite3.Connection, ativo_id: int) -> Ativo:
    """Um ativo. ValueError se não existe."""
    linha = conn.execute("SELECT * FROM ativos WHERE id = ?", (ativo_id,)).fetchone()
    if linha is None:
        raise ValueError(f"O investimento {ativo_id} não existe.")
    return _ativo_da_linha(linha)


def conta_investimentos(conn: sqlite3.Connection) -> int:
    """Id da conta "Investimentos" (tipo investimento); cria se ainda não existe."""
    linha = conn.execute("SELECT id FROM contas WHERE nome = ?", (NOME_CONTA,)).fetchone()
    if linha:
        return linha["id"]
    with conn:
        return conn.execute("INSERT INTO contas (nome, tipo) VALUES (?, 'investimento')", (NOME_CONTA,)).lastrowid


def _categoria_transferencia(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM categorias WHERE nome = ?", (CATEGORIA_TRANSFERENCIA,)).fetchone()
    if linha:
        return linha["id"]
    with conn:
        return conn.execute(
            "INSERT INTO categorias (nome, tipo) VALUES (?, 'transferencia')", (CATEGORIA_TRANSFERENCIA,)
        ).lastrowid


def _garantir_caixinhas(conn: sqlite3.Connection) -> Ativo | None:
    """O ativo "Caixinhas (metas)". Nasce (100% do CDI) na primeira vez que há dinheiro nas Caixinhas; antes disso,
    None."""
    linha = conn.execute("SELECT * FROM ativos WHERE caixinhas = 1").fetchone()
    if linha:
        return _ativo_da_linha(linha)
    conta = conn.execute("SELECT id FROM contas WHERE nome = ?", (metas.NOME_CAIXINHAS,)).fetchone()
    if conta is None or conn.execute("SELECT 1 FROM lancamentos WHERE conta_id = ? LIMIT 1", (conta["id"],)).fetchone() is None:
        return None
    with conn:
        novo = conn.execute(
            "INSERT INTO ativos (nome, tipo, indexador, taxa, caixinhas) VALUES (?, 'renda_fixa', 'cdi', 100.0, 1)",
            (NOME_CAIXINHAS,),
        ).lastrowid
    return obter(conn, novo)


def ativo_caixinhas(conn: sqlite3.Connection) -> Ativo | None:
    """O ativo das caixinhas das metas, ou None se ainda não há dinheiro guardado."""
    return _garantir_caixinhas(conn)


def _ativos(conn: sqlite3.Connection, incluir_encerrados: bool = False) -> list[Ativo]:
    """Renda fixa e Tesouro (as caixinhas primeiro), na ordem de cadastro."""
    _garantir_caixinhas(conn)
    marcas = ", ".join("?" * len(TIPOS_F8))
    extra = "" if incluir_encerrados else "AND ativo = 1"
    linhas = conn.execute(f"SELECT * FROM ativos WHERE tipo IN ({marcas}) {extra} ORDER BY caixinhas DESC, id", TIPOS_F8)
    return [_ativo_da_linha(r) for r in linhas]


def _validar(
    hoje: date, nome: str, tipo: str, instituicao: str | None, indexador: str | None, taxa: float | None,
    vencimento: date | None, vencimento_atual: date | None = None,
) -> tuple[str, str | None, date | None]:
    """Confere os campos. Devolve (nome, instituição, vencimento) limpos."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Dê um nome ao investimento.")
    if tipo not in TIPOS:
        raise ValueError(f"Tipo desconhecido: {tipo}")
    if tipo in TIPOS_F8:
        if indexador not in INDEXADORES:
            raise ValueError("Escolha o indexador.")
        if tipo == "tesouro" and indexador == "cdi":
            raise ValueError("O Tesouro Direto não rende pelo CDI: escolha Selic, IPCA+ ou prefixado.")
        if taxa is None:
            raise ValueError("Digite a taxa.")
        if taxa < 0 or (taxa == 0 and indexador != "ipca"):
            raise ValueError("A taxa precisa ser maior que zero.")
        if taxa > TAXA_MAXIMA[indexador]:
            raise ValueError(f"Taxa alta demais para esse indexador (máximo {_num(TAXA_MAXIMA[indexador])}).")
    if vencimento is not None and vencimento < hoje and vencimento != vencimento_atual:
        raise ValueError("Esse vencimento já passou.")
    return nome, (instituicao or "").strip() or None, vencimento


def validar_ticker(conn: sqlite3.Connection, ticker: str | None, ativo_id: int | None = None) -> str:
    """O ticker em maiúsculas ("petr4" -> "PETR4"). ValueError se vazio, fora do padrão ou de outro ativo."""
    ticker = cotacoes.normalizar_ticker(ticker)
    if not ticker:
        raise ValueError("Digite o ticker, como PETR4 ou HGLG11.")
    if not cotacoes.ticker_valido(ticker):
        raise ValueError("Ticker inválido. Use 4 letras e o número, como PETR4 ou HGLG11.")
    outro = conn.execute("SELECT id FROM ativos WHERE ticker = ? AND id IS NOT ?", (ticker, ativo_id)).fetchone()
    if outro:
        raise ValueError(f"Já existe um ativo com o ticker {ticker}.")
    return ticker


def _campos_do_tipo(
    conn: sqlite3.Connection, tipo: str, ticker: str | None, indexador: str | None, taxa: float | None,
    vencimento: date | None, ativo_id: int | None = None,
) -> tuple[str | None, str | None, float | None, date | None]:
    """(ticker, indexador, taxa, vencimento) coerentes com o tipo: ações e FIIs têm ticker e nada de taxa nem vencimento;
    renda fixa e Tesouro têm taxa e nada de ticker."""
    if tipo in TIPOS_RV:
        return validar_ticker(conn, ticker, ativo_id), None, None, None
    return None, indexador, taxa, vencimento


def criar(
    conn: sqlite3.Connection,
    hoje: date,
    *,
    nome: str,
    tipo: str,
    instituicao: str | None = None,
    indexador: str | None = None,
    taxa: float | None = None,
    vencimento: date | None = None,
    ticker: str | None = None,
) -> int:
    """Cria o investimento e devolve o id. `taxa` em %: 110 = 110% do CDI; 6.2 = IPCA + 6,2%; 12.5 = 12,5% a.a.
    Ações e FIIs (`tipo` "acao" ou "fii") usam `ticker` em vez de indexador e taxa."""
    ticker, indexador, taxa, vencimento = _campos_do_tipo(conn, tipo, ticker, indexador, taxa, vencimento)
    nome, instituicao, vencimento = _validar(hoje, nome, tipo, instituicao, indexador, taxa, vencimento)
    with conn:
        return conn.execute(
            """
            INSERT INTO ativos (nome, tipo, instituicao, indexador, taxa, vencimento, criado_em, ticker)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (nome, tipo, instituicao, indexador, taxa, vencimento.isoformat() if vencimento else None, hoje.isoformat(), ticker),
        ).lastrowid


def editar(
    conn: sqlite3.Connection,
    hoje: date,
    ativo_id: int,
    *,
    nome: str,
    tipo: str,
    instituicao: str | None = None,
    indexador: str | None = None,
    taxa: float | None = None,
    vencimento: date | None = None,
    ticker: str | None = None,
) -> None:
    """Troca os dados (o histórico de aportes e operações fica). Nas caixinhas só o indexador e a taxa mudam. Não dá
    pra trocar entre renda fixa/Tesouro e ação/FII: o histórico de um não serve pro outro."""
    atual = obter(conn, ativo_id)
    if atual.caixinhas:
        nome, tipo, instituicao, vencimento = atual.nome, atual.tipo, atual.instituicao, atual.vencimento
    if (tipo in TIPOS_RV) != (atual.tipo in TIPOS_RV):
        raise ValueError("Não dá pra trocar entre renda fixa/Tesouro e ações/FIIs. Crie outro investimento.")
    ticker, indexador, taxa, vencimento = _campos_do_tipo(conn, tipo, ticker, indexador, taxa, vencimento, ativo_id)
    nome, instituicao, vencimento = _validar(
        hoje, nome, tipo, instituicao, indexador, taxa, vencimento, vencimento_atual=atual.vencimento
    )
    with conn:
        conn.execute(
            "UPDATE ativos SET nome = ?, tipo = ?, instituicao = ?, indexador = ?, taxa = ?, vencimento = ?, ticker = ? WHERE id = ?",
            (nome, tipo, instituicao, indexador, taxa, vencimento.isoformat() if vencimento else None, ticker, ativo_id),
        )


def encerrar(conn: sqlite3.Connection, hoje: date, ativo_id: int) -> None:
    """Tira o investimento da lista (o histórico fica). Só quando não há mais nada nele: resgate antes."""
    ativo = obter(conn, ativo_id)
    if ativo.caixinhas:
        raise ValueError("As caixinhas das metas não se encerram: o dinheiro delas é gerido na aba Metas.")
    if ativo.tipo in TIPOS_RV:
        if renda_variavel.posicao(conn, ativo_id, hoje)["quantidade"] > 0:
            raise ValueError("Ainda há cotas na carteira. Venda tudo antes de encerrar.")
    elif valor_atual(conn, ativo_id, hoje)[0] > 0:
        raise ValueError("Ainda há dinheiro aplicado. Resgate tudo antes de encerrar.")
    with conn:
        conn.execute("UPDATE ativos SET ativo = 0 WHERE id = ?", (ativo_id,))


def reativar(conn: sqlite3.Connection, ativo_id: int) -> None:
    obter(conn, ativo_id)
    with conn:
        conn.execute("UPDATE ativos SET ativo = 1 WHERE id = ?", (ativo_id,))


# --- Aportar, resgatar e saldo real ------------------------------------------------------------------------


def _conta_do_dia_a_dia(conn: sqlite3.Connection, conta_id: int, rotulo: str) -> None:
    conta = conn.execute("SELECT tipo, ativa FROM contas WHERE id = ?", (conta_id,)).fetchone()
    if conta is None or conta["tipo"] not in ("corrente", "dinheiro") or not conta["ativa"]:
        raise ValueError(f"Escolha uma conta corrente ou a carteira {rotulo}.")


def _ativo_que_recebe_movimento(conn: sqlite3.Connection, ativo_id: int) -> Ativo:
    ativo = obter(conn, ativo_id)
    if ativo.caixinhas:
        raise ValueError("O dinheiro das caixinhas se move na aba Metas (Guardar e Retirar).")
    if ativo.tipo not in TIPOS_F8:
        raise ValueError("Este tipo de ativo ainda não recebe movimentos.")
    if not ativo.ativo:
        raise ValueError("Este investimento foi encerrado. Reative-o pra movimentar.")
    return ativo


def _transferir(
    conn: sqlite3.Connection, ativo: Ativo, tipo: str, valor: int, saida_id: int, entrada_id: int, data: date, descricao: str
) -> str:
    transferencia_id = uuid.uuid4().hex
    categoria_id = _categoria_transferencia(conn)
    with conn:
        for conta_id, sinal in ((saida_id, -1), (entrada_id, 1)):
            conn.execute(
                """
                INSERT INTO lancamentos
                    (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem, transferencia_id)
                VALUES (?, ?, ?, ?, ?, 'transferencia', 'manual', ?)
                """,
                (conta_id, data.isoformat(), sinal * valor, descricao, categoria_id, transferencia_id),
            )
        conn.execute(
            "INSERT INTO movimentacoes_ativos (ativo_id, tipo, data, valor_centavos, transferencia_id) VALUES (?, ?, ?, ?, ?)",
            (ativo.id, tipo, data.isoformat(), valor, transferencia_id),
        )
    return transferencia_id


def aportar(conn: sqlite3.Connection, ativo_id: int, valor_centavos: int, conta_origem_id: int, data: date) -> str:
    """Aporta no investimento: transferência da conta pra "Investimentos". Devolve o `transferencia_id`."""
    ativo = _ativo_que_recebe_movimento(conn, ativo_id)
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    _conta_do_dia_a_dia(conn, conta_origem_id, "de onde sai o dinheiro")
    return _transferir(
        conn, ativo, "aporte", valor_centavos, conta_origem_id, conta_investimentos(conn), data, f"Aporte: {ativo.nome}"
    )


def resgatar(conn: sqlite3.Connection, ativo_id: int, valor_centavos: int, conta_destino_id: int, data: date) -> str:
    """Resgata: transferência de "Investimentos" de volta pra conta. Não passa do valor atual estimado na data."""
    ativo = _ativo_que_recebe_movimento(conn, ativo_id)
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    disponivel = valor_atual(conn, ativo_id, data)[0]
    if valor_centavos > disponivel:
        raise ValueError(
            f"Só há ≈ {formatar_brl(disponivel)} nesse investimento. Se o saldo no banco é maior, use Atualizar saldo antes."
            if disponivel
            else "Não há nada aplicado nesse investimento."
        )
    _conta_do_dia_a_dia(conn, conta_destino_id, "pra onde vai o dinheiro")
    return _transferir(
        conn, ativo, "resgate", valor_centavos, conta_investimentos(conn), conta_destino_id, data, f"Resgate: {ativo.nome}"
    )


def registrar_saldo_real(conn: sqlite3.Connection, ativo_id: int, valor_centavos: int, data: date) -> None:
    """Registra o saldo que o banco mostra na data. Daí em diante a estimativa parte dele. Zero é válido (zerou).
    Um segundo saldo na mesma data substitui o primeiro."""
    ativo = obter(conn, ativo_id)
    if ativo.tipo not in TIPOS_F8:
        raise ValueError("Este tipo de ativo ainda não aceita saldo real.")
    if valor_centavos is None or valor_centavos < 0:
        raise ValueError("O saldo não pode ser negativo.")
    with conn:
        conn.execute(
            "DELETE FROM movimentacoes_ativos WHERE ativo_id = ? AND tipo = 'saldo_real' AND data = ?", (ativo_id, data.isoformat())
        )
        conn.execute(
            "INSERT INTO movimentacoes_ativos (ativo_id, tipo, data, valor_centavos) VALUES (?, 'saldo_real', ?, ?)",
            (ativo_id, data.isoformat(), valor_centavos),
        )


# --- Histórico, correção e exclusão ----------------------------------------------------------------


def historico(conn: sqlite3.Connection, ativo_id: int) -> list[dict]:
    """Tudo que foi registrado no ativo, do mais recente pro mais antigo. Cada item: `fonte` ("mov" = renda fixa/Tesouro,
    "rv" = ação/FII), `id`, `tipo`, `data`, `valor`, `quantidade`, `preco` e `taxas` (None/0 onde não se aplica) e
    `afeta_conta`."""
    if obter(conn, ativo_id).tipo in TIPOS_RV:
        itens = [
            {"fonte": "rv", "id": o.id, "tipo": o.tipo, "data": o.data, "valor": o.valor_centavos, "quantidade": o.quantidade,
             "preco": o.preco_centavos, "taxas": o.taxas_centavos, "afeta_conta": o.afeta_conta}
            for o in renda_variavel.operacoes(conn, ativo_id)
        ]
    else:
        itens = [
            {"fonte": "mov", "id": r["id"], "tipo": r["tipo"], "data": date.fromisoformat(r["data"]), "valor": r["valor_centavos"],
             "quantidade": None, "preco": None, "taxas": 0, "afeta_conta": r["transferencia_id"] is not None}
            for r in conn.execute("SELECT * FROM movimentacoes_ativos WHERE ativo_id = ?", (ativo_id,))
        ]
    return sorted(itens, key=lambda i: (i["data"], i["id"]), reverse=True)


def _movimentacao(conn: sqlite3.Connection, mov_id: int) -> sqlite3.Row:
    linha = conn.execute("SELECT * FROM movimentacoes_ativos WHERE id = ?", (mov_id,)).fetchone()
    if linha is None:
        raise ValueError("Esse registro não existe mais.")
    return linha


def editar_movimentacao(conn: sqlite3.Connection, mov_id: int, *, valor: int, data: date) -> None:
    """Corrige o valor e a data de um aporte, resgate ou saldo real. Aporte e resgate acertam os dois lados da
    transferência. Mesmas regras de criar: valor > 0 (saldo real aceita zero) e o resgate não passa do valor na data.

    shortcut: editar ou apagar um aporte não reconfere resgates posteriores (o valor estimado já nunca fica negativo)."""
    mov = _movimentacao(conn, mov_id)
    if valor is None or valor < (0 if mov["tipo"] == "saldo_real" else 1):
        raise ValueError("O saldo não pode ser negativo." if mov["tipo"] == "saldo_real" else "O valor precisa ser maior que zero.")
    with conn:
        if mov["tipo"] == "resgate":  # zera o resgate antigo e confere o que sobra na nova data
            conn.execute("UPDATE movimentacoes_ativos SET valor_centavos = 0 WHERE id = ?", (mov_id,))
            disponivel = valor_atual(conn, mov["ativo_id"], data)[0]
            if valor > disponivel:
                raise ValueError(
                    f"Só há ≈ {formatar_brl(disponivel)} nesse investimento nessa data."
                    if disponivel
                    else "Não há nada aplicado nesse investimento nessa data."
                )
        if mov["tipo"] == "saldo_real":  # um só saldo por dia
            conn.execute(
                "DELETE FROM movimentacoes_ativos WHERE ativo_id = ? AND tipo = 'saldo_real' AND data = ? AND id <> ?",
                (mov["ativo_id"], data.isoformat(), mov_id),
            )
        conn.execute("UPDATE movimentacoes_ativos SET valor_centavos = ?, data = ? WHERE id = ?", (valor, data.isoformat(), mov_id))
        if mov["transferencia_id"]:
            conn.execute(
                "UPDATE lancamentos SET data = ?, valor_centavos = CASE WHEN valor_centavos > 0 THEN ? ELSE -? END "
                "WHERE transferencia_id = ?",
                (data.isoformat(), valor, valor, mov["transferencia_id"]),
            )


def excluir_movimentacao(conn: sqlite3.Connection, mov_id: int) -> None:
    """Apaga o registro e, em aporte e resgate, os dois lados da transferência."""
    mov = _movimentacao(conn, mov_id)
    with conn:
        conn.execute("DELETE FROM movimentacoes_ativos WHERE id = ?", (mov_id,))
        if mov["transferencia_id"]:
            conn.execute("DELETE FROM lancamentos WHERE transferencia_id = ?", (mov["transferencia_id"],))


# --- Valor atual -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Evento:
    data: date
    tipo: str  # aporte | resgate | saldo_real
    centavos: int


def _eventos(conn: sqlite3.Connection, ativo: Ativo, ate: date) -> list[_Evento]:
    """Os movimentos do ativo até `ate`, em ordem. No mesmo dia, aporte e resgate vêm antes do saldo real."""
    eventos = [
        _Evento(date.fromisoformat(r["data"]), r["tipo"], r["valor_centavos"])
        for r in conn.execute(
            "SELECT tipo, data, valor_centavos FROM movimentacoes_ativos WHERE ativo_id = ? AND data <= ? ORDER BY id",
            (ativo.id, ate.isoformat()),
        )
    ]
    if ativo.caixinhas:  # o aplicado das caixinhas são as transferências da conta "Caixinhas"
        conta = conn.execute("SELECT id FROM contas WHERE nome = ?", (metas.NOME_CAIXINHAS,)).fetchone()
        if conta:
            for r in conn.execute(
                "SELECT data, valor_centavos FROM lancamentos WHERE conta_id = ? AND data <= ? ORDER BY id",
                (conta["id"], ate.isoformat()),
            ):
                v = r["valor_centavos"]
                eventos.append(_Evento(date.fromisoformat(r["data"]), "aporte" if v > 0 else "resgate", abs(v)))
    return sorted(eventos, key=lambda e: (e.data, e.tipo == "saldo_real"))


@dataclass(frozen=True)
class _Valor:
    centavos: int
    estimado: bool
    base: date | None  # data do último saldo real usado


def _avaliar(eventos: list[_Evento], curva: _Curva, alvo: date) -> _Valor:
    """O valor na data `alvo`: a partir do zero, aplica cada evento e o rendimento entre eles."""
    saldo, ultimo, estimado, base = 0.0, None, False, None
    for e in eventos:
        if e.data > alvo:
            break
        if ultimo is not None and e.data > ultimo and saldo > 0:
            fator, falta = curva.fator(ultimo, e.data)
            saldo, estimado = saldo * fator, estimado or falta
        ultimo = e.data
        if e.tipo == "aporte":
            saldo += e.centavos
        elif e.tipo == "resgate":
            saldo = max(0.0, saldo - e.centavos)
        else:
            saldo, base = float(e.centavos), e.data
    if ultimo is not None and alvo > ultimo and saldo > 0:
        fator, falta = curva.fator(ultimo, alvo)
        saldo, estimado = saldo * fator, estimado or falta
    return _Valor(round(saldo), estimado, base)


def inicio_necessario(conn: sqlite3.Connection, hoje: date) -> date:
    """A data mais antiga de que as contas precisam de taxa: o 1º de janeiro ou o primeiro aporte, o que vier antes."""
    datas = [date(hoje.year, 1, 1)]
    for sql in (
        "SELECT MIN(data) FROM movimentacoes_ativos",
        "SELECT MIN(l.data) FROM lancamentos l JOIN contas c ON c.id = l.conta_id WHERE c.nome = 'Caixinhas'",
    ):
        primeira = conn.execute(sql).fetchone()[0]
        if primeira:
            datas.append(date.fromisoformat(primeira))
    return min(datas)


class _Contexto:
    """Tudo que as contas de uma chamada compartilham: os ativos, seus movimentos e as curvas (calculadas uma vez)."""

    def __init__(self, conn: sqlite3.Connection, hoje: date, fim: date | None = None, ativos: list[Ativo] | None = None):
        self.conn, self.hoje = conn, hoje
        self.ativos = _ativos(conn, incluir_encerrados=True) if ativos is None else ativos
        self.eventos = {a.id: _eventos(conn, a, hoje) for a in self.ativos}
        inicio = min([inicio_necessario(conn, hoje)] + [ev[0].data for ev in self.eventos.values() if ev]) - timedelta(days=1)
        self.mercado = _Mercado(conn, hoje, inicio, max(hoje, fim or hoje))
        self._proventos: list[tuple[date, int]] | None = None

    @cached_property
    def progresso(self) -> "ProgressoAporte":
        """O mês de hoje contra a meta de aporte (calculado uma vez por chamada)."""
        return progresso_do_mes(self.conn, self.hoje)

    @cached_property
    def carteira(self) -> renda_variavel.Carteira:
        """Ações e FIIs (só carrega se alguém pedir)."""
        return renda_variavel.Carteira(self.conn, self.hoje)

    def curva(self, a: Ativo) -> _Curva:
        return self.mercado.curva(a.indexador, a.taxa, a.vencimento)

    def valor(self, a: Ativo, alvo: date) -> _Valor:
        return _avaliar(self.eventos[a.id], self.curva(a), alvo)

    def total(self, alvo: date, so: str | None = None) -> int:
        """Soma dos valores na data, ações e FIIs incluídos (em "outros"). `so`: "caixinhas" ou "outros" restringe."""
        soma = sum(
            self.valor(a, alvo).centavos
            for a in self.ativos
            if so is None or (so == "caixinhas") == a.caixinhas
        )
        return soma + (self.carteira.valor(alvo) if so != "caixinhas" else 0)

    def proventos_ate(self, alvo: date) -> int:
        """Proventos de ações e FIIs previstos de hoje (exclusive) até `alvo`, pelo histórico de 12 meses."""
        if self._proventos is None:
            self._proventos = renda_variavel.proventos_projetados(self.conn, self.hoje, carteira=self.carteira)
        return renda_variavel.proventos_ate(self._proventos, alvo)


def valor_atual(conn: sqlite3.Connection, ativo_id: int, hoje: date) -> tuple[int, bool, date | None]:
    """(centavos, estimado, base_data) do investimento hoje.

    `estimado`: faltou dado de indicador num período antigo e foi usada a última taxa conhecida.
    `base_data`: data do último saldo real usado (None se a conta parte só dos aportes).
    """
    ativo = obter(conn, ativo_id)
    v = _Contexto(conn, hoje, ativos=[ativo]).valor(ativo, hoje)
    return v.centavos, v.estimado, v.base


# --- Posições (a lista da aba) ---------------------------------------------------------------------


@dataclass
class Posicao:
    """Um investimento com os números de hoje. `aplicado` = aportes − resgates (nunca negativo); `rendimento` = valor
    atual − aportado + resgatado (desde o início, bruto); `rendimento_pct` = rendimento ÷ total aportado."""

    ativo: Ativo
    aplicado: int
    aportado: int
    valor_atual: int
    estimado: bool
    base_data: date | None
    rendimento: int
    rendimento_pct: float | None


def _posicao(ctx: _Contexto, a: Ativo) -> Posicao:
    v = ctx.valor(a, ctx.hoje)
    eventos = ctx.eventos[a.id]
    aportado = sum(e.centavos for e in eventos if e.tipo == "aporte")
    resgatado = sum(e.centavos for e in eventos if e.tipo == "resgate")
    rendimento = v.centavos - aportado + resgatado
    return Posicao(
        ativo=a, aplicado=max(0, aportado - resgatado), aportado=aportado, valor_atual=v.centavos, estimado=v.estimado,
        base_data=v.base, rendimento=rendimento, rendimento_pct=rendimento / aportado * 100 if aportado else None,
    )


def listar(conn: sqlite3.Connection, hoje: date, incluir_encerrados: bool = False) -> list[Posicao]:
    """Os investimentos (renda fixa e Tesouro; as caixinhas das metas primeiro), com valor atual e rendimento."""
    ctx = _Contexto(conn, hoje)
    return [_posicao(ctx, a) for a in ctx.ativos if a.ativo or incluir_encerrados]


# --- Rendimento e comparação com o CDI ---------------------------------------------------------------------


@dataclass(frozen=True)
class Rendimento:
    """`centavos`: o que rendeu no período (bruto; saldo real conta como rendimento); `percentual`: rendimento ÷
    o aplicado no período (saldo no início + aportes − resgates), None sem nada aplicado."""

    centavos: int
    percentual: float | None


def _rendimento_de(ctx: _Contexto, ativos: list[Ativo], inicio: date, fim: date) -> tuple[Rendimento, float]:
    """(rendimento, o que o mesmo dinheiro teria rendido a 100% do CDI) de `ativos` entre `inicio` e `fim`."""
    antes = inicio - timedelta(days=1)
    cdi = ctx.mercado.curva("cdi", 100.0)
    rendeu, aplicado, equivalente = 0, 0, 0.0
    for a in ativos:
        v0, v1 = ctx.valor(a, antes).centavos, ctx.valor(a, fim).centavos
        fluxos = [
            (e.data, e.centavos if e.tipo == "aporte" else -e.centavos)
            for e in ctx.eventos[a.id]
            if e.tipo != "saldo_real" and inicio <= e.data <= fim
        ]
        rendeu += v1 - v0 - sum(f for _d, f in fluxos)
        # sobre o aplicado, não o capital médio do período: dinheiro de poucos dias inflaria o percentual
        aplicado += v0 + sum(f for _d, f in fluxos)
        equivalente += v0 * (cdi.fator(antes, fim)[0] - 1) + sum(f * (cdi.fator(d, fim)[0] - 1) for d, f in fluxos)
    return Rendimento(rendeu, rendeu / aplicado * 100 if aplicado > 0 else None), equivalente


def rendimento(conn: sqlite3.Connection, ativo_id: int, inicio: date, fim: date, hoje: date | None = None) -> Rendimento:
    """Quanto o investimento rendeu entre `inicio` e `fim` (inclusive). `hoje` padrão: `fim`."""
    ativo = obter(conn, ativo_id)
    return _rendimento_de(_Contexto(conn, hoje or fim, ativos=[ativo]), [ativo], inicio, fim)[0]


def rendimento_portfolio(conn: sqlite3.Connection, inicio: date, fim: date, hoje: date | None = None) -> Rendimento:
    """O mesmo, somando todos os investimentos e as caixinhas."""
    ctx = _Contexto(conn, hoje or fim)
    return _rendimento_de(ctx, ctx.ativos, inicio, fim)[0]


def comparar_cdi(
    conn: sqlite3.Connection, inicio: date, fim: date, hoje: date | None = None, ativo_id: int | None = None
) -> float | None:
    """"% do CDI" no período: o rendimento dividido pelo que o mesmo dinheiro (mesmas datas de aporte e resgate)
    teria rendido a 100% do CDI. 98.0 = "rendeu 98% do CDI". None sem série de CDI ou sem dinheiro aplicado.
    `ativo_id=None` olha o portfólio todo."""
    ctx = _Contexto(conn, hoje or fim)
    ativos = ctx.ativos if ativo_id is None else [obter(conn, ativo_id)]
    rend, equivalente = _rendimento_de(ctx, ativos, inicio, fim)
    return rend.centavos / equivalente * 100 if equivalente > 0 else None


# --- Patrimônio, meta de aporte e previsão ----------------------------------------------------------------


def _patrimonio(conn: sqlite3.Connection, hoje: date, ctx: _Contexto) -> dict:
    investimentos, caixinhas = ctx.total(hoje, "outros"), ctx.total(hoje, "caixinhas")
    return {
        "investimentos": investimentos, "caixinhas": caixinhas, "renda_variavel": ctx.carteira.valor(hoje),
        "total": investimentos + caixinhas,
    }


def patrimonio(conn: sqlite3.Connection, hoje: date) -> dict:
    """O que você tem guardado hoje: investimentos + caixinhas. Conta corrente e dívida do cartão ficam de fora.

    Chaves: `investimentos` (renda fixa, Tesouro, ações e FIIs), `caixinhas`, `renda_variavel` (a parte de ações e
    FIIs, já dentro de `investimentos`), `total` (centavos).
    """
    return _patrimonio(conn, hoje, _Contexto(conn, hoje))


PREF_META_APORTE = "meta_aporte_mensal"  # centavos, como texto
MARGEM_META = 5000  # a até R$ 50 da meta (pra mais ou pra menos) conta como batida


def meta_aporte(conn: sqlite3.Connection) -> int | None:
    """A meta de aporte mensal em centavos, ou None se não há."""
    texto = db.ler_preferencia(conn, PREF_META_APORTE)
    return int(texto) if texto else None


def definir_meta_aporte(conn: sqlite3.Connection, centavos: int | None) -> None:
    """Grava a meta de aporte mensal. None ou 0 apaga."""
    if centavos is not None and centavos < 0:
        raise ValueError("A meta de aporte não pode ser negativa.")
    db.gravar_preferencia(conn, PREF_META_APORTE, str(centavos) if centavos else None)


def aportes_do_mes(conn: sqlite3.Connection, ano: int, mes: int) -> list[tuple[str, int]]:
    """(nome do ativo, centavos líquidos) de cada ativo com movimento no mês, o maior primeiro, sem zeros.

    Aporte soma e resgate desconta (saldo real não conta). Caixinhas: os lançamentos da conta "Caixinhas". Ações e FIIs:
    o dinheiro que saiu (compra, com taxas) ou voltou (venda, sem taxas) da conta; histórico importado não conta."""
    mes_texto = f"{ano:04d}-{mes:02d}"
    por_ativo: dict[str, int] = {}

    def somar(nome: str, centavos: int) -> None:
        por_ativo[nome] = por_ativo.get(nome, 0) + centavos

    for r in conn.execute(
        "SELECT a.nome, m.tipo, m.valor_centavos FROM movimentacoes_ativos m JOIN ativos a ON a.id = m.ativo_id "
        "WHERE m.tipo IN ('aporte', 'resgate') AND substr(m.data, 1, 7) = ?",
        (mes_texto,),
    ):
        somar(r["nome"], r["valor_centavos"] if r["tipo"] == "aporte" else -r["valor_centavos"])
    for r in conn.execute(
        "SELECT a.nome, o.tipo, o.valor_centavos, o.taxas_centavos FROM operacoes_rv o JOIN ativos a ON a.id = o.ativo_id "
        "WHERE o.tipo IN ('compra', 'venda') AND o.afeta_conta = 1 AND substr(o.data, 1, 7) = ?",
        (mes_texto,),
    ):
        somar(r["nome"], r["valor_centavos"] + r["taxas_centavos"] if r["tipo"] == "compra" else -(r["valor_centavos"] - r["taxas_centavos"]))
    caixinhas = conn.execute(
        "SELECT COALESCE(SUM(l.valor_centavos), 0) FROM lancamentos l JOIN contas c ON c.id = l.conta_id "
        "WHERE c.nome = ? AND substr(l.data, 1, 7) = ?",
        (metas.NOME_CAIXINHAS, mes_texto),
    ).fetchone()[0]
    somar(NOME_CAIXINHAS, caixinhas)
    return sorted(((n, v) for n, v in por_ativo.items() if v), key=lambda item: -item[1])


@dataclass(frozen=True)
class ProgressoAporte:
    """O mês de hoje contra a meta. `situacao`: "sem_meta", "faltando", "batida" (a até MARGEM_META da meta) ou
    "passou". `falta` só é maior que zero em "faltando"."""

    meta: int | None
    aportado: int
    por_ativo: list[tuple[str, int]]
    situacao: str
    falta: int


def progresso_do_mes(conn: sqlite3.Connection, hoje: date) -> ProgressoAporte:
    por_ativo = aportes_do_mes(conn, hoje.year, hoje.month)
    aportado, meta = sum(v for _n, v in por_ativo), meta_aporte(conn)
    if meta is None:
        return ProgressoAporte(None, aportado, por_ativo, "sem_meta", 0)
    if aportado < meta - MARGEM_META:
        return ProgressoAporte(meta, aportado, por_ativo, "faltando", meta - aportado)
    return ProgressoAporte(meta, aportado, por_ativo, "batida" if aportado <= meta + MARGEM_META else "passou", 0)


def previsao_fim_do_ano(conn: sqlite3.Connection, hoje: date) -> dict:
    """Patrimônio guardado em 31/12 se a meta de aporte for cumprida: o de hoje + o que falta da meta neste mês + a
    meta nos meses seguintes + o rendimento (da carteira de hoje, e dos aportes futuros como se rendessem igual) + os
    proventos estimados. Sem meta, só o rendimento e os proventos. Conta corrente e cartão não entram.

    Chaves: `alvo`, `patrimonio_hoje` (igual a `patrimonio()["total"]`), `meta`, `aportes_planejados`,
    `rendimento_projetado` (da carteira de hoje), `rendimento_dos_aportes`, `proventos_projetados` (ações e FIIs: o
    histórico de proventos por cota repetido até 31/12; os preços ficam como estão hoje), `total` (centavos, exceto
    `alvo` e `meta`).
    """
    return _previsao(hoje, _Contexto(conn, hoje, fim=date(hoje.year, 12, 31)))


def _previsao(hoje: date, ctx: "_Contexto") -> dict:
    """Única montagem da previsão de 31/12 (usada por `previsao_fim_do_ano` e por `resumo`)."""
    return _projetar(hoje, date(hoje.year, 12, 31), ctx)


def _projetar(hoje: date, alvo: date, ctx: _Contexto) -> dict:
    """O patrimônio guardado em `alvo` (hoje ou depois): ver `previsao_fim_do_ano`."""
    progresso = ctx.progresso
    patrimonio_hoje = ctx.total(hoje)
    rendimento_carteira = ctx.total(alvo) - patrimonio_hoje
    dias = (alvo - hoje).days
    # a carteira de hoje rende essa taxa por dia; os aportes futuros rendem igual a partir do dia em que entram
    taxa = (ctx.total(alvo) / patrimonio_hoje) ** (1 / dias) - 1 if patrimonio_hoje > 0 and dias > 0 else 0.0
    aportes = [(hoje, progresso.falta)] if progresso.meta is not None else []
    if progresso.meta is not None:
        m = hoje.year * 12 + hoje.month
        aportes += [
            (date((m + k - 1) // 12, (m + k - 1) % 12 + 1, 1), progresso.meta) for k in range(1, alvo.year * 12 + alvo.month - m + 1)
        ]
    planejados = sum(valor for _d, valor in aportes)
    rendimento_aportes = round(sum(valor * ((1 + taxa) ** (alvo - d).days - 1) for d, valor in aportes))
    proventos = ctx.proventos_ate(alvo)
    return {
        "alvo": alvo, "patrimonio_hoje": patrimonio_hoje, "meta": progresso.meta, "aportes_planejados": planejados,
        "rendimento_projetado": rendimento_carteira, "rendimento_dos_aportes": rendimento_aportes,
        "proventos_projetados": proventos,
        "total": patrimonio_hoje + planejados + rendimento_carteira + rendimento_aportes + proventos,
    }


def serie_patrimonio_no_ano(conn: sqlite3.Connection, hoje: date) -> pd.DataFrame:
    """O patrimônio guardado (investimentos + caixinhas) ao longo do ano: um ponto por semana desde 1º de janeiro até
    hoje (o último ponto é hoje) e a projeção, ao fim de cada mês, até 31/12, com a mesma regra da previsão (os aportes
    planejados até aquela data e o rendimento). Colunas: data, centavos, projetado (bool)."""
    fim_do_ano = date(hoje.year, 12, 31)
    ctx = _Contexto(conn, hoje, fim=fim_do_ano)
    passadas, d = [], date(hoje.year, 1, 1)
    while d < hoje:
        passadas.append(d)
        d += timedelta(days=7)
    linhas = [(d, ctx.total(d), False) for d in passadas] + [(hoje, _patrimonio(conn, hoje, ctx)["total"], False)]
    fins_de_mes = [date(hoje.year, m, calendar.monthrange(hoje.year, m)[1]) for m in range(hoje.month, 12)]
    # fim de mês colado em hoje só repetiria o ponto de hoje: o primeiro projetado fica a uma semana ou mais
    for d in [f for f in fins_de_mes if (f - hoje).days >= 7] + ([fim_do_ano] if hoje < fim_do_ano else []):
        linhas.append((d, _projetar(hoje, d, ctx)["total"], True))
    df = pd.DataFrame(linhas, columns=["data", "centavos", "projetado"])
    df["data"] = pd.to_datetime(df["data"])
    return df.astype({"centavos": "int64", "projetado": "bool"})


@dataclass(frozen=True)
class ResumoInvestimentos:
    """Os números do topo da aba. `pct_cdi` é o rendimento do ano comparado ao CDI (None sem dados)."""

    patrimonio: dict
    progresso: ProgressoAporte
    rendimento_ano: Rendimento
    pct_cdi: float | None
    previsao: dict


def resumo(conn: sqlite3.Connection, hoje: date) -> ResumoInvestimentos:
    fim_do_ano = date(hoje.year, 12, 31)
    ctx = _Contexto(conn, hoje, fim=fim_do_ano)
    rend, equivalente = _rendimento_de(ctx, ctx.ativos, date(hoje.year, 1, 1), hoje)
    return ResumoInvestimentos(
        patrimonio=_patrimonio(conn, hoje, ctx),
        progresso=ctx.progresso,
        rendimento_ano=rend,
        pct_cdi=rend.centavos / equivalente * 100 if equivalente > 0 else None,
        previsao=_previsao(hoje, ctx),
    )
