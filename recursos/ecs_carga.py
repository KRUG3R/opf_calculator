"""ECS Fargate - Carga: consumidor Kafka que alimenta a tabela de saldo agregado.

Para cada evento de alteracao de saldo a aplicacao Java:

  1. le o evento do topico;
  2. consulta a API de consentimento do cliente;
  3. so para quem tem consentimento, busca o resource_id do account_id;
  4. grava o registro no DynamoDB de saldo agregado.

O numero de tasks e o maximo entre tres restricoes - CPU, concorrencia de I/O e
particoes do topico - porque num consumidor Kafka o paralelismo nao passa do
numero de particoes, por mais CPU que se adicione.
"""

import math

from . import base, fargate_comum
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha
from .fargate_comum import ARQUITETURAS, extrair_precos

ID = "ecs-carga"
NOME = "ECS Fargate — Carga"
NOME_CURTO = "ECS Carga"
DESCRICAO = "Consumidor Kafka que alimenta a tabela de saldo agregado."
OFFER_CODE = fargate_comum.OFFER_CODE
ORDEM = 40
STATUS = "ativo"

SEGUNDOS_POR_DIA = 86400.0

MODOS_VOLUMETRIA = [
    ("eventos_por_minuto", "Informar eventos por minuto"),
    ("derivar_de_clientes", "Derivar de clientes × alterações/dia"),
]

CAMPOS = [
    base.grupo(
        "Volumetria do tópico",
        [
            base.escolha("modo_volumetria", "Como medir o volume", MODOS_VOLUMETRIA),
            base.numero(
                "eventos_por_minuto",
                "Eventos por minuto no tópico",
                ajuda="Alterações de saldo publicadas no Kafka.",
                visivel_quando={"modo_volumetria": "eventos_por_minuto"},
            ),
            base.numero(
                "numero_clientes",
                "Número de clientes",
                visivel_quando={"modo_volumetria": "derivar_de_clientes"},
            ),
            base.numero(
                "alteracoes_por_cliente_por_dia",
                "Alterações de saldo por cliente por dia",
                passo=0.1,
                visivel_quando={"modo_volumetria": "derivar_de_clientes"},
            ),
            base.numero(
                "percentual_eventos_com_consentimento",
                "Eventos de clientes com consentimento (%)",
                passo=1,
                maximo=100,
                ajuda="Só estes seguem para a busca do resource_id e para a escrita no DynamoDB.",
            ),
            base.numero("fator_pico", "Fator de pico", passo=0.1),
            base.booleano("herdar_regiao_do_alb", "Herdar a região do ALB"),
            base.regiao(),
            base.numero("horas_por_dia", "Horas por dia", maximo=24),
            base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
        ],
    ),
    base.grupo(
        "Processamento por evento",
        [
            base.numero(
                "tempo_cpu_por_evento_ms",
                "CPU por evento (ms)",
                passo=0.5,
                ajuda="Desserialização do evento e chamada de consentimento, para todo evento.",
            ),
            base.numero(
                "tempo_cpu_adicional_ms",
                "CPU adicional com consentimento (ms)",
                passo=0.5,
                ajuda="Busca do resource_id e escrita no DynamoDB.",
            ),
            base.numero("latencia_consentimento_ms", "Latência da API de consentimento (ms)"),
            base.numero("latencia_resource_ms", "Latência da busca do resource_id (ms)"),
            base.numero("latencia_escrita_ms", "Latência da escrita no DynamoDB (ms)"),
            base.numero(
                "concorrencia_por_task",
                "Eventos simultâneos por task",
                minimo=1,
                ajuda="Threads ou chamadas assíncronas em voo numa task.",
            ),
            base.numero("utilizacao_alvo_cpu", "Utilização alvo de CPU (%)", passo=5, minimo=1, maximo=100),
        ],
    ),
    base.grupo(
        "Tópico Kafka",
        [
            base.numero(
                "particoes_topico",
                "Partições do tópico",
                minimo=1,
                ajuda="Limite rígido de paralelismo: uma partição é consumida por um consumidor.",
            ),
            base.numero(
                "consumidores_por_task",
                "Consumidores por task",
                minimo=1,
                ajuda="Threads de consumo dentro de cada task.",
            ),
        ],
    ),
    base.grupo(
        "Tamanho da task",
        [
            base.escolha("arquitetura", "Arquitetura", ARQUITETURAS),
            base.numero("vcpu_por_task", "vCPU por task", passo=0.25, minimo=0.25),
            base.numero("memoria_gb_por_task", "Memória por task (GB)", passo=0.5, minimo=0.5),
            base.numero("armazenamento_efemero_gb", "Armazenamento efêmero (GB)", minimo=20, maximo=200),
        ],
    ),
    base.grupo(
        "Auto Scaling e perfil de carga",
        [
            base.numero("tasks_minimas", "Tasks mínimas (ASG)", minimo=1),
            base.numero("tasks_maximas", "Tasks máximas (ASG)", minimo=1),
            base.numero("horas_pico_por_dia", "Horas de pico por dia", maximo=24),
            base.numero("percentual_eventos_fora_pico", "Eventos fora do pico (% do pico)", passo=5, maximo=100),
        ],
    ),
]


