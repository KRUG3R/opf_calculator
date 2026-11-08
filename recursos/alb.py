"""Application Load Balancer: campos de entrada, modelo de LCU e custo mensal."""

import aws_pricing
from . import base
from .base import GB, KB, cartao, celula, fmt_bytes, fmt_int, fmt_moeda, fmt_num, linha

ID = "alb"
NOME = "Application Load Balancer"
NOME_CURTO = "ALB"
ORDEM = 20  # posicao no fluxo: API GW -> ALB -> ECS
DESCRICAO = "ALB que recebe as chamadas das IFs e distribui para os targets."
OFFER_CODE = "AWSELB"
STATUS = "ativo"

MODOS_ENTRADA = [("por_exemplo_json", "Medir exemplo JSON"), ("manual", "Informar KB manualmente")]
MODOS_SAIDA = [
    ("por_exemplo_json", "Medir exemplo JSON"),
    ("por_registro", "KB por registro × página"),
    ("por_request", "KB fixo por request"),
]

CAMPOS = [
    base.grupo(
        "Volumetria",
        [
            base.regiao(),
            base.numero("tpm_por_if", "TPM por IF (transações/min)"),
            base.numero("numero_ifs", "Número estimado de IFs", minimo=1),
            base.numero("registros_por_pagina", "Registros por página"),
            base.numero("response_time_s", "Response time (s)", passo=0.1),
        ],
    ),
    base.grupo(
        "Payload de entrada",
        [
            base.escolha("modo_payload_entrada", "Como medir", MODOS_ENTRADA),
            base.texto_longo(
                "exemplo_payload_entrada",
                "Exemplo do payload de entrada",
                linhas=7,
                visivel_quando={"modo_payload_entrada": "por_exemplo_json"},
                medida="entrada",
            ),
            base.numero(
                "payload_entrada_kb",
                "Payload de entrada (KB/request)",
                passo=0.1,
                visivel_quando={"modo_payload_entrada": "manual"},
            ),
        ],
    ),
    base.grupo(
        "Payload de saída",
        [
            base.escolha("modo_payload_saida", "Como medir", MODOS_SAIDA),
            base.texto_longo(
                "exemplo_payload_saida_envelope",
                "Envelope da resposta",
                linhas=12,
                ajuda="Array de registros vazio; contado 1× por resposta.",
                visivel_quando={"modo_payload_saida": "por_exemplo_json"},
                medida="envelope",
            ),
            base.texto_longo(
                "exemplo_payload_saida_registro",
                "Exemplo de 1 registro",
                linhas=4,
                ajuda="Multiplicado por registros/página.",
                visivel_quando={"modo_payload_saida": "por_exemplo_json"},
                medida="registro",
            ),
            base.numero(
                "payload_saida_por_registro_kb",
                "Payload de saída por registro (KB)",
                passo=0.1,
                visivel_quando={"modo_payload_saida": "por_registro"},
            ),
            base.numero(
                "payload_saida_kb",
                "Payload de saída fixo (KB/request)",
                visivel_quando={"modo_payload_saida": "por_request"},
            ),
            base.booleano("minificar_json", "Medir o JSON minificado (sem indentação)"),
        ],
    ),
    base.grupo(
        "Protocolo e janela",
        [
            base.numero("reuso_conexao", "Reuso de conexão (requests por conexão)", minimo=1),
            base.numero("regras_por_request", "Regras processadas por request"),
            base.numero("overhead_http_kb", "Overhead HTTP (KB/request)", passo=0.1),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
            base.numero("fator_pico", "Fator de pico", passo=0.1),
            base.numero("zonas_disponibilidade", "Zonas de disponibilidade (informativo)", minimo=1),
        ],
    ),
]


# ---------- precos ----------

# Variantes que nao representam o ALB padrao da regiao (Outposts, Local Zones com
# tarifa propria, capacidade reservada etc). O prefixo do usagetype pode trazer a
# sigla da regiao ("SAE1-TS-LoadBalancerUsage") ou vir sem ela ("TS-LoadBalancerUsage").
MODIFICADORES_IGNORADOS = ("OUTPOSTS", "TS", "RESERVED", "DEDICATED")


