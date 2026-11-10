"""S3: bucket de staging onde o Kafka Sink grava os eventos antes da carga.

O volume vem do sink: GB gravados e quantidade de arquivos (cada arquivo e um
PUT). As leituras sao as do Glue a cada execucao. O armazenamento depende da
retencao: o staging costuma guardar poucos dias.
"""

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "s3-staging"
NOME = "S3 — Staging"
NOME_CURTO = "S3"
DESCRICAO = "Bucket onde o sink grava os eventos até o Glue carregá-los."
OFFER_CODE = "AmazonS3"
ORDEM = 60
STATUS = "ativo"

CAMPOS = [
    base.grupo(
        "Volume",
        [
            base.booleano(
                "herdar_do_sink",
                "Herdar volume do Kafka Sink",
                ajuda="GB gravados e número de arquivos vêm do recurso de sink.",
            ),
            base.regiao(),
            base.numero("gb_gravados_mes", "GB gravados por mês", passo=1, visivel_quando={"herdar_do_sink": False}),
            base.numero("arquivos_mes", "Arquivos gravados por mês", visivel_quando={"herdar_do_sink": False}),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
        ],
    ),
    base.grupo(
        "Retenção e leitura",
        [
            base.numero(
                "retencao_dias",
                "Retenção no staging (dias)",
                minimo=1,
                ajuda="Quanto tempo o arquivo fica no bucket depois de carregado.",
            ),
            base.numero(
                "leituras_por_arquivo",
                "Leituras (GET) por arquivo",
                passo=0.1,
                ajuda="1 = o Glue lê cada arquivo uma vez. Reprocessamentos aumentam esse número.",
            ),
            base.numero(
                "requests_listagem_por_execucao",
                "LIST por execução do Glue",
                ajuda="Listagens de prefixo para descobrir os arquivos novos.",
            ),
            base.numero(
                "execucoes_glue_mes",
                "Execuções do Glue por mês",
                visivel_quando={"herdar_do_glue": False},
            ),
            base.booleano("herdar_do_glue", "Herdar as execuções do Glue"),
        ],
    ),
]


# ---------- precos ----------

SUFIXOS = {
    "put_request": "Requests-Tier1",
    "get_request": "Requests-Tier2",
    "storage_gb_mes": "TimedStorage-ByteHrs",
}


def extrair_precos(offer, region):
    valores = {}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        # só o S3 Standard de objetos comuns
        if attrs.get("storageClass") not in (None, "General Purpose"):
            continue
        ut = attrs.get("usagetype", "")
        for chave, sufixo in SUFIXOS.items():
            if chave in valores or not base.usagetype_padrao(ut, sufixo):
                continue
            faixas = aws_pricing.faixas_on_demand(offer, sku)
            if faixas:
                valores[chave] = faixas
            break
    return valores if all(c in valores for c in SUFIXOS) else None


def region_efetiva(entradas, config):
    return entradas["region"]


# ---------- volumetria ----------

def _do_sink(config):
    if not (config or {}).get("recursos", {}).get("kafka-sink", {}).get("habilitado", False):
        return None
    from . import calcular as calcular_recurso

    try:
        resultado = calcular_recurso(config, "kafka-sink")
    except (ValueError, KeyError):
        return None
    return resultado.get("expostos")


def _do_glue(config):
    if not (config or {}).get("recursos", {}).get("glue-carga", {}).get("habilitado", False):
        return None
    from . import calcular as calcular_recurso

    try:
        resultado = calcular_recurso(config, "glue-carga")
    except (ValueError, KeyError):
        return None
    return resultado.get("expostos")


