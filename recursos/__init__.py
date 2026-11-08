"""Registro de recursos da calculadora.

Para adicionar um recurso novo (DynamoDB, ECS, ...), crie um modulo neste pacote
expondo:

    ID, NOME, DESCRICAO, OFFER_CODE, STATUS   # metadados
    CAMPOS                                    # grupos de campos (ver base.py)
    extrair_precos(offer, region) -> dict|None
    calcular(entradas, recurso_cfg, preco) -> dict

e registre o modulo em ATIVOS. Os defaults, limites e precos de fallback ficam em
config.json, em "recursos".<id>.
"""

import copy

import aws_pricing
import cambio

from . import (
    alb,
    api_gateway,
    base,
    cloudwatch,
    dynamodb_paginacao,
    dynamodb_saldo,
    ecs_carga,
    ecs_consulta,
)

ATIVOS = [
    api_gateway,
    alb,
    ecs_consulta,
    ecs_carga,
    dynamodb_saldo,
    dynamodb_paginacao,
    cloudwatch,
]

# Recursos ja previstos, sem modelo de custo definido ainda. Aparecem na tela de
# totais como planejados e viram modulos em ATIVOS quando o modelo for fechado.
# "ordem" posiciona o recurso no fluxo da chamada, independente de estar ativo ou
# planejado: API GW -> ALB -> ECS de consulta -> DynamoDB. O ECS de carga alimenta
# as tabelas por fora do caminho da requisicao e o CloudWatch observa os ECSs.
PLANEJADOS = []


# Campos que aparecem na tela de totais para simulacao rapida. Mexer neles
# recalcula todos os recursos, inclusive os que herdam a volumetria do ALB.
CONTROLES_GLOBAIS = [
    ("alb", "tpm_por_if"),
    ("alb", "numero_ifs"),
    ("alb", "registros_por_pagina"),
    ("alb", "response_time_s"),
]


def meta(modulo):
    return {
        "id": modulo.ID,
        "nome": modulo.NOME,
        "nome_curto": getattr(modulo, "NOME_CURTO", modulo.NOME),
        "ordem": getattr(modulo, "ORDEM", 100),
        "descricao": modulo.DESCRICAO,
        "offer_code": modulo.OFFER_CODE,
        "status": modulo.STATUS,
    }


def _por_fluxo(itens):
    return sorted(itens, key=lambda i: (i.get("ordem", 100), i["nome"]))


def listar(config):
    """Metadados de todos os recursos, na ordem do fluxo da chamada."""
    itens = []
    for modulo in ATIVOS:
        dados = meta(modulo)
        dados["habilitado"] = bool(config.get("recursos", {}).get(modulo.ID, {}).get("habilitado", True))
        itens.append(dados)
    for planejado in PLANEJADOS:
        dados = dict(planejado)
        dados["habilitado"] = False
        itens.append(dados)
    return _por_fluxo(itens)


def obter(recurso_id):
    for modulo in ATIVOS:
        if modulo.ID == recurso_id:
            return modulo
    return None


def obter_planejado(recurso_id):
    for planejado in PLANEJADOS:
        if planejado["id"] == recurso_id:
            return planejado
    return None


def config_do_recurso(config, recurso_id):
    recurso_cfg = config.get("recursos", {}).get(recurso_id)
    if recurso_cfg is None:
        raise ValueError("Recurso '%s' nao esta configurado em config.json." % recurso_id)
    return recurso_cfg


def calcular(config, recurso_id, recebidas=None):
    """Calcula um recurso. Retorna o detalhamento completo, pronto para a tela."""
    modulo = obter(recurso_id)
    if modulo is None:
        raise ValueError("Recurso '%s' nao existe ou ainda nao tem modelo de custo." % recurso_id)

    recurso_cfg = config_do_recurso(config, recurso_id)
    entradas = base.normalizar(modulo.CAMPOS, recurso_cfg["entradas"], recebidas)
    if hasattr(modulo, "region_efetiva"):
        region = modulo.region_efetiva(entradas, config)
    else:
        region = entradas["region"]
    preco, aviso_preco = aws_pricing.obter_precos(
        config,
        region,
        modulo.OFFER_CODE,
        modulo.extrair_precos,
        recurso_cfg.get("precos_fallback"),
    )
    resultado = modulo.calcular(entradas, recurso_cfg, preco, config)
    resultado.update(meta(modulo))
    resultado["entradas"] = entradas
    resultado["preco_unitario"] = preco.to_dict()
    if aviso_preco:
        resultado["avisos"] = [aviso_preco] + list(resultado.get("avisos") or [])
    return resultado


