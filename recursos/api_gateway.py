"""API Gateway: porta de entrada das APIs das IFs, com uma rota para o ALB.

O preco e por request, escalonado por volume mensal, e nao depende do numero de
rotas nem do tamanho do payload. A integracao privada com o ALB usa VPC Link, que
em HTTP API nao tem custo adicional; a REST API so faz integracao privada com NLB.
"""

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "api-gateway"
NOME = "API Gateway"
NOME_CURTO = "API GW"
DESCRICAO = "Porta de entrada das APIs das IFs; encaminha as chamadas para o ALB."
OFFER_CODE = "AmazonApiGateway"
ORDEM = 10
STATUS = "ativo"

TIPOS_API = [("http", "HTTP API (recomendada)"), ("rest", "REST API")]
# Rotulo curto para tabelas e cartoes, sem o "(recomendada)" do seletor.
ROTULOS_API = {"http": "HTTP API", "rest": "REST API"}

INTEGRACOES = [
    ("vpc_link_alb", "VPC Link privado para o ALB"),
    ("http_publica", "Integração HTTP pública"),
]

# Tamanhos de cache dedicado oferecidos para REST API, em GB.
TAMANHOS_CACHE = ["nenhum", "0.5", "1.55", "6.05", "13.5", "28.4", "58.2", "118", "237"]

CAMPOS = [
    base.grupo(
        "Volumetria",
        [
            base.booleano(
                "herdar_volumetria_do_alb",
                "Herdar volumetria do ALB",
                ajuda="Usa região, TPM por IF, nº de IFs, fator de pico e janela do ALB.",
            ),
            base.regiao(),
            base.numero("tpm_por_if", "TPM por IF (transações/min)", visivel_quando={"herdar_volumetria_do_alb": False}),
            base.numero("numero_ifs", "Número estimado de IFs", minimo=1, visivel_quando={"herdar_volumetria_do_alb": False}),
            base.numero("fator_pico", "Fator de pico", passo=0.1, visivel_quando={"herdar_volumetria_do_alb": False}),
            base.numero("horas_por_dia", "Horas por dia", maximo=24, visivel_quando={"herdar_volumetria_do_alb": False}),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31, visivel_quando={"herdar_volumetria_do_alb": False}),
        ],
    ),
    base.grupo(
        "API",
        [
            base.escolha("tipo_api", "Tipo de API", TIPOS_API),
            base.escolha("integracao", "Integração com o ALB", INTEGRACOES),
            base.numero(
                "numero_rotas",
                "Rotas configuradas",
                minimo=1,
                ajuda="Informativo: o preço não varia com o número de rotas.",
            ),
            base.numero(
                "requests_por_transacao",
                "Requests no API GW por transação",
                passo=0.1,
                ajuda="1 = cada transação da IF é um request. Inclua aqui preflights ou retries.",
            ),
            base.escolha(
                "cache_gb",
                "Cache dedicado (GB)",
                [(t, "sem cache" if t == "nenhum" else t + " GB") for t in TAMANHOS_CACHE],
                ajuda="Só existe em REST API, cobrado por hora.",
                visivel_quando={"tipo_api": "rest"},
            ),
        ],
    ),
    base.grupo(
        "Limites da conta",
        [
            base.numero(
                "limite_rps_conta",
                "Limite de requests/s da conta",
                ajuda="Cota padrão do API Gateway na região; o pico é comparado com ela.",
            ),
        ],
    ),
]


# ---------- precos ----------

SUFIXO_HTTP = "ApiGatewayHttpRequest"
SUFIXO_REST = "ApiGatewayRequest"
PREFIXO_CACHE = "ApiGatewayCacheUsage:"


def extrair_precos(offer, region):
    """Faixas de preco de HTTP API e REST API, e o preco-hora de cada tamanho de cache."""
    valores = {"cache_hora": {}}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        ut = attrs.get("usagetype", "")

        if base.usagetype_padrao(ut, SUFIXO_HTTP):
            valores["http_requests"] = aws_pricing.faixas_on_demand(offer, sku)
        elif base.usagetype_padrao(ut, SUFIXO_REST):
            valores["rest_requests"] = aws_pricing.faixas_on_demand(offer, sku)
        elif PREFIXO_CACHE in ut:
            tamanho = ut.split(PREFIXO_CACHE, 1)[1].replace("GB", "")
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is not None:
                valores["cache_hora"][tamanho] = preco

    if valores.get("http_requests") and valores.get("rest_requests"):
        return valores
    return None


# ---------- volumetria ----------

CAMPOS_HERDADOS = ("region", "tpm_por_if", "numero_ifs", "fator_pico", "horas_por_dia", "dias_por_mes")