def calcular(entradas, recurso_cfg, preco, config=None):
    avisos = []
    gb_gravados = entradas["gb_gravados_mes"]
    arquivos = entradas["arquivos_mes"]
    origem = "valores próprios"

    if entradas.get("herdar_do_sink"):
        sink = _do_sink(config)
        if sink:
            gb_gravados = sink["gb_gravados_mes"]
            arquivos = sink["arquivos_mes"]
            origem = "Kafka Sink"
        else:
            avisos.append("Herança do sink ligada, mas o recurso não está disponível; usando os valores próprios.")

    execucoes_glue = entradas["execucoes_glue_mes"]
    if entradas.get("herdar_do_glue"):
        glue = _do_glue(config)
        if glue and "execucoes_mes" in glue:
            execucoes_glue = glue["execucoes_mes"]
        else:
            avisos.append("Herança do Glue ligada, mas o recurso não está disponível; usando o valor próprio.")

    puts = arquivos
    gets = arquivos * entradas["leituras_por_arquivo"]
    lists = execucoes_glue * entradas["requests_listagem_por_execucao"]

    # Armazenamento médio: o que entra no mês, mantido pela retenção.
    dias_mes = entradas["dias_por_mes"]
    gb_armazenados = gb_gravados * min(entradas["retencao_dias"], dias_mes) / dias_mes

    custo_put, _ = base.custo_escalonado(preco.valores["put_request"], puts)
    custo_get, _ = base.custo_escalonado(preco.valores["get_request"], gets + lists)
    custo_storage, _ = base.custo_escalonado(preco.valores["storage_gb_mes"], gb_armazenados)
    total = custo_put + custo_get + custo_storage
    moeda = preco.moeda
    horas_mes = entradas["horas_por_dia"] * dias_mes

    return {
        "total_mes": total,
        "moeda": moeda,
        "expostos": {
            "gb_gravados_mes": gb_gravados,
            "arquivos_mes": arquivos,
            "gb_armazenados": gb_armazenados,
            "unidades_mes": puts + gets + lists,
        },
        "resumo": [
            {"rotulo": "Gravado/mês", "valor": fmt_num(gb_gravados, 1) + " GB"},
            {"rotulo": "PUTs", "valor": fmt_int(puts)},
            {"rotulo": "GET + LIST", "valor": fmt_int(gets + lists)},
            {"rotulo": "Armazenado", "valor": fmt_num(gb_armazenados, 1) + " GB"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do S3",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("Requisições PUT", fmt_int(puts), "um por arquivo entregue pelo sink"),
            cartao("Armazenamento médio", fmt_num(gb_armazenados, 1) + " GB",
                   "retenção de %s dias" % fmt_num(entradas["retencao_dias"], 0)),
        ],
        "avisos": avisos,
        "secoes": [
            base.tabela(
                "Volume",
                None,
                [
                    linha([celula("Origem"), celula(origem)]),
                    linha([celula("Região"), celula(preco.region)]),
                    linha([celula("GB gravados no mês"), celula(fmt_num(gb_gravados, 2), num=True)]),
                    linha([celula("Arquivos gravados"), celula(fmt_int(arquivos), num=True)]),
                    linha([celula("Execuções do Glue"), celula(fmt_int(execucoes_glue), num=True)]),
                    linha([
                        celula("Armazenamento médio"),
                        celula(
                            "%s GB (retenção de %s dias)"
                            % (fmt_num(gb_armazenados, 2), fmt_num(entradas["retencao_dias"], 0)),
                            num=True,
                        ),
                    ]),
                ],
            ),
            base.tabela(
                "Composição do custo",
                ["Item", "Quantidade", "Preço unitário", "Total mês"],
                [
                    linha([
                        celula("PUT (gravação dos arquivos)"),
                        celula(fmt_int(puts), num=True),
                        celula(fmt_moeda(preco.valores["put_request"][0]["preco"] * 1e6, moeda, 2) + "/milhão", num=True),
                        celula(fmt_moeda(custo_put, moeda), num=True),
                    ]),
                    linha([
                        celula("GET + LIST (leitura pelo Glue)"),
                        celula(fmt_int(gets + lists), num=True),
                        celula(fmt_moeda(preco.valores["get_request"][0]["preco"] * 1e6, moeda, 2) + "/milhão", num=True),
                        celula(fmt_moeda(custo_get, moeda), num=True),
                    ]),
                    linha([
                        celula("Armazenamento"),
                        celula(fmt_num(gb_armazenados, 2) + " GB-mês", num=True),
                        celula(fmt_moeda(preco.valores["storage_gb_mes"][0]["preco"], moeda, 4) + "/GB-mês", num=True),
                        celula(fmt_moeda(custo_storage, moeda), num=True),
                    ]),
                    linha(
                        [celula("Total mensal"), celula(""), celula(""), celula(fmt_moeda(total, moeda), num=True)],
                        classe="total",
                    ),
                ],
            ),
            base.tabela(
                "Preço unitário e fonte",
                None,
                [
                    linha([celula("Região"), celula(preco.region)]),
                    linha([celula("Fonte"), celula(preco.fonte)]),
                    linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
                ],
            ),
            base.lista(
                "Notas",
                [
                    "O custo do S3 aqui é dominado por PUTs quando o sink gera muitos arquivos pequenos: "
                    "PUT custa 12,5× o GET por requisição.",
                    "A retenção só afeta o armazenamento, que nesta escala costuma ser centavos.",
                    "Não estão inclusos: versionamento, replicação, Data Transfer para fora da região e "
                    "classes de armazenamento diferentes do Standard.",
                ],
            ),
        ],
    }
