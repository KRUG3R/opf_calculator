"""Kafka Sink: entrega dos eventos de saldo do tópico para o S3.

Dois caminhos possíveis, ambos cobrados pela AWS:

  MSK Connect   conector S3 Sink rodando em MCUs provisionadas (MCU-hora)
  Data Channel  entrega gerenciada do MSK direto para o S3, por GB processado

O recurso tambem publica a volumetria que o resto da cadeia consome: eventos por
mes, GB gravados e quantos arquivos (PUTs) chegam ao bucket.
"""

import math

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "kafka-sink"
NOME = "Kafka Sink → S3"
NOME_CURTO = "Kafka Sink"
DESCRICAO = "Entrega os eventos de saldo do tópico Kafka no bucket S3."
OFFER_CODE = "AmazonMSK"
ORDEM = 50
STATUS = "ativo"

SEGUNDOS_POR_DIA = 86400.0

MODOS = [
    ("msk_connect", "MSK Connect (conector S3 Sink)"),
    ("data_channel", "MSK Data Channel para S3"),
]

CAMPOS = [
    base.grupo(
        "Volumetria do tópico",
        [
            base.booleano("herdar_regiao_do_alb", "Herdar a região do ALB"),
            base.regiao(),
            base.numero(
                "eventos_por_minuto",
                "Eventos de saldo por minuto",
                ajuda="Registros publicados no tópico de alterações de saldo.",
            ),
            base.numero(
                "bytes_por_evento",
                "Bytes por evento",
                ajuda="Tamanho do registro serializado no tópico.",
            ),
            base.numero(
                "fator_compressao",
                "Compressão na gravação",
                passo=0.5,
                minimo=1,
                ajuda="Parquet/Avro com snappy costuma comprimir de 3× a 8×.",
            ),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
        ],
    ),
    base.grupo(
        "Entrega",
        [
            base.escolha("modo", "Como os dados chegam ao S3", MODOS),
            base.numero(
                "mcus",
                "MCUs provisionadas",
                ajuda="1 MCU = 1 vCPU e 4 GB. O conector escala em múltiplos de MCU.",
                visivel_quando={"modo": "msk_connect"},
            ),
            base.numero(
                "workers",
                "Workers do conector",
                minimo=1,
                visivel_quando={"modo": "msk_connect"},
            ),
            base.numero(
                "intervalo_flush_segundos",
                "Intervalo de flush (segundos)",
                minimo=1,
                ajuda="Define quantos arquivos chegam ao S3 — e quantos PUTs são cobrados.",
            ),
            base.numero(
                "particoes_topico",
                "Partições do tópico",
                minimo=1,
                ajuda="Cada partição gera seu próprio arquivo a cada flush.",
            ),
        ],
    ),
]


# ---------- precos ----------

SUFIXOS = {
    "mcu_hora": "Kafka.mcu.general",
    "data_channel_gb": "DataChannel-ProcessedBytes-S3Destination",
}


def extrair_precos(offer, region):
    valores = {}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        ut = attrs.get("usagetype", "")
        for chave, sufixo in SUFIXOS.items():
            if chave in valores or not base.usagetype_padrao(ut, sufixo):
                continue
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is not None:
                valores[chave] = preco
            break
    return valores if "mcu_hora" in valores else None


