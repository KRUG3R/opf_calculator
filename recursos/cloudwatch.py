"""CloudWatch: metricas e alarmes que disparam o auto scaling dos ECS.

Neste desenho o CloudWatch serve ao auto scaling: os logs das aplicacoes vao para
o Datadog, que ja esta pago e fica fora desta conta.

As metricas que o target tracking usa - CPU e memoria do servico (AWS/ECS) e
ALBRequestCountPerTarget (AWS/ApplicationELB) - sao publicadas pela AWS sem custo.
O que custa, aqui:

  alarmes            2 por politica de escala, mais os operacionais
  metricas de escala qualquer metrica customizada usada como gatilho, como o lag
                     do consumidor Kafka no ECS de carga
  coleta externa     o Datadog puxando metricas do CloudWatch via GetMetricData,
                     ou recebendo um Metric Stream
  logs               apenas se alguma coisa continuar escrevendo no CloudWatch Logs
"""

import aws_pricing
from . import base
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha

ID = "cloudwatch"
NOME = "CloudWatch"
NOME_CURTO = "CloudWatch"
DESCRICAO = "Métricas e alarmes que disparam o auto scaling dos ECSs."
OFFER_CODE = "AmazonCloudWatch"
ORDEM = 70
STATUS = "ativo"

MODOS_COLETA = [
    ("getmetricdata", "Datadog puxando por GetMetricData"),
    ("metric_streams", "Metric Streams (push, quase em tempo real)"),
    ("nenhuma", "Nenhuma integração externa"),
]
CLASSES_LOG = [("standard", "Standard"), ("infrequent_access", "Infrequent Access")]

CAMPOS = [
    base.grupo(
        "Escopo",
        [
            base.booleano("herdar_regiao_do_alb", "Herdar a região do ALB"),
            base.regiao(),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
        ],
    ),
    base.grupo(
        "Auto scaling",
        [
            base.numero("numero_servicos", "Serviços ECS com auto scaling", minimo=1),
            base.numero(
                "politicas_escala_por_servico",
                "Políticas de escala por serviço",
                minimo=1,
                ajuda="Cada política de target tracking cria 2 alarmes.",
            ),
            base.numero(
                "alarmes_por_politica",
                "Alarmes por política",
                minimo=1,
                ajuda="O target tracking cria 2: um de scale-out e um de scale-in.",
            ),
            base.numero(
                "metricas_padrao_observadas",
                "Métricas padrão usadas como gatilho",
                ajuda="CPU e memória do serviço (AWS/ECS) e ALBRequestCountPerTarget: sem custo.",
            ),
            base.numero(
                "metricas_customizadas",
                "Métricas customizadas de escala",
                ajuda="Gatilhos que a equipe publica, como o lag do consumidor Kafka.",
            ),
        ],
    ),
    base.grupo(
        "Coleta pelo Datadog",
        [
            base.escolha("modo_coleta_externa", "Como o Datadog obtém as métricas", MODOS_COLETA),
            base.numero(
                "metricas_coletadas",
                "Métricas coletadas por ciclo",
                visivel_quando={"modo_coleta_externa": "getmetricdata"},
            ),
            base.numero(
                "intervalo_coleta_minutos",
                "Intervalo de coleta (minutos)",
                minimo=1,
                ajuda="O padrão da integração AWS do Datadog é 10 minutos.",
                visivel_quando={"modo_coleta_externa": "getmetricdata"},
            ),
            base.numero(
                "metricas_no_stream",
                "Métricas no Metric Stream",
                visivel_quando={"modo_coleta_externa": "metric_streams"},
            ),
            base.numero(
                "atualizacoes_por_metrica_por_minuto",
                "Atualizações por métrica por minuto",
                passo=0.5,
                visivel_quando={"modo_coleta_externa": "metric_streams"},
            ),
        ],
    ),
    base.grupo(
        "Alarmes operacionais",
        [
            base.numero(
                "alarmes_operacionais",
                "Alarmes de operação",
                ajuda="Fora do auto scaling: 5xx, TargetResponseTime, UnHealthyHostCount, lag máximo...",
            ),
            base.numero("alarmes_alta_resolucao", "Alarmes de alta resolução"),
            base.numero("alarmes_compostos", "Alarmes compostos"),
        ],
    ),
    base.grupo(
        "Logs no CloudWatch (opcional)",
        [
            base.booleano(
                "logs_no_cloudwatch",
                "Alguma aplicação ainda escreve no CloudWatch Logs",
                ajuda="Desligado: os logs vão para o Datadog e não entram nesta conta.",
            ),
            base.escolha(
                "classe_logs",
                "Classe dos logs",
                CLASSES_LOG,
                visivel_quando={"logs_no_cloudwatch": True},
            ),
            base.numero(
                "gb_logs_por_mes",
                "GB de log ingeridos por mês",
                passo=1,
                visivel_quando={"logs_no_cloudwatch": True},
            ),
            base.numero(
                "retencao_dias",
                "Retenção dos logs (dias)",
                minimo=1,
                visivel_quando={"logs_no_cloudwatch": True},
            ),
            base.numero(
                "fator_compressao_logs",
                "Fator de compressão no armazenamento",
                passo=0.5,
                minimo=1,
                visivel_quando={"logs_no_cloudwatch": True},
            ),
            base.numero(
                "gb_escaneados_insights_mes",
                "GB escaneados no Logs Insights por mês",
                visivel_quando={"logs_no_cloudwatch": True},
            ),
        ],
    ),
]