def config_efetivo(config, recebidas_por_recurso):
    """Aplica os ajustes recebidos sobre os defaults, para que a heranca enxergue os novos valores.

    Sem isso, mexer no TPM do ALB nao mudaria o ECS e o API Gateway, que leem a
    volumetria do ALB direto da configuracao.
    """
    if not recebidas_por_recurso:
        return config
    novo = copy.deepcopy(config)
    for recurso_id, recebidas in recebidas_por_recurso.items():
        modulo = obter(recurso_id)
        recurso_cfg = novo.get("recursos", {}).get(recurso_id)
        if modulo is None or not recurso_cfg:
            continue
        recurso_cfg["entradas"] = base.normalizar(modulo.CAMPOS, recurso_cfg["entradas"], recebidas)
    return novo


def controles(config, cotacao=None):
    """Campos de simulacao rapida da tela de totais, com os valores em vigor."""
    itens = []
    if cotacao:
        itens.append({
            "recurso": "cambio",
            "recurso_nome": "Câmbio",
            "campo": {
                "nome": "cambio",
                "rotulo": "Dólar (R$)",
                "tipo": "numero",
                "passo": 0.01,
                "minimo": 0,
                "maximo": None,
                "ajuda": cotacao.get("fonte"),
                "visivel_quando": None,
            },
            "valor": round(cotacao["valor"], 4),
        })
    for recurso_id, nome_campo in CONTROLES_GLOBAIS:
        modulo = obter(recurso_id)
        recurso_cfg = config.get("recursos", {}).get(recurso_id)
        if modulo is None or not recurso_cfg:
            continue
        campo = base.encontrar_campo(modulo.CAMPOS, nome_campo)
        if campo is None or nome_campo not in recurso_cfg["entradas"]:
            continue
        itens.append({
            "recurso": recurso_id,
            "recurso_nome": modulo.NOME,
            "campo": campo,
            "valor": recurso_cfg["entradas"][nome_campo],
        })
    return itens


def calcular_totais(config, recebidas_por_recurso=None):
    """Soma os recursos habilitados e devolve o resumo de cada um para a tela de totais."""
    recebidas_por_recurso = dict(recebidas_por_recurso or {})
    # A cotação vem junto dos ajustes da tela, fora do dicionário de recursos.
    cotacao = cambio.obter(config, recebidas_por_recurso.pop("cambio", None))
    config_base = config
    config = config_efetivo(config, recebidas_por_recurso)
    itens = []
    avisos = []
    total_mes = 0.0
    moeda = "USD"

    for modulo in ATIVOS:
        recurso_cfg = config.get("recursos", {}).get(modulo.ID, {})
        if not recurso_cfg.get("habilitado", True):
            itens.append(dict(meta(modulo), status="desabilitado", habilitado=False))
            continue
        try:
            resultado = calcular(config, modulo.ID, recebidas_por_recurso.get(modulo.ID))
        except ValueError as exc:
            itens.append(dict(meta(modulo), status="erro", erro=str(exc), habilitado=True))
            avisos.append("%s: %s" % (modulo.NOME, exc))
            continue
        total_mes += resultado["total_mes"]
        moeda = resultado["moeda"]
        avisos.extend(resultado.get("avisos") or [])
        itens.append(
            dict(
                meta(modulo),
                habilitado=True,
                total_mes=resultado["total_mes"],
                total_mes_fmt=base.fmt_moeda(resultado["total_mes"], resultado["moeda"]),
                moeda=resultado["moeda"],
                region=resultado["entradas"].get("region"),
                resumo=resultado["resumo"],
            )
        )

    for planejado in PLANEJADOS:
        itens.append(dict(planejado, habilitado=False))

    itens = _por_fluxo(itens)
    for item in itens:
        if item.get("total_mes") and total_mes:
            item["participacao"] = item["total_mes"] / total_mes
        else:
            item["participacao"] = 0.0

    horas_mes = 720.0
    for modulo in ATIVOS:
        entradas = config.get("recursos", {}).get(modulo.ID, {}).get("entradas", {})
        if entradas.get("horas_por_dia") and entradas.get("dias_por_mes"):
            horas_mes = float(entradas["horas_por_dia"]) * float(entradas["dias_por_mes"])
            break

    for item in itens:
        if item.get("total_mes"):
            item["total_mes_brl"] = item["total_mes"] * cotacao["valor"]
            item["total_mes_brl_fmt"] = base.fmt_moeda(item["total_mes_brl"], "BRL")

    if cotacao.get("aviso"):
        avisos.append(cotacao["aviso"])

    total_brl = total_mes * cotacao["valor"]
    resultado = {
        "moeda": moeda,
        "total_mes": total_mes,
        "total_mes_fmt": base.fmt_moeda(total_mes, moeda),
        "total_ano_fmt": base.fmt_moeda(total_mes * 12, moeda),
        "total_hora_fmt": base.fmt_moeda(total_mes / horas_mes if horas_mes else 0, moeda, 4),
        "cambio": dict(cotacao, valor_fmt=base.fmt_moeda(cotacao["valor"], "BRL", 4)),
        "total_mes_brl": total_brl,
        "total_mes_brl_fmt": base.fmt_moeda(total_brl, "BRL"),
        "total_ano_brl_fmt": base.fmt_moeda(total_brl * 12, "BRL"),
        "recursos": itens,
        "controles": controles(config, cotacao),
        "avisos": sorted(set(avisos)),
    }

    if recebidas_por_recurso:
        resultado["baseline"] = _baseline(config_base, moeda)
    return resultado


