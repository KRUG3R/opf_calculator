"""AWS Glue: carga dos eventos do S3 para o Aurora.

Dois modos:

  agendado    o job roda a cada N minutos; paga-se DPU x duracao de cada execucao
  streaming   o job fica de pe o tempo todo, consumindo continuamente

No modo agendado o intervalo define tanto o custo quanto a latencia do dado: o
saldo so aparece no Aurora depois que o job roda.
"""

import math

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "glue-carga"
NOME = "AWS Glue — Carga"
NOME_CURTO = "Glue"
DESCRICAO = "Job que lê o S3 e carrega o Aurora."
OFFER_CODE = "AWSGlue"
ORDEM = 70
STATUS = "ativo"

MODOS = [("agendado", "Job agendado a cada N minutos"), ("streaming", "Glue Streaming (contínuo)")]
VERSOES = [
    ("gen2", "Glue 6.0+ (Gen2)"),
    ("gen1", "Glue 4.0/5.0"),
    ("flex_gen2", "Flex, Glue 6.0+"),
    ("flex_gen1", "Flex, Glue 4.0/5.0"),
]
# O Glue cobra por segundo, com 1 minuto de mínimo por execução.
MINIMO_COBRADO_MIN = 1.0

CAMPOS = [
    base.grupo(
        "Execução",
        [
            base.escolha("modo", "Modo do job", MODOS),
            base.escolha("versao", "Versão / tipo de worker", VERSOES),
            base.numero(
                "intervalo_minutos",
                "Intervalo entre execuções (minutos)",
                minimo=1,
                ajuda="Também é a latência máxima do dado: o saldo só chega ao Aurora na execução seguinte.",
                visivel_quando={"modo": "agendado"},
            ),
            base.numero(
                "duracao_execucao_min",
                "Duração de cada execução (minutos)",
                passo=0.5,
                ajuda="Inclui o tempo de inicialização do job.",
                visivel_quando={"modo": "agendado"},
            ),
            base.numero("dpus", "DPUs do job", passo=1, minimo=2),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
            base.regiao(),
        ],
    ),
    base.grupo(
        "Volume processado",
        [
            base.booleano(
                "herdar_do_sink",
                "Herdar o volume do Kafka Sink",
                ajuda="Eventos e GB por mês vêm do sink; serve para dimensionar e avisar sobre sobrecarga.",
            ),
            base.numero("eventos_mes", "Eventos por mês", visivel_quando={"herdar_do_sink": False}),
            base.numero(
                "eventos_por_dpu_por_segundo",
                "Eventos processados por DPU por segundo",
                ajuda="Capacidade de ingestão de uma DPU, usada para alertar sobre janela insuficiente.",
            ),
        ],
    ),
]


# ---------- precos ----------