# ---------- volumetria ----------

def resolver_volumetria(entradas, config):
    """Eventos por segundo no topico e regiao efetiva."""
    avisos = []
    region = entradas["region"]

    if entradas.get("herdar_regiao_do_alb"):
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if entradas_alb and "region" in entradas_alb:
            region = entradas_alb["region"]
        else:
            avisos.append("Heranca de regiao ligada, mas o ALB nao esta configurado; usando a regiao propria.")

    if entradas["modo_volumetria"] == "derivar_de_clientes":
        eventos_por_minuto = (
            entradas["numero_clientes"] * entradas["alteracoes_por_cliente_por_dia"] / (SEGUNDOS_POR_DIA / 60.0)
        )
    else:
        eventos_por_minuto = entradas["eventos_por_minuto"]

    eventos_por_minuto *= entradas["fator_pico"]
    return {
        "region": region,
        "eventos_por_minuto": eventos_por_minuto,
        "eventos_por_segundo": eventos_por_minuto / 60.0,
    }, avisos


def region_efetiva(entradas, config):
    volumetria, _ = resolver_volumetria(entradas, config)
    return volumetria["region"]


# ---------- dimensionamento ----------

def _restricoes(eventos_s, entradas, pct_consentimento):
    """Tasks necessarias por CPU, por concorrencia de I/O e pelo numero de particoes."""
    cpu_medio_ms = entradas["tempo_cpu_por_evento_ms"] + pct_consentimento * entradas["tempo_cpu_adicional_ms"]
    latencia_media_ms = entradas["latencia_consentimento_ms"] + pct_consentimento * (
        entradas["latencia_resource_ms"] + entradas["latencia_escrita_ms"]
    )

    vcpus = eventos_s * (cpu_medio_ms / 1000.0) / (entradas["utilizacao_alvo_cpu"] / 100.0)
    tasks_cpu = vcpus / entradas["vcpu_por_task"]

    em_voo = eventos_s * (latencia_media_ms / 1000.0)
    tasks_concorrencia = em_voo / entradas["concorrencia_por_task"]

    tasks_particoes = entradas["particoes_topico"] / entradas["consumidores_por_task"]

    return {
        "eventos_s": eventos_s,
        "cpu_medio_ms": cpu_medio_ms,
        "latencia_media_ms": latencia_media_ms,
        "vcpus_necessarias": vcpus,
        "em_voo": em_voo,
        "tasks_cpu": tasks_cpu,
        "tasks_concorrencia": tasks_concorrencia,
        "tasks_particoes": tasks_particoes,
        "tasks_brutas": max(tasks_cpu, tasks_concorrencia),
        "restricao": "CPU" if tasks_cpu >= tasks_concorrencia else "Concorrência",
    }


def _aplicar_limites(faixa, entradas):
    """Arredonda, aplica o ASG e o teto de particoes do topico."""
    tasks = max(1, int(math.ceil(faixa["tasks_brutas"])))
    tasks = max(tasks, int(entradas["tasks_minimas"]))
    faixa["tasks_desejadas"] = tasks

    teto_particoes = int(faixa["tasks_particoes"])
    faixa["limitado_por_particoes"] = tasks > teto_particoes
    tasks = min(tasks, max(1, teto_particoes))

    faixa["no_teto_asg"] = tasks > int(entradas["tasks_maximas"])
    faixa["tasks"] = min(tasks, int(entradas["tasks_maximas"]))
    faixa["paralelismo"] = faixa["tasks"] * entradas["consumidores_por_task"]
    return faixa