def resolver_volumetria(entradas, config):
    """Aplica a volumetria do ALB quando a heranca esta ligada."""
    avisos = []
    volumetria = {chave: entradas[chave] for chave in CAMPOS_HERDADOS}
    herdado = False

    if entradas.get("herdar_volumetria_do_alb"):
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if entradas_alb:
            for chave in CAMPOS_HERDADOS:
                if chave in entradas_alb:
                    volumetria[chave] = entradas_alb[chave]
            herdado = True
        else:
            avisos.append(
                "Heranca de volumetria ligada, mas o ALB nao esta configurado; usando os valores proprios deste recurso."
            )
    return volumetria, herdado, avisos


def region_efetiva(entradas, config):
    volumetria, _, _ = resolver_volumetria(entradas, config)
    return volumetria["region"]


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    """Calcula o custo mensal do API Gateway para a volumetria informada."""
    volumetria, herdado, avisos = resolver_volumetria(entradas, config)

    tpm_total = volumetria["tpm_por_if"] * volumetria["numero_ifs"] * volumetria["fator_pico"]
    tps_pico = tpm_total / 60.0
    horas_mes = volumetria["horas_por_dia"] * volumetria["dias_por_mes"]
    minutos_mes = horas_mes * 60.0

    requests_por_transacao = entradas["requests_por_transacao"]
    requests_mes = tpm_total * minutos_mes * requests_por_transacao
    rps_pico = tps_pico * requests_por_transacao

    tipo = entradas["tipo_api"]
    chave_faixas = "http_requests" if tipo == "http" else "rest_requests"
    faixas = preco.valores.get(chave_faixas) or []
    custo_requests, detalhes_faixas = base.custo_escalonado(faixas, requests_mes)

    # Cache dedicado existe apenas em REST API.
    cache_gb = entradas["cache_gb"] if tipo == "rest" else "nenhum"
    preco_cache_hora = 0.0
    if cache_gb != "nenhum":
        preco_cache_hora = (preco.valores.get("cache_hora") or {}).get(cache_gb, 0.0)
        if not preco_cache_hora:
            avisos.append("Sem preco para o cache de %s GB nesta regiao; o cache nao foi somado." % cache_gb)
    custo_cache = horas_mes * preco_cache_hora

    total = custo_requests + custo_cache

    if tipo == "rest" and entradas["integracao"] == "vpc_link_alb":
        avisos.append(
            "A REST API so faz integracao privada (VPC Link) com Network Load Balancer: "
            "para chegar ao ALB seria preciso um NLB na frente, ou usar HTTP API."
        )
    if rps_pico > entradas["limite_rps_conta"]:
        avisos.append(
            "O pico de %s requests/s passa do limite de %s da conta: e preciso pedir aumento de cota, "
            "senao as chamadas excedentes recebem 429."
            % (fmt_num(rps_pico, 0), fmt_num(entradas["limite_rps_conta"], 0))
        )

    moeda = preco.moeda
    rotulo_tipo = ROTULOS_API[tipo]

    return {
        "total_mes": total,
        "moeda": moeda,
        "resumo": [
            {"rotulo": "Tipo", "valor": rotulo_tipo},
            {"rotulo": "Requests/mês", "valor": fmt_int(requests_mes)},
            {"rotulo": "Pico", "valor": fmt_num(rps_pico, 0) + " req/s"},
            {"rotulo": "Custo por milhão", "valor": fmt_moeda(total / requests_mes * 1e6 if requests_mes else 0, moeda, 4)},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do API Gateway",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("Requests no mês", fmt_int(requests_mes), rotulo_tipo),
            cartao("Pico", fmt_num(rps_pico, 0) + " req/s", "limite da conta: " + fmt_num(entradas["limite_rps_conta"], 0)),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas,
            volumetria,
            herdado,
            preco,
            {
                "tpm_total": tpm_total,
                "tps_pico": tps_pico,
                "rps_pico": rps_pico,
                "requests_mes": requests_mes,
                "horas_mes": horas_mes,
                "tipo": tipo,
                "rotulo_tipo": rotulo_tipo,
                "faixas": detalhes_faixas,
                "custo_requests": custo_requests,
                "cache_gb": cache_gb,
                "preco_cache_hora": preco_cache_hora,
                "custo_cache": custo_cache,
                "total": total,
            },
        ),
    }


