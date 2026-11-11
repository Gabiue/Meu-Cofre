"""Metas (docs/design/telas.md, F7): juntar dinheiro pra algo, com prazo ou com um valor por mês.

**Onde o dinheiro fica.** Na conta "Caixinhas" (tipo investimento). Guardar R$ X é uma **transferência** da conta
corrente (ou carteira) pra Caixinhas, e retirar é a volta; as duas pernas levam o `meta_id` da meta e o mesmo
`transferencia_id`. Por ser transferência, não conta como gasto nem como renda, e como as Caixinhas não são
"Em conta", o Livre não conta com o que está guardado.

**Contas de data** (sempre contando o mês atual como o primeiro mês):

- prazo -> mensal: meses = do mês de hoje até o mês do prazo, inclusive; mensal = restante ÷ meses, pra cima;
- mensal -> data: meses = restante ÷ mensal, pra cima; a data é o mês em que o último pedaço é guardado.

**Viabilidade.** A sobra média real do mês (ver `sobra_media`) é repartida pelas metas ativas em ordem de
prioridade: cada uma recebe o que precisa por mês (`necessario`) até a sobra acabar (`alocado`). Quem fica pronta
no mês pedido "cabe"; quem recebe só parte e passa do mês pedido é "parcial"; quem não recebe nada "nao_cabe". Meta que
já juntou o valor é "cumprida".

Tudo que depende do dia recebe `hoje` (nada de `date.today()` aqui). Dinheiro em centavos; datas de mês são
sempre o dia 1.
"""

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date

from meu_cofre import relatorios
from meu_cofre.lancamentos import dividir_em_parcelas, formatar_brl

NOME_CAIXINHAS = "Caixinhas"
CATEGORIA_GUARDAR = "Guardar em meta"
MODOS = ("prazo", "mensal")
SITUACOES = ("cabe", "parcial", "nao_cabe", "cumprida")
MESES_DA_SOBRA = 3  # a sobra média olha os últimos 3 meses completos
DIA_LIMITE_INICIO = 5  # o mês em que o uso começou só vale como "completo" se o 1º lançamento for até este dia
MESES_DA_RESERVA = 6  # reserva de emergência = 6 meses de gastos essenciais
MAX_PARCELAS = 24


# --- Contas de data e de valor (sem banco) -----------------------------------------------------


def _indice(d: date) -> int:
    return d.year * 12 + d.month - 1