# ---------- precos ----------

SUFIXOS = {
    "metrica_mes": "CW:MetricMonitorUsage",
    "alarme_mes": "CW:AlarmMonitorUsage",
    "alarme_alta_resolucao_mes": "CW:HighResAlarmMonitorUsage",
    "alarme_composto_mes": "CW:CompositeAlarmMonitorUsage",
    "getmetricdata_metrica": "CW:GMD-Metrics",
    "metric_stream_update": "CW:MetricStreamUsage",
    "api_request": "CW:Requests",
    "log_ingestao_gb": "DataProcessing-Bytes",
    "log_ingestao_ia_gb": "DataProcessingIA-Bytes",
    "log_storage_gb_mes": "TimedStorage-ByteHrs",
    "log_storage_ia_gb_mes": "TimedStorage-IA-ByteHrs",
    "insights_scan_gb": "DataScanned-Bytes",
}


def extrair_precos(offer, region):
    """Faixas de preco de metricas, alarmes, coleta por API e logs."""
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
            faixas = aws_pricing.faixas_on_demand(offer, sku)
            if faixas:
                valores[chave] = faixas
            break

    obrigatorios = ("metrica_mes", "alarme_mes", "getmetricdata_metrica", "metric_stream_update")
    if all(c in valores for c in obrigatorios):
        return valores
    return None


# ---------- volumetria ----------

def region_efetiva(entradas, config):
    if entradas.get("herdar_regiao_do_alb"):
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if entradas_alb and "region" in entradas_alb:
            return entradas_alb["region"]
    return entradas["region"]


SERVICOS_OBSERVADOS = ["ecs-consulta", "ecs-carga"]


