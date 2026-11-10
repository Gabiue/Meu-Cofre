"""Importação do extrato OFX de uma conta, em duas etapas (#14), no mesmo molde da importação da B3.

1. `ler(caminho)` lê o OFX (SGML 1.x, com ou sem tags de fechamento) e devolve o `Extrato`.
2. `prever(conn, extrato, conta_id, hoje)` classifica cada transação sem gravar nada: **nova**, **já importada**,
   **conciliada** (casa com um lançamento manual ainda não conferido: mesma conta, mesmo valor, data a até 3 dias) ou
   **dúvida** (mais de um lançamento manual casa; o usuário escolhe; ou um aporte/compra de investimento com valor
   quase igual e data até 7 dias antes) ou **antes** (data anterior ao primeiro lançamento da conta: já está no saldo
   inicial, só entra se o usuário marcar).
3. `confirmar(conn, previa)` grava tudo numa transação e, se pedido, acerta o saldo inicial da conta pra que o saldo na
   data do `LEDGERBAL` (saldo final do extrato) seja o do banco.

Deduplicação: pelo `FITID` quando o banco o gera de verdade (Inter). OFX convertido de PDF (Caixa) inventa o FITID
(data + sequencial), então `fitid_confiavel` dá falso e a identidade passa a ser a `chave_dedup`: hash de conta, data,
valor, descrição normalizada e da ordem entre transações idênticas do mesmo dia (duas compras iguais viram duas).

O leitor é tolerante: o cabeçalho de encoding pode mentir (tenta UTF-8, cai pra cp1252), CRLF ou LF, datas com hora e
fuso, valor com ponto ou vírgula, descrição em `NAME`, `MEMO` ou nos dois.
"""

import hashlib
import html
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from meu_cofre import cadastros, investimentos, lancamentos, renda_variavel

