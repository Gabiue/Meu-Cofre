"""Importação da planilha de **movimentações** da Área do Investidor da B3 (xlsx), em duas etapas (F9).

1. `prever(conn, caminho)` lê a planilha e devolve a `Previa` sem gravar nada: operações **novas**, **duplicadas** (já
   importadas antes), linhas **ignoradas** com o motivo e os **ativos novos** que serão criados (o tipo, ação ou FII,
   é inferido e a tela deixa o usuário trocar: `AtivoNovo.tipo`).
2. `confirmar(conn, previa)` grava tudo numa transação, com `afeta_conta = 0` e origem "b3": o histórico só monta a
   posição (preço médio, proventos), sem mexer em nenhuma conta.

Reimportar não duplica: cada linha vira uma `chave_dedup` (data, tipo, ticker, quantidade, valor, instituição e a
ordem entre linhas idênticas do mesmo arquivo), e a chave é única no banco.

A planilha é tolerante: nomes de coluna com ou sem acento, em qualquer caixa e com espaços a mais; valores como número
ou texto ("R$ 1.234,56", "-"). Entram compras e vendas ("Transferência - Liquidação", com Crédito = compra e
Débito = venda), proventos ("Dividendo", "Juros Sobre Capital Próprio", "Rendimento") e eventos corporativos de ações
e FIIs:

- "Bonificação em Ativos": `bonificacao` (+ cotas, + o custo atribuído pela empresa; sem custo, o preço médio cai);
- "Desdobro"/"Desdobramento" e "Grupamento": `desdobramento` e `grupamento`, com a quantidade **com sinal** (Crédito
  +, Débito −); o custo não muda e o preço médio se ajusta;
- "Fração em Ativos": `fracao`, sai da posição ao preço médio, sem lucro nem prejuízo;
- Subscrição exercida ("Direitos de Subscrição - Exercido", "Subscrição", "Recibo de Subscrição"): vira `compra` do
  ticker-base (HGLG13 -> HGLG11, ITSA9 -> ITSA3, ITSA10 -> ITSA4).

O resto (Tesouro, direito de subscrição ainda não exercido, "Atualização" do recibo, "Transferência" de custódia entre
corretoras, "Leilão de Fração"...) é ignorado e aparece na prévia, com o motivo.
"""

import hashlib
import re
import sqlite3
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl

from meu_cofre import investimentos

# nome normalizado da coluna -> campo
COLUNAS = {
    "entrada/saida": "sentido", "entrada saida": "sentido", "e/s": "sentido",
    "data": "data",
    "movimentacao": "movimentacao",
    "produto": "produto",
    "instituicao": "instituicao",
    "quantidade": "quantidade",
    "preco unitario": "preco", "preco": "preco",
    "valor da operacao": "valor", "valor": "valor",
}
OBRIGATORIAS = ("data", "movimentacao", "produto")
EVENTOS = ("bonificacao", "desdobramento", "grupamento", "fracao")  # mexem na posição, nunca numa conta
PADRAO_TICKER = re.compile(r"^([A-Z]{4}\d{1,2})F?$")  # PETR4, HGLG11 e o fracionário PETR4F
LINHAS_PROCURANDO_CABECALHO = 30


class ImportacaoErro(ValueError):
    """A planilha não pôde ser lida (arquivo inválido, colunas que faltam)."""


@dataclass
class OperacaoB3:
    linha: int  # linha na planilha
    ticker: str
    nome: str
    tipo: str  # compra | venda | dividendo | jcp | rendimento | bonificacao | desdobramento | grupamento | fracao
    data: date
    quantidade: float | None  # desdobramento e grupamento: com sinal (entrou +, saiu −)
    preco_centavos: int | None
    valor_centavos: int
    chave: str


@dataclass
class Ignorada:
    linha: int
    motivo: str
    resumo: str  # o que a linha dizia, pra reconhecer na planilha


@dataclass
class AtivoNovo:
    ticker: str
    nome: str
    tipo: str  # acao | fii (editável antes de confirmar)


