"""Relatório mensal em markdown pra levar a uma IA ("o Python analisa, a IA interpreta").

O app só monta o texto; quem copia e cola onde quiser é o dono. Nada sai daqui (sem rede, sem API).

**Mês e base.** O mês pedido pode ser o atual (sai marcado "parcial", contando só até hoje). As comparações usam a
média dos até 3 meses completos antes dele que tenham lançamentos (mesma regra de `metas.sobra_media`: o mês em que
o uso começou só vale se começou até o dia 5). Sem nenhum, as partes comparativas dizem "ainda sem histórico".
Transferências (fatura, aportes, guardar em meta) ficam fora de tudo, pela mesma regra de `relatorios.resumo_mes`.

**Privacidade** (`descricao_publica`). Pix pra pessoas nunca mostra o nome. Viram "Pix enviado" / "Pix recebido"
(pelo sinal do valor): (a) descrições no formato de Pix do extrato ("Pix enviado: Cp :123-FULANO", "Pix recebido: ...");
(b) lançamentos com método pix em categorias de receita que não são Salário/Rendimentos (Dinheiro recebido, Renda
extra, Reembolsos...). Em "Presentes e doações" a descrição nunca aparece, em qualquer método (vira "Pix enviado" no
Pix e "Presente ou doação" no resto), porque costuma ter o nome de quem recebeu. Vale em toda seção que mostra descrição.
"""

import re
import sqlite3
from collections import Counter
from datetime import date

from meu_cofre import investimentos, metas, recorrentes, relatorios
from meu_cofre.lancamentos import formatar_brl, normalizar, padrao_sugerido

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")
DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")

FATOR_CATEGORIA = 1.5  # categoria dispara com gasto >= 1,5x a média...
ALTA_MINIMA = 5000  # ...e pelo menos R$ 50 acima dela
FATOR_FREQUENCIA = 2  # frequência dispara com >= 2x as ocorrências da média...
OCORRENCIAS_MINIMAS = 4  # ...e pelo menos 4 no mês

SEM_HISTORICO = "ainda sem histórico"
_PIX_DO_EXTRATO = re.compile(r"^pix (enviado|recebido)\b")
_CATEGORIAS_DE_RENDA_PROPRIA = ("Salário", "Rendimentos")
_CATEGORIA_DE_PESSOAS = "Presentes e doações"

PEDIDO_PARA_IA = (
    "Aponte padrões nestes números e faça perguntas sobre eles, pra eu pensar no que está por trás. "
    "Sem sermão e sem conselho genérico de economia. "
    "Os valores estão em reais; se o mês estiver marcado como parcial, ele ainda não terminou."
)


def nome_do_mes(ano: int, mes: int) -> str:
    """(2026, 10) -> "Outubro de 2026"."""
    return f"{MESES[mes - 1].capitalize()} de {ano}"


def descricao_publica(descricao: str, categoria: str, categoria_tipo: str | None, metodo: str | None, centavos: int) -> str:
    """A descrição que pode ir no relatório: sem nome de pessoa (ver o topo do módulo)."""
    de_pessoas = categoria == _CATEGORIA_DE_PESSOAS or (
        categoria_tipo == "receita" and categoria not in _CATEGORIAS_DE_RENDA_PROPRIA
    )
    if _PIX_DO_EXTRATO.match(normalizar(descricao)) or (metodo == "pix" and de_pessoas):
        return "Pix enviado" if centavos < 0 else "Pix recebido"
    if categoria == _CATEGORIA_DE_PESSOAS:
        return "Presente ou doação"
    return descricao


def _saidas(conn: sqlite3.Connection, ano: int, mes: int, ate: date | None = None) -> list[dict]:
    """Saídas do mês (sem transferências) com a descrição já pública. Chaves: data, valor (positivo), descricao, categoria,
    fixo (veio de uma recorrente)."""
    inicio, fim = relatorios.limites_do_mes(ano, mes)
    if ate is not None:
        fim = min(fim, ate)
    linhas = conn.execute(
        f"""
        SELECT l.id, l.data, -l.valor_centavos AS valor, l.descricao, l.metodo, l.recorrente_id,
               COALESCE(c.nome, 'A categorizar') AS categoria, c.tipo AS categoria_tipo
        {relatorios._SEM_TRANSFERENCIA}
          AND l.valor_centavos < 0 AND l.data BETWEEN ? AND ?
        ORDER BY l.data, l.id
        """,
        (inicio.isoformat(), fim.isoformat()),
    )
    return [
        {
            "id": r["id"], "data": date.fromisoformat(r["data"]), "valor": r["valor"], "categoria": r["categoria"],
            "fixo": r["recorrente_id"] is not None,
            "descricao": descricao_publica(r["descricao"], r["categoria"], r["categoria_tipo"], r["metodo"], -r["valor"]),
        }
        for r in linhas
    ]


