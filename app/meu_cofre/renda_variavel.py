"""Renda variável (docs/design/telas.md, F9): ações e FIIs com ticker, preço médio e proventos.

**Operações.** Compras, vendas e proventos (dividendo, JCP, rendimento) ficam em `operacoes_rv`. Com `conta_id`, uma
compra é uma **transferência** da conta pra "Investimentos" (valor + taxas) e uma venda é a volta (valor − taxas); um
provento vira entrada na conta (categoria "Rendimentos"). Sem conta (`afeta_conta = 0`) só a posição muda: é o
histórico importado da B3, de dinheiro que já passou por fora do app.

**Preço médio.** Média ponderada das compras, taxas incluídas. Venda não muda o preço médio (só a quantidade e o custo,
na mesma proporção); zerar a posição zera o preço médio e a recompra começa do zero. O lucro das vendas (líquido de
taxas, sem IR) fica em `realizado`. Dentro do mesmo dia valem as compras, depois os eventos e por fim as vendas.

**Eventos corporativos** (só importados da B3, nunca mexem em conta): `bonificacao` soma cotas e o custo atribuído
(`valor_centavos`, 0 se a empresa não atribuiu: o preço médio cai); `desdobramento` e `grupamento` somam a
`quantidade` **com sinal** (entrou +, saiu −) sem mudar o custo, então o preço médio se ajusta; `fracao` tira cotas ao
preço médio, como uma venda, mas sem lucro nem prejuízo.

**Cotação.** Última cotação guardada em `cotacoes` até a data (ver `cotacoes.py`). Sem nenhuma, a posição não tem valor
atual nem resultado, e o patrimônio usa o custo (pra não despencar só porque a rede falhou).

**Proventos.** `estimar_proventos` repete, nos próximos 12 meses, o que cada cota pagou nos 12 anteriores
(`proventos_ref`) × a quantidade de hoje. Sem histórico público, usa o que o próprio usuário recebeu (cada provento ÷
as cotas que tinha na data). É uma **projeção, não uma garantia**. O DY de 12 meses é o que o usuário
recebeu nesse período ÷ o valor atual da posição.

Este módulo e `investimentos.py` se importam: aqui `investimentos` só é usado dentro de funções, nunca ao importar.

Tudo que depende do dia recebe `hoje` (nada de `date.today()` aqui). Dinheiro em centavos; quantidades em cotas.
"""

import sqlite3
import uuid
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

import pandas as pd

from meu_cofre import cotacoes, investimentos
from meu_cofre.lancamentos import somar_meses

TIPOS_PROVENTO = ("dividendo", "jcp", "rendimento")
NOMES_PROVENTO = {"dividendo": "Dividendo", "jcp": "JCP", "rendimento": "Rendimento"}
NOMES_EVENTO = {"bonificacao": "Bonificação", "desdobramento": "Desdobramento", "grupamento": "Grupamento", "fracao": "Fração"}
CATEGORIA_PROVENTOS = "Rendimentos"
_EPS = 1e-9  # quantidade abaixo disso é zero (resto de ponto flutuante)


# --- Operações gravadas -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Operacao:
    id: int
    ativo_id: int
    tipo: str
    data: date
    quantidade: float | None
    preco_centavos: int | None
    valor_centavos: int
    taxas_centavos: int
    afeta_conta: bool
    origem: str


def _operacao_da_linha(r: sqlite3.Row) -> Operacao:
    return Operacao(
        id=r["id"], ativo_id=r["ativo_id"], tipo=r["tipo"], data=date.fromisoformat(r["data"]), quantidade=r["quantidade"],
        preco_centavos=r["preco_centavos"], valor_centavos=r["valor_centavos"], taxas_centavos=r["taxas_centavos"],
        afeta_conta=bool(r["afeta_conta"]), origem=r["origem"],
    )


def operacoes(conn: sqlite3.Connection, ativo_id: int, ate: date | None = None) -> list[Operacao]:
    """As operações do ativo (até `ate`, inclusive), da mais antiga pra mais nova."""
    sql = "SELECT * FROM operacoes_rv WHERE ativo_id = ?" + (" AND data <= ?" if ate else "") + " ORDER BY data, id"
    args = (ativo_id, ate.isoformat()) if ate else (ativo_id,)
    return [_operacao_da_linha(r) for r in conn.execute(sql, args)]