def _mes(indice: int) -> date:
    return date(indice // 12, indice % 12 + 1, 1)


def _teto(a: int, b: int) -> int:
    """a ÷ b arredondado pra cima (a >= 0, b > 0)."""
    return -(-a // b)


def meses_ate_prazo(hoje: date, prazo: date) -> int:
    """Quantos meses há até o prazo, contando o mês de hoje e o do prazo. Prazo já passado vale 1 (é pra já)."""
    return max(1, _indice(prazo) - _indice(hoje) + 1)


def mensal_para_prazo(restante_centavos: int, hoje: date, prazo: date) -> int:
    """Quanto guardar por mês pra juntar o restante até o prazo (arredondado pra cima). Restante <= 0 dá 0."""
    if restante_centavos <= 0:
        return 0
    return _teto(restante_centavos, meses_ate_prazo(hoje, prazo))


def meses_para_mensal(restante_centavos: int, mensal_centavos: int) -> int:
    """Quantos meses guardando `mensal` pra juntar o restante (pra cima). Restante <= 0 dá 0."""
    if mensal_centavos <= 0:
        raise ValueError("O valor por mês precisa ser maior que zero.")
    return 0 if restante_centavos <= 0 else _teto(restante_centavos, mensal_centavos)


def data_para_mensal(restante_centavos: int, hoje: date, mensal_centavos: int) -> date:
    """Mês (dia 1) em que o restante fica pronto guardando `mensal` por mês, a partir do mês de hoje."""
    meses = meses_para_mensal(restante_centavos, mensal_centavos)
    return _mes(_indice(hoje) + max(meses, 1) - 1)


def meses_de_diferenca(de: date, ate: date) -> int:
    """Meses entre as duas datas (por mês do calendário; pode ser negativo)."""
    return _indice(ate) - _indice(de)


# --- Viabilidade: o cálculo puro ---------------------------------------------------------------


@dataclass(frozen=True)
class EntradaMeta:
    """O que o cálculo de viabilidade precisa de uma meta. A ordem da lista é a prioridade."""

    id: int | None
    nome: str
    restante: int
    modo: str
    prazo: date | None = None
    mensal_centavos: int | None = None


@dataclass(frozen=True)
class Viabilidade:
    """Como uma meta se sai com a sobra que lhe cabe.

    `necessario`: mensal pedido pela meta; `alocado`: o que a sobra realmente deu a ela; `pedida`: mês em que
    ela queria ficar pronta (o prazo, ou a data do mensal); `prevista`: mês em que fica pronta com o `alocado`
    (None se não recebe nada); `atraso_meses`: previsto − pedido (None se não recebe nada).
    """

    meta_id: int | None
    nome: str
    restante: int
    necessario: int
    alocado: int
    situacao: str  # cabe | parcial | nao_cabe | cumprida
    pedida: date
    prevista: date | None
    atraso_meses: int | None


def _viabilidade_de(e: EntradaMeta, hoje: date, disponivel: int) -> Viabilidade:
    mes_atual = _mes(_indice(hoje))
    if e.restante <= 0:
        return Viabilidade(e.id, e.nome, 0, 0, 0, "cumprida", mes_atual, mes_atual, 0)
    if e.modo == "prazo":
        necessario = mensal_para_prazo(e.restante, hoje, e.prazo)
        pedida = _mes(max(_indice(e.prazo), _indice(hoje)))
    else:
        necessario = e.mensal_centavos
        pedida = data_para_mensal(e.restante, hoje, necessario)
    alocado = min(necessario, max(disponivel, 0))
    if alocado == 0:
        return Viabilidade(e.id, e.nome, e.restante, necessario, 0, "nao_cabe", pedida, None, None)
    prevista = data_para_mensal(e.restante, hoje, alocado)
    atraso = max(0, meses_de_diferenca(pedida, prevista))
    situacao = "cabe" if atraso == 0 else "parcial"  # recebendo um pouco menos, mas ainda pronta no mês pedido, cabe
    return Viabilidade(e.id, e.nome, e.restante, necessario, alocado, situacao, pedida, prevista, atraso)


def calcular_viabilidade(entradas: list[EntradaMeta], hoje: date, sobra_centavos: int) -> list[Viabilidade]:
    """Reparte a sobra mensal pelas metas, na ordem recebida (a de cima é a mais prioritária)."""
    disponivel = max(sobra_centavos, 0)
    resultado = []
    for e in entradas:
        v = _viabilidade_de(e, hoje, disponivel)
        disponivel -= v.alocado
        resultado.append(v)
    return resultado


# --- Sobra e reserva (do histórico) ------------------------------------------------------------


def _meses_completos(conn: sqlite3.Connection, hoje: date) -> list[tuple[int, int]]:
    """Últimos meses fechados que contam pra média.

    O mês em que o uso do app começou fica de fora se o primeiro lançamento veio depois do dia
    DIA_LIMITE_INICIO: ele tem só alguns dias de gastos e distorceria a média (ex.: começou dia 27).
    """
    linha = conn.execute("SELECT MIN(data) FROM lancamentos").fetchone()
    if not linha or not linha[0]:
        return []
    primeira = date.fromisoformat(linha[0])
    meses = []
    for ano, mes in _meses_anteriores(hoje, MESES_DA_SOBRA):
        if (ano, mes) < (primeira.year, primeira.month):
            continue
        if (ano, mes) == (primeira.year, primeira.month) and primeira.day > DIA_LIMITE_INICIO:
            continue
        meses.append((ano, mes))
    return meses


def _meses_anteriores(hoje: date, n: int) -> list[tuple[int, int]]:
    """Os `n` meses completos antes do mês de hoje, do mais recente pro mais antigo."""
    base = _indice(hoje)
    return [((base - i) // 12, (base - i) % 12 + 1) for i in range(1, n + 1)]


def _media(valores: list[int]) -> int:
    return int(round(sum(valores) / len(valores)))


def sobra_media(conn: sqlite3.Connection, hoje: date) -> tuple[int, int, bool]:
    """Quanto sobra por mês, em média: (centavos, meses usados, é estimativa?). Pode ser negativo.

    Média de (entradas − saídas, sem transferências) dos últimos 3 meses completos que tenham lançamentos
    (o mês em que o uso começou só conta se começou até o dia DIA_LIMITE_INICIO).
    Sem nenhum mês completo: (0, 0, True). O mês em andamento não entra: multiplicá-lo como se fosse inteiro enganaria.
    """
    sobras = []
    for ano, mes in _meses_completos(conn, hoje):
        r = relatorios.resumo_mes(conn, ano, mes)
        if r.entradas or r.saidas:
            sobras.append(r.sobra)
    if sobras:
        return _media(sobras), len(sobras), False
    return 0, 0, True


def gasto_essencial_mensal(conn: sqlite3.Connection, hoje: date) -> tuple[int, int]:
    """Média mensal dos gastos essenciais: (centavos, meses usados). Mesma regra de meses da `sobra_media`."""
    gastos = []
    for ano, mes in _meses_completos(conn, hoje):
        r = relatorios.resumo_mes(conn, ano, mes)
        if r.entradas or r.saidas:
            gastos.append(relatorios.gastos_essenciais_mes(conn, ano, mes))
    if gastos:
        return _media(gastos), len(gastos)
    return 0, 0  # sem mês completo: alguns dias não dizem quanto custa um mês


def reserva_sugerida(conn: sqlite3.Connection, hoje: date) -> tuple[int, int, int]:
    """Reserva de emergência sugerida: (alvo, gasto essencial por mês, meses usados). Alvo = 6 × a média."""
    gasto, meses = gasto_essencial_mensal(conn, hoje)
    return MESES_DA_RESERVA * gasto, gasto, meses


# --- Contas e categoria do dinheiro guardado ---------------------------------------------------


def conta_caixinhas(conn: sqlite3.Connection) -> int:
    """Id da conta "Caixinhas" (tipo investimento); cria se ainda não existe."""
    linha = conn.execute("SELECT id FROM contas WHERE nome = ?", (NOME_CAIXINHAS,)).fetchone()
    if linha:
        return linha["id"]
    with conn:
        return conn.execute(
            "INSERT INTO contas (nome, tipo) VALUES (?, 'investimento')", (NOME_CAIXINHAS,)
        ).lastrowid


def _categoria_guardar(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM categorias WHERE nome = ?", (CATEGORIA_GUARDAR,)).fetchone()
    if linha:
        return linha["id"]
    with conn:
        return conn.execute(
            "INSERT INTO categorias (nome, tipo) VALUES (?, 'transferencia')", (CATEGORIA_GUARDAR,)
        ).lastrowid


# --- A meta no banco ---------------------------------------------------------------------------


@dataclass
class Meta:
    id: int
    nome: str
    valor_alvo_centavos: int
    modo: str
    prazo: date | None
    mensal_centavos: int | None
    prioridade: int
    reserva: bool
    criada_em: date
    concluida_em: date | None
    arquivada: bool
    guardado: int
    viabilidade: Viabilidade | None = field(default=None)  # só nas metas ativas

    @property
    def restante(self) -> int:
        return max(0, self.valor_alvo_centavos - self.guardado)

    @property
    def progresso(self) -> float:
        """Guardado ÷ alvo, de 0 a 1."""
        return min(1.0, max(0.0, self.guardado / self.valor_alvo_centavos))

    @property
    def estado(self) -> str:
        """"ativa", "concluida" ou "arquivada"."""
        if self.arquivada:
            return "arquivada"
        return "concluida" if self.concluida_em else "ativa"

    def entrada(self) -> EntradaMeta:
        return EntradaMeta(self.id, self.nome, self.restante, self.modo, self.prazo, self.mensal_centavos)


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def _guardados(conn: sqlite3.Connection) -> dict[int, int]:
    """Quanto cada meta tem guardado: o que entrou menos o que saiu da conta Caixinhas."""
    linhas = conn.execute(
        """
        SELECT l.meta_id, SUM(l.valor_centavos) AS total
        FROM lancamentos l JOIN contas ct ON ct.id = l.conta_id
        WHERE l.meta_id IS NOT NULL AND ct.tipo = 'investimento'
        GROUP BY l.meta_id
        """
    )
    return {r["meta_id"]: r["total"] for r in linhas}


def _meta_da_linha(r: sqlite3.Row, guardado: int) -> Meta:
    return Meta(
        id=r["id"],
        nome=r["nome"],
        valor_alvo_centavos=r["valor_alvo_centavos"],
        modo=r["modo"],
        prazo=_data(r["prazo"]),
        mensal_centavos=r["mensal_centavos"],
        prioridade=r["prioridade"],
        reserva=bool(r["reserva"]),
        criada_em=_data(r["criada_em"]),
        concluida_em=_data(r["concluida_em"]),
        arquivada=bool(r["arquivada"]),
        guardado=guardado,
    )


def _todas(conn: sqlite3.Connection) -> list[Meta]:
    guardados = _guardados(conn)
    linhas = conn.execute("SELECT * FROM metas ORDER BY prioridade, id")
    return [_meta_da_linha(r, guardados.get(r["id"], 0)) for r in linhas]


def _ativas(conn: sqlite3.Connection) -> list[Meta]:
    return [m for m in _todas(conn) if m.estado == "ativa"]


def obter(conn: sqlite3.Connection, meta_id: int, hoje: date | None = None) -> Meta:
    """Uma meta. Com `hoje` e ela ativa, já vem com a `viabilidade`. ValueError se não existe."""
    linha = conn.execute("SELECT * FROM metas WHERE id = ?", (meta_id,)).fetchone()
    if linha is None:
        raise ValueError(f"A meta {meta_id} não existe.")
    meta = _meta_da_linha(linha, _guardados(conn).get(meta_id, 0))
    if hoje is not None and meta.estado == "ativa":
        meta.viabilidade = next(v for v in viabilidade(conn, hoje) if v.meta_id == meta_id)
    return meta


def guardado(conn: sqlite3.Connection, meta_id: int) -> int:
    """Quanto a meta tem guardado hoje (centavos)."""
    return _guardados(conn).get(meta_id, 0)


def _movimentos(conn: sqlite3.Connection, meta_id: int) -> list[tuple[date, int]]:
    """(data, centavos) de cada entrada (+) e saída (−) da meta na conta Caixinhas, em ordem de data."""
    linhas = conn.execute(
        """
        SELECT l.data, l.valor_centavos FROM lancamentos l JOIN contas ct ON ct.id = l.conta_id
        WHERE l.meta_id = ? AND ct.tipo = 'investimento' ORDER BY l.data, l.id
        """,
        (meta_id,),
    )
    return [(date.fromisoformat(r["data"]), r["valor_centavos"]) for r in linhas]


def evolucao(conn: sqlite3.Connection, meta_id: int, hoje: date) -> list[tuple[date, int]]:
    """Quanto a meta tinha guardado no fim de cada mês, do mês em que foi criada (ou do 1º movimento, se antes)
    até o mês de hoje: [(1º dia do mês, centavos)]. O mês de hoje vale o saldo de agora."""
    movimentos = _movimentos(conn, meta_id)
    criada = obter(conn, meta_id).criada_em
    inicio = min([criada] + [d for d, _v in movimentos])
    pontos, saldo, i = [], 0, 0
    for indice in range(_indice(inicio), _indice(hoje) + 1):
        mes = _mes(indice)
        while i < len(movimentos) and _indice(movimentos[i][0]) <= indice:
            saldo += movimentos[i][1]
            i += 1
        pontos.append((mes, saldo))
    return pontos


def ritmo_medio(conn: sqlite3.Connection, meta_id: int, hoje: date) -> int | None:
    """Quanto entrou de fato na meta por mês (guardado − retirado), em média, nos últimos MESES_DA_SOBRA meses
    completos desde que ela existe. None se ainda não houve mês completo (a meta é deste mês)."""
    criada = obter(conn, meta_id).criada_em
    meses = [m for m in _meses_anteriores(hoje, MESES_DA_SOBRA) if m >= (criada.year, criada.month)]
    if not meses:
        return None
    por_mes = dict.fromkeys(meses, 0)
    for d, v in _movimentos(conn, meta_id):
        if (d.year, d.month) in por_mes:
            por_mes[(d.year, d.month)] += v
    return _media(list(por_mes.values()))


def guardado_total(conn: sqlite3.Connection) -> int:
    """Tudo que está guardado em metas."""
    return sum(_guardados(conn).values())


def tem_reserva(conn: sqlite3.Connection) -> bool:
    """Já existe uma meta de reserva de emergência (não arquivada)?"""
    return conn.execute("SELECT 1 FROM metas WHERE reserva = 1 AND arquivada = 0 LIMIT 1").fetchone() is not None


def viabilidade(conn: sqlite3.Connection, hoje: date) -> list[Viabilidade]:
    """A viabilidade de cada meta ativa, em ordem de prioridade, pela sobra média real."""
    sobra = sobra_media(conn, hoje)[0]
    return calcular_viabilidade([m.entrada() for m in _ativas(conn)], hoje, sobra)


def listar(conn: sqlite3.Connection, hoje: date, incluir_arquivadas: bool = False) -> list[Meta]:
    """Metas ativas (em ordem de prioridade, com `viabilidade`), depois as concluídas (a mais recente primeiro)
    e, se pedido, as arquivadas."""
    todas = _todas(conn)
    ativas = [m for m in todas if m.estado == "ativa"]
    for m, v in zip(ativas, calcular_viabilidade([m.entrada() for m in ativas], hoje, sobra_media(conn, hoje)[0])):
        m.viabilidade = v
    concluidas = sorted((m for m in todas if m.estado == "concluida"), key=lambda m: (m.concluida_em, m.id), reverse=True)
    arquivadas = [m for m in todas if m.estado == "arquivada"] if incluir_arquivadas else []
    return ativas + concluidas + arquivadas


@dataclass(frozen=True)
class ResumoMetas:
    """Os números do topo da aba. `comprometido`: soma do que as metas ativas pedem por mês; `livre`: o que
    sobra da sobra média pra novas metas (nunca negativo)."""

    guardado: int
    sobra: int
    meses_usados: int
    estimativa: bool
    comprometido: int
    livre: int


def resumo(conn: sqlite3.Connection, hoje: date) -> ResumoMetas:
    sobra, meses, estimativa = sobra_media(conn, hoje)
    ativas = _ativas(conn)
    viabilidades = calcular_viabilidade([m.entrada() for m in ativas], hoje, sobra)
    comprometido = sum(v.necessario for v in viabilidades)
    return ResumoMetas(guardado_total(conn), sobra, meses, estimativa, comprometido, max(0, sobra - comprometido))


# --- Criar, editar e organizar -----------------------------------------------------------------


def _validar(
    hoje: date,
    nome: str,
    valor_alvo_centavos: int,
    modo: str,
    prazo: date | None,
    mensal_centavos: int | None,
    prazo_atual: date | None = None,
) -> tuple[str, date | None, int | None]:
    """Confere os campos e devolve (nome, prazo, mensal) limpos: só o campo do modo escolhido sobra."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Dê um nome à meta.")
    if valor_alvo_centavos is None or valor_alvo_centavos <= 0:
        raise ValueError("O valor da meta precisa ser maior que zero.")
    if modo not in MODOS:
        raise ValueError(f"Modo desconhecido: {modo}")
    if modo == "prazo":
        if prazo is None:
            raise ValueError("Escolha o prazo.")
        if _indice(prazo) < _indice(hoje) and prazo != prazo_atual:  # prazo antigo de uma meta existente é tolerado
            raise ValueError("Esse prazo já passou.")
        return nome, prazo, None
    if mensal_centavos is None or mensal_centavos <= 0:
        raise ValueError("Diga quanto quer guardar por mês.")
    return nome, None, mensal_centavos


def _checar_reserva(conn: sqlite3.Connection, reserva: bool, ignorar_id: int | None = None) -> None:
    if reserva and conn.execute(
        "SELECT 1 FROM metas WHERE reserva = 1 AND arquivada = 0 AND id IS NOT ?", (ignorar_id,)
    ).fetchone():
        raise ValueError("Já existe uma meta de reserva de emergência.")


def criar(
    conn: sqlite3.Connection,
    hoje: date,
    *,
    nome: str,
    valor_alvo_centavos: int,
    modo: str,
    prazo: date | None = None,
    mensal_centavos: int | None = None,
    reserva: bool = False,
) -> int:
    """Cria a meta e devolve o id. Entra no fim da fila de prioridade; a reserva de emergência, no topo."""
    nome, prazo, mensal_centavos = _validar(hoje, nome, valor_alvo_centavos, modo, prazo, mensal_centavos)
    _checar_reserva(conn, reserva)
    with conn:
        if reserva:
            conn.execute("UPDATE metas SET prioridade = prioridade + 1")
            prioridade = 0
        else:
            prioridade = conn.execute("SELECT COALESCE(MAX(prioridade) + 1, 0) FROM metas").fetchone()[0]
        return conn.execute(
            """
            INSERT INTO metas (nome, valor_alvo_centavos, modo, prazo, mensal_centavos, prioridade, reserva, criada_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome, valor_alvo_centavos, modo, prazo.isoformat() if prazo else None, mensal_centavos,
                prioridade, int(reserva), hoje.isoformat(),
            ),
        ).lastrowid


def editar(
    conn: sqlite3.Connection,
    hoje: date,
    meta_id: int,
    *,
    nome: str,
    valor_alvo_centavos: int,
    modo: str,
    prazo: date | None = None,
    mensal_centavos: int | None = None,
    reserva: bool = False,
) -> None:
    """Troca os dados da meta (o guardado não muda). Marcar como reserva leva a meta pro topo da fila."""
    atual = obter(conn, meta_id)
    nome, prazo, mensal_centavos = _validar(
        hoje, nome, valor_alvo_centavos, modo, prazo, mensal_centavos, prazo_atual=atual.prazo
    )
    _checar_reserva(conn, reserva, ignorar_id=meta_id)
    with conn:
        conn.execute(
            """
            UPDATE metas SET nome = ?, valor_alvo_centavos = ?, modo = ?, prazo = ?, mensal_centavos = ?, reserva = ?
            WHERE id = ?
            """,
            (nome, valor_alvo_centavos, modo, prazo.isoformat() if prazo else None, mensal_centavos, int(reserva), meta_id),
        )
    if reserva and not atual.reserva and atual.estado == "ativa":
        _renumerar(conn, [meta_id] + [m.id for m in _ativas(conn) if m.id != meta_id])


def _renumerar(conn: sqlite3.Connection, ids_em_ordem: list[int]) -> None:
    """Dá prioridades 0, 1, 2... às metas ativas na ordem dada (as demais ficam depois)."""
    with conn:
        for posicao, id_ in enumerate(ids_em_ordem):
            conn.execute("UPDATE metas SET prioridade = ? WHERE id = ?", (posicao, id_))


def mover_prioridade(conn: sqlite3.Connection, meta_id: int, passo: int) -> None:
    """Sobe (-1) ou desce (+1) a meta na fila de prioridade. No limite, não faz nada."""
    if passo not in (-1, 1):
        raise ValueError("O passo é -1 (sobe) ou +1 (desce).")
    meta = obter(conn, meta_id)
    if meta.estado != "ativa":
        return
    ids = [m.id for m in _ativas(conn)]
    i, j = ids.index(meta_id), ids.index(meta_id) + passo
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    _renumerar(conn, ids)


def concluir(conn: sqlite3.Connection, hoje: date, meta_id: int) -> None:
    """Marca a meta como concluída (sai da fila de prioridade). O que ainda está guardado nela continua lá."""
    obter(conn, meta_id)
    with conn:
        conn.execute("UPDATE metas SET concluida_em = ? WHERE id = ?", (hoje.isoformat(), meta_id))


def arquivar(conn: sqlite3.Connection, meta_id: int) -> None:
    """Arquiva (esconde) a meta sem apagar. O que está guardado nela continua contando como guardado."""
    obter(conn, meta_id)
    with conn:
        conn.execute("UPDATE metas SET arquivada = 1 WHERE id = ?", (meta_id,))


def reativar(conn: sqlite3.Connection, meta_id: int) -> None:
    """Traz de volta uma meta concluída ou arquivada, no fim da fila."""
    meta = obter(conn, meta_id)
    _checar_reserva(conn, meta.reserva, ignorar_id=meta_id)
    with conn:
        proxima = conn.execute("SELECT COALESCE(MAX(prioridade) + 1, 0) FROM metas").fetchone()[0]
        conn.execute(
            "UPDATE metas SET concluida_em = NULL, arquivada = 0, prioridade = ? WHERE id = ?", (proxima, meta_id)
        )


# --- Guardar e retirar -------------------------------------------------------------------------


def _conta_do_dia_a_dia(conn: sqlite3.Connection, conta_id: int, rotulo: str) -> sqlite3.Row:
    conta = conn.execute("SELECT * FROM contas WHERE id = ?", (conta_id,)).fetchone()
    if conta is None or conta["tipo"] not in ("corrente", "dinheiro") or not conta["ativa"]:
        raise ValueError(f"Escolha uma conta corrente ou a carteira {rotulo}.")
    return conta


def _transferir(
    conn: sqlite3.Connection, meta: Meta, valor: int, saida_id: int, entrada_id: int, data: date, descricao: str
) -> str:
    transferencia_id = uuid.uuid4().hex
    categoria_id = _categoria_guardar(conn)
    with conn:
        for conta_id, sinal in ((saida_id, -1), (entrada_id, 1)):
            conn.execute(
                """
                INSERT INTO lancamentos
                    (conta_id, data, valor_centavos, descricao, categoria_id, metodo, origem, transferencia_id, meta_id)
                VALUES (?, ?, ?, ?, ?, 'transferencia', 'manual', ?, ?)
                """,
                (conta_id, data.isoformat(), sinal * valor, descricao, categoria_id, transferencia_id, meta.id),
            )
    return transferencia_id


def guardar(conn: sqlite3.Connection, meta_id: int, valor_centavos: int, conta_origem_id: int, data: date) -> str:
    """Guarda dinheiro na meta: transferência da conta pra Caixinhas. Devolve o `transferencia_id`.

    Só metas ativas recebem dinheiro. Passar do valor-alvo é permitido (o progresso para em 100%).
    """
    meta = obter(conn, meta_id)
    if meta.estado != "ativa":
        raise ValueError("Esta meta já foi encerrada. Reative-a pra guardar de novo.")
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    _conta_do_dia_a_dia(conn, conta_origem_id, "de onde sai o dinheiro")
    return _transferir(
        conn, meta, valor_centavos, conta_origem_id, conta_caixinhas(conn), data, f"Guardado: {meta.nome}"
    )


def retirar(conn: sqlite3.Connection, meta_id: int, valor_centavos: int, conta_destino_id: int, data: date) -> str:
    """Tira dinheiro da meta: transferência das Caixinhas de volta pra conta. Não passa do que está guardado.

    Vale também pra meta concluída ou arquivada (é como se recupera o que sobrou nela).
    """
    meta = obter(conn, meta_id)
    if valor_centavos <= 0:
        raise ValueError("O valor precisa ser maior que zero.")
    if valor_centavos > meta.guardado:
        raise ValueError(
            f"Só há {formatar_brl(meta.guardado)} guardados nessa meta." if meta.guardado else "Não há nada guardado nessa meta."
        )
    _conta_do_dia_a_dia(conn, conta_destino_id, "pra onde vai o dinheiro")
    return _transferir(
        conn, meta, valor_centavos, conta_caixinhas(conn), conta_destino_id, data, f"Retirado: {meta.nome}"
    )


# --- Prévias: o que muda com uma meta nova ou editada ------------------------------------------


@dataclass(frozen=True)
class Atraso:
    """Uma meta que demora mais por causa de outra. `meses` None = deixa de caber na sobra."""

    meta_id: int
    nome: str
    meses: int | None


@dataclass(frozen=True)
class Previa:
    """Resultado de simular uma meta: a viabilidade dela, de todas as ativas (já com ela) e quem atrasa."""

    meta: Viabilidade
    todas: list[Viabilidade]
    atrasos: list[Atraso]
    sobra: int
    estimativa: bool = False  # a sobra veio de poucos dados


def _atrasos(antes: list[Viabilidade], depois: list[Viabilidade], ignorar_id: int | None) -> list[Atraso]:
    previstas = {v.meta_id: v.prevista for v in antes}
    atrasos = []
    for v in depois:
        antiga = previstas.get(v.meta_id)
        if v.meta_id == ignorar_id or antiga is None:
            continue
        if v.prevista is None:
            atrasos.append(Atraso(v.meta_id, v.nome, None))
        elif (extra := meses_de_diferenca(antiga, v.prevista)) > 0:
            atrasos.append(Atraso(v.meta_id, v.nome, extra))
    return atrasos


def simular_nova_meta(
    conn: sqlite3.Connection,
    hoje: date,
    *,
    nome: str,
    valor_alvo_centavos: int,
    modo: str,
    prazo: date | None = None,
    mensal_centavos: int | None = None,
    reserva: bool = False,
) -> Previa:
    """Como ficariam todas as metas se esta fosse criada agora (sem gravar). ValueError se os dados não servem."""
    nome, prazo, mensal_centavos = _validar(hoje, nome, valor_alvo_centavos, modo, prazo, mensal_centavos)
    ativas = _ativas(conn)
    sobra, _meses, estimativa = sobra_media(conn, hoje)
    nova = EntradaMeta(None, nome, valor_alvo_centavos, modo, prazo, mensal_centavos)
    entradas = [m.entrada() for m in ativas]
    antes = calcular_viabilidade(entradas, hoje, sobra)
    com_a_nova = [nova] + entradas if reserva else entradas + [nova]
    depois = calcular_viabilidade(com_a_nova, hoje, sobra)
    propria = next(v for v in depois if v.meta_id is None)
    return Previa(propria, depois, _atrasos(antes, depois, None), sobra, estimativa)


def simular_edicao(
    conn: sqlite3.Connection,
    hoje: date,
    meta_id: int,
    *,
    nome: str,
    valor_alvo_centavos: int,
    modo: str,
    prazo: date | None = None,
    mensal_centavos: int | None = None,
    reserva: bool = False,
) -> Previa:
    """Como ficariam todas as metas se esta fosse editada assim (sem gravar). O guardado dela é mantido."""
    atual = obter(conn, meta_id)
    nome, prazo, mensal_centavos = _validar(
        hoje, nome, valor_alvo_centavos, modo, prazo, mensal_centavos, prazo_atual=atual.prazo
    )
    ativas = _ativas(conn)
    sobra, _meses, estimativa = sobra_media(conn, hoje)
    editada = EntradaMeta(meta_id, nome, max(0, valor_alvo_centavos - atual.guardado), modo, prazo, mensal_centavos)
    antes = calcular_viabilidade([m.entrada() for m in ativas], hoje, sobra)
    entradas = [editada if m.id == meta_id else m.entrada() for m in ativas]
    if reserva and not atual.reserva:
        entradas.sort(key=lambda e: e.id != meta_id)  # a reserva vai pro topo (sort é estável)
    depois = calcular_viabilidade(entradas, hoje, sobra)
    propria = next(v for v in depois if v.meta_id == meta_id)
    return Previa(propria, depois, _atrasos(antes, depois, meta_id), sobra, estimativa)


# --- Comprar agora ou parcelar? ----------------------------------------------------------------


def _meses_com_ritmo_reduzido(restante: int, ritmo_reduzido: int, meses_reduzidos: int, ritmo_normal: int) -> int:
    """Meses pra juntar o restante guardando `ritmo_reduzido` nos primeiros `meses_reduzidos` e depois `ritmo_normal`."""
    if ritmo_reduzido > 0 and ritmo_reduzido * meses_reduzidos >= restante:
        return _teto(restante, ritmo_reduzido)
    faltante = restante - ritmo_reduzido * meses_reduzidos
    return meses_reduzidos + _teto(faltante, ritmo_normal)


def simular_compra(
    conn: sqlite3.Connection, hoje: date, valor_centavos: int, parcelas: int, acrescimo_centavos: int = 0
) -> dict:
    """Compara comprar à vista (juntando antes) com parcelar. Não grava nada.

    `valor_centavos`: preço à vista; `acrescimo_centavos`: quanto o parcelado custa a mais no total.
    Devolve `{"valor_centavos", "parcelas", "estimativa", "a_vista": {...}, "parcelado": {...}}`:

    - a_vista: `cabe_agora` (cabe no Livre de hoje), `livre_agora`, `guardando_por_mes` (a sobra livre depois
      das metas), `meses` e `mes_previsto` pra juntar o valor com ela (None se não sobra nada pra isso);
    - parcelado: `parcela_centavos`, `total_centavos`, `acrescimo_centavos`, `mes_final` (mês da última parcela),
      `sobra_antes` e `sobra_depois` (a sobra média sem e com a parcela, enquanto ela dura) e `atrasos`
      (lista de `Atraso`: metas que ficam mais longe por causa das parcelas; `meses` None = deixa de caber).
    """
    if valor_centavos <= 0:
        raise ValueError("O valor da compra precisa ser maior que zero.")
    if not 1 <= parcelas <= MAX_PARCELAS:
        raise ValueError(f"O número de parcelas vai de 1 a {MAX_PARCELAS}.")
    if acrescimo_centavos < 0:
        raise ValueError("O acréscimo não pode ser negativo.")

    r = resumo(conn, hoje)
    livre_agora = relatorios.livre_ate_fim_do_mes(conn, hoje)
    if r.livre > 0:
        meses, mes_previsto = meses_para_mensal(valor_centavos, r.livre), data_para_mensal(valor_centavos, hoje, r.livre)
    else:
        meses, mes_previsto = None, None
    a_vista = {
        "cabe_agora": livre_agora >= valor_centavos,
        "livre_agora": livre_agora,
        "guardando_por_mes": r.livre,
        "meses": meses,
        "mes_previsto": mes_previsto,
    }

    total = valor_centavos + acrescimo_centavos
    parcela = dividir_em_parcelas(total, parcelas)[0]
    ativas = _ativas(conn)
    entradas = [m.entrada() for m in ativas]
    antes = calcular_viabilidade(entradas, hoje, r.sobra)
    depois = calcular_viabilidade(entradas, hoje, r.sobra - parcela)
    atrasos = []
    for a, d in zip(antes, depois):
        if a.alocado <= 0 or a.situacao == "cumprida" or d.alocado >= a.alocado:
            continue
        meses_antes = meses_para_mensal(a.restante, a.alocado)
        meses_depois = _meses_com_ritmo_reduzido(a.restante, d.alocado, parcelas, a.alocado)
        if meses_depois > meses_antes:
            atrasos.append(Atraso(a.meta_id, a.nome, meses_depois - meses_antes))
    parcelado = {
        "parcela_centavos": parcela,
        "total_centavos": total,
        "acrescimo_centavos": acrescimo_centavos,
        "mes_final": _mes(_indice(hoje) + parcelas - 1),
        "sobra_antes": r.sobra,
        "sobra_depois": r.sobra - parcela,
        "atrasos": atrasos,
    }
    return {
        "valor_centavos": valor_centavos,
        "parcelas": parcelas,
        "estimativa": r.estimativa,
        "a_vista": a_vista,
        "parcelado": parcelado,
    }