def _chave(descricao: str) -> str:
    return padrao_sugerido(descricao) or normalizar(descricao)


def _meses_da_base(conn: sqlite3.Connection, ano: int, mes: int) -> list[tuple[int, int]]:
    """Até 3 meses completos antes do pedido, com lançamentos (a regra de `metas.sobra_media`)."""
    candidatos = metas._meses_completos(conn, date(ano, mes, 1))
    return [(a, m) for a, m in candidatos if any(vars(relatorios.resumo_mes(conn, a, m)).values())]


def _pct(parte: int, total: int) -> str:
    return f"{round(parte * 100 / total)}%" if total else "—"


def _variacao(atual: int, soma_base: int, n: int) -> str:
    """Variação do mês sobre a média (soma_base ÷ n); "—" quando a média é zero."""
    return f"{round((atual * n - soma_base) * 100 / soma_base):+d}%" if soma_base else "—"


def _media_brl(soma: int, n: int) -> str:
    return formatar_brl(round(soma / n))


def _tabela(cabecalho: list[str], linhas: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)] + [
        "| " + " | ".join(linha) + " |" for linha in linhas
    ]


def _resumo(conn, ano, mes, ate) -> list[str]:
    r = relatorios.resumo_mes(conn, ano, mes, ate)
    _inicio, fim = relatorios.limites_do_mes(ano, mes)
    # Só as ativas e não pausadas hoje: um fixo encerrado depois do mês não entra nos meses passados.
    df = recorrentes.listar(conn, fim)
    fixos = int(-df.loc[~df["pausada"] & (df["valor_centavos"] < 0) & (df["inicio"] <= fim), "valor_centavos"].sum())
    return [
        "## Resumo",
        f"- Entrou: {formatar_brl(r.entradas)}",
        f"- Saiu: {formatar_brl(r.saidas)}",
        f"- Sobrou: {formatar_brl(r.sobra)}",
        f"- Taxa de poupança: {_pct(r.sobra, r.entradas)}",
        f"- Comprometido com fixos (recorrentes de saída): {formatar_brl(fixos)} = {_pct(fixos, r.entradas)} da renda",
    ]


def _categorias(saidas, base, n) -> tuple[list[str], dict[str, int], dict[str, int]]:
    """A tabela de categorias, mais o gasto do mês e a soma da base por categoria."""
    mes_cat, base_cat = Counter(), Counter()
    for s in saidas:
        mes_cat[s["categoria"]] += s["valor"]
    for linha in base:
        base_cat.update(linha)
    nomes = sorted(set(mes_cat) | set(base_cat), key=lambda c: (-mes_cat[c], c))
    media = f"Média ({n} {'mês' if n == 1 else 'meses'})"
    linhas = [
        [c, formatar_brl(mes_cat[c]), _media_brl(base_cat[c], n) if n else "—", _variacao(mes_cat[c], base_cat[c], n) if n else "—"]
        for c in nomes
    ]
    texto = ["## Categorias (gasto do mês × média da base)"]
    texto += _tabela(["Categoria", "Mês", media if n else "Média", "Variação"], linhas) if linhas else ["Sem gastos no mês."]
    if not n:
        texto.append(f"Comparação com a média: {SEM_HISTORICO}.")
    return texto, mes_cat, base_cat