def _ordenar(ops: list[Operacao]) -> list[Operacao]:
    """Por data; no mesmo dia as compras, os eventos e só então as vendas (vender o que se comprou no dia nunca estoura)."""
    return sorted(ops, key=lambda o: (o.data, 0 if o.tipo == "compra" else 2 if o.tipo == "venda" else 1, o.id))


# --- Preço médio e custo (percorrendo as operações) ---------------------------------------------------


@dataclass
class _Estado:
    quantidade: float = 0.0
    custo: float = 0.0  # centavos, taxas das compras incluídas
    realizado: float = 0.0  # lucro das vendas, centavos
    minimo: float = 0.0  # a menor quantidade vista (negativa = vendeu mais do que tinha)

    @property
    def preco_medio(self) -> float | None:
        return self.custo / self.quantidade if self.quantidade > _EPS else None


def _estado(ops: list[Operacao]) -> _Estado:
    """Aplica compras e vendas em ordem. Venda além da posição (histórico incompleto) vende só o que há."""
    e = _Estado()
    for o in _ordenar(ops):
        if o.tipo == "compra":
            e.quantidade += o.quantidade
            e.custo += o.valor_centavos + o.taxas_centavos
        elif o.tipo == "bonificacao":
            e.quantidade += o.quantidade
            e.custo += o.valor_centavos
        elif o.tipo in ("desdobramento", "grupamento"):
            e.quantidade += o.quantidade  # com sinal; o custo fica
            e.minimo = min(e.minimo, e.quantidade)
            if e.quantidade <= _EPS:
                e.quantidade, e.custo = 0.0, 0.0
        elif o.tipo == "fracao":
            e.minimo = min(e.minimo, e.quantidade - o.quantidade)
            tirada = min(o.quantidade, e.quantidade)
            if e.quantidade > _EPS:
                e.custo -= e.custo * tirada / e.quantidade
                e.quantidade -= tirada
            if e.quantidade <= _EPS:
                e.quantidade, e.custo = 0.0, 0.0
        elif o.tipo == "venda":
            liquido = o.valor_centavos - o.taxas_centavos
            e.minimo = min(e.minimo, e.quantidade - o.quantidade)
            if e.quantidade <= _EPS:
                continue  # nada a vender: o lucro não é calculável
            vendida = min(o.quantidade, e.quantidade)
            custo_da_venda = e.custo * vendida / e.quantidade
            e.realizado += liquido * vendida / o.quantidade - custo_da_venda
            e.custo -= custo_da_venda
            e.quantidade -= vendida
            if e.quantidade <= _EPS:
                e.quantidade, e.custo = 0.0, 0.0  # zerou: o preço médio recomeça na próxima compra
    return e


def _recebido(ops: list[Operacao], inicio: date | None, fim: date) -> int:
    """Proventos recebidos com data em (`inicio`, `fim`]; sem `inicio`, desde sempre."""
    return sum(
        o.valor_centavos for o in ops if o.tipo in TIPOS_PROVENTO and o.data <= fim and (inicio is None or o.data > inicio)
    )


# --- Validação e registro -----------------------------------------------------------------------------


def _ativo_rv(conn: sqlite3.Connection, ativo_id: int, movimenta: bool = True):
    """O ativo, se for ação/FII. `movimenta`: exige que não esteja encerrado."""
    ativo = investimentos.obter(conn, ativo_id)
    if ativo.tipo not in investimentos.TIPOS_RV:
        raise ValueError("Isso vale só pra ações e FIIs.")
    if movimenta and not ativo.ativo:
        raise ValueError("Este ativo foi encerrado. Reative-o pra movimentar.")
    return ativo


def _quantidade_valida(quantidade: float) -> float:
    if quantidade is None or quantidade <= 0:
        raise ValueError("A quantidade precisa ser maior que zero.")
    return float(quantidade)


def _preco_e_taxas(preco: int, taxas: int) -> None:
    if preco is None or preco <= 0:
        raise ValueError("O preço precisa ser maior que zero.")
    if taxas is None or taxas < 0:
        raise ValueError("As taxas não podem ser negativas.")


