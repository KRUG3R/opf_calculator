"""RDS Aurora PostgreSQL: base relacional consultada pelas IFs.

Recebe tres fluxos de escrita - a carga de saldos vinda do Glue e os dois
consumidores Kafka (consentimentos e resources) - e serve as consultas do ECS.

Capacidade em dois modos:

  serverless_v2  ACU-hora, dimensionada pela carga de transacoes
  provisionado   instancia writer + reader, por hora

Alem da capacidade, cobram-se armazenamento, I/O (na classe Standard) e backup.
A classe I/O-Optimized nao cobra I/O, mas o ACU e o armazenamento custam mais -
compensa a partir de ~25% da conta em I/O.
"""

import math

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "aurora"
NOME = "Aurora PostgreSQL"
NOME_CURTO = "Aurora"
DESCRICAO = "Base relacional com saldos, consentimentos e resources."
OFFER_CODE = "AmazonRDS"
ORDEM = 40
STATUS = "ativo"

MODOS = [("serverless_v2", "Serverless v2 (ACU)"), ("provisionado", "Instâncias provisionadas")]
CLASSES = [("standard", "Standard (cobra I/O)"), ("io_optimized", "I/O-Optimized (I/O incluso)")]
INSTANCIAS = [
    ("db.t4g.medium", "db.t4g.medium"),
    ("db.t4g.large", "db.t4g.large"),
    ("db.r7g.large", "db.r7g.large"),
    ("db.r7g.xlarge", "db.r7g.xlarge"),
    ("db.r6g.large", "db.r6g.large"),
    ("db.r6g.xlarge", "db.r6g.xlarge"),
]

CAMPOS = [
    base.grupo(
        "Capacidade",
        [
            base.escolha("modo_capacidade", "Modo", MODOS),
            base.escolha("classe_armazenamento", "Classe", CLASSES),
            base.regiao(),
            base.numero("acu_minima", "ACU mínima", passo=0.5, minimo=0.5, visivel_quando={"modo_capacidade": "serverless_v2"}),
            base.numero("acu_maxima", "ACU máxima", passo=1, minimo=1, visivel_quando={"modo_capacidade": "serverless_v2"}),
            base.numero(
                "transacoes_por_acu_por_segundo",
                "Transações por ACU por segundo",
                ajuda="Capacidade de uma ACU, usada para dimensionar a média a partir da carga.",
                visivel_quando={"modo_capacidade": "serverless_v2"},
            ),
            base.escolha("instancia", "Classe da instância", INSTANCIAS, visivel_quando={"modo_capacidade": "provisionado"}),
            base.numero("replicas_leitura", "Réplicas de leitura", ajuda="Além da instância writer."),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
        ],
    ),
    base.grupo(
        "Carga",
        [
            base.booleano(
                "herdar_carga",
                "Herdar a carga dos outros recursos",
                ajuda="Consultas do ECS de consulta e escritas do Glue, do ECS de consentimentos e do de resources.",
            ),
            base.numero("consultas_por_segundo", "Consultas por segundo", passo=1, visivel_quando={"herdar_carga": False}),
            base.numero("escritas_por_segundo", "Escritas por segundo", passo=1, visivel_quando={"herdar_carga": False}),
            base.numero(
                "ios_por_consulta",
                "I/Os por consulta",
                passo=0.1,
                ajuda="Páginas de 8 KB lidas do storage; o cache do buffer pool reduz esse número.",
            ),
            base.numero("ios_por_escrita", "I/Os por escrita", passo=0.1),
        ],
    ),
    base.grupo(
        "Dados",
        [
            base.numero("storage_gb", "Armazenamento (GB)"),
            base.numero("backup_retido_gb", "Backup além do cluster (GB)",
                        ajuda="O backup do tamanho do cluster é gratuito; cobra-se o que passa disso."),
        ],
    ),
]


# ---------- precos ----------

SUFIXOS_SIMPLES = {
    "acu_hora": "Aurora:ServerlessV2Usage",
    "acu_hora_io_opt": "Aurora:ServerlessV2IOOptimizedUsage",
    "storage_gb_mes": "Aurora:StorageUsage",
    "storage_gb_mes_io_opt": "Aurora:IO-OptimizedStorageUsage",
    "io_request": "Aurora:StorageIOUsage",
    "backup_gb_mes": "Aurora:BackupUsage",
}