def calcular(entradas, recurso_cfg, preco, config=None):
    volumetria, avisos = resolver_volumetria(entradas, config)
    avisos = list(avisos)
    avisos.extend(fargate_comum.validar_combo(entradas))

    pct = entradas["percentual_eventos_com_consentimento"] / 100.0
    eventos_s_pico = volumetria["eventos_por_segundo"]
    eventos_s_fora = eventos_s_pico * (entradas["percentual_eventos_fora_pico"] / 100.0)

    horas_por_dia = entradas["horas_por_dia"]
    dias_por_mes = entradas["dias_por_mes"]
    horas_mes = horas_por_dia * dias_por_mes
    horas_pico_dia = min(entradas["horas_pico_por_dia"], horas_por_dia)
    horas_fora_dia = max(0.0, horas_por_dia - horas_pico_dia)

    pico = _aplicar_limites(_restricoes(eventos_s_pico, entradas, pct), entradas)
    fora = _aplicar_limites(_restricoes(eventos_s_fora, entradas, pct), entradas)

    if pico["limitado_por_particoes"]:
        avisos.append(
            "O pico pede %s tasks, mas %s particoes com %s consumidores por task limitam o paralelismo a %s tasks: "
            "o lag do topico vai crescer. Aumente as particoes ou os consumidores por task."
            % (
                fmt_int(pico["tasks_desejadas"]),
                fmt_int(entradas["particoes_topico"]),
                fmt_int(entradas["consumidores_por_task"]),
                fmt_int(int(pico["tasks_particoes"])),
            )
        )
    if pico["no_teto_asg"]:
        avisos.append(
            "O pico exige %s tasks, acima do maximo de %s do ASG: o consumo nao acompanha a producao do topico."
            % (fmt_int(pico["tasks_desejadas"]), fmt_int(entradas["tasks_maximas"]))
        )

    # ---- escritas geradas no DynamoDB de saldo ----
    escritas_s = eventos_s_pico * pct
    aviso_cruzado = _conferir_tabela_de_saldo(escritas_s, config)
    if aviso_cruzado:
        avisos.append(aviso_cruzado)

    # ---- custo ----
    tarifa = fargate_comum.precos_da_task(entradas, preco)
    task_horas_mes = dias_por_mes * (horas_pico_dia * pico["tasks"] + horas_fora_dia * fora["tasks"])
    vcpu_horas_mes = task_horas_mes * entradas["vcpu_por_task"]
    gb_horas_mes = task_horas_mes * entradas["memoria_gb_por_task"]
    storage_gb_horas_mes = task_horas_mes * tarifa["storage_cobrado_gb"]

    custo_vcpu = vcpu_horas_mes * tarifa["preco_vcpu"]
    custo_memoria = gb_horas_mes * tarifa["preco_gb"]
    custo_storage = storage_gb_horas_mes * tarifa["preco_storage"]
    total = custo_vcpu + custo_memoria + custo_storage

    moeda = preco.moeda
    eventos_mes = volumetria["eventos_por_minuto"] * 60.0 * (
        horas_pico_dia * dias_por_mes
    ) + eventos_s_fora * 3600.0 * horas_fora_dia * dias_por_mes

    return {
        "total_mes": total,
        "moeda": moeda,
        # Numeros que outros recursos consomem (ex.: CloudWatch)
        "expostos": {
            "tasks_pico": pico["tasks"],
            "tasks_fora": fora["tasks"],
            "task_horas_mes": task_horas_mes,
            "unidades_mes": eventos_mes,
            "eventos_mes": eventos_mes,
            "servicos": 1,
            # Carga que este consumidor joga na tabela de saldo: para cada evento com
            # consentimento ele le o resource_id pelo GSI account_id e grava o registro.
            "eventos_s_pico": eventos_s_pico,
            "eventos_com_consentimento_s": escritas_s,
            "leituras_dynamo_s": escritas_s,
            "escritas_dynamo_s": escritas_s,
        },
        "resumo": [
            {"rotulo": "Eventos/min", "valor": fmt_int(volumetria["eventos_por_minuto"])},
            {"rotulo": "Tasks no pico", "valor": fmt_int(pico["tasks"])},
            {"rotulo": "Restrição", "valor": "Partições" if pico["limitado_por_particoes"] else pico["restricao"]},
            {"rotulo": "Escritas geradas", "valor": fmt_num(escritas_s, 0) + "/s"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do Fargate",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao(
                "Tasks no pico",
                fmt_int(pico["tasks"]),
                "restrição: %s%s"
                % (
                    "partições" if pico["limitado_por_particoes"] else pico["restricao"],
                    " · fora do pico: %s" % fmt_int(fora["tasks"]) if horas_fora_dia > 0 else "",
                ),
            ),
            cartao(
                "Eventos por segundo",
                fmt_num(eventos_s_pico, 0),
                "%s com consentimento → %s escritas/s"
                % (fmt_num(entradas["percentual_eventos_com_consentimento"], 0) + "%", fmt_num(escritas_s, 0)),
            ),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas,
            volumetria,
            pico,
            fora,
            tarifa,
            preco,
            {
                "pct": pct,
                "eventos_s_pico": eventos_s_pico,
                "eventos_s_fora": eventos_s_fora,
                "eventos_mes": eventos_mes,
                "escritas_s": escritas_s,
                "horas_pico_dia": horas_pico_dia,
                "horas_fora_dia": horas_fora_dia,
                "horas_mes": horas_mes,
                "dias_por_mes": dias_por_mes,
                "task_horas_mes": task_horas_mes,
                "vcpu_horas_mes": vcpu_horas_mes,
                "gb_horas_mes": gb_horas_mes,
                "storage_gb_horas_mes": storage_gb_horas_mes,
                "custo_vcpu": custo_vcpu,
                "custo_memoria": custo_memoria,
                "custo_storage": custo_storage,
                "total": total,
            },
        ),
    }


