"""Acesso a AWS Price List API, com cache local e fallback por recurso.

Cada recurso informa seu offer code (AWSELB, AmazonDynamoDB, AmazonECS...) e uma
funcao extratora que localiza, no offer file da regiao, os precos que lhe interessam.
"""

import json
import os
import time
import urllib.error
import urllib.request


class Precos:
    """Precos unitarios de um recurso em uma regiao.

    'valores' e um dicionario livre, definido pelo extrator do recurso
    (ex.: {"alb_hora": 0.034, "lcu_hora": 0.011}).
    """

    def __init__(self, region, valores, moeda, fonte, publicado_em=None):
        self.region = region
        self.valores = valores
        self.moeda = moeda
        self.fonte = fonte
        self.publicado_em = publicado_em

    def __getitem__(self, chave):
        return self.valores[chave]

    def to_dict(self):
        dados = {
            "region": self.region,
            "moeda": self.moeda,
            "fonte": self.fonte,
            "publicado_em": self.publicado_em,
        }
        dados.update(self.valores)
        return dados


def _caminho_cache(cfg, offer_code, region):
    cache_dir = cfg.get("cache_dir", ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "%s-%s.json" % (offer_code.lower(), region))


def _ler_cache(cfg, offer_code, region):
    caminho = _caminho_cache(cfg, offer_code, region)
    if not os.path.exists(caminho):
        return None
    idade_h = (time.time() - os.path.getmtime(caminho)) / 3600.0
    if idade_h > float(cfg.get("cache_horas", 24)):
        return None
    try:
        with open(caminho, "r") as fp:
            return json.load(fp)
    except (ValueError, OSError):
        return None


def _gravar_cache(cfg, offer_code, region, dados):
    try:
        with open(_caminho_cache(cfg, offer_code, region), "w") as fp:
            json.dump(dados, fp)
    except OSError:
        pass


def _baixar_offer_regional(cfg, offer_code, region):
    url = "%s/offers/v1.0/aws/%s/current/%s/index.json" % (
        cfg.get("base_url", "https://pricing.us-east-1.amazonaws.com"),
        offer_code,
        region,
    )
    req = urllib.request.Request(url, headers={"User-Agent": "calculadora-aws-custom/1.0"})
    with urllib.request.urlopen(req, timeout=float(cfg.get("timeout_segundos", 20))) as resp:
        return json.loads(resp.read().decode("utf-8"))


def preco_on_demand(offer, sku):
    """Primeiro priceDimension on-demand do SKU, em USD."""
    termos = offer.get("terms", {}).get("OnDemand", {}).get(sku, {})
    for termo in termos.values():
        for dim in termo.get("priceDimensions", {}).values():
            valor = dim.get("pricePerUnit", {}).get("USD")
            if valor is not None:
                return float(valor)
    return None


def faixas_on_demand(offer, sku):
    """Faixas de preco escalonado de um SKU, ordenadas por inicio.

    Cada faixa e {"inicio", "fim" (None = sem teto), "preco", "unidade", "descricao"}.
    """
    faixas = []
    termos = offer.get("terms", {}).get("OnDemand", {}).get(sku, {})
    for termo in termos.values():
        for dim in termo.get("priceDimensions", {}).values():
            preco = dim.get("pricePerUnit", {}).get("USD")
            if preco is None:
                continue
            fim = dim.get("endRange")
            faixas.append({
                "inicio": float(dim.get("beginRange") or 0),
                "fim": None if fim in (None, "Inf") else float(fim),
                "preco": float(preco),
                "unidade": dim.get("unit"),
                "descricao": dim.get("description"),
            })
    return sorted(faixas, key=lambda f: f["inicio"])


def _converter_numeros(valor):
    """Converte numeros do fallback preservando dicionarios e listas aninhados."""
    if isinstance(valor, dict):
        return {k: _converter_numeros(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_converter_numeros(v) for v in valor]
    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return valor


def obter_precos(config, region, offer_code, extrator, fallback):
    """Retorna (Precos, aviso).

    'extrator' recebe (offer, region) e devolve um dicionario de precos, ou None
    quando nao encontrou tudo o que precisava. 'fallback' e a secao de precos
    locais do recurso em config.json.
    """
    cfg_api = config.get("pricing_api", {})
    aviso = None

    if cfg_api.get("habilitada", True):
        offer = _ler_cache(cfg_api, offer_code, region)
        origem = "cache local da AWS Price List API"
        if offer is None:
            try:
                offer = _baixar_offer_regional(cfg_api, offer_code, region)
                origem = "AWS Price List API (consulta ao vivo)"
                _gravar_cache(cfg_api, offer_code, region, offer)
            except (urllib.error.URLError, ValueError, OSError) as exc:
                offer = None
                aviso = "Nao foi possivel consultar a AWS Price List API (%s). Usando precos do config.json." % exc
        if offer is not None:
            valores = extrator(offer, region)
            if valores:
                return (
                    Precos(
                        region,
                        valores,
                        "USD",
                        "%s - offer %s" % (origem, offer_code),
                        offer.get("publicationDate"),
                    ),
                    aviso,
                )
            aviso = "A API respondeu mas nao trouxe os SKUs esperados de %s para %s. Usando precos do config.json." % (
                offer_code,
                region,
            )

    precos = (fallback or {}).get(region)
    if not precos:
        raise ValueError(
            "Sem preco para a regiao %s: a API nao respondeu e nao existe fallback em config.json." % region
        )
    valores = _converter_numeros(precos)
    return (
        Precos(
            region,
            valores,
            (fallback or {}).get("moeda", "USD"),
            "config.json (fallback): %s" % (fallback or {}).get("fonte", "valores locais"),
        ),
        aviso,
    )