def _lancar_transferencia(
    conn: sqlite3.Connection, saida_id: int, entrada_id: int, valor: int, data: date, descricao: str
) -> str:
    transferencia_id = uuid.uuid4().hex
    categoria_id = investimentos._categoria_transferencia(conn)
    for conta_id, sinal in ((saida_id, -1), (entrada_id, 1)):
        conn.execute(
            """
            INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem, transferencia_id)
            VALUES (?, ?, ?, ?, ?, 'transferencia', 'manual', ?)
            """,
            (conta_id, data.isoformat(), sinal * valor, descricao, categoria_id, transferencia_id),
        )
    return transferencia_id


def _qtd(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def comprar(
    conn: sqlite3.Connection, ativo_id: int, data: date, quantidade: float, preco: int, taxas: int = 0, conta_id: int | None = None
) -> int:
    """Registra uma compra. `preco` = centavos por cota; `taxas` = centavos (corretagem, emolumentos).

    Com `conta_id`, tira da conta (valor + taxas) por uma transferência pra "Investimentos"; sem ela só a posição muda.
    Devolve o id da operação.
    """
    ativo = _ativo_rv(conn, ativo_id)
    quantidade = _quantidade_valida(quantidade)
    _preco_e_taxas(preco, taxas)
    valor = round(quantidade * preco)
    if conta_id is not None:
        investimentos._conta_do_dia_a_dia(conn, conta_id, "de onde sai o dinheiro")
    with conn:
        transferencia_id = (
            _lancar_transferencia(
                conn, conta_id, investimentos.conta_investimentos(conn), valor + taxas, data,
                f"Compra: {ativo.ticker} ({_qtd(quantidade)})",
            )
            if conta_id is not None
            else None
        )
        return conn.execute(
            """
            INSERT INTO operacoes_rv
                (ativo_id, tipo, data, quantidade, preco_centavos, valor_centavos, taxas_centavos, afeta_conta, transferencia_id)
            VALUES (?, 'compra', ?, ?, ?, ?, ?, ?, ?)
            """,
            (ativo_id, data.isoformat(), quantidade, preco, valor, taxas, int(conta_id is not None), transferencia_id),
        ).lastrowid


def vender(
    conn: sqlite3.Connection, ativo_id: int, data: date, quantidade: float, preco: int, taxas: int = 0, conta_id: int | None = None
) -> int:
    """Registra uma venda (mesmos parâmetros de `comprar`). Não pode passar do que se tem na data nem deixar uma venda
    posterior sem cotas. Com `conta_id`, o líquido (valor − taxas) volta pra conta. Devolve o id da operação."""
    ativo = _ativo_rv(conn, ativo_id)
    quantidade = _quantidade_valida(quantidade)
    _preco_e_taxas(preco, taxas)
    valor = round(quantidade * preco)
    if valor - taxas <= 0:
        raise ValueError("As taxas não podem ser maiores que o valor da venda.")
    teste = Operacao(0, ativo_id, "venda", data, quantidade, preco, valor, taxas, conta_id is not None, "manual")
    if _estado(operacoes(conn, ativo_id) + [teste]).minimo < -_EPS:
        tem = _estado(operacoes(conn, ativo_id, data)).quantidade
        raise ValueError(f"Você só tem {_qtd(tem)} cotas de {ativo.ticker} em {data:%d/%m/%Y}.")
    if conta_id is not None:
        investimentos._conta_do_dia_a_dia(conn, conta_id, "pra onde vai o dinheiro")
    with conn:
        transferencia_id = (
            _lancar_transferencia(
                conn, investimentos.conta_investimentos(conn), conta_id, valor - taxas, data,
                f"Venda: {ativo.ticker} ({_qtd(quantidade)})",
            )
            if conta_id is not None
            else None
        )
        return conn.execute(
            """
            INSERT INTO operacoes_rv
                (ativo_id, tipo, data, quantidade, preco_centavos, valor_centavos, taxas_centavos, afeta_conta, transferencia_id)
            VALUES (?, 'venda', ?, ?, ?, ?, ?, ?, ?)
            """,
            (ativo_id, data.isoformat(), quantidade, preco, valor, taxas, int(conta_id is not None), transferencia_id),
        ).lastrowid


def _categoria_proventos(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM categorias WHERE nome = ?", (CATEGORIA_PROVENTOS,)).fetchone()
    if linha:
        return linha["id"]
    return conn.execute("INSERT INTO categorias (nome, tipo) VALUES (?, 'receita')", (CATEGORIA_PROVENTOS,)).lastrowid


def registrar_provento(
    conn: sqlite3.Connection, ativo_id: int, tipo: str, data: date, valor: int, conta_id: int | None = None
) -> int:
    """Registra um provento recebido. `tipo`: "dividendo", "jcp" ou "rendimento"; `valor` = centavos recebidos.

    Com `conta_id`, entra na conta (categoria "Rendimentos", ligado à operação); sem ela só entra no histórico de
    proventos. Devolve o id da operação."""
    ativo = _ativo_rv(conn, ativo_id)
    if tipo not in TIPOS_PROVENTO:
        raise ValueError(f"Tipo de provento desconhecido: {tipo}")
    if valor is None or valor <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    if conta_id is not None:
        investimentos._conta_do_dia_a_dia(conn, conta_id, "onde o dinheiro entrou")
    with conn:
        lancamento_id = (
            conn.execute(
                """
                INSERT INTO lancamentos (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem)
                VALUES (?, ?, ?, ?, ?, 'pix', 'manual')
                """,
                (conta_id, data.isoformat(), valor, f"{NOMES_PROVENTO[tipo]}: {ativo.ticker}", _categoria_proventos(conn)),
            ).lastrowid
            if conta_id is not None
            else None
        )
        return conn.execute(
            """
            INSERT INTO operacoes_rv (ativo_id, tipo, data, valor_centavos, afeta_conta, lancamento_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ativo_id, tipo, data.isoformat(), valor, int(conta_id is not None), lancamento_id),
        ).lastrowid


# --- Corrigir e apagar ----------------------------------------------------------------------------------


def _operacao_gravada(conn: sqlite3.Connection, operacao_id: int) -> tuple[Operacao, sqlite3.Row]:
    linha = conn.execute("SELECT * FROM operacoes_rv WHERE id = ?", (operacao_id,)).fetchone()
    if linha is None:
        raise ValueError("Essa operação não existe mais.")
    return _operacao_da_linha(linha), linha


def _conferir_posicao(conn: sqlite3.Connection, ativo_id: int, antes: Operacao, depois: Operacao | None) -> None:
    """Troca `antes` por `depois` (None = apagar) e barra se alguma venda passar a vender mais do que havia."""
    todas = operacoes(conn, ativo_id)
    novas = [o for o in todas if o.id != antes.id] + ([depois] if depois else [])
    if _estado(novas).minimo < min(_estado(todas).minimo, 0.0) - _EPS:
        raise ValueError("Isso deixaria uma venda vendendo mais cotas do que você tinha. Ajuste ou exclua a venda primeiro.")


def editar_operacao(
    conn: sqlite3.Connection, operacao_id: int, *, data: date, quantidade: float | None = None, preco: int | None = None,
    taxas: int = 0, valor: int | None = None,
) -> None:
    """Corrige uma operação. Compra e venda: `quantidade`, `preco` (centavos por cota) e `taxas`; provento: `valor`.
    O tipo não muda. Evento corporativo (bonificação, desdobramento...) não se edita: exclui e importa de novo. Com `afeta_conta`, a transferência (ou a entrada do provento) acompanha. O preço médio e a posição
    saem das operações, então se acertam sozinhos. Não deixa uma venda sem as cotas que ela vende."""
    op, linha = _operacao_gravada(conn, operacao_id)
    if op.tipo in NOMES_EVENTO:
        raise ValueError(f"{NOMES_EVENTO[op.tipo]} não se edita. Exclua e importe de novo, se precisar.")
    ticker = investimentos.obter(conn, op.ativo_id).ticker
    if op.tipo in TIPOS_PROVENTO:
        if valor is None or valor <= 0:
            raise ValueError("O valor precisa ser maior que zero.")
        novo = Operacao(op.id, op.ativo_id, op.tipo, data, None, None, valor, 0, op.afeta_conta, op.origem)
    else:
        quantidade = _quantidade_valida(quantidade)
        _preco_e_taxas(preco, taxas)
        valor = round(quantidade * preco)
        if op.tipo == "venda" and valor - taxas <= 0:
            raise ValueError("As taxas não podem ser maiores que o valor da venda.")
        novo = Operacao(op.id, op.ativo_id, op.tipo, data, quantidade, preco, valor, taxas, op.afeta_conta, op.origem)
        _conferir_posicao(conn, op.ativo_id, op, novo)
    with conn:
        conn.execute(
            "UPDATE operacoes_rv SET data = ?, quantidade = ?, preco_centavos = ?, valor_centavos = ?, taxas_centavos = ? WHERE id = ?",
            (data.isoformat(), novo.quantidade, novo.preco_centavos, novo.valor_centavos, novo.taxas_centavos, operacao_id),
        )
        if op.tipo in TIPOS_PROVENTO and linha["lancamento_id"]:
            conn.execute("UPDATE lancamentos SET data = ?, valor_centavos = ? WHERE id = ?", (data.isoformat(), valor, linha["lancamento_id"]))
        elif op.tipo not in TIPOS_PROVENTO and linha["transferencia_id"]:
            total = valor + taxas if op.tipo == "compra" else valor - taxas
            nome = "Compra" if op.tipo == "compra" else "Venda"
            conn.execute(
                "UPDATE lancamentos SET data = ?, descricao = ?, valor_centavos = CASE WHEN valor_centavos > 0 THEN ? ELSE -? END "
                "WHERE transferencia_id = ?",
                (data.isoformat(), f"{nome}: {ticker} ({_qtd(quantidade)})", total, total, linha["transferencia_id"]),
            )


def excluir_operacao(conn: sqlite3.Connection, operacao_id: int) -> None:
    """Apaga a operação e o que ela lançou nas contas (os dois lados da transferência, ou a entrada do provento)."""
    op, linha = _operacao_gravada(conn, operacao_id)
    if op.tipo not in TIPOS_PROVENTO:
        _conferir_posicao(conn, op.ativo_id, op, None)
    with conn:
        conn.execute("DELETE FROM operacoes_rv WHERE id = ?", (operacao_id,))
        if linha["transferencia_id"]:
            conn.execute("DELETE FROM lancamentos WHERE transferencia_id = ?", (linha["transferencia_id"],))
        if linha["lancamento_id"]:
            conn.execute("DELETE FROM lancamentos WHERE id = ?", (linha["lancamento_id"],))


# --- Posição --------------------------------------------------------------------------------------------


def _posicao(ativo, ops: list[Operacao], cotacao: tuple[int, date] | None, hoje: date) -> dict:
    ops = [o for o in ops if o.data <= hoje]
    e = _estado(ops)
    quantidade = e.quantidade if e.quantidade > _EPS else 0.0
    custo = round(e.custo)
    cot, cot_data = cotacao if cotacao else (None, None)
    valor_atual = round(quantidade * cot) if cot is not None else None
    resultado = valor_atual - custo if valor_atual is not None and quantidade else None
    proventos_12m = _recebido(ops, somar_meses(hoje, -12), hoje)
    return {
        "ativo": ativo,
        "quantidade": quantidade,
        "preco_medio": e.preco_medio,  # centavos por cota (fração); None sem posição
        "custo": custo,
        "cotacao": cot,
        "cotacao_data": cot_data,
        "valor_atual": valor_atual,
        "resultado": resultado,
        "resultado_pct": resultado / custo * 100 if resultado is not None and custo > 0 else None,
        "realizado": round(e.realizado),
        "proventos_12m": proventos_12m,
        "proventos_total": _recebido(ops, None, hoje),
        "dy_12m": proventos_12m / valor_atual * 100 if valor_atual and quantidade else None,
    }


def posicao(conn: sqlite3.Connection, ativo_id: int, hoje: date) -> dict:
    """Os números do ativo hoje.

    Chaves: `ativo`, `quantidade`, `preco_medio` (centavos por cota, fração; None sem posição), `custo`, `cotacao`
    e `cotacao_data` (None sem cotação), `valor_atual`, `resultado` e `resultado_pct` (None sem cotação ou sem
    posição), `realizado` (lucro das vendas), `proventos_12m`, `proventos_total` e `dy_12m` (% do valor atual; None
    sem valor).
    """
    ativo = _ativo_rv(conn, ativo_id, movimenta=False)
    return _posicao(ativo, operacoes(conn, ativo_id, hoje), cotacoes.ultima(conn, ativo.ticker, hoje), hoje)


def listar(conn: sqlite3.Connection, hoje: date, incluir_encerrados: bool = False) -> list[dict]:
    """A posição de cada ação e FII (ordem de cadastro). Encerrados só com `incluir_encerrados`."""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM ativos WHERE tipo IN ('acao', 'fii') AND ativo >= ? ORDER BY id", (0 if incluir_encerrados else 1,)
        )
    ]
    return [posicao(conn, i, hoje) for i in ids]


# --- Valor da carteira (patrimônio) -----------------------------------------------------------------------


class Carteira:
    """Ações e FIIs na memória, pra avaliar a carteira em várias datas sem voltar ao banco (patrimônio e gráfico).

    O valor de um ativo numa data é a quantidade nela × a cotação mais recente até ela; sem nenhuma cotação, o custo.
    """

    def __init__(self, conn: sqlite3.Connection, hoje: date):
        self.hoje = hoje
        linhas = conn.execute("SELECT id, ticker FROM ativos WHERE tipo IN ('acao', 'fii')").fetchall()
        self._ops = {r["id"]: operacoes(conn, r["id"]) for r in linhas}
        self._ticker = {r["id"]: r["ticker"] for r in linhas}
        self._precos: dict[str, tuple[list[date], list[int]]] = {}
        for ticker in {r["ticker"] for r in linhas if r["ticker"]}:
            historico = cotacoes.historico(conn, ticker)
            self._precos[ticker] = ([d for d, _p in historico], [p for _d, p in historico])

    def __bool__(self) -> bool:
        return any(self._ops.values())

    def _preco_ate(self, ticker: str | None, dia: date) -> int | None:
        datas, precos = self._precos.get(ticker, ([], []))
        i = bisect_right(datas, dia)
        return precos[i - 1] if i else None

    def valor(self, alvo: date) -> int:
        """O valor da carteira em `alvo` (centavos). Datas depois de hoje valem o mesmo que hoje: sem suposição de alta."""
        alvo = min(alvo, self.hoje)
        total = 0.0
        for ativo_id, ops in self._ops.items():
            e = _estado([o for o in ops if o.data <= alvo])
            if e.quantidade <= _EPS:
                continue
            preco = self._preco_ate(self._ticker[ativo_id], alvo)
            total += e.quantidade * preco if preco is not None else e.custo
        return round(total)

    def quantidades(self) -> dict[int, float]:
        """A quantidade de cada ativo hoje (só os que têm posição)."""
        qs = {i: _estado([o for o in ops if o.data <= self.hoje]).quantidade for i, ops in self._ops.items()}
        return {i: q for i, q in qs.items() if q > _EPS}


# --- Proventos: recebidos e estimativa -------------------------------------------------------------------------


def proventos_recebidos_no_ano(conn: sqlite3.Connection, hoje: date) -> int:
    """Tudo que ações e FIIs pagaram de 1º de janeiro até hoje (centavos)."""
    return conn.execute(
        """
        SELECT COALESCE(SUM(valor_centavos), 0) FROM operacoes_rv
        WHERE tipo IN ('dividendo', 'jcp', 'rendimento') AND data >= ? AND data <= ?
        """,
        (date(hoje.year, 1, 1).isoformat(), hoje.isoformat()),
    ).fetchone()[0]


def _um_ano_depois(d: date, anos: int = 1) -> date:
    try:
        return d.replace(year=d.year + anos)
    except ValueError:  # 29/02
        return d.replace(year=d.year + anos, day=28)


def _por_cota_do_historico(conn: sqlite3.Connection, ativo_id: int, ticker: str, hoje: date) -> list[tuple[date, float]]:
    """(data, reais por cota) dos últimos 12 meses: o histórico público (`proventos_ref`) ou, se ele não existe, o que o
    próprio usuário recebeu (cada provento ÷ as cotas que ele tinha na data)."""
    janela = lambda d: somar_meses(hoje, -12) < d <= hoje  # noqa: E731
    publico = [(d, v) for d, v in cotacoes.proventos_por_cota(conn, ticker) if janela(d)]
    if publico:
        return publico
    ops = operacoes(conn, ativo_id, hoje)
    proprio = []
    for o in ops:
        if o.tipo in TIPOS_PROVENTO and janela(o.data):
            cotas = _estado([x for x in ops if x.data <= o.data]).quantidade
            if cotas > _EPS:
                proprio.append((o.data, o.valor_centavos / cotas / 100))
    return proprio


def _datas_projetadas(
    conn: sqlite3.Connection, ativo_id: int, quantidade: float, hoje: date, meses: int
) -> list[tuple[date, int]]:
    """(data, centavos) de cada provento previsto nos próximos `meses`: o por cota dos 12 meses até hoje (ver
    `_por_cota_do_historico`), deslocado um ano (ou mais) pra frente, × `quantidade`."""
    ticker = conn.execute("SELECT ticker FROM ativos WHERE id = ?", (ativo_id,)).fetchone()["ticker"]
    if not ticker or quantidade <= _EPS:
        return []
    fim = somar_meses(hoje, meses)
    projetados = []
    for dia, por_cota in _por_cota_do_historico(conn, ativo_id, ticker, hoje):
        for anos in range(1, meses // 12 + 2):
            novo = _um_ano_depois(dia, anos)
            if hoje < novo <= fim:
                projetados.append((novo, round(quantidade * por_cota * 100)))
    return sorted(projetados)


def _meses_a_frente(hoje: date, meses: int) -> list[str]:
    """['2026-10', '2026-11', ...]: `meses` meses a partir do mês de hoje."""
    primeiro = hoje.replace(day=1)
    return [f"{d.year}-{d.month:02d}" for d in (somar_meses(primeiro, k) for k in range(meses))]


def _tabela_mensal(projetados: list[tuple[date, int]], hoje: date, meses: int) -> pd.DataFrame:
    totais = dict.fromkeys(_meses_a_frente(hoje, meses), 0)
    for dia, centavos in projetados:
        chave = f"{dia.year}-{dia.month:02d}"
        if chave in totais:
            totais[chave] += centavos
    return pd.DataFrame({"mes": list(totais), "centavos": list(totais.values())}).astype({"centavos": "int64"})


def estimar_proventos(conn: sqlite3.Connection, ativo_id: int, hoje: date, meses: int = 12) -> pd.DataFrame:
    """Quanto o ativo deve pagar nos próximos `meses` (a partir do mês de hoje), mês a mês: a quantidade de hoje × o
    que cada cota pagou nos 12 meses até hoje (`proventos_ref`), no mesmo mês do ano que vem. Colunas: `mes`
    ("AAAA-MM"), `centavos`. **Projeção pelo histórico, não garantia.** Sem histórico, tudo zero."""
    _ativo_rv(conn, ativo_id, movimenta=False)
    quantidade = posicao(conn, ativo_id, hoje)["quantidade"]
    return _tabela_mensal(_datas_projetadas(conn, ativo_id, quantidade, hoje, meses), hoje, meses)


def proventos_projetados(
    conn: sqlite3.Connection, hoje: date, meses: int = 12, carteira: Carteira | None = None
) -> list[tuple[date, int]]:
    """Os proventos previstos de todos os ativos ativos nos próximos `meses`, como (data, centavos) em ordem."""
    carteira = carteira or Carteira(conn, hoje)
    ativos = {r["id"] for r in conn.execute("SELECT id FROM ativos WHERE ativo = 1")}
    projetados = []
    for ativo_id, quantidade in carteira.quantidades().items():
        if ativo_id in ativos:
            projetados += _datas_projetadas(conn, ativo_id, quantidade, hoje, meses)
    return sorted(projetados)


def estimar_proventos_carteira(conn: sqlite3.Connection, hoje: date, meses: int = 12) -> pd.DataFrame:
    """O mesmo de `estimar_proventos`, somando todos os ativos ativos."""
    return _tabela_mensal(proventos_projetados(conn, hoje, meses), hoje, meses)


def proventos_ate(projetados: list[tuple[date, int]], alvo: date) -> int:
    """A soma dos proventos previstos com data até `alvo`."""
    return sum(c for d, c in projetados if d <= alvo)