def extrair_precos(offer, region):
    """Preços do Aurora PostgreSQL: ACU, storage, I/O, backup e instâncias."""
    valores = {"instancias": {}, "instancias_io_opt": {}}
    for sku, produto in offer.get("products", {}).items():
        attrs = produto.get("attributes", {})
        if attrs.get("locationType", "AWS Region") != "AWS Region":
            continue
        if attrs.get("regionCode", region) != region:
            continue
        engine = attrs.get("databaseEngine") or ""
        if engine not in ("Aurora PostgreSQL", "Any"):
            continue
        ut = attrs.get("usagetype", "")

        for chave, sufixo in SUFIXOS_SIMPLES.items():
            if chave in valores or not base.usagetype_padrao(ut, sufixo):
                continue
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is not None:
                valores[chave] = preco
            break

        tipo = attrs.get("instanceType")
        if tipo and attrs.get("deploymentOption") in (None, "Single-AZ"):
            preco = aws_pricing.preco_on_demand(offer, sku)
            if preco is None:
                continue
            if ut.endswith("InstanceUsage:" + _sufixo_instancia(tipo)):
                valores["instancias"][tipo] = preco
            elif ut.endswith("InstanceUsageIOOptimized:" + _sufixo_instancia(tipo)):
                valores["instancias_io_opt"][tipo] = preco

    obrigatorios = ("acu_hora", "storage_gb_mes", "io_request")
    return valores if all(c in valores for c in obrigatorios) else None


def _sufixo_instancia(tipo):
    """No usagetype, db.r6g.xlarge aparece como db.r6g.xl."""
    return tipo.replace("xlarge", "xl") if tipo.endswith("xlarge") and not tipo.endswith("2xlarge") else tipo


def region_efetiva(entradas, config):
    return entradas["region"]


# ---------- carga herdada ----------

FONTES_ESCRITA = [
    ("glue-carga", "escritas_aurora_mes", "Glue (saldos)"),
    ("ecs-consentimentos", "escritas_aurora_s", "ECS de consentimentos"),
    ("ecs-resources", "escritas_aurora_s", "ECS de resources"),
]


def _expostos(config, recurso_id):
    if not (config or {}).get("recursos", {}).get(recurso_id, {}).get("habilitado", False):
        return None
    from . import calcular as calcular_recurso

    try:
        return calcular_recurso(config, recurso_id).get("expostos")
    except (ValueError, KeyError):
        return None