# Cenarios de otimizacao avaliados na tela de proposta: cada um e recalculado
# sobre a configuracao atual e a economia sai da diferenca de total.
OPORTUNIDADES = [
    {
        "titulo": "Leitura eventual na tabela de saldo",
        "detalhe": "O polling de saldo agregado costuma tolerar consistência eventual, "
        "que custa metade da leitura fortemente consistente.",
        "overrides": {"dynamodb-saldo": {"consistencia_leitura": "eventual"}},
    },
    {
        "titulo": "Capacidade provisionada no DynamoDB",
        "detalhe": "A carga é constante 24×7, caso em que on-demand é mais caro que "
        "capacidade provisionada com auto scaling.",
        "overrides": {
            "dynamodb-saldo": {"modo_capacidade": "provisionado"},
            "dynamodb-paginacao": {"modo_capacidade": "provisionado"},
        },
    },
    {
        "titulo": "GSI if_id com projeção KEYS_ONLY",
        "detalhe": "Se o polling só precisa dos resourceIds, o índice guarda 210 B por "
        "item em vez de 365 B e cada consulta lê menos blocos.",
        "overrides": {"dynamodb-saldo": {"gsi_if_projecao": "keys_only"}},
    },
    {
        "titulo": "Tasks em Graviton (ARM)",
        "detalhe": "O Fargate ARM custa cerca de 20% menos por vCPU-hora e por GB-hora.",
        "overrides": {"ecs-consulta": {"arquitetura": "arm"}, "ecs-carga": {"arquitetura": "arm"}},
    },
    {
        "titulo": "Token de paginação imutável",
        "detalhe": "Sem reescrever o cursor a cada página, desaparece uma escrita por "
        "página servida na tabela de paginação.",
        "overrides": {"dynamodb-paginacao": {"atualiza_cursor_por_pagina": False}},
    },
]

# Indicadores da tela de proposta: (recurso, campo, rotulo, formato).
INDICADORES = [
    ("alb", "numero_ifs", "Instituições financeiras", "inteiro"),
    ("alb", "tpm_por_if", "TPM por IF", "inteiro"),
    ("alb", "registros_por_pagina", "Registros por página", "inteiro"),
    ("alb", "response_time_s", "Response time alvo", "segundos"),
    ("ecs-carga", "numero_clientes", "Clientes monitorados", "inteiro"),
    ("ecs-carga", "eventos_por_minuto", "Eventos de saldo por minuto", "inteiro"),
    ("dynamodb-saldo", "numero_registros", "Registros na tabela de saldo", "inteiro"),
]


def _formatar_indicador(valor, formato):
    if formato == "segundos":
        return base.fmt_num(valor, 1) + " s"
    return base.fmt_int(valor)


