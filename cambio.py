"""Cotacao do dolar, para expressar em reais os custos cobrados em USD.

Segue o mesmo padrao dos precos da AWS: consulta uma fonte publica, guarda em
cache local e cai no valor gravado em config.json quando a consulta falha.
"""

import json
import os
import time
import urllib.error
import urllib.request

PADROES = {
    "usd_brl": 5.503,
    "buscar_automatico": True,
    "url": "https://economia.awesomeapi.com.br/json/last/USD-BRL",
    "cache_horas": 6,
    "timeout_segundos": 10,
    "cache_dir": ".cache",
    "fonte_fallback": "config.json",
}

ARQUIVO_CACHE = "cambio-usd-brl.json"


def _config(config):
    return dict(PADROES, **(config.get("cambio") or {}))


def _caminho_cache(cfg):
    os.makedirs(cfg["cache_dir"], exist_ok=True)
    return os.path.join(cfg["cache_dir"], ARQUIVO_CACHE)


def _ler_cache(cfg):
    caminho = _caminho_cache(cfg)
    if not os.path.exists(caminho):
        return None
    idade_h = (time.time() - os.path.getmtime(caminho)) / 3600.0
    if idade_h > float(cfg["cache_horas"]):
        return None
    try:
        with open(caminho, "r") as fp:
            return json.load(fp)
    except (ValueError, OSError):
        return None


def _gravar_cache(cfg, dados):
    try:
        with open(_caminho_cache(cfg), "w") as fp:
            json.dump(dados, fp)
    except OSError:
        pass


def _buscar(cfg):
    """Le a cotacao de venda na fonte publica."""
    req = urllib.request.Request(cfg["url"], headers={"User-Agent": "calculadora-aws-custom/1.0"})
    with urllib.request.urlopen(req, timeout=float(cfg["timeout_segundos"])) as resp:
        bruto = json.loads(resp.read().decode("utf-8"))
    cotacao = bruto.get("USDBRL") or {}
    valor = cotacao.get("bid") or cotacao.get("ask")
    if valor is None:
        raise ValueError("resposta sem cotação")
    return {
        "valor": float(valor),
        "atualizado_em": cotacao.get("create_date"),
        "fonte": "AwesomeAPI (USD-BRL, cotação de venda)",
    }


def obter(config, override=None):
    """Retorna a cotacao em vigor: override da tela, cache, API ou fallback."""
    cfg = _config(config)
    fallback = float(cfg["usd_brl"])

    if override is not None:
        try:
            valor = float(str(override).replace(",", "."))
        except (TypeError, ValueError):
            valor = None
        if valor and valor > 0:
            return {"valor": valor, "fonte": "informado na tela", "atualizado_em": None, "aviso": None}

    if not cfg["buscar_automatico"]:
        return {"valor": fallback, "fonte": cfg["fonte_fallback"], "atualizado_em": None, "aviso": None}

    cacheado = _ler_cache(cfg)
    if cacheado:
        return dict(cacheado, aviso=None, fonte=cacheado.get("fonte", "") + " (cache)")

    try:
        dados = _buscar(cfg)
        _gravar_cache(cfg, dados)
        return dict(dados, aviso=None)
    except (urllib.error.URLError, ValueError, OSError, KeyError) as exc:
        return {
            "valor": fallback,
            "fonte": cfg["fonte_fallback"],
            "atualizado_em": None,
            "aviso": "Não foi possível consultar a cotação do dólar (%s). Usando o valor de config.json." % exc,
        }