def resolver_carga(entradas, config, segundos_mes):
    """Consultas e escritas por segundo vindas dos recursos vizinhos."""
    if not entradas.get("herdar_carga"):
        return (
            entradas["consultas_por_segundo"],
            entradas["escritas_por_segundo"],
            [],
            [],
        )

    consultas_s = 0.0
    escritas_s = 0.0
    origens_leitura = []
    origens_escrita = []

    consulta = _expostos(config, "ecs-consulta")
    if consulta and consulta.get("consultas_aurora_s"):
        consultas_s += consulta["consultas_aurora_s"]
        origens_leitura.append(("ECS de consulta", consulta["consultas_aurora_s"]))

    for recurso_id, chave, rotulo in FONTES_ESCRITA:
        dados = _expostos(config, recurso_id)
        if not dados:
            continue
        if chave.endswith("_mes"):
            valor = dados.get(chave, 0.0) / segundos_mes if segundos_mes else 0.0
        else:
            valor = dados.get(chave, 0.0)
        if valor:
            escritas_s += valor
            origens_escrita.append((rotulo, valor))

    return consultas_s, escritas_s, origens_leitura, origens_escrita


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    avisos = []
    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    segundos_mes = horas_mes * 3600.0
    io_optimized = entradas["classe_armazenamento"] == "io_optimized"

    consultas_s, escritas_s, origens_leitura, origens_escrita = resolver_carga(entradas, config, segundos_mes)
    if entradas.get("herdar_carga") and not (origens_leitura or origens_escrita):
        avisos.append("Herança de carga ligada, mas nenhum recurso vizinho respondeu; usando os valores próprios.")
        consultas_s = entradas["consultas_por_segundo"]
        escritas_s = entradas["escritas_por_segundo"]

    tps_total = consultas_s + escritas_s
    custos = []

    if entradas["modo_capacidade"] == "serverless_v2":
        por_acu = entradas["transacoes_por_acu_por_segundo"] or 1.0
        acu_calculada = tps_total / por_acu
        acu_media = min(max(acu_calculada, entradas["acu_minima"]), entradas["acu_maxima"])
        if acu_calculada > entradas["acu_maxima"]:
            avisos.append(
                "A carga pede %s ACUs, acima do teto de %s: o banco vai saturar nos picos."
                % (fmt_num(acu_calculada, 1), fmt_num(entradas["acu_maxima"], 0))
            )
        instancias = 1 + entradas["replicas_leitura"]
        preco_acu = preco["acu_hora_io_opt"] if io_optimized else preco["acu_hora"]
        acu_horas = acu_media * instancias * horas_mes
        custo_capacidade = acu_horas * preco_acu
        custos.append(("ACU-horas", fmt_num(acu_horas, 0) + " ACU-h", custo_capacidade))
        detalhe_capacidade = "%s ACU × %s instância(s)" % (fmt_num(acu_media, 1), fmt_int(instancias))
    else:
        tipo = entradas["instancia"]
        tabela = preco.valores.get("instancias_io_opt" if io_optimized else "instancias", {})
        preco_hora = tabela.get(tipo)
        if preco_hora is None:
            preco_hora = (preco.valores.get("instancias") or {}).get(tipo, 0.0)
            avisos.append("Sem preço I/O-Optimized para %s nesta região; usando o preço Standard." % tipo)
        instancias = 1 + entradas["replicas_leitura"]
        horas_instancia = instancias * horas_mes
        custo_capacidade = horas_instancia * preco_hora
        custos.append(("Horas de instância", fmt_num(horas_instancia, 0) + " h", custo_capacidade))
        acu_media = None
        detalhe_capacidade = "%s × %s" % (fmt_int(instancias), tipo)

    # I/O só é cobrado na classe Standard.
    ios_mes = (consultas_s * entradas["ios_por_consulta"] + escritas_s * entradas["ios_por_escrita"]) * segundos_mes
    custo_io = 0.0 if io_optimized else ios_mes * preco["io_request"]
    if not io_optimized:
        custos.append(("I/O de armazenamento", fmt_int(ios_mes) + " I/Os", custo_io))

    preco_storage = preco["storage_gb_mes_io_opt"] if io_optimized else preco["storage_gb_mes"]
    custo_storage = entradas["storage_gb"] * preco_storage
    custos.append(("Armazenamento", fmt_num(entradas["storage_gb"], 0) + " GB-mês", custo_storage))

    custo_backup = entradas["backup_retido_gb"] * preco.valores.get("backup_gb_mes", 0.0)
    if entradas["backup_retido_gb"]:
        custos.append(("Backup adicional", fmt_num(entradas["backup_retido_gb"], 0) + " GB-mês", custo_backup))

    total = sum(valor for _, _, valor in custos)
    moeda = preco.moeda

    # Vale a pena trocar de classe?
    if not io_optimized and total and custo_io / total > 0.25:
        alternativo = _custo_alternativo(entradas, preco, horas_mes, tps_total, True)
        if alternativo is not None and alternativo < total:
            avisos.append(
                "O I/O é %s%% da conta. Na classe I/O-Optimized o total cairia para %s."
                % (fmt_num(custo_io / total * 100, 0), fmt_moeda(alternativo, moeda))
            )

    return {
        "total_mes": total,
        "moeda": moeda,
        "expostos": {
            "consultas_s": consultas_s,
            "escritas_s": escritas_s,
            "acu_media": acu_media,
            "unidades_mes": tps_total * segundos_mes,
        },
        "resumo": [
            {"rotulo": "Consultas/s", "valor": fmt_num(consultas_s, 0)},
            {"rotulo": "Escritas/s", "valor": fmt_num(escritas_s, 0)},
            {"rotulo": "Capacidade", "valor": detalhe_capacidade},
            {"rotulo": "Armazenamento", "valor": fmt_num(entradas["storage_gb"], 0) + " GB"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do Aurora",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("Capacidade", detalhe_capacidade, dict(CLASSES)[entradas["classe_armazenamento"]]),
            cartao("Carga", fmt_num(tps_total, 0) + " TPS",
                   "%s consultas/s + %s escritas/s" % (fmt_num(consultas_s, 0), fmt_num(escritas_s, 0))),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas, preco, origens_leitura, origens_escrita,
            {
                "consultas_s": consultas_s,
                "escritas_s": escritas_s,
                "tps_total": tps_total,
                "acu_media": acu_media,
                "detalhe_capacidade": detalhe_capacidade,
                "ios_mes": ios_mes,
                "custo_io": custo_io,
                "custos": custos,
                "total": total,
                "horas_mes": horas_mes,
                "io_optimized": io_optimized,
            },
        ),
    }


def _custo_alternativo(entradas, preco, horas_mes, tps_total, io_opt):
    """Total aproximado na outra classe de armazenamento, para o aviso."""
    try:
        if entradas["modo_capacidade"] == "serverless_v2":
            por_acu = entradas["transacoes_por_acu_por_segundo"] or 1.0
            acu = min(max(tps_total / por_acu, entradas["acu_minima"]), entradas["acu_maxima"])
            capacidade = acu * (1 + entradas["replicas_leitura"]) * horas_mes * (
                preco["acu_hora_io_opt"] if io_opt else preco["acu_hora"]
            )
        else:
            tabela = preco.valores.get("instancias_io_opt" if io_opt else "instancias", {})
            preco_hora = tabela.get(entradas["instancia"])
            if preco_hora is None:
                return None
            capacidade = (1 + entradas["replicas_leitura"]) * horas_mes * preco_hora
        storage = entradas["storage_gb"] * (
            preco["storage_gb_mes_io_opt"] if io_opt else preco["storage_gb_mes"]
        )
        return capacidade + storage + entradas["backup_retido_gb"] * preco.valores.get("backup_gb_mes", 0.0)
    except KeyError:
        return None


def _secoes(entradas, preco, origens_leitura, origens_escrita, v):
    moeda = preco.moeda

    linhas_carga = [
        linha([celula("Origem"), celula("herdada dos recursos vizinhos" if origens_leitura or origens_escrita else "valores próprios")]),
    ]
    for rotulo, valor in origens_leitura:
        linhas_carga.append(linha([celula(rotulo + " (leitura)"), celula(fmt_num(valor, 1) + "/s", num=True)]))
    for rotulo, valor in origens_escrita:
        linhas_carga.append(linha([celula(rotulo + " (escrita)"), celula(fmt_num(valor, 1) + "/s", num=True)]))
    linhas_carga += [
        linha([celula("Consultas por segundo"), celula(fmt_num(v["consultas_s"], 1), num=True)], classe="total"),
        linha([celula("Escritas por segundo"), celula(fmt_num(v["escritas_s"], 1), num=True)], classe="total"),
        linha([celula("I/Os no mês"), celula(fmt_int(v["ios_mes"]), num=True)]),
    ]

    return [
        base.tabela("Carga aplicada", None, linhas_carga),
        base.tabela(
            "Capacidade",
            None,
            [
                linha([celula("Modo"), celula(dict(MODOS)[entradas["modo_capacidade"]])]),
                linha([celula("Classe"), celula(dict(CLASSES)[entradas["classe_armazenamento"]])]),
                linha([celula("Configuração"), celula(v["detalhe_capacidade"])]),
                linha([
                    celula("ACU dimensionada"),
                    celula(
                        "%s ACU para %s TPS (%s por ACU)"
                        % (
                            fmt_num(v["acu_media"], 1),
                            fmt_num(v["tps_total"], 0),
                            fmt_num(entradas["transacoes_por_acu_por_segundo"], 0),
                        )
                        if v["acu_media"] is not None
                        else "—"
                    ),
                ]),
                linha([celula("Réplicas de leitura"), celula(fmt_int(entradas["replicas_leitura"]), num=True)]),
            ],
        ),
        base.tabela(
            "Composição do custo",
            ["Item", "Quantidade", "Total mês"],
            [
                linha([celula(nome), celula(qtd, num=True), celula(fmt_moeda(valor, moeda), num=True)])
                for nome, qtd, valor in v["custos"]
            ]
            + [linha([celula("Total mensal"), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)], classe="total")],
        ),
        base.tabela(
            "Preço unitário e fonte",
            None,
            [
                linha([celula("Região"), celula(preco.region)]),
                linha([celula("ACU-hora"), celula(fmt_moeda(preco["acu_hora"], moeda, 4), num=True)]),
                linha([celula("ACU-hora I/O-Optimized"), celula(fmt_moeda(preco.valores.get("acu_hora_io_opt", 0), moeda, 4), num=True)]),
                linha([celula("Armazenamento"), celula(fmt_moeda(preco["storage_gb_mes"], moeda, 4) + "/GB-mês", num=True)]),
                linha([celula("Armazenamento I/O-Optimized"), celula(fmt_moeda(preco.valores.get("storage_gb_mes_io_opt", 0), moeda, 4) + "/GB-mês", num=True)]),
                linha([celula("I/O"), celula(fmt_moeda(preco["io_request"] * 1e6, moeda, 4) + "/milhão", num=True)]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "A classe I/O-Optimized não cobra I/O, mas ACU e armazenamento custam mais — vale a partir "
                "de cerca de 25% da conta em I/O.",
                "O Serverless v2 cobra por ACU-hora em cada instância, writer e réplicas; a ACU aqui é "
                "dimensionada pela carga, dentro do mínimo e do máximo configurados.",
                "O backup até o tamanho do cluster é gratuito: cobra-se apenas a retenção além disso.",
                "Não estão inclusos: RDS Proxy, Data API, snapshots exportados e Data Transfer entre AZs.",
                "Premissas estimadas: I/Os por consulta e por escrita, e transações por ACU.",
            ],
        ),
    ]