JANELA_DIAS = 3  # tolerância entre a data do lançamento manual e a do extrato
# Tesouro e nota da B3 debitam valor um pouco diferente do lançado à mão, e na liquidação (dias depois)
DIAS_INVESTIMENTO = 7
BANCOS = {"077": "inter", "104": "caixa"}  # FID do OFX -> `contas.banco` (sem acento, minúsculo)
SAIDAS = {"DEBIT", "PAYMENT", "FEE", "SRVCHG", "ATM", "CHECK", "DIRECTDEBIT"}  # valor positivo nesses tipos é saída
# Pix, TED e DOC podem ser entre contas próprias: o histórico não adivinha: só uma regra do usuário categoriza (senão, "A categorizar")
PADRAO_TRANSFERENCIA = re.compile(r"\b(pix|ted|doc|transf\w*)\b", re.IGNORECASE)
PADRAO_HORA = re.compile(r"^\s*-?\s*(\d{2}:\d{2}:\d{2})\b\s*-?\s*")  # Caixa: "- 14:03:55 COMPRA ..."
TAGS_TRANSACAO = {"TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "NAME", "MEMO", "CHECKNUM"}


class ImportacaoErro(ValueError):
    """O arquivo não pôde ser lido ou a importação não pode ser feita."""


@dataclass
class Transacao:
    fitid: str
    data: date
    valor_centavos: int  # com sinal: entrada > 0
    descricao: str  # limpa (sem o horário da Caixa)
    descricao_original: str  # NAME/MEMO como vieram
    tipo: str
    hora: str | None = None
    checknum: str = ""


@dataclass
class Extrato:
    banco: str
    fid: str
    inicio: date | None
    fim: date | None
    saldo_centavos: int | None
    saldo_data: date | None
    transacoes: list[Transacao]


@dataclass
class Item:
    transacao: Transacao
    situacao: str  # nova | ja | conciliada | duvida | antes
    chave: str
    categoria_id: int | None = None  # só pras novas (None = "A categorizar")
    lancamento_id: int | None = None  # conciliada: o manual; dúvida: o escolhido (None = importar como nova)
    candidatos: list[dict] = field(default_factory=list)  # dúvida: manuais que casam (id, data, descricao, valor_centavos)
    importar: bool = False  # antes: o usuário quis importar mesmo assim

    @property
    def destino(self) -> str:
        """O que vai acontecer: ja | conciliada | nova (dúvida vira uma das duas, conforme a escolha)."""
        if self.situacao == "duvida":
            return "conciliada" if self.lancamento_id else "nova"
        if self.situacao == "antes":
            return "nova" if self.importar else "ja"
        return self.situacao


@dataclass
class Previa:
    extrato: Extrato
    conta_id: int
    arquivo: str
    confiavel: bool
    hoje: date
    itens: list[Item] = field(default_factory=list)

    def _com(self, situacao: str) -> list[Item]:
        return [i for i in self.itens if i.situacao == situacao]

    @property
    def novas(self) -> list[Item]:
        return self._com("nova")

    @property
    def conciliadas(self) -> list[Item]:
        return self._com("conciliada")

    @property
    def ja_importadas(self) -> list[Item]:
        return self._com("ja")

    @property
    def antes(self) -> list[Item]:
        return self._com("antes")

    @property
    def duvidas(self) -> list[Item]:
        return self._com("duvida")

    @property
    def a_gravar(self) -> int:
        """Quantos lançamentos novos serão criados (novas + dúvidas importadas como nova)."""
        return sum(1 for i in self.itens if i.destino == "nova")


# --- Leitura ----------------------------------------------------------------------------------------------


def _texto(caminho: Path | str) -> str:
    try:
        bruto = Path(caminho).read_bytes()
    except OSError as erro:
        raise ImportacaoErro("Não consegui abrir o arquivo.") from erro
    try:
        return bruto.decode("utf-8-sig")  # o cabeçalho do Inter diz cp1252, mas o conteúdo é UTF-8
    except UnicodeDecodeError:
        return bruto.decode("cp1252", errors="replace")


def _data(valor: str | None) -> date | None:
    """'20261101235959[-3:BRT]' -> 2026-11-01 (hora e fuso não importam)."""
    achou = re.match(r"\s*(\d{8})", valor or "")
    try:
        return datetime.strptime(achou.group(1), "%Y%m%d").date() if achou else None
    except ValueError:
        return None


def _centavos(valor: str | None) -> int | None:
    """OFX usa ponto decimal ('-12.34'); aceita também '1.234,56'."""
    texto = (valor or "").strip().replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return int((Decimal(texto) * 100).to_integral_value(rounding="ROUND_HALF_UP"))
    except InvalidOperation:
        return None


def _descricao(nome: str, memo: str) -> str:
    """NAME e MEMO sem repetir um no outro, sem as aspas que o Inter põe no nome do Pix."""
    nome, memo = nome.replace('"', "").strip(), memo.replace('"', "").strip()
    a, b = lancamentos.normalizar(nome), lancamentos.normalizar(memo)
    if not b or b == a or b in a:
        return nome or memo
    if not a or a in b:
        return memo
    return f"{nome} - {memo}"


def ler(caminho: Path | str) -> Extrato:
    """Lê o OFX. `ImportacaoErro` se não for um extrato OFX com transações."""
    texto = _texto(caminho)
    if not re.search(r"<OFX>", texto, re.IGNORECASE):
        raise ImportacaoErro("Este arquivo não parece um extrato OFX. Baixe o extrato em formato OFX no app do banco.")
    cab: dict[str, str] = {}
    brutas: list[dict[str, str]] = []
    contexto = ""  # último agregado aberto: STMTTRN | LEDGERBAL | outro
    for fecha, tag, valor in re.findall(r"<(/?)([A-Za-z0-9.]+)>([^<\r\n]*)", texto):
        tag, valor = tag.upper(), html.unescape(valor).strip()
        if tag in ("STMTTRN", "LEDGERBAL", "AVAILBAL", "BANKTRANLIST"):
            contexto = "" if fecha else tag
            if tag == "STMTTRN" and not fecha:
                brutas.append({})
        elif fecha:
            continue
        elif contexto == "STMTTRN" and tag in TAGS_TRANSACAO:
            brutas[-1][tag] = valor
        elif contexto == "LEDGERBAL" and tag in ("BALAMT", "DTASOF"):
            cab["L" + tag] = valor
        elif tag in ("ORG", "FID", "DTSTART", "DTEND"):
            cab.setdefault(tag, valor)
    transacoes: list[Transacao] = []
    for b in brutas:
        data, valor = _data(b.get("DTPOSTED")), _centavos(b.get("TRNAMT"))
        if data is None or valor is None:
            raise ImportacaoErro("Há uma transação sem data ou sem valor válido no arquivo.")
        tipo = b.get("TRNTYPE", "").upper()
        if tipo in SAIDAS and valor > 0:
            valor = -valor
        original = " | ".join(p for p in (b.get("NAME", ""), b.get("MEMO", "")) if p)
        bruta = _descricao(b.get("NAME", ""), b.get("MEMO", ""))
        achou = PADRAO_HORA.match(bruta)
        limpa = bruta[achou.end() :].strip() if achou else bruta
        transacoes.append(
            Transacao(b.get("FITID", ""), data, valor, limpa or "Sem descrição", original, tipo,
                      achou.group(1) if achou else None, b.get("CHECKNUM", ""))
        )
    if not transacoes:
        raise ImportacaoErro("Não encontrei transações neste extrato.")
    return Extrato(
        banco=cab.get("ORG", ""), fid=cab.get("FID", ""), inicio=_data(cab.get("DTSTART")), fim=_data(cab.get("DTEND")),
        saldo_centavos=_centavos(cab.get("LBALAMT")), saldo_data=_data(cab.get("LDTASOF")), transacoes=transacoes,
    )


def fitid_confiavel(extrato: Extrato) -> bool:
    """Falso quando o FITID parece gerado por conversor: vazio, repetido, igual ao CHECKNUM ou data + sequencial."""
    ids = [t.fitid for t in extrato.transacoes]
    if not all(ids) or len(set(ids)) != len(ids):
        return False
    if all(t.fitid == t.checknum for t in extrato.transacoes):
        return False
    return not all(t.fitid.startswith(t.data.strftime("%Y%m%d")) and len(t.fitid) <= 11 for t in extrato.transacoes)


def conta_sugerida(conn: sqlite3.Connection, extrato: Extrato) -> int | None:
    """A conta corrente do banco do extrato (Inter = 077, Caixa = 104), se houver."""
    banco = BANCOS.get(extrato.fid)
    for c in cadastros.contas_por_tipo(conn, ("corrente",)):
        if banco and lancamentos.normalizar(c["banco"] or "") == banco:
            return c["id"]
    return None


# --- Prévia -----------------------------------------------------------------------------------------------


def _chaves(extrato: Extrato, conta_id: int) -> list[str]:
    """Uma chave por transação: conta, data, valor, descrição normalizada e a ordem entre as idênticas (pela hora)."""
    grupos: dict[tuple, list[int]] = {}
    for i, t in enumerate(extrato.transacoes):
        grupos.setdefault((t.data, t.valor_centavos, lancamentos.normalizar(t.descricao)), []).append(i)
    chaves = [""] * len(extrato.transacoes)
    for (data, valor, desc), indices in grupos.items():
        indices.sort(key=lambda i: extrato.transacoes[i].hora or "")  # estável: empate fica na ordem do arquivo
        for ordem, i in enumerate(indices):
            base = f"{conta_id}|{data.isoformat()}|{valor}|{desc}|{ordem}"
            chaves[i] = "ofx:" + hashlib.sha1(base.encode()).hexdigest()
    return chaves


def prever(conn: sqlite3.Connection, extrato: Extrato, conta_id: int, hoje: date | None = None, arquivo: str = "") -> Previa:
    """Diz o que seria feito com cada transação. Não grava nada."""
    previa = Previa(extrato, conta_id, arquivo, fitid_confiavel(extrato), hoje or date.today())
    fitids = {r[0] for r in conn.execute("SELECT fitid FROM lancamentos WHERE conta_id = ? AND fitid IS NOT NULL", (conta_id,))}
    existentes = {r[0] for r in conn.execute("SELECT chave_dedup FROM lancamentos WHERE conta_id = ? AND chave_dedup IS NOT NULL", (conta_id,))}
    for t, chave in zip(extrato.transacoes, _chaves(extrato, conta_id)):
        ja = (t.fitid in fitids) if previa.confiavel else (chave in existentes)
        previa.itens.append(Item(t, "ja" if ja else "nova", chave))

    manuais = [
        {"id": r["id"], "data": date.fromisoformat(r["data"]), "descricao": r["descricao"], "valor_centavos": r["valor_centavos"]}
        for r in conn.execute(
            "SELECT id, data, descricao, valor_centavos FROM lancamentos "
            "WHERE conta_id = ? AND conferido = 0 AND fitid IS NULL AND chave_dedup IS NULL ORDER BY data, id",
            (conta_id,),
        )
    ]
    livres = list(manuais)
    abertos = [i for i in previa.itens if i.situacao == "nova"]
    for dist in range(JANELA_DIAS + 1):  # o mais próximo primeiro
        for item in list(abertos):
            achados = [m for m in livres if m["valor_centavos"] == item.transacao.valor_centavos
                       and abs((m["data"] - item.transacao.data).days) == dist]
            if len(achados) == 1:
                item.situacao, item.lancamento_id = "conciliada", achados[0]["id"]
                livres.remove(achados[0])
                abertos.remove(item)
            elif achados:
                item.situacao = "duvida"
                abertos.remove(item)
    usados = {i.lancamento_id for i in previa.itens if i.situacao == "conciliada"}
    for item in previa.duvidas:  # padrão: o mais próximo ainda não escolhido (o usuário pode trocar)
        t = item.transacao
        item.candidatos = sorted(
            (m for m in manuais if m["valor_centavos"] == t.valor_centavos and m["id"] not in usados
             and abs((m["data"] - t.data).days) <= JANELA_DIAS),
            key=lambda m: abs((m["data"] - t.data).days),
        )
        item.lancamento_id = next((m["id"] for m in item.candidatos if m["id"] not in usados), None)
        usados.add(item.lancamento_id)
    _duvidas_de_investimento(conn, previa, conta_id, usados)
    inicio = conn.execute("SELECT MIN(data) FROM lancamentos WHERE conta_id = ?", (conta_id,)).fetchone()[0]
    for item in previa.novas:  # sem lançamentos na conta não há "início do uso"
        if inicio and item.transacao.data < date.fromisoformat(inicio):
            item.situacao = "antes"
    for item in previa.novas + previa.antes:
        t = item.transacao
        tipo = "receita" if t.valor_centavos > 0 else "despesa"
        if PADRAO_TRANSFERENCIA.search(t.descricao):  # pode ser entre contas próprias: só uma regra explícita decide
            item.categoria_id = lancamentos.aplicar_regras(conn, t.descricao, tipo)
        else:
            item.categoria_id = lancamentos.sugerir_categoria(conn, t.descricao, tipo)
    return previa


def _duvidas_de_investimento(conn: sqlite3.Connection, previa: Previa, conta_id: int, usados: set) -> None:
    """Saída "nova" que casa quase (centavos, até 7 dias depois) com um aporte ou compra de ação lançado à mão vira dúvida."""
    manuais = [
        {"id": r["id"], "data": date.fromisoformat(r["data"]), "descricao": r["descricao"], "valor_centavos": r["valor_centavos"]}
        for r in conn.execute(
            """
            SELECT l.id, l.data, l.descricao, l.valor_centavos FROM lancamentos l
            JOIN lancamentos o ON o.transferencia_id = l.transferencia_id AND o.id <> l.id
            JOIN contas c ON c.id = o.conta_id AND c.tipo = 'investimento'
            WHERE l.conta_id = ? AND l.valor_centavos < 0 AND l.conferido = 0 AND l.fitid IS NULL AND l.chave_dedup IS NULL
              AND (EXISTS (SELECT 1 FROM movimentacoes_ativos m WHERE m.transferencia_id = l.transferencia_id AND m.tipo = 'aporte')
                   OR EXISTS (SELECT 1 FROM operacoes_rv r WHERE r.transferencia_id = l.transferencia_id AND r.tipo = 'compra'))
            ORDER BY l.data, l.id
            """,
            (conta_id,),
        )
    ]
    for item in previa.novas:
        t = item.transacao
        if t.valor_centavos >= 0:
            continue
        limite = max(100, round(abs(t.valor_centavos) * 0.01))
        item.candidatos = sorted(
            (m for m in manuais if m["id"] not in usados and 0 <= (t.data - m["data"]).days <= DIAS_INVESTIMENTO
             and abs(m["valor_centavos"] - t.valor_centavos) <= limite),
            key=lambda m: (abs(m["valor_centavos"] - t.valor_centavos), (t.data - m["data"]).days),
        )
        if item.candidatos:
            item.situacao, item.lancamento_id = "duvida", item.candidatos[0]["id"]
            usados.add(item.lancamento_id)


def _acertar_valor(conn: sqlite3.Connection, lancamento_id: int, real: int) -> None:
    """O extrato manda: leva o aporte ou a compra de ação lançados à mão pro valor debitado de verdade."""
    l = conn.execute("SELECT transferencia_id FROM lancamentos WHERE id = ?", (lancamento_id,)).fetchone()
    mov = conn.execute("SELECT id, data FROM movimentacoes_ativos WHERE transferencia_id = ?", (l["transferencia_id"],)).fetchone()
    if mov:
        investimentos.editar_movimentacao(conn, mov["id"], valor=real, data=date.fromisoformat(mov["data"]))
        return
    op = conn.execute("SELECT * FROM operacoes_rv WHERE transferencia_id = ?", (l["transferencia_id"],)).fetchone()
    preco, taxas = op["preco_centavos"], real - op["valor_centavos"]  # a diferença vai pras taxas (corretagem, emolumentos)...
    if taxas < 0:  # ...e se o extrato debitou menos que a operação, é o preço que cede
        preco = int(real / op["quantidade"])
        taxas = max(0, real - round(op["quantidade"] * preco))
    renda_variavel.editar_operacao(conn, op["id"], data=date.fromisoformat(op["data"]), quantidade=op["quantidade"], preco=preco, taxas=taxas)


def diferenca_saldo(conn: sqlite3.Connection, previa: Previa) -> int | None:
    """Quanto o saldo inicial mudaria pra a conta fechar com o `LEDGERBAL` (None se o extrato não traz saldo)."""
    e = previa.extrato
    if e.saldo_centavos is None or e.saldo_data is None:
        return None
    novas = sum(i.transacao.valor_centavos for i in previa.itens if i.destino == "nova" and i.transacao.data <= e.saldo_data)
    return e.saldo_centavos - cadastros.saldo_da_conta(conn, previa.conta_id, e.saldo_data) - novas


# --- Confirmação ------------------------------------------------------------------------------------------


def confirmar(conn: sqlite3.Connection, previa: Previa, escolhas: dict[int, int | None] | None = None,
              ajustar_saldo: bool = True) -> dict:
    """Grava as novas, concilia as casadas e registra a importação, tudo ou nada.

    `escolhas`: {posição do item em `previa.itens`: id do lançamento manual (ou None = importar como nova)}, pras dúvidas;
    pras linhas "antes" do início do uso, True = importar. Conciliar com valor diferente acerta o manual pro do extrato.
    Devolve `{"novas", "conciliadas", "ja_importadas", "diferenca_saldo"}` (a diferença é 0 se não acertou o saldo)."""
    for pos, escolha in (escolhas or {}).items():
        item = previa.itens[pos]
        if item.situacao == "antes":  # a escolha é importar (truthy) ou não
            item.importar = bool(escolha)
            continue
        if item.situacao != "duvida" or (escolha is not None and escolha not in {m["id"] for m in item.candidatos}):
            raise ImportacaoErro("Escolha inválida para uma transação em dúvida.")
        item.lancamento_id = escolha
    escolhidos = [i.lancamento_id for i in previa.itens if i.destino == "conciliada"]
    if len(escolhidos) != len(set(escolhidos)):
        raise ImportacaoErro("O mesmo lançamento foi escolhido para mais de uma transação.")
    if ajustar_saldo:
        if conn.execute("SELECT tipo FROM contas WHERE id = ?", (previa.conta_id,)).fetchone()["tipo"] not in ("corrente", "dinheiro"):
            raise ImportacaoErro("Só conta corrente tem saldo inicial pra acertar.")

    # Antes da importação de propósito: o ajuste de saldo abaixo precisa dos valores reais. As funções do domínio
    # gravam sozinhas; se a importação falhar depois, o manual fica com o valor do banco, só sem a marca de conferido.
    for item in previa.itens:
        if item.destino == "conciliada":
            atual = conn.execute("SELECT valor_centavos FROM lancamentos WHERE id = ?", (item.lancamento_id,)).fetchone()[0]
            if atual != item.transacao.valor_centavos:
                try:
                    _acertar_valor(conn, item.lancamento_id, abs(item.transacao.valor_centavos))
                except ValueError as erro:
                    raise ImportacaoErro(str(erro)) from erro
    novas = conciliadas = 0
    with conn:
        importacao_id = conn.execute(
            "INSERT INTO importacoes (arquivo, banco, formato, conta_id, linhas_lidas) VALUES (?, ?, 'ofx', ?, ?)",
            (previa.arquivo or "extrato.ofx", previa.extrato.banco, previa.conta_id, len(previa.itens)),
        ).lastrowid
        for item in previa.itens:
            t, destino = item.transacao, item.destino
            fitid = t.fitid if previa.confiavel else None
            if destino == "nova":
                novas += conn.execute(
                    """
                    INSERT OR IGNORE INTO lancamentos
                        (conta_id, data, valor_centavos, descricao_original, descricao, categoria_id, metodo, status,
                         origem, importacao_id, conferido, fitid, chave_dedup)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'importacao', ?, 1, ?, ?)
                    """,
                    (previa.conta_id, t.data.isoformat(), t.valor_centavos, t.descricao_original, t.descricao, item.categoria_id,
                     "pix" if re.search(r"\bpix\b", t.descricao, re.IGNORECASE) else None,
                     "previsto" if t.data > previa.hoje else "realizado", importacao_id, fitid, item.chave),
                ).rowcount
            elif destino == "conciliada":
                conciliadas += conn.execute(
                    "UPDATE lancamentos SET conferido = 1, fitid = ?, chave_dedup = ?, descricao_original = ?, importacao_id = ? "
                    "WHERE id = ? AND conferido = 0",
                    (fitid, item.chave, t.descricao_original, importacao_id, item.lancamento_id),
                ).rowcount
        ja = len(previa.itens) - novas - conciliadas
        conn.execute("UPDATE importacoes SET linhas_novas = ?, linhas_duplicadas = ? WHERE id = ?", (novas + conciliadas, ja, importacao_id))
    diferenca = 0
    e = previa.extrato
    if ajustar_saldo and e.saldo_centavos is not None and e.saldo_data is not None:
        diferenca = e.saldo_centavos - cadastros.saldo_da_conta(conn, previa.conta_id, e.saldo_data)
        if diferenca:
            cadastros.definir_saldo_inicial(
                conn, previa.conta_id, cadastros.obter_conta(conn, previa.conta_id)["saldo_inicial_centavos"] + diferenca
            )
    return {"novas": novas, "conciliadas": conciliadas, "ja_importadas": ja, "diferenca_saldo": diferenca}
