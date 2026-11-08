"""Beneficios: economia esperada com a adocao do Aggregated Polling.

O racional e que hoje cada requisicao de saldo tem um custo unitario, e parte
desse custo e variavel (cai quando o volume cai). O polling agregado reduz o
volume de requisicoes; a economia incide so sobre a parcela variavel:

    beneficio mensal = volume x custo unitario x % de reducao x % variavel
"""

from recursos import base

MOEDA = "BRL"

PADROES = {
    "volume_requisicoes_mes": 613000000.0,
    "custo_unitario": 0.00042,
    "percentual_custo_fixo": 70.0,
    "percentual_custo_variavel": 30.0,
    "percentual_reducao": 71.0,
    "racional": "",
}

CAMPOS_NUMERICOS = (
    "volume_requisicoes_mes",
    "custo_unitario",
    "percentual_custo_fixo",
    "percentual_custo_variavel",
    "percentual_reducao",
)
PERCENTUAIS = ("percentual_custo_fixo", "percentual_custo_variavel", "percentual_reducao")


def normalizar(dados, atual=None):
    """Valida e normaliza a secao de beneficios vinda do formulario."""
    atual = dict(PADROES, **(atual or {}))
    dados = dados or {}
    resultado = {}
    erros = []

    for campo in CAMPOS_NUMERICOS:
        bruto = dados.get(campo, atual.get(campo))
        try:
            valor = float(str(bruto).replace(",", "."))
        except (TypeError, ValueError):
            erros.append("'%s' não é um número válido: %r" % (campo, bruto))
            continue
        if valor < 0:
            erros.append("'%s' não pode ser negativo." % campo)
            continue
        if campo in PERCENTUAIS and valor > 100:
            erros.append("'%s' não pode passar de 100." % campo)
            continue
        resultado[campo] = valor

    resultado["racional"] = str(dados.get("racional", atual.get("racional", "")) or "")

    if erros:
        raise ValueError(" ".join(erros))
    return resultado


def calcular(config):
    """Beneficio mensal e anual, com a cadeia de calculo aberta."""
    cfg = dict(PADROES, **(config.get("beneficios") or {}))
    avisos = []

    volume = float(cfg["volume_requisicoes_mes"])
    unitario = float(cfg["custo_unitario"])
    pct_fixo = float(cfg["percentual_custo_fixo"])
    pct_variavel = float(cfg["percentual_custo_variavel"])
    pct_reducao = float(cfg["percentual_reducao"])

    soma = pct_fixo + pct_variavel
    if abs(soma - 100.0) > 0.01:
        avisos.append(
            "Custo fixo (%s%%) e variável (%s%%) somam %s%%, não 100%%: confira a divisão."
            % (base.fmt_num(pct_fixo, 0), base.fmt_num(pct_variavel, 0), base.fmt_num(soma, 0))
        )

    custo_total = volume * unitario
    custo_fixo = custo_total * pct_fixo / 100.0
    custo_variavel = custo_total * pct_variavel / 100.0

    requisicoes_evitadas = volume * pct_reducao / 100.0
    beneficio_mes = custo_variavel * pct_reducao / 100.0
    beneficio_ano = beneficio_mes * 12

    return {
        "moeda": MOEDA,
        "entradas": cfg,
        "volume_requisicoes_mes": volume,
        "volume_fmt": base.fmt_int(volume),
        "custo_unitario": unitario,
        "custo_unitario_fmt": base.fmt_moeda(unitario, MOEDA, 5),
        "custo_total_mes": custo_total,
        "custo_total_mes_fmt": base.fmt_moeda(custo_total, MOEDA),
        "custo_fixo_mes": custo_fixo,
        "custo_fixo_mes_fmt": base.fmt_moeda(custo_fixo, MOEDA),
        "custo_variavel_mes": custo_variavel,
        "custo_variavel_mes_fmt": base.fmt_moeda(custo_variavel, MOEDA),
        "percentual_custo_fixo": pct_fixo,
        "percentual_custo_variavel": pct_variavel,
        "percentual_reducao": pct_reducao,
        "requisicoes_evitadas": requisicoes_evitadas,
        "requisicoes_evitadas_fmt": base.fmt_int(requisicoes_evitadas),
        "beneficio_mes": beneficio_mes,
        "beneficio_mes_fmt": base.fmt_moeda(beneficio_mes, MOEDA),
        "beneficio_ano": beneficio_ano,
        "beneficio_ano_fmt": base.fmt_moeda(beneficio_ano, MOEDA),
        "racional": cfg.get("racional", ""),
        "passos": [
            {
                "rotulo": "Volume de requisições de saldo",
                "detalhe": "por mês",
                "valor": base.fmt_int(volume),
            },
            {
                "rotulo": "Custo unitário por requisição",
                "detalhe": "×",
                "valor": base.fmt_moeda(unitario, MOEDA, 5),
            },
            {
                "rotulo": "Custo total das requisições",
                "detalhe": "= volume × unitário",
                "valor": base.fmt_moeda(custo_total, MOEDA),
                "destaque": True,
            },
            {
                "rotulo": "Parcela variável",
                "detalhe": "× %s%% (a parcela fixa de %s%% não cai com o volume)"
                % (base.fmt_num(pct_variavel, 0), base.fmt_num(pct_fixo, 0)),
                "valor": base.fmt_moeda(custo_variavel, MOEDA),
            },
            {
                "rotulo": "Redução esperada",
                "detalhe": "× %s%%" % base.fmt_num(pct_reducao, 0),
                "valor": base.fmt_moeda(beneficio_mes, MOEDA),
                "destaque": True,
            },
        ],
        "avisos": avisos,
    }