def _conferir_tabela_de_saldo(escritas_s, config):
    """Compara as escritas geradas aqui com o que a tabela de saldo tem configurado."""
    entradas_saldo = ((config or {}).get("recursos", {}).get("dynamodb-saldo") or {}).get("entradas")
    if not entradas_saldo:
        return None
    if entradas_saldo.get("herdar_carga_dos_ecs"):
        return None  # a tabela de saldo já usa estes números
    registros = entradas_saldo.get("numero_registros") or 0
    por_dia = entradas_saldo.get("atualizacoes_por_registro_por_dia") or 0
    if not registros or not por_dia:
        return None
    escritas_saldo = registros * por_dia / SEGUNDOS_POR_DIA
    if escritas_saldo <= 0:
        return None
    razao = escritas_s / escritas_saldo
    if 0.8 <= razao <= 1.25:
        return None
    return (
        "Este consumidor gera %s escritas/s, mas o DynamoDB de saldo esta configurado para %s/s "
        "(%s registros x %s atualizacoes/dia). Alinhe 'atualizacoes_por_registro_por_dia' na tabela de saldo "
        "para que os dois recursos contem a mesma carga."
        % (
            fmt_num(escritas_s, 0),
            fmt_num(escritas_saldo, 0),
            fmt_int(registros),
            fmt_num(por_dia, 2),
        )
    )