SUFIXOS = {
    "gen1": "ETL-DPU-Hour",
    "gen2": "ETL-DPU-Hour-Gen2",
    "flex_gen1": "ETL-Flex-DPU-Hour",
    "flex_gen2": "ETL-Flex-DPU-Hour-Gen2",
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
    return valores if "gen1" in valores else None


def region_efetiva(entradas, config):
    return entradas["region"]


def _do_sink(config):
    if not (config or {}).get("recursos", {}).get("kafka-sink", {}).get("habilitado", False):
        return None
    from . import calcular as calcular_recurso

    try:
        return calcular_recurso(config, "kafka-sink").get("expostos")
    except (ValueError, KeyError):
        return None


# ---------- calculo ----------

INTERVALOS_COMPARADOS = [1, 5, 10, 15, 30, 60]


def _tabela_intervalos(entradas, preco, preco_dpu, horas_mes, minutos_mes, eventos_mes):
    """Quanto custaria o mesmo job em cada intervalo, e em streaming."""
    moeda = preco.moeda
    duracao = max(entradas["duracao_execucao_min"], MINIMO_COBRADO_MIN)
    linhas = []

    for intervalo in INTERVALOS_COMPARADOS:
        execucoes = minutos_mes / intervalo
        dpu_h = execucoes * (duracao / 60.0) * entradas["dpus"]
        custo = dpu_h * preco_dpu
        sobrepoe = entradas["duracao_execucao_min"] >= intervalo
        atual = entradas["modo"] == "agendado" and intervalo == entradas["intervalo_minutos"]
        linhas.append(
            linha(
                [
                    celula("A cada %s min%s" % (fmt_int(intervalo), " ← atual" if atual else "")),
                    celula(fmt_int(execucoes), num=True),
                    celula(fmt_num(dpu_h, 0) + " DPU-h", num=True),
                    celula("até %s min" % fmt_int(intervalo)),
                    celula(fmt_moeda(custo, moeda) + ("  ⚠" if sobrepoe else ""), num=True),
                ],
                classe="dominante" if atual else None,
            )
        )

    dpu_h_stream = entradas["dpus"] * horas_mes
    atual_stream = entradas["modo"] == "streaming"
    linhas.append(
        linha(
            [
                celula("Glue Streaming" + (" ← atual" if atual_stream else "")),
                celula("contínuo"),
                celula(fmt_num(dpu_h_stream, 0) + " DPU-h", num=True),
                celula("segundos"),
                celula(fmt_moeda(dpu_h_stream * preco_dpu, moeda), num=True),
            ],
            classe="dominante" if atual_stream else None,
        )
    )

    return base.tabela(
        "Custo por intervalo de carga",
        ["Frequência", "Execuções/mês", "DPU-horas", "Latência do dado", "Custo mês"],
        linhas,
        explica="Mesmo job (%s DPUs, %s min por execução) em cada frequência. O ⚠ marca os intervalos "
        "menores que a duração da execução, em que os jobs se sobrepõem."
        % (fmt_int(entradas["dpus"]), fmt_num(entradas["duracao_execucao_min"], 1)),
    )


def calcular(entradas, recurso_cfg, preco, config=None):
    avisos = []
    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    minutos_mes = horas_mes * 60.0

    eventos_mes = entradas["eventos_mes"]
    gb_mes = None
    if entradas.get("herdar_do_sink"):
        sink = _do_sink(config)
        if sink:
            eventos_mes = sink["eventos_mes"]
            gb_mes = sink["gb_gravados_mes"]
        else:
            avisos.append("Herança do sink ligada, mas o recurso não está disponível; usando o valor próprio.")

    preco_dpu = preco.valores.get(entradas["versao"]) or preco["gen1"]

    if entradas["modo"] == "streaming":
        execucoes = 0.0
        dpu_horas = entradas["dpus"] * horas_mes
        latencia = "contínua"
        duracao_cobrada = None
    else:
        execucoes = minutos_mes / entradas["intervalo_minutos"]
        duracao_cobrada = max(entradas["duracao_execucao_min"], MINIMO_COBRADO_MIN)
        dpu_horas = execucoes * (duracao_cobrada / 60.0) * entradas["dpus"]
        latencia = "até %s min" % fmt_num(entradas["intervalo_minutos"], 0)
        if entradas["duracao_execucao_min"] >= entradas["intervalo_minutos"]:
            avisos.append(
                "Cada execução leva %s min, igual ou mais que o intervalo de %s min: as execuções se "
                "sobrepõem e a fila cresce."
                % (fmt_num(entradas["duracao_execucao_min"], 1), fmt_num(entradas["intervalo_minutos"], 0))
            )
        # Capacidade: o lote de um intervalo precisa caber na duração da execução.
        eventos_por_lote = eventos_mes / execucoes if execucoes else 0.0
        capacidade_lote = (
            entradas["dpus"] * entradas["eventos_por_dpu_por_segundo"] * entradas["duracao_execucao_min"] * 60.0
        )
        if capacidade_lote and eventos_por_lote > capacidade_lote:
            avisos.append(
                "Cada execução recebe %s eventos, acima dos %s que %s DPUs processam em %s min: aumente DPUs "
                "ou a duração."
                % (
                    fmt_int(eventos_por_lote),
                    fmt_int(capacidade_lote),
                    fmt_int(entradas["dpus"]),
                    fmt_num(entradas["duracao_execucao_min"], 1),
                )
            )

    custo = dpu_horas * preco_dpu
    moeda = preco.moeda

    secoes = [
        base.tabela(
            "Execução",
            None,
            [
                linha([celula("Modo"), celula(dict(MODOS)[entradas["modo"]])]),
                linha([celula("Versão"), celula(dict(VERSOES)[entradas["versao"]])]),
                linha([celula("DPUs"), celula(fmt_int(entradas["dpus"]), num=True)]),
                linha([celula("Execuções no mês"), celula(fmt_int(execucoes) if execucoes else "contínuo", num=True)]),
                linha([
                    celula("Duração cobrada"),
                    celula(
                        "%s min por execução (mínimo de %s min)"
                        % (fmt_num(duracao_cobrada, 1), fmt_num(MINIMO_COBRADO_MIN, 0))
                        if duracao_cobrada
                        else "%s h no mês" % fmt_num(horas_mes, 0)
                    ),
                ]),
                linha([celula("DPU-horas no mês"), celula(fmt_num(dpu_horas, 1), num=True)]),
                linha([celula("Latência do dado"), celula(latencia)]),
            ],
        ),
        base.tabela(
            "Volume processado",
            None,
            [
                linha([celula("Eventos no mês"), celula(fmt_int(eventos_mes), num=True)]),
                linha([
                    celula("Eventos por execução"),
                    celula(fmt_int(eventos_mes / execucoes) if execucoes else "fluxo contínuo", num=True),
                ]),
                linha([celula("GB lidos do S3"), celula(fmt_num(gb_mes, 2) + " GB" if gb_mes else "—", num=True)]),
            ],
        ),
        base.tabela(
            "Composição do custo",
            ["Item", "Quantidade", "Preço unitário", "Total mês"],
            [
                linha([
                    celula("DPU-horas"),
                    celula(fmt_num(dpu_horas, 1) + " DPU-h", num=True),
                    celula(fmt_moeda(preco_dpu, moeda, 4) + "/DPU-h", num=True),
                    celula(fmt_moeda(custo, moeda), num=True),
                ]),
                linha(
                    [celula("Total mensal"), celula(""), celula(""), celula(fmt_moeda(custo, moeda), num=True)],
                    classe="total",
                ),
            ],
        ),
        _tabela_intervalos(entradas, preco, preco_dpu, horas_mes, minutos_mes, eventos_mes),
        base.tabela(
            "Preço por versão",
            ["Versão", "DPU-hora"],
            [
                linha([
                    celula(rotulo + (" ← selecionada" if chave == entradas["versao"] else "")),
                    celula(fmt_moeda(preco.valores.get(chave, 0), moeda, 4), num=True),
                ], classe="dominante" if chave == entradas["versao"] else None)
                for chave, rotulo in VERSOES
            ],
            explica="Glue 6.0+ custa 30% menos por DPU-hora que a 4.0/5.0; o modo Flex é mais barato ainda, "
            "mas roda em capacidade não garantida — serve para cargas tolerantes a atraso.",
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
                "O Glue cobra por segundo de execução, com mínimo de 1 minuto por job — intervalos muito "
                "curtos pagam esse mínimo muitas vezes.",
                "O intervalo entre execuções é a latência do dado: com 5 minutos, o saldo pode levar até "
                "5 minutos para aparecer no Aurora.",
                "No modo streaming não há intervalo nem mínimo: paga-se as DPUs o tempo todo, e a latência "
                "cai para segundos.",
                "Não estão inclusos: Data Catalog, crawlers e o custo do Aurora que recebe a carga.",
                "Premissas estimadas: duração da execução, DPUs e capacidade por DPU.",
            ],
        ),
    ]

    return {
        "total_mes": custo,
        "moeda": moeda,
        "expostos": {
            "execucoes_mes": execucoes,
            "dpu_horas_mes": dpu_horas,
            "eventos_mes": eventos_mes,
            "escritas_aurora_mes": eventos_mes,
            "latencia_minutos": (0 if entradas["modo"] == "streaming" else entradas["intervalo_minutos"]),
            "unidades_mes": eventos_mes,
        },
        "resumo": [
            {"rotulo": "Modo", "valor": "Streaming" if entradas["modo"] == "streaming" else "A cada %s min" % fmt_num(entradas["intervalo_minutos"], 0)},
            {"rotulo": "DPU-horas/mês", "valor": fmt_num(dpu_horas, 0)},
            {"rotulo": "Execuções/mês", "valor": fmt_int(execucoes) if execucoes else "—"},
            {"rotulo": "Latência", "valor": latencia},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do Glue",
                fmt_moeda(custo, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(custo / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(custo * 12, moeda)),
                destaque=True,
            ),
            cartao("DPU-horas", fmt_num(dpu_horas, 0),
                   "%s DPUs · %s" % (fmt_int(entradas["dpus"]), dict(MODOS)[entradas["modo"]])),
            cartao("Latência do dado", latencia,
                   "%s execuções/mês" % fmt_int(execucoes) if execucoes else "consumo contínuo"),
        ],
        "avisos": avisos,
        "secoes": secoes,
    }