def _fora_do_padrao(conn, ano, mes, saidas, mes_cat, base_cat, base_saidas, n) -> list[str]:
    texto = ["## Fora do padrão"]
    achou = False
    if n:
        altas = [
            c for c in sorted(mes_cat, key=lambda c: -mes_cat[c])
            if mes_cat[c] * n >= FATOR_CATEGORIA * base_cat[c] and mes_cat[c] * n - base_cat[c] >= ALTA_MINIMA * n
        ]
        if altas:
            achou = True
            texto.append("Categorias bem acima da média:")
            texto += [
                f"- {c}: {formatar_brl(mes_cat[c])} (média {_media_brl(base_cat[c], n)}, {_variacao(mes_cat[c], base_cat[c], n)})"
                for c in altas
            ]
        vezes, nomes = Counter(), {}
        for s in saidas:
            vezes[_chave(s["descricao"])] += 1
            nomes.setdefault(_chave(s["descricao"]), Counter())[s["descricao"]] += 1
        antes = Counter(_chave(s["descricao"]) for mes_base in base_saidas for s in mes_base)
        frequentes = [
            k for k, v in vezes.most_common()
            if v >= OCORRENCIAS_MINIMAS and v * n >= FATOR_FREQUENCIA * antes[k]
        ]
        if frequentes:
            achou = True
            texto.append("Compras que dispararam em frequência:")
            texto += [
                f"- {nomes[k].most_common(1)[0][0]}: {vezes[k]} vezes no mês (média {antes[k] / n:.1f}".replace(".", ",") + ")"
                for k in frequentes
            ]
    else:
        texto.append(f"Categorias e frequência acima da média: {SEM_HISTORICO}.")
    novas = recorrentes.listar(conn, date(ano, mes, 1))
    novas = novas[(novas["valor_centavos"] < 0) & (novas["inicio"].map(lambda d: (d.year, d.month)) == (ano, mes))]
    if len(novas):
        achou = True
        texto.append("Assinaturas e fixos novos:")
        texto += [
            f"- {descricao_publica(r.descricao, r.categoria or '', None, r.metodo, r.valor_centavos)}: "
            f"{formatar_brl(-r.valor_centavos)}/mês ({r.categoria or 'sem categoria'})"
            for r in novas.itertuples()
        ]
    if n and not achou:
        texto.append("Nada disparou: nenhuma categoria, frequência ou fixo novo fora do padrão.")
    # fixos já estão no Resumo: aqui só o que variou
    maiores = sorted((s for s in saidas if not s["fixo"]), key=lambda s: (-s["valor"], s["data"], s["id"]))[:3]
    if maiores:
        texto.append("Os 3 maiores gastos do mês (fora os fixos):")
        texto += [
            f"{i}. {s['descricao']}: {formatar_brl(s['valor'])} ({s['categoria']}, {s['data']:%d/%m})"
            for i, s in enumerate(maiores, 1)
        ]
    return texto


def _onde_e_quando(saidas, mes_cat) -> list[str]:
    """Só os gastos variáveis: fixos (aluguel, assinaturas) já estão no Resumo e só repetiriam o óbvio."""
    texto = ["## Onde e quando"]
    saidas = [s for s in saidas if not s["fixo"]]
    if not saidas:
        return texto + ["Sem gastos variáveis no mês."]
    mes_cat = Counter()
    vezes_cat = Counter()
    for s in saidas:
        mes_cat[s["categoria"]] += s["valor"]
        vezes_cat[s["categoria"]] += 1
    por_descricao: dict[str, list[int]] = {}
    nomes: dict[str, Counter] = {}
    for s in saidas:
        k = _chave(s["descricao"])
        por_descricao.setdefault(k, []).append(s["valor"])
        nomes.setdefault(k, Counter())[s["descricao"]] += 1
    topo = sorted(por_descricao, key=lambda k: (-sum(por_descricao[k]), k))[:5]
    texto.append("Onde mais gastou (por descrição, fora os fixos):")
    texto += [
        f"{i}. {nomes[k].most_common(1)[0][0]}: {formatar_brl(sum(por_descricao[k]))} em {len(por_descricao[k])} "
        f"{'vez' if len(por_descricao[k]) == 1 else 'vezes'}"
        for i, k in enumerate(topo, 1)
    ]
    maiores = sorted(mes_cat, key=lambda c: (-mes_cat[c], c))[:3]
    por_dia = {(c, d): 0 for c in maiores for d in range(7)}
    for s in saidas:
        if s["categoria"] in maiores:
            por_dia[(s["categoria"], s["data"].weekday())] += s["valor"]
    texto += ["", "Dia da semana × 3 maiores categorias:"]
    texto += _tabela(
        ["Dia", *maiores], [[DIAS[d], *(formatar_brl(por_dia[(c, d)]) for c in maiores)] for d in range(7)]
    )
    # a frase só diz algo com uma categoria de várias compras (um pagamento único "concentra 100%" num dia)
    frequente = next((c for c in maiores if vezes_cat[c] >= 2), None)
    if frequente:
        dia = max(range(7), key=lambda d: (por_dia[(frequente, d)], -d))
        texto += ["", f"{DIAS[dia]} concentra {_pct(por_dia[(frequente, dia)], mes_cat[frequente])} de {frequente}."]
    return texto