@dataclass
class Previa:
    novas: list[OperacaoB3] = field(default_factory=list)
    duplicadas: list[OperacaoB3] = field(default_factory=list)
    ignoradas: list[Ignorada] = field(default_factory=list)
    ativos_novos: list[AtivoNovo] = field(default_factory=list)

    def tipo_de(self, ticker: str) -> str | None:
        return next((a.tipo for a in self.ativos_novos if a.ticker == ticker), None)


# --- Leitura de células -------------------------------------------------------------------------------


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _normal(valor) -> str:
    """Texto sem acento, minúsculo e com espaços simples: pra comparar cabeçalhos e movimentações."""
    texto = re.sub(r"\s+", " ", _sem_acento(str(valor if valor is not None else ""))).strip().lower()
    return re.sub(r"\s*/\s*", "/", texto)  # "Entrada / Saída" = "Entrada/Saída"


def _numero(valor) -> Decimal | None:
    """Número de uma célula: aceita número, "R$ 1.234,56", "10,5" e "-" (vazio -> None)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))
    texto = str(valor).replace("R$", "").replace(" ", "").strip().replace(" ", "")
    if texto in ("", "-", "--"):
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", texto):
        texto = texto.replace(".", "")  # 1.000 e 1.000.000 são milhares
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _centavos(reais: Decimal | None) -> int | None:
    return None if reais is None else int((reais * 100).to_integral_value(rounding="ROUND_HALF_UP"))


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def _linhas(caminho: Path | str) -> list[tuple[int, dict]]:
    """As linhas de dados da primeira aba com o cabeçalho esperado: [(número da linha, {campo: valor})]."""
    try:
        livro = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    except (OSError, KeyError, zipfile.BadZipFile, openpyxl.utils.exceptions.InvalidFileException) as erro:
        raise ImportacaoErro("Não consegui abrir o arquivo. Use a planilha .xlsx de movimentações da Área do Investidor da B3.") from erro
    try:
        for aba in livro.worksheets:
            linhas = list(aba.iter_rows(values_only=True))
            for i, celulas in enumerate(linhas[:LINHAS_PROCURANDO_CABECALHO]):
                campos = {j: COLUNAS[_normal(c)] for j, c in enumerate(celulas) if _normal(c) in COLUNAS}
                if all(obrig in campos.values() for obrig in OBRIGATORIAS):
                    return [
                        (i + 2 + k, {campo: (resto[j] if j < len(resto) else None) for j, campo in campos.items()})
                        for k, resto in enumerate(linhas[i + 1 :])
                    ]
    finally:
        livro.close()
    raise ImportacaoErro("Não achei as colunas Data, Movimentação e Produto. Esta é a planilha de movimentações da B3?")


# --- Interpretação de uma linha -------------------------------------------------------------------------


def inferir_tipo(ticker: str, nome: str) -> str:
    """FII se o nome diz ou se o ticker termina em 11 (o usuário pode trocar: units de ações também terminam em 11)."""
    nome_normal = _normal(nome)
    if re.search(r"\bfii\b|fdo inv|fundo", nome_normal) or ticker.endswith("11"):
        return "fii"
    return "acao"


def _nome_legivel(nome: str) -> str:
    """"PETROLEO BRASILEIRO S.A. PETROBRAS" -> "Petroleo Brasileiro S.A. Petrobras" (a B3 grita em maiúsculas)."""
    nome = nome.strip()
    return nome.title() if nome.isupper() else nome


def _ticker_base(ticker: str) -> str:
    """Ticker do papel a que o direito/recibo de subscrição dá origem: FII 12-15 -> 11 (HGLG13 -> HGLG11); ação 1 e 9
    -> 3, 2 e 10 -> 4 (ITSA9 -> ITSA3, ITSA10 -> ITSA4). Qualquer outro fica como está."""
    raiz, numero = ticker[:4], ticker[4:]
    return raiz + {"12": "11", "13": "11", "14": "11", "15": "11", "1": "3", "9": "3", "2": "4", "10": "4"}.get(numero, numero)


def _classificar(movimentacao: str, sentido: str) -> tuple[str | None, str]:
    """(tipo, motivo). tipo None = ignorar, com o motivo. "subscricao" é compra do ticker-base (ver `_ticker_base`)."""
    mov = _normal(movimentacao)
    credito, debito = sentido.startswith("cred"), sentido.startswith("deb")
    if "dividendo" in mov:
        return "dividendo", ""
    if "juros sobre capital" in mov:
        return "jcp", ""
    if mov.startswith("rendimento"):
        return "rendimento", ""
    if "liquidacao" in mov:
        if credito:
            return "compra", ""
        if debito:
            return "venda", ""
        return None, "Não deu pra saber se foi compra ou venda (coluna Entrada/Saída)"
    if mov.startswith("bonificacao"):
        return ("bonificacao", "") if credito else (None, "Bonificação sem crédito: não importada")
    if mov.startswith(("desdobr", "grupamento")):
        if not (credito or debito):
            return None, "Não deu pra saber se as cotas entraram ou saíram (coluna Entrada/Saída)"
        return ("desdobramento" if mov.startswith("desdobr") else "grupamento"), ""
    if mov.startswith("fracao"):
        return ("fracao", "") if debito else (None, "Fração sem débito: não importada")
    if mov.startswith("leilao de fracao"):
        return None, "Valor das frações vendidas em leilão: lance como entrada na conta se quiser"
    if mov in ("direitos de subscricao - exercido", "subscricao", "recibo de subscricao"):
        return ("subscricao", "") if credito else (None, "Subscrição sem crédito: não importada")
    if mov == "atualizacao":
        return None, "Conversão do recibo: já contado na subscrição"
    if mov in ("direito de subscricao", "cessao de direitos") or mov.startswith("cessao de direitos"):
        return None, "Direito de subscrição ainda não exercido: só entra quando for exercido"
    if mov == "transferencia":
        return None, "Troca de corretora: a posição não muda"
    return None, f"Movimentação não importada: {movimentacao.strip() or 'sem descrição'}"


def _chave(o: OperacaoB3, instituicao: str, ordem: int) -> str:
    partes = [o.data.isoformat(), o.tipo, o.ticker, f"{o.quantidade or 0:g}", str(o.valor_centavos), _normal(instituicao), str(ordem)]
    return "b3:" + hashlib.sha1("|".join(partes).encode()).hexdigest()


def _ler(caminho: Path | str) -> tuple[list[OperacaoB3], list[Ignorada]]:
    operacoes: list[OperacaoB3] = []
    ignoradas: list[Ignorada] = []
    vistas: dict[str, int] = {}  # quantas linhas idênticas já apareceram neste arquivo
    for numero, c in _linhas(caminho):
        if all(v in (None, "") for v in c.values()):
            continue  # linha em branco
        produto = str(c.get("produto") or "").strip()
        resumo = " · ".join(str(c[k]).strip() for k in ("data", "movimentacao", "produto") if c.get(k) not in (None, ""))[:120]

        def ignorar(motivo: str, numero: int = numero, resumo: str = resumo) -> None:
            ignoradas.append(Ignorada(numero, motivo, resumo))

        codigo, _sep, nome = produto.partition(" - ")
        achou = PADRAO_TICKER.match(codigo.strip().upper())
        sentido = _normal(c.get("sentido"))
        tipo, motivo = _classificar(str(c.get("movimentacao") or ""), sentido)
        if tipo is None:
            ignorar(motivo)
            continue
        if not achou:
            ignorar("O produto não é ação nem FII")
            continue
        ticker = achou.group(1)
        if tipo == "subscricao":
            tipo, ticker = "compra", _ticker_base(ticker)
        dia = _data(c.get("data"))
        if dia is None:
            ignorar("Data inválida")
            continue
        quantidade, preco, valor = _numero(c.get("quantidade")), _centavos(_numero(c.get("preco"))), _centavos(_numero(c.get("valor")))
        if tipo in ("compra", "venda", *EVENTOS):
            if quantidade is None or quantidade <= 0:
                ignorar("Sem quantidade")
                continue
        if tipo in EVENTOS:
            valor = max(valor or 0, 0)  # só a bonificação traz valor (o custo atribuído); nos outros fica 0
            if tipo in ("desdobramento", "grupamento") and sentido.startswith("deb"):
                quantidade = -quantidade
        elif tipo in ("compra", "venda"):
            if valor is None and preco is not None:
                valor = round(float(quantidade) * preco)
            if preco is None and valor is not None:
                preco = round(valor / float(quantidade))
        if tipo not in EVENTOS and (valor is None or valor <= 0):
            ignorar("Sem valor")
            continue
        instituicao = str(c.get("instituicao") or "")
        op = OperacaoB3(
            linha=numero, ticker=ticker, nome=nome.strip(), tipo=tipo, data=dia,
            quantidade=float(quantidade) if quantidade is not None else None,
            preco_centavos=preco if tipo in ("compra", "venda") else None, valor_centavos=valor, chave="",
        )
        base = _chave(op, instituicao, 0)
        ordem = vistas[base] = vistas.get(base, -1) + 1
        op.chave = _chave(op, instituicao, ordem)
        operacoes.append(op)
    return operacoes, ignoradas


# --- Prévia e confirmação ------------------------------------------------------------------------------


def prever(conn: sqlite3.Connection, caminho: Path | str) -> Previa:
    """Lê a planilha e diz o que seria gravado. Não grava nada. `ImportacaoErro` se o arquivo não serve."""
    operacoes, ignoradas = _ler(caminho)
    ja = {r[0] for r in conn.execute("SELECT chave_dedup FROM operacoes_rv WHERE chave_dedup IS NOT NULL")}
    existentes = {r["ticker"] for r in conn.execute("SELECT ticker FROM ativos WHERE ticker IS NOT NULL")}
    previa = Previa(ignoradas=ignoradas)
    nomes: dict[str, str] = {}
    for o in operacoes:
        if o.chave in ja:
            previa.duplicadas.append(o)
            continue
        previa.novas.append(o)
        if o.nome and not nomes.get(o.ticker):
            nomes[o.ticker] = o.nome
        else:
            nomes.setdefault(o.ticker, "")
    previa.ativos_novos = [
        AtivoNovo(t, _nome_legivel(n) or t, inferir_tipo(t, n)) for t, n in nomes.items() if t not in existentes
    ]
    return previa


def confirmar(conn: sqlite3.Connection, previa: Previa, hoje: date | None = None) -> dict:
    """Grava os ativos novos e as operações novas da prévia, tudo ou nada. Devolve `{"operacoes": n, "ativos": m}`.

    Uma linha que entrou no banco depois da prévia (outra importação) é pulada, nunca duplicada."""
    hoje = hoje or date.today()
    for a in previa.ativos_novos:
        if a.tipo not in investimentos.TIPOS_RV:
            raise ImportacaoErro(f"Escolha ação ou FII para {a.ticker}.")
    criados = operacoes = 0
    with conn:
        ids = {r["ticker"]: r["id"] for r in conn.execute("SELECT id, ticker FROM ativos WHERE ticker IS NOT NULL")}
        for a in previa.ativos_novos:
            if a.ticker not in ids:
                ids[a.ticker] = conn.execute(
                    "INSERT INTO ativos (nome, tipo, criado_em, ticker) VALUES (?, ?, ?, ?)",
                    (a.nome.strip() or a.ticker, a.tipo, hoje.isoformat(), a.ticker),
                ).lastrowid
                criados += 1
        for o in previa.novas:
            if o.ticker not in ids:
                raise ImportacaoErro(f"O ativo {o.ticker} não existe e não está na lista de ativos novos.")
            operacoes += conn.execute(
                """
                INSERT OR IGNORE INTO operacoes_rv
                    (ativo_id, tipo, data, quantidade, preco_centavos, valor_centavos, afeta_conta, origem, chave_dedup)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'b3', ?)
                """,
                (ids[o.ticker], o.tipo, o.data.isoformat(), o.quantidade, o.preco_centavos, o.valor_centavos, o.chave),
            ).rowcount
    return {"operacoes": operacoes, "ativos": criados}
