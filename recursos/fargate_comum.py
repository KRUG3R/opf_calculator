"""Partes comuns aos recursos ECS Fargate: tamanhos de task, precos e custo por task-hora."""

import aws_pricing
from . import base
from .base import fmt_num

OFFER_CODE = "AmazonECS"

ARQUITETURAS = [("x86", "x86_64 (Intel/AMD)"), ("arm", "ARM64 (Graviton)")]

# Combinacoes validas de Fargate: vCPU -> (memoria minima, memoria maxima) em GB.
COMBOS_FARGATE = {
    0.25: (0.5, 2),
    0.5: (1, 4),
    1: (2, 8),
    2: (4, 16),
    4: (8, 30),
    8: (16, 60),
    16: (32, 120),
}

# Fargate nao cobra os primeiros 20 GB de armazenamento efemero de cada task.
STORAGE_EFEMERO_GRATUITO_GB = 20

SUFIXOS = {
    "x86": {"vcpu_hora": "Fargate-vCPU-Hours:perCPU", "gb_hora": "Fargate-GB-Hours"},
    "arm": {"vcpu_hora": "Fargate-ARM-vCPU-Hours:perCPU", "gb_hora": "Fargate-ARM-GB-Hours"},
}
SUFIXO_STORAGE = "Fargate-EphemeralStorage-GB-Hours"


def extrair_precos(offer, region):
    """Localiza os SKUs de vCPU-hora, GB-hora (x86 e ARM) e armazenamento efemero."""
    procurados = {
        "vcpu_hora_x86": SUFIXOS["x86"]["vcpu_hora"],
        "gb_hora_x86": SUFIXOS["x86"]["gb_hora"],
        "vcpu_hora_arm": SUFIXOS["arm"]["vcpu_hora"],
        "gb_hora_arm": SUFIXOS["arm"]["gb_hora"],
        "storage_efemero_gb_hora": SUFIXO_STORAGE,
    }
    valores = {}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        ut = attrs.get("usagetype", "")
        for chave, sufixo in procurados.items():
            if chave in valores or not base.usagetype_padrao(ut, sufixo):
                continue
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is not None:
                valores[chave] = preco
            break
    obrigatorios = ("vcpu_hora_x86", "gb_hora_x86", "vcpu_hora_arm", "gb_hora_arm")
    if all(c in valores for c in obrigatorios):
        valores.setdefault("storage_efemero_gb_hora", 0.0)
        return valores
    return None


def precos_da_task(entradas, preco):
    """Precos unitarios da arquitetura escolhida e custo de uma task-hora."""
    arq = entradas["arquitetura"]
    preco_vcpu = preco["vcpu_hora_%s" % arq]
    preco_gb = preco["gb_hora_%s" % arq]
    preco_storage = preco.valores.get("storage_efemero_gb_hora", 0.0)
    storage_cobrado_gb = max(0.0, entradas["armazenamento_efemero_gb"] - STORAGE_EFEMERO_GRATUITO_GB)
    custo_hora = (
        entradas["vcpu_por_task"] * preco_vcpu
        + entradas["memoria_gb_por_task"] * preco_gb
        + storage_cobrado_gb * preco_storage
    )
    return {
        "arquitetura": arq,
        "arquitetura_rotulo": dict(ARQUITETURAS)[arq],
        "preco_vcpu": preco_vcpu,
        "preco_gb": preco_gb,
        "preco_storage": preco_storage,
        "storage_cobrado_gb": storage_cobrado_gb,
        "custo_task_hora": custo_hora,
    }


def validar_combo(entradas):
    """Avisos quando vCPU e memoria nao formam um tamanho suportado pelo Fargate."""
    avisos = []
    faixa = COMBOS_FARGATE.get(entradas["vcpu_por_task"])
    if faixa is None:
        avisos.append(
            "vCPU por task de %s nao e um tamanho de Fargate (validos: %s)."
            % (fmt_num(entradas["vcpu_por_task"], 2), ", ".join(fmt_num(v, 2) for v in sorted(COMBOS_FARGATE)))
        )
    elif not faixa[0] <= entradas["memoria_gb_por_task"] <= faixa[1]:
        avisos.append(
            "Com %s vCPU o Fargate aceita de %s a %s GB de memoria; %s GB nao e uma combinacao valida."
            % (
                fmt_num(entradas["vcpu_por_task"], 2),
                fmt_num(faixa[0], 1),
                fmt_num(faixa[1], 1),
                fmt_num(entradas["memoria_gb_por_task"], 1),
            )
        )
    return avisos