def _metas_e_investimentos(conn, ano, mes, hoje) -> list[str]:
    texto = ["## Metas e investimentos"]
    ativas = [m for m in metas.listar(conn, hoje) if m.estado == "ativa"]
    if ativas:
        texto.append("Metas ativas:")
        texto += [
            f"- {m.nome}: {formatar_brl(m.guardado)} de {formatar_brl(m.valor_alvo_centavos)} ({round(m.progresso * 100)}%)"
            for m in ativas
        ]
    else:
        texto.append("Nenhuma meta ativa.")
    # progresso_do_mes só olha o ano e o mês da data, então serve pra qualquer mês.
    p = investimentos.progresso_do_mes(conn, date(ano, mes, 1))
    falta = "faltam" if (ano, mes) == (hoje.year, hoje.month) else "faltaram"  # o mês atual ainda não acabou
    linha = f"Aportes no mês: {formatar_brl(p.aportado)}"
    if p.meta is not None:
        linha += f" para uma meta de aporte de {formatar_brl(p.meta)}"
        linha += {"faltando": f" ({falta} {formatar_brl(p.falta)})", "batida": " (meta batida)", "passou": " (acima da meta)"}[p.situacao]
    else:
        linha += " (sem meta de aporte definida)"
    texto.append(linha)
    texto += [f"- {nome}: {formatar_brl(v)}" for nome, v in p.por_ativo]
    texto.append(f"Patrimônio guardado hoje: {formatar_brl(investimentos.patrimonio(conn, hoje)['total'])}")
    texto.append(
        f"Previsão para 31/12/{hoje.year} (cumprindo a meta de aporte): "
        f"{formatar_brl(investimentos.previsao_fim_do_ano(conn, hoje)['total'])}"
    )
    return texto


def gerar(conn: sqlite3.Connection, ano: int, mes: int, hoje: date, contexto: str = "") -> str:
    """O relatório do mês (ano, mes) em markdown. `hoje` marca o mês atual como parcial e dá a posição de patrimônio e metas."""
    parcial = (ano, mes) == (hoje.year, hoje.month)
    ate = hoje if parcial else None
    base = _meses_da_base(conn, ano, mes)
    n = len(base)
    saidas = _saidas(conn, ano, mes, ate)
    base_saidas = [_saidas(conn, a, m) for a, m in base]
    base_por_categoria = [_soma(mes_base) for mes_base in base_saidas]

    titulo = f"# Relatório financeiro: {nome_do_mes(ano, mes)}" + (f" (parcial, até {hoje:%d/%m})" if parcial else "")
    nomes_base = ", ".join(f"{MESES[m - 1][:3]}/{a}" for a, m in base)
    cabecalho = [titulo, f"Base de comparação: média de {n} {'mês completo' if n == 1 else 'meses completos'} ({nomes_base})."
                 if n else f"Base de comparação: {SEM_HISTORICO}."]
    categorias, mes_cat, base_cat = _categorias(saidas, base_por_categoria, n)
    secoes = [
        cabecalho,
        _resumo(conn, ano, mes, ate),
        categorias,
        _fora_do_padrao(conn, ano, mes, saidas, mes_cat, base_cat, base_saidas, n),
        _onde_e_quando(saidas, mes_cat),
        _metas_e_investimentos(conn, ano, mes, hoje),
    ]
    if contexto.strip():
        secoes.append(["## Sobre mim", contexto.strip()])
    secoes.append(["## Pedido", PEDIDO_PARA_IA])
    return "\n\n".join("\n".join(s) for s in secoes) + "\n"


def _soma(saidas: list[dict]) -> dict[str, int]:
    total: Counter = Counter()
    for s in saidas:
        total[s["categoria"]] += s["valor"]
    return dict(total)