def _secoes(entradas, volumetria, pico, fora, tarifa, preco, v):
    moeda = preco.moeda

    def linhas_restricao(faixa):
        linhas = [
            linha(
                [
                    celula("CPU"),
                    celula(
                        "%s ev/s × %s ms / %s%% = %s vCPU"
                        % (
                            fmt_num(faixa["eventos_s"], 0),
                            fmt_num(faixa["cpu_medio_ms"], 1),
                            fmt_num(entradas["utilizacao_alvo_cpu"], 0),
                            fmt_num(faixa["vcpus_necessarias"], 1),
                        )
                    ),
                    celula(fmt_num(faixa["tasks_cpu"], 2), num=True),
                ],
                classe="dominante" if faixa["restricao"] == "CPU" and not faixa["limitado_por_particoes"] else None,
            ),
            linha(
                [
                    celula("Concorrência de I/O"),
                    celula(
                        "%s ev/s × %s ms = %s em voo / %s por task"
                        % (
                            fmt_num(faixa["eventos_s"], 0),
                            fmt_num(faixa["latencia_media_ms"], 1),
                            fmt_num(faixa["em_voo"], 0),
                            fmt_int(entradas["concorrencia_por_task"]),
                        )
                    ),
                    celula(fmt_num(faixa["tasks_concorrencia"], 2), num=True),
                ],
                classe="dominante"
                if faixa["restricao"] == "Concorrência" and not faixa["limitado_por_particoes"]
                else None,
            ),
            linha(
                [
                    celula("Teto de partições"),
                    celula(
                        "%s partições / %s consumidores por task"
                        % (fmt_int(entradas["particoes_topico"]), fmt_int(entradas["consumidores_por_task"]))
                    ),
                    celula(fmt_num(faixa["tasks_particoes"], 2), num=True),
                ],
                classe="dominante" if faixa["limitado_por_particoes"] else None,
            ),
            linha(
                [
                    celula("Tasks provisionadas"),
                    celula(
                        "ASG entre %s e %s · paralelismo %s"
                        % (
                            fmt_int(entradas["tasks_minimas"]),
                            fmt_int(entradas["tasks_maximas"]),
                            fmt_int(faixa["paralelismo"]),
                        )
                    ),
                    celula(fmt_int(faixa["tasks"]), num=True),
                ],
                classe="total",
            ),
        ]
        return linhas

    secoes = [
        base.tabela(
            "Volumetria do tópico",
            None,
            [
                linha([
                    celula("Origem do volume"),
                    celula(dict(MODOS_VOLUMETRIA)[entradas["modo_volumetria"]]),
                ]),
                linha([celula("Região"), celula(volumetria["region"])]),
                linha([celula("Eventos por minuto"), celula(fmt_int(volumetria["eventos_por_minuto"]), num=True)]),
                linha([celula("Eventos por segundo"), celula(fmt_num(v["eventos_s_pico"], 1), num=True)]),
                linha([
                    celula("Com consentimento"),
                    celula(
                        "%s%% → %s eventos/s"
                        % (
                            fmt_num(entradas["percentual_eventos_com_consentimento"], 0),
                            fmt_num(v["escritas_s"], 1),
                        ),
                        num=True,
                    ),
                ]),
                linha([celula("Eventos no mês"), celula(fmt_int(v["eventos_mes"]), num=True)]),
                linha([
                    celula("Janela"),
                    celula("%s h/dia × %s dias" % (fmt_num(entradas["horas_por_dia"], 0), fmt_int(v["dias_por_mes"]))),
                ]),
            ],
        ),
        base.tabela(
            "Custo por evento",
            None,
            [
                linha([
                    celula("CPU média por evento"),
                    celula(
                        "%s ms + %s%% × %s ms = %s ms"
                        % (
                            fmt_num(entradas["tempo_cpu_por_evento_ms"], 1),
                            fmt_num(entradas["percentual_eventos_com_consentimento"], 0),
                            fmt_num(entradas["tempo_cpu_adicional_ms"], 1),
                            fmt_num(pico["cpu_medio_ms"], 2),
                        ),
                        num=True,
                    ),
                ]),
                linha([
                    celula("Latência média por evento"),
                    celula(
                        "%s ms + %s%% × (%s + %s) ms = %s ms"
                        % (
                            fmt_num(entradas["latencia_consentimento_ms"], 0),
                            fmt_num(entradas["percentual_eventos_com_consentimento"], 0),
                            fmt_num(entradas["latencia_resource_ms"], 0),
                            fmt_num(entradas["latencia_escrita_ms"], 0),
                            fmt_num(pico["latencia_media_ms"], 1),
                        ),
                        num=True,
                    ),
                ]),
            ],
            explica="A consulta de consentimento acontece para todo evento; a busca do resource_id e a "
            "escrita no DynamoDB, só para quem tem consentimento.",
        ),
        base.tabela(
            "Dimensionamento no pico",
            ["Restrição", "Cálculo", "Tasks"],
            linhas_restricao(pico),
            explica="As tasks atendem a restrição mais exigente, e o paralelismo nunca passa do número de "
            "partições do tópico.",
        ),
    ]

    if v["horas_fora_dia"] > 0:
        secoes.append(
            base.tabela(
                "Dimensionamento fora do pico",
                ["Restrição", "Cálculo", "Tasks"],
                linhas_restricao(fora),
                explica="%s h/dia a %s%% do volume de pico."
                % (fmt_num(v["horas_fora_dia"], 0), fmt_num(entradas["percentual_eventos_fora_pico"], 0)),
            )
        )

    secoes += [
        base.tabela(
            "Task e perfil do ASG",
            None,
            [
                linha([celula("Arquitetura"), celula(tarifa["arquitetura_rotulo"])]),
                linha([
                    celula("Tamanho da task"),
                    celula(
                        "%s vCPU · %s GB"
                        % (fmt_num(entradas["vcpu_por_task"], 2), fmt_num(entradas["memoria_gb_por_task"], 1))
                    ),
                ]),
                linha([
                    celula("Consumidores por task"),
                    celula(fmt_int(entradas["consumidores_por_task"]), num=True),
                ]),
                linha([celula("Custo por task-hora"), celula(fmt_moeda(tarifa["custo_task_hora"], moeda, 4), num=True)]),
                linha([
                    celula("Tasks no pico / fora do pico" if v["horas_fora_dia"] > 0 else "Tasks provisionadas"),
                    celula(
                        "%s / %s" % (fmt_int(pico["tasks"]), fmt_int(fora["tasks"]))
                        if v["horas_fora_dia"] > 0
                        else fmt_int(pico["tasks"]),
                        num=True,
                    ),
                ]),
                linha([celula("Task-horas no mês"), celula(fmt_num(v["task_horas_mes"], 0), num=True)]),
            ],
        ),
        base.tabela(
            "Composição do custo",
            ["Item", "Quantidade", "Preço unitário", "Total mês"],
            [
                linha([
                    celula("vCPU-horas"),
                    celula(fmt_num(v["vcpu_horas_mes"], 0) + " vCPU-h", num=True),
                    celula(fmt_moeda(tarifa["preco_vcpu"], moeda, 4) + "/vCPU-h", num=True),
                    celula(fmt_moeda(v["custo_vcpu"], moeda), num=True),
                ]),
                linha([
                    celula("GB-horas de memória"),
                    celula(fmt_num(v["gb_horas_mes"], 0) + " GB-h", num=True),
                    celula(fmt_moeda(tarifa["preco_gb"], moeda, 4) + "/GB-h", num=True),
                    celula(fmt_moeda(v["custo_memoria"], moeda), num=True),
                ]),
                linha([
                    celula("Armazenamento efêmero"),
                    celula(fmt_num(v["storage_gb_horas_mes"], 0) + " GB-h", num=True),
                    celula(fmt_moeda(tarifa["preco_storage"], moeda, 6) + "/GB-h", num=True),
                    celula(fmt_moeda(v["custo_storage"], moeda), num=True),
                ]),
                linha(
                    [celula("Total mensal"), celula(""), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)],
                    classe="total",
                ),
                linha([
                    celula("Custo por milhão de eventos"),
                    celula(""),
                    celula(""),
                    celula(
                        fmt_moeda(v["total"] / v["eventos_mes"] * 1e6 if v["eventos_mes"] else 0, moeda, 4),
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
                linha([
                    celula("vCPU-hora (%s)" % tarifa["arquitetura_rotulo"]),
                    celula(fmt_moeda(tarifa["preco_vcpu"], moeda, 4), num=True),
                ]),
                linha([
                    celula("GB-hora (%s)" % tarifa["arquitetura_rotulo"]),
                    celula(fmt_moeda(tarifa["preco_gb"], moeda, 4), num=True),
                ]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "O paralelismo de um grupo de consumidores Kafka é limitado pelo número de partições: "
                "tasks × consumidores por task não deve passar das partições, senão há tasks ociosas.",
                "A escrita no DynamoDB de saldo gerada aqui é a mesma carga que a tabela de saldo precisa "
                "suportar: a calculadora avisa quando os dois recursos estão configurados com volumes diferentes.",
                "Um cache de consentimento (Redis ou local) derruba a latência e a CPU do passo que roda em "
                "100% dos eventos — é a otimização de maior efeito neste fluxo.",
                "Não estão inclusos: o cluster Kafka (MSK ou terceiro), Data Transfer entre AZs, ECR, "
                "CloudWatch e a capacidade de escrita do DynamoDB.",
                "Premissas estimadas: CPU por evento, latências das dependências, concorrência por task, "
                "partições do tópico e o percentual de clientes com consentimento.",
            ],
        ),
    ]
    return secoes