def _secoes(entradas, volumetria, herdado, preco, v):
    moeda = preco.moeda

    def faixa_rotulo(f):
        if f["fim"] is None:
            return "acima de %s" % fmt_int(f["inicio"])
        return "%s a %s" % (fmt_int(f["inicio"]), fmt_int(f["fim"]))

    linhas_faixas = [
        linha([
            celula(faixa_rotulo(f)),
            celula(fmt_int(f["quantidade"]), num=True),
            celula(fmt_moeda(f["preco"] * 1e6, moeda, 2) + "/milhão", num=True),
            celula(fmt_moeda(f["subtotal"], moeda), num=True),
        ])
        for f in v["faixas"]
    ]
    linhas_faixas.append(
        linha(
            [
                celula("Total de requests"),
                celula(fmt_int(v["requests_mes"]), num=True),
                celula(""),
                celula(fmt_moeda(v["custo_requests"], moeda), num=True),
            ],
            classe="total",
        )
    )

    linhas_custo = [
        linha([
            celula("Requests (%s)" % v["rotulo_tipo"]),
            celula(fmt_int(v["requests_mes"]), num=True),
            celula("escalonado", num=True),
            celula(fmt_moeda(v["custo_requests"], moeda), num=True),
        ])
    ]
    if v["cache_gb"] != "nenhum":
        linhas_custo.append(
            linha([
                celula("Cache dedicado de %s GB" % v["cache_gb"]),
                celula(fmt_int(v["horas_mes"]) + " h", num=True),
                celula(fmt_moeda(v["preco_cache_hora"], moeda, 4) + "/h", num=True),
                celula(fmt_moeda(v["custo_cache"], moeda), num=True),
            ])
        )
    linhas_custo += [
        linha(
            [celula("Total mensal"), celula(""), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)],
            classe="total",
        ),
        linha([
            celula("Custo por IF / mês"),
            celula(""),
            celula(""),
            celula(fmt_moeda(v["total"] / volumetria["numero_ifs"], moeda), num=True),
        ]),
        linha([
            celula("Custo por milhão de requests"),
            celula(""),
            celula(""),
            celula(fmt_moeda(v["total"] / v["requests_mes"] * 1e6 if v["requests_mes"] else 0, moeda, 4), num=True),
        ]),
    ]

    return [
        base.tabela(
            "Volumetria aplicada",
            None,
            [
                linha([celula("Origem"), celula("herdada do ALB" if herdado else "própria deste recurso")]),
                linha([celula("Região"), celula(volumetria["region"])]),
                linha([celula("TPM por IF"), celula(fmt_int(volumetria["tpm_por_if"]), num=True)]),
                linha([celula("Número de IFs"), celula(fmt_int(volumetria["numero_ifs"]), num=True)]),
                linha([celula("TPM total"), celula(fmt_int(v["tpm_total"]), num=True)]),
                linha([celula("Requests por transação"), celula(fmt_num(entradas["requests_por_transacao"], 2), num=True)]),
                linha([celula("Pico"), celula(fmt_num(v["rps_pico"], 1) + " req/s", num=True)]),
                linha([celula("Requests no mês"), celula(fmt_int(v["requests_mes"]), num=True)]),
                linha([
                    celula("Janela"),
                    celula("%s h/dia × %s dias" % (fmt_num(volumetria["horas_por_dia"], 0), fmt_int(volumetria["dias_por_mes"]))),
                ]),
            ],
        ),
        base.tabela(
            "Configuração",
            None,
            [
                linha([celula("Tipo de API"), celula(v["rotulo_tipo"])]),
                linha([celula("Integração"), celula(dict(INTEGRACOES)[entradas["integracao"]])]),
                linha([celula("Rotas configuradas"), celula(fmt_int(entradas["numero_rotas"]), num=True)]),
                linha([celula("Cache dedicado"), celula("sem cache" if v["cache_gb"] == "nenhum" else v["cache_gb"] + " GB")]),
                linha([celula("Limite de requests/s da conta"), celula(fmt_num(entradas["limite_rps_conta"], 0), num=True)]),
            ],
        ),
        base.tabela(
            "Faixas de preço aplicadas",
            ["Faixa de requests no mês", "Quantidade", "Preço", "Subtotal"],
            linhas_faixas,
            explica="O preço por request cai conforme o volume mensal acumulado entra nas faixas seguintes.",
        ),
        base.tabela("Composição do custo", ["Item", "Quantidade", "Preço unitário", "Total mês"], linhas_custo),
        base.tabela(
            "Preço unitário e fonte",
            None,
            [
                linha([celula("Região"), celula(preco.region)]),
                linha([
                    celula("Primeira faixa (%s)" % v["rotulo_tipo"]),
                    celula(
                        fmt_moeda(v["faixas"][0]["preco"] * 1e6, moeda, 2) + "/milhão" if v["faixas"] else "—",
                        num=True,
                    ),
                ]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "O preço é por request e não varia com o número de rotas nem com o tamanho do payload "
                "(o limite de payload é 10 MB).",
                "O VPC Link da HTTP API não tem custo adicional e integra direto com o ALB; "
                "a REST API só faz integração privada com NLB.",
                "A HTTP API custa cerca de 1/3 da REST API por request, mas não tem request/response "
                "transformation, API keys nem cache dedicado.",
                "Não estão inclusos: Data Transfer Out, WAF, certificados, custom domain e os recursos "
                "por trás da integração (ALB, ECS, DynamoDB).",
            ],
        ),
    ]