def visao_geral(config):
    """Dados da tela de proposta: total, indicadores, fluxo, custos e oportunidades."""
    totais = calcular_totais(config)
    moeda = totais["moeda"]
    total_mes = totais["total_mes"]

    indicadores = []
    for recurso_id, campo, rotulo, formato in INDICADORES:
        entradas = config.get("recursos", {}).get(recurso_id, {}).get("entradas")
        if not entradas or campo not in entradas:
            continue
        indicadores.append({
            "id": campo,
            "rotulo": rotulo,
            "valor": _formatar_indicador(entradas[campo], formato),
        })

    # Premissa de modelo, não de volumetria: vale o comparativo da tela de slots.
    slot_variavel = (config.get("premissas") or {}).get("slot_variavel", True)
    indicadores.append({
        "id": "slot_variavel",
        "rotulo": "Slot adotado",
        "valor": "Variável" if slot_variavel else "Fixo",
        "nota": "janela relativa ao momento da consulta" if slot_variavel else "janelas fixas de 1 minuto",
        "href": "#/slots",
    })

    entradas_alb = config.get("recursos", {}).get("alb", {}).get("entradas", {})
    tpm_total = entradas_alb.get("tpm_por_if", 0) * entradas_alb.get("numero_ifs", 0)
    if tpm_total:
        indicadores.insert(
            2,
            {
                "id": "tpm_total",
                "rotulo": "TPM total",
                "valor": base.fmt_int(tpm_total),
                "nota": "%s por IF × %s IFs"
                % (
                    base.fmt_int(entradas_alb.get("tpm_por_if", 0)),
                    base.fmt_int(entradas_alb.get("numero_ifs", 0)),
                ),
            },
        )

    ativos = [r for r in totais["recursos"] if r.get("total_mes")]
    custos = sorted(ativos, key=lambda r: r["total_mes"], reverse=True)
    maior = custos[0]["total_mes"] if custos else 0.0
    for item in custos:
        item["proporcao_da_maior"] = item["total_mes"] / maior if maior else 0.0

    fluxo = [
        {
            "id": r["id"],
            "nome": r["nome"],
            "nome_curto": r.get("nome_curto", r["nome"]),
            "descricao": r.get("descricao", ""),
            "ordem": r.get("ordem", 100),
            "total_mes_fmt": r.get("total_mes_fmt"),
            "status": r["status"],
        }
        for r in totais["recursos"]
    ]

    oportunidades = []
    for cenario in OPORTUNIDADES:
        if not all(rid in config.get("recursos", {}) for rid in cenario["overrides"]):
            continue
        try:
            alternativo = calcular_totais(config, cenario["overrides"])
        except ValueError:
            continue
        economia = total_mes - alternativo["total_mes"]
        oportunidades.append({
            "titulo": cenario["titulo"],
            "detalhe": cenario["detalhe"],
            "total_mes_fmt": alternativo["total_mes_fmt"],
            "economia": economia,
            "economia_fmt": base.fmt_moeda(abs(economia), moeda),
            "economia_pct": (economia / total_mes * 100) if total_mes else 0.0,
        })
    oportunidades.sort(key=lambda o: o["economia"], reverse=True)

    return {
        "moeda": moeda,
        "total_mes": total_mes,
        "total_mes_fmt": totais["total_mes_fmt"],
        "total_ano_fmt": totais["total_ano_fmt"],
        "total_hora_fmt": totais["total_hora_fmt"],
        "cambio": totais["cambio"],
        "total_mes_brl": totais["total_mes_brl"],
        "total_mes_brl_fmt": totais["total_mes_brl_fmt"],
        "total_ano_brl_fmt": totais["total_ano_brl_fmt"],
        "region": entradas_alb.get("region"),
        "indicadores": indicadores,
        "custos": custos,
        "fluxo": fluxo,
        "oportunidades": oportunidades,
        "avisos": totais["avisos"],
    }


def _baseline(config, moeda):
    """Totais com os valores gravados em config.json, para comparar com a simulacao."""
    por_recurso = {}
    total = 0.0
    for modulo in ATIVOS:
        if not config.get("recursos", {}).get(modulo.ID, {}).get("habilitado", True):
            continue
        try:
            resultado = calcular(config, modulo.ID)
        except ValueError:
            continue
        por_recurso[modulo.ID] = resultado["total_mes"]
        total += resultado["total_mes"]
    return {
        "total_mes": total,
        "total_mes_fmt": base.fmt_moeda(total, moeda),
        "por_recurso": por_recurso,
    }
