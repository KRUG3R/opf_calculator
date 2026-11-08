"""Partes comuns aos recursos DynamoDB: escolhas, blocos de capacidade e precos."""

import aws_pricing
from . import base

OFFER_CODE = "AmazonDynamoDB"

BLOCO_LEITURA = 4096.0  # 4 KB por unidade de leitura
BLOCO_ESCRITA = 1024.0  # 1 KB por unidade de escrita
SEGUNDOS_POR_DIA = 86400.0

MODOS_CAPACIDADE = [("on_demand", "On-demand (por request)"), ("provisionado", "Provisionado + auto scaling")]
CLASSES_TABELA = [("standard", "Standard"), ("standard_ia", "Standard-IA (acesso infrequente)")]
CONSISTENCIAS = [("eventual", "Eventualmente consistente"), ("forte", "Fortemente consistente")]
PROJECOES = [("keys_only", "KEYS_ONLY"), ("all", "ALL")]

SUFIXOS_PADRAO = {
    "rru": "ReadRequestUnits",
    "wru": "WriteRequestUnits",
    "rcu_hora": "ReadCapacityUnit-Hrs",
    "wcu_hora": "WriteCapacityUnit-Hrs",
    "storage_gb_mes": "TimedStorage-ByteHrs",
}
SUFIXOS_IA = {
    "ia_rru": "IA-ReadRequestUnits",
    "ia_wru": "IA-WriteRequestUnits",
    "ia_rcu_hora": "IA-ReadCapacityUnit-Hrs",
    "ia_wcu_hora": "IA-WriteCapacityUnit-Hrs",
    "ia_storage_gb_mes": "IA-TimedStorage-ByteHrs",
}
SUFIXOS_EXTRAS = {
    "pitr_gb_mes": "TimedPITRStorage-ByteHrs",
    "streams_requests": "Streams-Requests",
}


def extrair_precos(offer, region):
    """Faixas de preco de capacidade, armazenamento, PITR e Streams."""
    procurados = {}
    procurados.update(SUFIXOS_PADRAO)
    procurados.update(SUFIXOS_IA)
    procurados.update(SUFIXOS_EXTRAS)

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
            faixas = aws_pricing.faixas_on_demand(offer, sku)
            if faixas:
                valores[chave] = faixas
            break

    if all(c in valores for c in SUFIXOS_PADRAO):
        return valores
    return None


def seletor_de_faixas(preco, entradas):
    """Devolve uma funcao que escolhe as faixas certas conforme classe e free tier."""
    prefixo = "ia_" if entradas.get("classe_tabela") == "standard_ia" else ""

    def faixas(chave, ignorar_classe=False):
        chaves = [chave] if ignorar_classe else [prefixo + chave, chave]
        obtidas = []
        for candidata in chaves:
            obtidas = preco.valores.get(candidata) or []
            if obtidas:
                break
        return obtidas if entradas.get("aplicar_free_tier") else base.faixas_sem_gratuito(obtidas)

    return faixas


def unidades_por_bloco(bytes_totais, bloco):
    """Blocos de capacidade consumidos, sempre arredondando para cima."""
    import math

    if bytes_totais <= 0:
        return 0.0
    return float(math.ceil(bytes_totais / bloco))