def contexto_dos_servicos(config):
    """Tasks dos servicos que escalam, so para dar contexto na tela (nao entram no preco)."""
    from . import calcular as calcular_recurso  # lazy: o pacote ainda esta carregando no import

    itens = []
    for recurso_id in SERVICOS_OBSERVADOS:
        if not (config or {}).get("recursos", {}).get(recurso_id, {}).get("habilitado", False):
            continue
        try:
            resultado = calcular_recurso(config, recurso_id)
        except (ValueError, KeyError):
            continue
        expostos = resultado.get("expostos") or {}
        if not expostos:
            continue
        itens.append({
            "nome": resultado.get("nome", recurso_id),
            "tasks_pico": expostos.get("tasks_pico", 0),
            "tasks_fora": expostos.get("tasks_fora", 0),
        })
    return itens


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    avisos = []
    region = region_efetiva(entradas, config)
    if entradas.get("herdar_regiao_do_alb") and region == entradas["region"]:
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if not entradas_alb:
            avisos.append("Heranca de regiao ligada, mas o ALB nao esta configurado; usando a regiao propria.")

    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    minutos_mes = horas_mes * 60.0

    def faixas(chave):
        return preco.valores.get(chave) or []

    custos = []

    # ---- metricas ----
    # As metricas do ALB sao do namespace AWS/ApplicationELB: publicadas sem custo.
    custo_metricas, detalhes_metricas = base.custo_escalonado(
        faixas("metrica_mes"), entradas["metricas_customizadas"]
    )
    if entradas["metricas_customizadas"]:
        custos.append(
            (
                "Métricas customizadas de escala",
                fmt_int(entradas["metricas_customizadas"]) + " métricas",
                custo_metricas,
            )
        )

    # ---- coleta externa (Datadog) ----
    modo = entradas["modo_coleta_externa"]
    metricas_requisitadas_mes = 0.0
    atualizacoes_mes = 0.0
    custo_coleta = 0.0

    if modo == "getmetricdata":
        ciclos_mes = minutos_mes / entradas["intervalo_coleta_minutos"]
        metricas_requisitadas_mes = ciclos_mes * entradas["metricas_coletadas"]
        custo_coleta, _ = base.custo_escalonado(faixas("getmetricdata_metrica"), metricas_requisitadas_mes)
        custos.append(
            (
                "GetMetricData (coleta do Datadog)",
                fmt_int(metricas_requisitadas_mes) + " métricas requisitadas",
                custo_coleta,
            )
        )
    elif modo == "metric_streams":
        atualizacoes_mes = (
            entradas["metricas_no_stream"] * entradas["atualizacoes_por_metrica_por_minuto"] * minutos_mes
        )
        custo_coleta, _ = base.custo_escalonado(faixas("metric_stream_update"), atualizacoes_mes)
        custos.append(
            ("Metric Streams", fmt_int(atualizacoes_mes) + " atualizações", custo_coleta)
        )

    # ---- alarmes ----
    # Cada politica de target tracking cria um alarme de scale-out e um de scale-in.
    alarmes_escala = (
        entradas["numero_servicos"]
        * entradas["politicas_escala_por_servico"]
        * entradas["alarmes_por_politica"]
    )
    alarmes_standard = alarmes_escala + entradas["alarmes_operacionais"]
    custo_alarmes, _ = base.custo_escalonado(faixas("alarme_mes"), alarmes_standard)
    custos.append(("Alarmes", fmt_int(alarmes_standard) + " alarmes", custo_alarmes))

    if entradas["alarmes_alta_resolucao"]:
        custo_hr, _ = base.custo_escalonado(
            faixas("alarme_alta_resolucao_mes"), entradas["alarmes_alta_resolucao"]
        )
        custos.append(
            ("Alarmes de alta resolução", fmt_int(entradas["alarmes_alta_resolucao"]) + " alarmes", custo_hr)
        )
    if entradas["alarmes_compostos"]:
        custo_comp, _ = base.custo_escalonado(faixas("alarme_composto_mes"), entradas["alarmes_compostos"])
        custos.append(("Alarmes compostos", fmt_int(entradas["alarmes_compostos"]) + " alarmes", custo_comp))

    # ---- logs (normalmente desligado: vao para o Datadog) ----
    gb_ingeridos = 0.0
    gb_armazenados = 0.0
    if entradas["logs_no_cloudwatch"]:
        ia = entradas["classe_logs"] == "infrequent_access"
        chave_ingestao = "log_ingestao_ia_gb" if ia else "log_ingestao_gb"
        chave_storage = "log_storage_ia_gb_mes" if ia else "log_storage_gb_mes"
        if ia and not faixas(chave_ingestao):
            chave_ingestao = "log_ingestao_gb"
            avisos.append("Sem preco de Infrequent Access nesta regiao; usando o preco Standard de ingestao.")

        gb_ingeridos = entradas["gb_logs_por_mes"]
        gb_armazenados = gb_ingeridos * (entradas["retencao_dias"] / 30.0) / entradas["fator_compressao_logs"]
        custo_ingestao, _ = base.custo_escalonado(faixas(chave_ingestao), gb_ingeridos)
        custo_storage, _ = base.custo_escalonado(faixas(chave_storage), gb_armazenados)
        custos.append(("Logs ingeridos", fmt_num(gb_ingeridos, 1) + " GB", custo_ingestao))
        custos.append(("Logs armazenados", fmt_num(gb_armazenados, 1) + " GB-mês", custo_storage))
        if entradas["gb_escaneados_insights_mes"]:
            custo_insights, _ = base.custo_escalonado(
                faixas("insights_scan_gb"), entradas["gb_escaneados_insights_mes"]
            )
            custos.append(
                ("Logs Insights", fmt_num(entradas["gb_escaneados_insights_mes"], 1) + " GB escaneados", custo_insights)
            )

    total = sum(valor for _, _, valor in custos)
    moeda = preco.moeda
    contexto = contexto_dos_servicos(config)

    return {
        "total_mes": total,
        "moeda": moeda,
        "resumo": [
            {"rotulo": "Alarmes de escala", "valor": fmt_int(alarmes_escala)},
            {"rotulo": "Alarmes totais", "valor": fmt_int(alarmes_standard + entradas["alarmes_compostos"])},
            {"rotulo": "Métricas pagas", "valor": fmt_int(entradas["metricas_customizadas"])},
            {"rotulo": "Logs", "valor": "Datadog" if not entradas["logs_no_cloudwatch"] else fmt_num(gb_ingeridos, 0) + " GB"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do CloudWatch",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao(
                "Alarmes de auto scaling",
                fmt_int(alarmes_escala),
                "%s serviços × %s políticas × %s alarmes"
                % (
                    fmt_int(entradas["numero_servicos"]),
                    fmt_int(entradas["politicas_escala_por_servico"]),
                    fmt_int(entradas["alarmes_por_politica"]),
                ),
            ),
            cartao(
                "Métricas de gatilho",
                fmt_int(entradas["metricas_padrao_observadas"] + entradas["metricas_customizadas"]),
                "%s padrão (grátis) + %s customizadas"
                % (fmt_int(entradas["metricas_padrao_observadas"]), fmt_int(entradas["metricas_customizadas"])),
            ),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas,
            preco,
            {
                "region": region,
                "horas_mes": horas_mes,
                "minutos_mes": minutos_mes,
                "modo": modo,
                "custo_metricas": custo_metricas,
                "metricas_requisitadas_mes": metricas_requisitadas_mes,
                "atualizacoes_mes": atualizacoes_mes,
                "custo_coleta": custo_coleta,
                "alarmes_standard": alarmes_standard,
                "alarmes_escala": alarmes_escala,
                "detalhes_metricas": detalhes_metricas,
                "gb_ingeridos": gb_ingeridos,
                "gb_armazenados": gb_armazenados,
                "custos": custos,
                "total": total,
                "contexto": contexto,
            },
        ),
    }