def extrair_precos(offer, region):
    """Localiza os SKUs de ALB-hora e LCU-hora padrao da regiao no offer file do AWSELB."""
    valores = {}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("operation") != "LoadBalancing:Application":
            continue
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        ut = attrs.get("usagetype", "")
        for sufixo, chave in (("LoadBalancerUsage", "alb_hora"), ("LCUUsage", "lcu_hora")):
            if not base.usagetype_padrao(ut, sufixo, MODIFICADORES_IGNORADOS):
                continue
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is not None:
                valores[chave] = preco
            break
    if "alb_hora" in valores and "lcu_hora" in valores:
        return valores
    return None


# ---------- payload ----------

def dimensionar_payloads(entradas):
    """Resolve os bytes de entrada e saida por request conforme o modo escolhido."""
    minificar = bool(entradas.get("minificar_json", True))
    registros = entradas["registros_por_pagina"]
    avisos = []

    medida_entrada = base.medir_json(entradas.get("exemplo_payload_entrada"), minificar)
    medida_envelope = base.medir_json(entradas.get("exemplo_payload_saida_envelope"), minificar)
    medida_registro = base.medir_json(entradas.get("exemplo_payload_saida_registro"), minificar)

    if entradas["modo_payload_entrada"] == "por_exemplo_json":
        entrada_bytes = medida_entrada["bytes"]
        if medida_entrada["json_valido"] is False:
            avisos.append("O exemplo de payload de entrada nao e JSON valido; medido como texto bruto.")
    else:
        entrada_bytes = entradas["payload_entrada_kb"] * KB

    # Os registros entram no array do envelope; cada um a partir do segundo
    # acrescenta o separador ",". O envelope e contado uma vez por resposta.
    separadores_bytes = max(0.0, registros - 1.0)

    if entradas["modo_payload_saida"] == "por_exemplo_json":
        saida_bytes = medida_envelope["bytes"] + registros * medida_registro["bytes"] + separadores_bytes
        for nome, medida in (("envelope", medida_envelope), ("registro", medida_registro)):
            if medida["json_valido"] is False:
                avisos.append("O exemplo de %s de saida nao e JSON valido; medido como texto bruto." % nome)
        if medida_registro["bytes"] == 0:
            avisos.append("Sem exemplo de registro de saida: o payload nao escala com registros_por_pagina.")
    elif entradas["modo_payload_saida"] == "por_registro":
        saida_bytes = registros * entradas["payload_saida_por_registro_kb"] * KB
        separadores_bytes = 0.0
    else:
        saida_bytes = entradas["payload_saida_kb"] * KB
        separadores_bytes = 0.0

    return {
        "entrada_bytes": entrada_bytes,
        "saida_bytes": saida_bytes,
        "medida_entrada": medida_entrada,
        "medida_envelope": medida_envelope,
        "medida_registro": medida_registro,
        "separadores_bytes": separadores_bytes,
        "registros_por_pagina": registros,
        "avisos": avisos,
    }


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    """Calcula LCUs e custo mensal do ALB. Retorna numeros e detalhamento pronto para a tela."""
    limites = recurso_cfg["lcu_limites"]

    tpm_total = entradas["tpm_por_if"] * entradas["numero_ifs"] * entradas["fator_pico"]
    tps = tpm_total / 60.0
    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    requests_mes = tpm_total * horas_mes * 60.0

    payload = dimensionar_payloads(entradas)
    overhead_bytes = entradas["overhead_http_kb"] * KB
    bytes_por_request = payload["entrada_bytes"] + payload["saida_bytes"] + overhead_bytes

    # Dimensao 1 - novas conexoes por segundo
    novas_conexoes_s = tps / entradas["reuso_conexao"]
    # Dimensao 2 - conexoes ativas (Little: concorrencia = taxa x duracao)
    conexoes_ativas = tps * entradas["response_time_s"]
    # Dimensao 3 - bytes processados
    gb_por_hora = (tps * 3600.0 * bytes_por_request) / GB
    # Dimensao 4 - avaliacoes de regra (as primeiras N regras nao contam)
    regras_cobradas = max(0.0, entradas["regras_por_request"] - limites["regras_gratuitas"])
    avaliacoes_s = tps * regras_cobradas

    dimensoes = [
        {
            "nome": "Novas conexões",
            "medido": novas_conexoes_s,
            "unidade": "conexões/s",
            "limite": "%s conexões/s por LCU" % fmt_num(limites["novas_conexoes_por_segundo"], 0),
            "lcu": novas_conexoes_s / limites["novas_conexoes_por_segundo"],
        },
        {
            "nome": "Conexões ativas",
            "medido": conexoes_ativas,
            "unidade": "conexões simultâneas",
            "limite": "%s conexões/min por LCU" % fmt_num(limites["conexoes_ativas_por_minuto"], 0),
            "lcu": conexoes_ativas / limites["conexoes_ativas_por_minuto"],
        },
        {
            "nome": "Bytes processados",
            "medido": gb_por_hora,
            "unidade": "GB/h",
            "limite": "%s GB/h por LCU" % fmt_num(limites["bytes_processados_gb_por_hora"], 0),
            "lcu": gb_por_hora / limites["bytes_processados_gb_por_hora"],
        },
        {
            "nome": "Avaliações de regra",
            "medido": avaliacoes_s,
            "unidade": "avaliações/s",
            "limite": "%s avaliações/s por LCU (%s regras gratuitas)"
            % (fmt_num(limites["avaliacoes_regra_por_segundo"], 0), fmt_num(limites["regras_gratuitas"], 0)),
            "lcu": avaliacoes_s / limites["avaliacoes_regra_por_segundo"],
        },
    ]

    dominante = max(dimensoes, key=lambda d: d["lcu"])
    lcu_cobrada = dominante["lcu"]

    moeda = preco.moeda
    custo_fixo = horas_mes * preco["alb_hora"]
    custo_lcu = horas_mes * lcu_cobrada * preco["lcu_hora"]
    total = custo_fixo + custo_lcu

    return {
        "total_mes": total,
        "moeda": moeda,
        # Numeros que outros recursos consomem (ex.: CloudWatch)
        "expostos": {
            "requests_mes": requests_mes,
            "lcu_cobrada": lcu_cobrada,
            "horas_mes": horas_mes,
            "tps": tps,
        },
        "resumo": [
            {"rotulo": "TPM total", "valor": fmt_int(tpm_total)},
            {"rotulo": "LCUs cobradas", "valor": fmt_num(lcu_cobrada, 2)},
            {"rotulo": "Gargalo", "valor": dominante["nome"]},
            {"rotulo": "Tráfego/mês", "valor": fmt_num((requests_mes * bytes_por_request) / GB, 1) + " GB"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do ALB",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano" % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("LCUs cobradas (média/h)", fmt_num(lcu_cobrada, 2), "gargalo: " + dominante["nome"]),
            cartao("TPM total / TPS", fmt_int(tpm_total), fmt_num(tps, 1) + " TPS"),
        ],
        "medidas": {
            "entrada": payload["medida_entrada"],
            "envelope": payload["medida_envelope"],
            "registro": payload["medida_registro"],
        },
        "avisos": payload["avisos"],
        "secoes": _secoes(entradas, payload, dimensoes, dominante, lcu_cobrada, preco, {
            "tpm_total": tpm_total,
            "tps": tps,
            "requests_mes": requests_mes,
            "horas_mes": horas_mes,
            "bytes_por_request": bytes_por_request,
            "overhead_bytes": overhead_bytes,
            "custo_fixo": custo_fixo,
            "custo_lcu": custo_lcu,
            "total": total,
        }),
    }


def _secoes(entradas, payload, dimensoes, dominante, lcu_cobrada, preco, v):
    moeda = preco.moeda
    modo_saida = entradas["modo_payload_saida"]

    linhas_payload = [
        linha([
            celula("Payload de entrada"),
            celula("exemplo JSON medido" if entradas["modo_payload_entrada"] == "por_exemplo_json" else "KB informado"),
            celula(fmt_bytes(payload["entrada_bytes"]), num=True),
        ])
    ]
    if modo_saida == "por_exemplo_json":
        registros = payload["registros_por_pagina"]
        bytes_registro = payload["medida_registro"]["bytes"]
        linhas_payload += [
            linha([
                celula("Envelope da resposta"),
                celula("1 × por resposta"),
                celula(fmt_bytes(payload["medida_envelope"]["bytes"]), num=True),
            ]),
            linha([
                celula("Registros"),
                celula("%s × %s" % (fmt_int(registros), fmt_bytes(bytes_registro))),
                celula(fmt_bytes(registros * bytes_registro), num=True),
            ]),
            linha([
                celula("Separadores do array"),
                celula("%s × 1 B" % fmt_int(max(0, registros - 1))),
                celula(fmt_bytes(payload["separadores_bytes"]), num=True),
            ]),
        ]
    linhas_payload += [
        linha(
            [celula("Payload de saída"), celula(""), celula(fmt_bytes(payload["saida_bytes"]), num=True)],
            classe="total",
        ),
        linha([
            celula("Overhead HTTP"),
            celula("headers e protocolo"),
            celula(fmt_bytes(v["overhead_bytes"]), num=True),
        ]),
        linha(
            [celula("Total por request"), celula(""), celula(fmt_bytes(v["bytes_por_request"]), num=True)],
            classe="total",
        ),
    ]

    return [
        base.tabela("Payload medido", ["Item", "Composição", "Tamanho"], linhas_payload),
        base.tabela(
            "Volumetria consolidada",
            None,
            [
                linha([celula("TPM por IF"), celula(fmt_int(entradas["tpm_por_if"]), num=True)]),
                linha([celula("Número de IFs"), celula(fmt_int(entradas["numero_ifs"]), num=True)]),
                linha([celula("TPM total"), celula(fmt_int(v["tpm_total"]), num=True)]),
                linha([celula("TPS total"), celula(fmt_num(v["tps"], 2), num=True)]),
                linha([celula("Requests por mês"), celula(fmt_int(v["requests_mes"]), num=True)]),
                linha([celula("Horas faturadas no mês"), celula(fmt_int(v["horas_mes"]), num=True)]),
                linha([celula("Tráfego por request"), celula(fmt_bytes(v["bytes_por_request"]), num=True)]),
                linha([
                    celula("Tráfego processado no mês"),
                    celula(fmt_num((v["requests_mes"] * v["bytes_por_request"]) / GB, 1) + " GB", num=True),
                ]),
            ],
        ),
        base.tabela(
            "Dimensões de LCU",
            ["Dimensão", "Valor medido", "Limite por LCU", "LCUs", ""],
            [
                linha(
                    [
                        celula(d["nome"]),
                        celula("%s %s" % (fmt_num(d["medido"], 2), d["unidade"]), num=True),
                        celula(d["limite"]),
                        celula(fmt_num(d["lcu"], 3), num=True),
                        celula("cobrada" if d is dominante else ""),
                    ],
                    classe="dominante" if d is dominante else None,
                )
                for d in dimensoes
            ],
            explica="O ALB cobra pela maior das quatro dimensões a cada hora. A linha destacada é o gargalo que define o custo.",
        ),
        base.tabela(
            "Composição do custo",
            ["Item", "Quantidade", "Preço unitário", "Total mês"],
            [
                linha([
                    celula("Horas de ALB"),
                    celula(fmt_int(v["horas_mes"]) + " h", num=True),
                    celula(fmt_moeda(preco["alb_hora"], moeda, 4) + "/h", num=True),
                    celula(fmt_moeda(v["custo_fixo"], moeda), num=True),
                ]),
                linha([
                    celula("LCU-horas"),
                    celula(fmt_num(lcu_cobrada * v["horas_mes"], 1) + " LCU-h", num=True),
                    celula(fmt_moeda(preco["lcu_hora"], moeda, 4) + "/LCU-h", num=True),
                    celula(fmt_moeda(v["custo_lcu"], moeda), num=True),
                ]),
                linha(
                    [celula("Total mensal"), celula(""), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)],
                    classe="total",
                ),
                linha([
                    celula("Custo por IF / mês"),
                    celula(""),
                    celula(""),
                    celula(fmt_moeda(v["total"] / entradas["numero_ifs"], moeda), num=True),
                ]),
                linha([
                    celula("Custo por milhão de requests"),
                    celula(""),
                    celula(""),
                    celula(
                        fmt_moeda((v["total"] / v["requests_mes"] * 1e6) if v["requests_mes"] else 0, moeda, 4),
                        num=True,
                    ),
                ]),
            ],
        ),
        base.tabela(
            "Preço unitário e fonte",
            None,
            [
                linha([celula("Região"), celula(preco.region)]),
                linha([celula("ALB-hora"), celula(fmt_moeda(preco["alb_hora"], moeda, 4), num=True)]),
                linha([celula("LCU-hora"), celula(fmt_moeda(preco["lcu_hora"], moeda, 4), num=True)]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "A taxa fixa de ALB-hora é por load balancer e não varia com o número de AZs "
                "(zonas_disponibilidade é informativo; afeta capacidade, não o preço do ALB).",
                "Conexões ativas estimadas pela Lei de Little: TPS × response time.",
                "Transferência de dados (Data Transfer Out para a internet) e NAT não estão inclusos neste total.",
            ],
        ),
    ]
