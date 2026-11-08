"""Investimento inicial: esforco das atividades de implantacao, em horas e em reais.

Cada atividade declara a comunidade responsavel, o esforco em horas, a duracao em
dias uteis e, opcionalmente, a atividade que precisa terminar antes dela. O
cronograma sai dessas dependencias: uma atividade comeca quando a predecessora
termina, e o calendario e contado em dias uteis, com 20 por mes.
"""

import math

from recursos import base

VALOR_HORA_PADRAO = 262.0
DIAS_UTEIS_POR_MES_PADRAO = 20
MOEDA = "BRL"

CAMPOS_TEXTO = ("id", "comunidade", "atividade", "predecessora")
CAMPOS_NUMERICOS = ("horas", "dias_uteis")


def _num(valor, padrao=0.0):
    try:
        numero = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return padrao
    return numero if numero >= 0 else padrao


def normalizar(dados, atual=None):
    """Valida e normaliza a secao de investimento vinda do formulario."""
    atual = atual or {}
    dados = dados or {}
    erros = []

    valor_hora = _num(dados.get("valor_hora", atual.get("valor_hora", VALOR_HORA_PADRAO)), -1)
    if valor_hora < 0:
        erros.append("'valor_hora' deve ser um número maior ou igual a zero.")
        valor_hora = VALOR_HORA_PADRAO

    dias_mes = _num(
        dados.get("dias_uteis_por_mes", atual.get("dias_uteis_por_mes", DIAS_UTEIS_POR_MES_PADRAO)), 0
    )
    if dias_mes < 1:
        erros.append("'dias_uteis_por_mes' deve ser pelo menos 1.")
        dias_mes = DIAS_UTEIS_POR_MES_PADRAO

    itens = []
    vistos = set()
    for indice, bruto in enumerate(dados.get("itens", atual.get("itens", [])) or []):
        item_id = str(bruto.get("id") or "").strip() or "a%d" % (indice + 1)
        if item_id in vistos:
            item_id = "%s-%d" % (item_id, indice + 1)
        vistos.add(item_id)
        itens.append({
            "id": item_id,
            "comunidade": str(bruto.get("comunidade") or "").strip(),
            "atividade": str(bruto.get("atividade") or "").strip(),
            "horas": _num(bruto.get("horas")),
            "dias_uteis": _num(bruto.get("dias_uteis")),
            "predecessora": str(bruto.get("predecessora") or "").strip(),
        })

    # Uma predecessora que nao existe mais e tratada como "sem predecessora".
    ids = {item["id"] for item in itens}
    for item in itens:
        if item["predecessora"] and item["predecessora"] not in ids:
            erros.append(
                "A atividade '%s' aponta para uma predecessora que não existe." % (item["atividade"] or item["id"])
            )
            item["predecessora"] = ""
        if item["predecessora"] == item["id"]:
            erros.append("A atividade '%s' não pode ser predecessora de si mesma." % (item["atividade"] or item["id"]))
            item["predecessora"] = ""

    if erros:
        raise ValueError(" ".join(erros))

    return {"valor_hora": valor_hora, "dias_uteis_por_mes": dias_mes, "itens": itens}


def _cronograma(itens):
    """Inicio e fim de cada atividade, em dias uteis a partir de zero.

    Resolve as dependencias por profundidade, com memoizacao; um ciclo e cortado
    tratando a atividade envolvida como se nao tivesse predecessora.
    """
    por_id = {item["id"]: item for item in itens}
    inicio = {}
    fim = {}
    avisos = []

    def resolver(item_id, visitando):
        if item_id in fim:
            return fim[item_id]
        item = por_id[item_id]
        if item_id in visitando:
            avisos.append(
                "Dependência circular em '%s': a atividade foi posicionada no início."
                % (item["atividade"] or item_id)
            )
            inicio[item_id] = 0.0
            fim[item_id] = item["dias_uteis"]
            return fim[item_id]

        visitando.add(item_id)
        predecessora = item["predecessora"]
        inicio[item_id] = resolver(predecessora, visitando) if predecessora else 0.0
        fim[item_id] = inicio[item_id] + item["dias_uteis"]
        visitando.discard(item_id)
        return fim[item_id]

    for item in itens:
        resolver(item["id"], set())
    return inicio, fim, avisos


def calcular(config):
    """Totais, cronograma e distribuicao por comunidade do investimento inicial."""
    cfg = config.get("investimento_inicial") or {}
    valor_hora = float(cfg.get("valor_hora", VALOR_HORA_PADRAO))
    dias_mes = float(cfg.get("dias_uteis_por_mes", DIAS_UTEIS_POR_MES_PADRAO)) or DIAS_UTEIS_POR_MES_PADRAO
    itens = cfg.get("itens") or []

    inicio, fim, avisos = _cronograma(itens)
    duracao_dias = max(fim.values()) if fim else 0.0
    duracao_meses = duracao_dias / dias_mes if dias_mes else 0.0
    meses = max(1, int(math.ceil(duracao_meses))) if duracao_dias else 0

    total_horas = sum(item["horas"] for item in itens)
    valor_total = total_horas * valor_hora

    detalhados = []
    for item in itens:
        custo = item["horas"] * valor_hora
        detalhados.append(dict(
            item,
            inicio=inicio.get(item["id"], 0.0),
            fim=fim.get(item["id"], 0.0),
            custo=custo,
            custo_fmt=base.fmt_moeda(custo, MOEDA),
            horas_fmt=base.fmt_num(item["horas"], 0) + " h",
            inicio_mes=(inicio.get(item["id"], 0.0) / dias_mes) + 1,
            participacao=(custo / valor_total) if valor_total else 0.0,
        ))

    por_comunidade = {}
    for item in itens:
        chave = item["comunidade"] or "Sem comunidade"
        acumulado = por_comunidade.setdefault(chave, {"comunidade": chave, "horas": 0.0, "atividades": 0})
        acumulado["horas"] += item["horas"]
        acumulado["atividades"] += 1
    comunidades = sorted(por_comunidade.values(), key=lambda c: c["horas"], reverse=True)
    maior = comunidades[0]["horas"] if comunidades else 0.0
    for comunidade in comunidades:
        comunidade["custo"] = comunidade["horas"] * valor_hora
        comunidade["custo_fmt"] = base.fmt_moeda(comunidade["custo"], MOEDA)
        comunidade["horas_fmt"] = base.fmt_num(comunidade["horas"], 0) + " h"
        comunidade["participacao"] = (comunidade["horas"] / total_horas) if total_horas else 0.0
        comunidade["proporcao_da_maior"] = (comunidade["horas"] / maior) if maior else 0.0

    return {
        "moeda": MOEDA,
        "valor_hora": valor_hora,
        "valor_hora_fmt": base.fmt_moeda(valor_hora, MOEDA),
        "dias_uteis_por_mes": dias_mes,
        "itens": detalhados,
        "comunidades": comunidades,
        "total_horas": total_horas,
        "total_horas_fmt": base.fmt_num(total_horas, 0) + " h",
        "valor_total": valor_total,
        "valor_total_fmt": base.fmt_moeda(valor_total, MOEDA),
        "duracao_dias": duracao_dias,
        "duracao_dias_fmt": base.fmt_num(duracao_dias, 0) + " dias úteis",
        "duracao_meses": duracao_meses,
        "meses": meses,
        "avisos": sorted(set(avisos)),
    }