def _secoes(entradas, preco, v):
    moeda = preco.moeda

    linhas_escopo = [linha([celula("Região"), celula(v["region"])])]
    for item in v["contexto"]:
        linhas_escopo.append(
            linha([
                celula(item["nome"]),
                celula(
                    "escala de %s a %s tasks"
                    % (fmt_int(min(item["tasks_fora"], item["tasks_pico"])), fmt_int(item["tasks_pico"]))
                ),
            ])
        )
    linhas_escopo += [
        linha([
            celula("Logs das aplicações"),
            celula(
                "CloudWatch Logs"
                if entradas["logs_no_cloudwatch"]
                else "enviados ao Datadog — fora desta conta"
            ),
        ]),
        linha([
            celula("Janela"),
            celula("%s h/dia × %s dias" % (fmt_num(entradas["horas_por_dia"], 0), fmt_int(entradas["dias_por_mes"]))),
        ]),
    ]

    secoes = [
        base.tabela("Escopo", None, linhas_escopo),
        base.tabela(
            "Métricas de gatilho",
            ["Origem", "Quantidade", "Custo"],
            [
                linha([
                    celula("Padrão (AWS/ECS, AWS/ApplicationELB)"),
                    celula(fmt_int(entradas["metricas_padrao_observadas"]), num=True),
                    celula("sem custo"),
                ]),
                linha([
                    celula("Customizadas (ex.: lag do Kafka)"),
                    celula(fmt_int(entradas["metricas_customizadas"]), num=True),
                    celula(fmt_moeda(v["custo_metricas"], moeda), num=True),
                ]),
            ],
            explica="CPU e memória do serviço e ALBRequestCountPerTarget são publicadas pela AWS sem custo. "
            "Só gatilhos customizados são cobrados, por métrica-mês.",
        ),
    ]

    if v["modo"] == "getmetricdata":
        secoes.append(
            base.tabela(
                "Coleta pelo Datadog",
                None,
                [
                    linha([celula("Modo"), celula("GetMetricData (pull)")]),
                    linha([
                        celula("Ciclos no mês"),
                        celula(
                            "%s min / %s min = %s ciclos"
                            % (
                                fmt_num(v["minutos_mes"], 0),
                                fmt_num(entradas["intervalo_coleta_minutos"], 0),
                                fmt_int(v["minutos_mes"] / entradas["intervalo_coleta_minutos"]),
                            ),
                            num=True,
                        ),
                    ]),
                    linha([
                        celula("Métricas requisitadas"),
                        celula(
                            "%s por ciclo = %s no mês"
                            % (fmt_int(entradas["metricas_coletadas"]), fmt_int(v["metricas_requisitadas_mes"])),
                            num=True,
                        ),
                    ]),
                    linha([celula("Custo"), celula(fmt_moeda(v["custo_coleta"], moeda), num=True)], classe="total"),
                ],
                explica="A integração AWS do Datadog lê as métricas pela API do CloudWatch, e cada métrica "
                "requisitada é cobrada. Intervalos mais curtos multiplicam esse item.",
            )
        )
    elif v["modo"] == "metric_streams":
        secoes.append(
            base.tabela(
                "Coleta pelo Datadog",
                None,
                [
                    linha([celula("Modo"), celula("Metric Streams (push)")]),
                    linha([
                        celula("Atualizações no mês"),
                        celula(
                            "%s métricas × %s/min × %s min = %s"
                            % (
                                fmt_int(entradas["metricas_no_stream"]),
                                fmt_num(entradas["atualizacoes_por_metrica_por_minuto"], 1),
                                fmt_num(v["minutos_mes"], 0),
                                fmt_int(v["atualizacoes_mes"]),
                            ),
                            num=True,
                        ),
                    ]),
                    linha([celula("Custo"), celula(fmt_moeda(v["custo_coleta"], moeda), num=True)], classe="total"),
                ],
                explica="O Metric Stream entrega as métricas quase em tempo real e é cobrado por atualização; "
                "não há custo de Firehose aqui se a entrega for direta ao parceiro.",
            )
        )

    secoes.append(
        base.tabela(
            "Alarmes",
            None,
            [
                linha([
                    celula("De auto scaling"),
                    celula(
                        "%s serviços × %s políticas × %s = %s"
                        % (
                            fmt_int(entradas["numero_servicos"]),
                            fmt_int(entradas["politicas_escala_por_servico"]),
                            fmt_int(entradas["alarmes_por_politica"]),
                            fmt_int(v["alarmes_escala"]),
                        ),
                        num=True,
                    ),
                ]),
                linha([celula("Operacionais"), celula(fmt_int(entradas["alarmes_operacionais"]), num=True)]),
                linha([celula("De alta resolução"), celula(fmt_int(entradas["alarmes_alta_resolucao"]), num=True)]),
                linha([celula("Compostos"), celula(fmt_int(entradas["alarmes_compostos"]), num=True)]),
                linha(
                    [
                        celula("Total"),
                        celula(
                            fmt_int(
                                v["alarmes_standard"]
                                + entradas["alarmes_alta_resolucao"]
                                + entradas["alarmes_compostos"]
                            ),
                            num=True,
                        ),
                    ],
                    classe="total",
                ),
            ],
        )
    )

    linhas_custo = [
        linha([celula(nome), celula(quantidade, num=True), celula(fmt_moeda(valor, moeda), num=True)])
        for nome, quantidade, valor in v["custos"]
    ]
    linhas_custo.append(
        linha([celula("Total mensal"), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)], classe="total")
    )
    secoes.append(base.tabela("Composição do custo", ["Item", "Quantidade", "Total mês"], linhas_custo))

    def primeiro_preco(chave, unidade, por_mil=False):
        obtidas = base.faixas_sem_gratuito(preco.valores.get(chave) or [])
        if not obtidas:
            return "—"
        valor = obtidas[0]["preco"] * (1000 if por_mil else 1)
        return fmt_moeda(valor, moeda, 4) + unidade

    secoes += [
        base.tabela(
            "Preço unitário e fonte",
            None,
            [
                linha([celula("Região"), celula(preco.region)]),
                linha([celula("Métricas padrão (AWS/ECS, AWS/ApplicationELB)"), celula("sem custo")]),
                linha([celula("Métrica customizada"), celula(primeiro_preco("metrica_mes", "/métrica-mês"), num=True)]),
                linha([celula("Alarme"), celula(primeiro_preco("alarme_mes", "/alarme-mês"), num=True)]),
                linha([celula("Alarme composto"), celula(primeiro_preco("alarme_composto_mes", "/alarme-mês"), num=True)]),
                linha([
                    celula("GetMetricData"),
                    celula(primeiro_preco("getmetricdata_metrica", "/mil métricas", por_mil=True), num=True),
                ]),
                linha([
                    celula("Metric Streams"),
                    celula(primeiro_preco("metric_stream_update", "/mil atualizações", por_mil=True), num=True),
                ]),
                linha([celula("Ingestão de logs"), celula(primeiro_preco("log_ingestao_gb", "/GB"), num=True)]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "As métricas que o target tracking usa — CPU e memória do serviço (AWS/ECS) e "
                "ALBRequestCountPerTarget (AWS/ApplicationELB) — são publicadas pela AWS sem custo. O que "
                "se paga no auto scaling são os alarmes: 2 por política de escala.",
                "Escalar por um gatilho customizado, como o lag do consumidor Kafka, custa por métrica-mês. "
                "Publicar o lag por partição em vez de por tópico multiplica esse item pelo número de partições.",
                "Os logs das aplicações vão para o Datadog e não entram nesta conta. Se alguma coisa voltar a "
                "escrever no CloudWatch Logs, ligue a seção de logs: a $0,90/GB a ingestão vira o maior item "
                "rapidamente.",
                "Puxar métricas para o Datadog não é de graça: cada métrica requisitada por GetMetricData é "
                "cobrada, e reduzir o intervalo de coleta multiplica esse item na mesma proporção.",
                "O custo do próprio Datadog (hosts, custom metrics, indexed logs) está fora desta calculadora.",
                "Não estão inclusos: X-Ray/traces, Synthetics, RUM e dashboards (os 3 primeiros são gratuitos).",
                "O free tier do CloudWatch (10 métricas, 10 alarmes, 5 GB de logs) não é modelado: é por conta, "
                "não por recurso.",
                "Premissas estimadas: quantidade de alarmes, métricas coletadas por ciclo e intervalo de coleta.",
            ],
        ),
    ]
    return secoes