def region_efetiva(entradas, config):
    if entradas.get("herdar_regiao_do_alb"):
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if entradas_alb and "region" in entradas_alb:
            return entradas_alb["region"]
    return entradas["region"]


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    avisos = []
    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    minutos_mes = horas_mes * 60.0

    eventos_s = entradas["eventos_por_minuto"] / 60.0
    eventos_mes = entradas["eventos_por_minuto"] * minutos_mes
    bytes_brutos_mes = eventos_mes * entradas["bytes_por_evento"]
    bytes_gravados_mes = bytes_brutos_mes / entradas["fator_compressao"]
    gb_gravados_mes = bytes_gravados_mes / base.GB
    mb_por_segundo = (eventos_s * entradas["bytes_por_evento"]) / (1024.0 * 1024.0)

    # Cada partição fecha um arquivo a cada flush: é isso que vira PUT no S3.
    flushes_por_minuto = 60.0 / entradas["intervalo_flush_segundos"]
    arquivos_mes = flushes_por_minuto * minutos_mes * entradas["particoes_topico"]
    bytes_por_arquivo = bytes_gravados_mes / arquivos_mes if arquivos_mes else 0.0
    if bytes_por_arquivo and bytes_por_arquivo < 1024 * 1024:
        avisos.append(
            "Os arquivos ficam com %s KB em média: arquivos pequenos encarecem os PUTs e deixam o Glue "
            "mais lento. Aumente o intervalo de flush." % base.fmt_num(bytes_por_arquivo / 1024, 0)
        )

    custos = []
    if entradas["modo"] == "msk_connect":
        mcu_horas = entradas["mcus"] * entradas["workers"] * horas_mes
        custo = mcu_horas * preco["mcu_hora"]
        custos.append(("MCU-horas do conector", fmt_num(mcu_horas, 0) + " MCU-h", custo))
        # Referência prática: uma MCU dá conta de ~10 MB/s de throughput no S3 Sink.
        capacidade_mb_s = entradas["mcus"] * entradas["workers"] * 10.0
        if mb_por_segundo > capacidade_mb_s:
            avisos.append(
                "O tópico entrega %s MB/s, acima dos ~%s MB/s que %s MCU(s) sustentam: aumente MCUs ou workers."
                % (
                    fmt_num(mb_por_segundo, 1),
                    fmt_num(capacidade_mb_s, 0),
                    fmt_int(entradas["mcus"] * entradas["workers"]),
                )
            )
    else:
        preco_gb = preco.valores.get("data_channel_gb")
        if preco_gb is None:
            avisos.append("Sem preço de Data Channel nesta região; o item não foi somado.")
            preco_gb = 0.0
        gb_processados = bytes_brutos_mes / base.GB
        custo = gb_processados * preco_gb
        custos.append(("GB processados", fmt_num(gb_processados, 1) + " GB", custo))

    total = sum(valor for _, _, valor in custos)
    moeda = preco.moeda

    return {
        "total_mes": total,
        "moeda": moeda,
        "expostos": {
            "eventos_por_minuto": entradas["eventos_por_minuto"],
            "eventos_s": eventos_s,
            "eventos_mes": eventos_mes,
            "gb_gravados_mes": gb_gravados_mes,
            "arquivos_mes": arquivos_mes,
            "bytes_por_arquivo": bytes_por_arquivo,
            "unidades_mes": eventos_mes,
        },
        "resumo": [
            {"rotulo": "Eventos/min", "valor": fmt_int(entradas["eventos_por_minuto"])},
            {"rotulo": "Gravado/mês", "valor": fmt_num(gb_gravados_mes, 1) + " GB"},
            {"rotulo": "Arquivos/mês", "valor": fmt_int(arquivos_mes)},
            {"rotulo": "Throughput", "valor": fmt_num(mb_por_segundo, 1) + " MB/s"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do sink",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("Volume gravado", fmt_num(gb_gravados_mes, 1) + " GB/mês",
                   "%s eventos/min · compressão %s×" % (fmt_int(entradas["eventos_por_minuto"]), fmt_num(entradas["fator_compressao"], 1))),
            cartao("Arquivos no S3", fmt_int(arquivos_mes),
                   "%s por arquivo · flush a cada %s s" % (base.fmt_bytes(bytes_por_arquivo), fmt_num(entradas["intervalo_flush_segundos"], 0))),
        ],
        "avisos": avisos,
        "secoes": [
            base.tabela(
                "Volumetria",
                None,
                [
                    linha([celula("Região"), celula(preco.region)]),
                    linha([celula("Eventos por minuto"), celula(fmt_int(entradas["eventos_por_minuto"]), num=True)]),
                    linha([celula("Eventos por segundo"), celula(fmt_num(eventos_s, 0), num=True)]),
                    linha([celula("Eventos no mês"), celula(fmt_int(eventos_mes), num=True)]),
                    linha([celula("Throughput"), celula(fmt_num(mb_por_segundo, 2) + " MB/s", num=True)]),
                    linha([
                        celula("Volume bruto / gravado"),
                        celula(
                            "%s → %s (compressão %s×)"
                            % (
                                fmt_num(bytes_brutos_mes / base.GB, 1) + " GB",
                                fmt_num(gb_gravados_mes, 1) + " GB",
                                fmt_num(entradas["fator_compressao"], 1),
                            ),
                            num=True,
                        ),
                    ]),
                ],
            ),
            base.tabela(
                "Arquivos entregues",
                None,
                [
                    linha([celula("Partições do tópico"), celula(fmt_int(entradas["particoes_topico"]), num=True)]),
                    linha([celula("Flush"), celula("a cada %s s" % fmt_num(entradas["intervalo_flush_segundos"], 0))]),
                    linha([celula("Arquivos por mês"), celula(fmt_int(arquivos_mes), num=True)]),
                    linha([celula("Tamanho médio do arquivo"), celula(base.fmt_bytes(bytes_por_arquivo), num=True)]),
                ],
                explica="Cada partição fecha um arquivo a cada flush. Esses arquivos são os PUTs cobrados no S3 "
                "e as unidades que o Glue vai ler.",
            ),
            base.tabela(
                "Composição do custo",
                ["Item", "Quantidade", "Total mês"],
                [
                    linha([celula(nome), celula(qtd, num=True), celula(fmt_moeda(valor, moeda), num=True)])
                    for nome, qtd, valor in custos
                ]
                + [linha([celula("Total mensal"), celula(""), celula(fmt_moeda(total, moeda), num=True)], classe="total")],
            ),
            base.tabela(
                "Preço unitário e fonte",
                None,
                [
                    linha([celula("Modo"), celula(dict(MODOS)[entradas["modo"]])]),
                    linha([celula("MCU-hora"), celula(fmt_moeda(preco["mcu_hora"], moeda, 4), num=True)]),
                    linha([
                        celula("Data Channel por GB"),
                        celula(fmt_moeda(preco.valores.get("data_channel_gb", 0), moeda, 4), num=True),
                    ]),
                    linha([celula("Fonte"), celula(preco.fonte)]),
                    linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
                ],
            ),
            base.lista(
                "Notas",
                [
                    "O cluster Kafka em si (MSK ou terceiro) não está nesta conta — só a entrega para o S3.",
                    "No MSK Connect o custo é por MCU-hora provisionada, independente do volume; no Data "
                    "Channel é por GB processado, sem capacidade a dimensionar.",
                    "O intervalo de flush troca custo de PUT por latência: flush curto gera muitos arquivos "
                    "pequenos, que encarecem o S3 e degradam a leitura do Glue.",
                    "Premissas estimadas: bytes por evento, fator de compressão e capacidade por MCU.",
                ],
            ),
        ],
    }
