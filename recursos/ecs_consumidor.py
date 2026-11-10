"""Base dos consumidores Kafka que gravam no Aurora.

Dois recursos usam esta logica - o de consentimentos e o de resources. Ambos leem
um topico, aplicam o registro no Aurora e escalam pelo mesmo trio de restricoes do
ECS de consulta, mais o teto de particoes do topico.
"""

import math

from . import base, fargate_comum
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha
from .fargate_comum import ARQUITETURAS

SEGUNDOS_POR_DIA = 86400.0


def construir_campos(rotulo_topico, rotulo_tabela):
    return [
        base.grupo(
            "Volumetria do tópico",
            [
                base.booleano("herdar_regiao_do_alb", "Herdar a região do ALB"),
                base.regiao(),
                base.numero("eventos_por_minuto", "Eventos por minuto", ajuda="Registros em %s." % rotulo_topico),
                base.numero(
                    "escritas_por_evento",
                    "Escritas no Aurora por evento",
                    passo=0.1,
                    ajuda="1 = cada evento vira um upsert em %s." % rotulo_tabela,
                ),
                base.numero("fator_pico", "Fator de pico", passo=0.1),
                base.numero("horas_por_dia", "Horas por dia", maximo=24),
                base.numero("dias_por_mes", "Dias por mês", minimo=1, maximo=31),
            ],
        ),
        base.grupo(
            "Processamento por evento",
            [
                base.numero("tempo_cpu_por_evento_ms", "CPU por evento (ms)", passo=0.5),
                base.numero("latencia_escrita_ms", "Latência da escrita no Aurora (ms)"),
                base.numero("concorrencia_por_task", "Eventos simultâneos por task", minimo=1),
                base.numero("utilizacao_alvo_cpu", "Utilização alvo de CPU (%)", passo=5, minimo=1, maximo=100),
            ],
        ),
        base.grupo(
            "Tópico Kafka",
            [
                base.numero("particoes_topico", "Partições do tópico", minimo=1),
                base.numero("consumidores_por_task", "Consumidores por task", minimo=1),
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
            "Auto Scaling",
            [
                base.numero("tasks_minimas", "Tasks mínimas (ASG)", minimo=1),
                base.numero("tasks_maximas", "Tasks máximas (ASG)", minimo=1),
            ],
        ),
    ]


def region_efetiva(entradas, config):
    if entradas.get("herdar_regiao_do_alb"):
        entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
        if entradas_alb and "region" in entradas_alb:
            return entradas_alb["region"]
    return entradas["region"]


def calcular(entradas, preco, rotulo_tabela):
    """Dimensiona as tasks e devolve o resultado pronto para a tela."""
    avisos = list(fargate_comum.validar_combo(entradas))

    eventos_s = entradas["eventos_por_minuto"] * entradas["fator_pico"] / 60.0
    horas_mes = entradas["horas_por_dia"] * entradas["dias_por_mes"]
    eventos_mes = eventos_s * horas_mes * 3600.0
    escritas_s = eventos_s * entradas["escritas_por_evento"]

    vcpus = eventos_s * (entradas["tempo_cpu_por_evento_ms"] / 1000.0) / (entradas["utilizacao_alvo_cpu"] / 100.0)
    tasks_cpu = vcpus / entradas["vcpu_por_task"]
    em_voo = eventos_s * (entradas["latencia_escrita_ms"] / 1000.0)
    tasks_concorrencia = em_voo / entradas["concorrencia_por_task"]
    tasks_particoes = entradas["particoes_topico"] / entradas["consumidores_por_task"]

    restricao = "CPU" if tasks_cpu >= tasks_concorrencia else "Concorrência"
    desejadas = max(1, int(math.ceil(max(tasks_cpu, tasks_concorrencia))), int(entradas["tasks_minimas"]))
    limitado_particoes = desejadas > int(tasks_particoes)
    tasks = min(desejadas, max(1, int(tasks_particoes)), int(entradas["tasks_maximas"]))

    if limitado_particoes:
        avisos.append(
            "O pico pede %s tasks, mas %s partições com %s consumidores por task limitam a %s: o lag cresce."
            % (
                fmt_int(desejadas),
                fmt_int(entradas["particoes_topico"]),
                fmt_int(entradas["consumidores_por_task"]),
                fmt_int(int(tasks_particoes)),
            )
        )
    if desejadas > int(entradas["tasks_maximas"]):
        avisos.append(
            "O pico exige %s tasks, acima do máximo de %s do ASG."
            % (fmt_int(desejadas), fmt_int(entradas["tasks_maximas"]))
        )

    tarifa = fargate_comum.precos_da_task(entradas, preco)
    task_horas = tasks * horas_mes
    custo_vcpu = task_horas * entradas["vcpu_por_task"] * tarifa["preco_vcpu"]
    custo_memoria = task_horas * entradas["memoria_gb_por_task"] * tarifa["preco_gb"]
    custo_storage = task_horas * tarifa["storage_cobrado_gb"] * tarifa["preco_storage"]
    total = custo_vcpu + custo_memoria + custo_storage
    moeda = preco.moeda

    return {
        "total_mes": total,
        "moeda": moeda,
        "expostos": {
            "tasks_pico": tasks,
            "tasks_fora": tasks,
            "task_horas_mes": task_horas,
            "eventos_mes": eventos_mes,
            "unidades_mes": eventos_mes,
            "escritas_aurora_s": escritas_s,
            "servicos": 1,
        },
        "resumo": [
            {"rotulo": "Eventos/min", "valor": fmt_int(entradas["eventos_por_minuto"])},
            {"rotulo": "Tasks", "valor": fmt_int(tasks)},
            {"rotulo": "Restrição", "valor": "Partições" if limitado_particoes else restricao},
            {"rotulo": "Escritas/s", "valor": fmt_num(escritas_s, 1)},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do Fargate",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao("Tasks", fmt_int(tasks),
                   "restrição: %s" % ("partições" if limitado_particoes else restricao.lower())),
            cartao("Escritas no Aurora", fmt_num(escritas_s, 1) + "/s",
                   "%s eventos/s no tópico" % fmt_num(eventos_s, 1)),
        ],
        "avisos": avisos,
        "secoes": [
            base.tabela(
                "Volumetria",
                None,
                [
                    linha([celula("Região"), celula(preco.region)]),
                    linha([celula("Eventos por minuto"), celula(fmt_int(entradas["eventos_por_minuto"]), num=True)]),
                    linha([celula("Eventos por segundo"), celula(fmt_num(eventos_s, 1), num=True)]),
                    linha([celula("Escritas em %s" % rotulo_tabela), celula(fmt_num(escritas_s, 1) + "/s", num=True)]),
                    linha([celula("Eventos no mês"), celula(fmt_int(eventos_mes), num=True)]),
                    linha([
                        celula("Janela"),
                        celula("%s h/dia × %s dias" % (fmt_num(entradas["horas_por_dia"], 0), fmt_int(entradas["dias_por_mes"]))),
                    ]),
                ],
            ),
            base.tabela(
                "Dimensionamento",
                ["Restrição", "Cálculo", "Tasks"],
                [
                    linha(
                        [
                            celula("CPU"),
                            celula(
                                "%s ev/s × %s ms / %s%% = %s vCPU"
                                % (
                                    fmt_num(eventos_s, 1),
                                    fmt_num(entradas["tempo_cpu_por_evento_ms"], 1),
                                    fmt_num(entradas["utilizacao_alvo_cpu"], 0),
                                    fmt_num(vcpus, 2),
                                )
                            ),
                            celula(fmt_num(tasks_cpu, 2), num=True),
                        ],
                        classe="dominante" if restricao == "CPU" and not limitado_particoes else None,
                    ),
                    linha(
                        [
                            celula("Concorrência de I/O"),
                            celula(
                                "%s ev/s × %s ms = %s em voo / %s por task"
                                % (
                                    fmt_num(eventos_s, 1),
                                    fmt_num(entradas["latencia_escrita_ms"], 0),
                                    fmt_num(em_voo, 1),
                                    fmt_int(entradas["concorrencia_por_task"]),
                                )
                            ),
                            celula(fmt_num(tasks_concorrencia, 2), num=True),
                        ],
                        classe="dominante" if restricao == "Concorrência" and not limitado_particoes else None,
                    ),
                    linha(
                        [
                            celula("Teto de partições"),
                            celula(
                                "%s partições / %s por task"
                                % (fmt_int(entradas["particoes_topico"]), fmt_int(entradas["consumidores_por_task"]))
                            ),
                            celula(fmt_num(tasks_particoes, 2), num=True),
                        ],
                        classe="dominante" if limitado_particoes else None,
                    ),
                    linha(
                        [
                            celula("Tasks provisionadas"),
                            celula("%s vCPU · %s GB · ASG %s–%s"
                                   % (fmt_num(entradas["vcpu_por_task"], 2), fmt_num(entradas["memoria_gb_por_task"], 1),
                                      fmt_int(entradas["tasks_minimas"]), fmt_int(entradas["tasks_maximas"]))),
                            celula(fmt_int(tasks), num=True),
                        ],
                        classe="total",
                    ),
                ],
            ),
            base.tabela(
                "Composição do custo",
                ["Item", "Quantidade", "Preço unitário", "Total mês"],
                [
                    linha([
                        celula("vCPU-horas"),
                        celula(fmt_num(task_horas * entradas["vcpu_por_task"], 0) + " vCPU-h", num=True),
                        celula(fmt_moeda(tarifa["preco_vcpu"], moeda, 4) + "/vCPU-h", num=True),
                        celula(fmt_moeda(custo_vcpu, moeda), num=True),
                    ]),
                    linha([
                        celula("GB-horas de memória"),
                        celula(fmt_num(task_horas * entradas["memoria_gb_por_task"], 0) + " GB-h", num=True),
                        celula(fmt_moeda(tarifa["preco_gb"], moeda, 4) + "/GB-h", num=True),
                        celula(fmt_moeda(custo_memoria, moeda), num=True),
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
                    linha([celula("Arquitetura"), celula(tarifa["arquitetura_rotulo"])]),
                    linha([celula("vCPU-hora"), celula(fmt_moeda(tarifa["preco_vcpu"], moeda, 4), num=True)]),
                    linha([celula("GB-hora"), celula(fmt_moeda(tarifa["preco_gb"], moeda, 4), num=True)]),
                    linha([celula("Fonte"), celula(preco.fonte)]),
                    linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
                ],
            ),
            base.lista(
                "Notas",
                [
                    "O paralelismo não passa do número de partições: tasks × consumidores por task.",
                    "As escritas geradas aqui entram na carga do Aurora, que as soma às dos outros fluxos.",
                    "Não estão inclusos: o cluster Kafka, ECR, Data Transfer entre AZs e o custo do Aurora.",
                    "Premissas estimadas: CPU por evento, latência da escrita, concorrência por task e partições.",
                ],
            ),
        ],
    }
