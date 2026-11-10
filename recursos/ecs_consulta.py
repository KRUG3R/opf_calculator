"""ECS Fargate - Consulta: tasks que atendem as consultas vindas do ALB.

A aplicacao consulta o Aurora e devolve a pagina de slots dentro do response
time alvo. O numero de tasks e o maximo entre duas restricoes:

  CPU          vCPUs = TPS x tempo de CPU por request / utilizacao alvo
  Concorrencia requests em voo (Lei de Little) / concorrencia por task

O Auto Scaling acompanha a demanda, entao o custo e calculado por faixa horaria:
tasks no pico durante as horas de pico e tasks fora do pico no restante da janela,
sempre respeitando o minimo e o maximo do ASG.
"""

import math

from . import base, fargate_comum
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha
from .fargate_comum import ARQUITETURAS, COMBOS_FARGATE, STORAGE_EFEMERO_GRATUITO_GB, extrair_precos

ID = "ecs-consulta"
NOME = "ECS Fargate — Consulta"
NOME_CURTO = "ECS Consulta"
DESCRICAO = "Tasks que atendem as consultas vindas do ALB."
OFFER_CODE = "AmazonECS"
ORDEM = 30
STATUS = "ativo"

CAMPOS = [
    base.grupo(
        "Volumetria",
        [
            base.booleano(
                "herdar_volumetria_do_alb",
                "Herdar volumetria do ALB",
                ajuda="Usa região, TPM por IF, nº de IFs, fator de pico, response time e janela do ALB.",
            ),
            base.regiao(),
            base.numero(
                "tpm_por_if",
                "TPM por IF (transações/min)",
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
            base.numero(
                "numero_ifs",
                "Número estimado de IFs",
                minimo=1,
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
            base.numero(
                "fator_pico",
                "Fator de pico",
                passo=0.1,
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
            base.numero(
                "response_time_s",
                "Response time alvo (s)",
                passo=0.1,
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
            base.numero(
                "horas_por_dia",
                "Horas por dia",
                maximo=24,
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
            base.numero(
                "dias_por_mes",
                "Dias por mês",
                minimo=1,
                maximo=31,
                visivel_quando={"herdar_volumetria_do_alb": False},
            ),
        ],
    ),
    base.grupo(
        "Tamanho da task",
        [
            base.escolha("arquitetura", "Arquitetura", ARQUITETURAS),
            base.numero("vcpu_por_task", "vCPU por task", passo=0.25, minimo=0.25),
            base.numero("memoria_gb_por_task", "Memória por task (GB)", passo=0.5, minimo=0.5),
            base.numero(
                "armazenamento_efemero_gb",
                "Armazenamento efêmero (GB)",
                minimo=20,
                maximo=200,
                ajuda="Os primeiros 20 GB por task são gratuitos.",
            ),
        ],
    ),
    base.grupo(
        "Desempenho da aplicação",
        [
            base.numero(
                "tempo_cpu_por_request_ms",
                "Tempo de CPU por request (ms)",
                passo=1,
                ajuda="Só o processamento; a espera pelo DynamoDB não consome CPU.",
            ),
            base.numero(
                "utilizacao_alvo_cpu",
                "Utilização alvo de CPU (%)",
                passo=5,
                minimo=1,
                maximo=100,
                ajuda="Alvo do target tracking do ASG.",
            ),
            base.numero(
                "concorrencia_por_task",
                "Requests simultâneos por task",
                minimo=1,
                ajuda="Quantas consultas em voo uma task sustenta dentro do response time.",
            ),
            base.numero(
                "consultas_aurora_por_transacao",
                "Consultas ao Aurora por transação",
                passo=0.1,
                ajuda="Quantas queries cada requisição dispara no banco.",
            ),
        ],
    ),
    base.grupo(
        "Auto Scaling e perfil de carga",
        [
            base.numero("tasks_minimas", "Tasks mínimas (ASG)", minimo=1),
            base.numero("tasks_maximas", "Tasks máximas (ASG)", minimo=1),
            base.numero(
                "horas_pico_por_dia",
                "Horas de pico por dia",
                maximo=24,
                ajuda="No restante da janela vale o percentual de TPM fora do pico.",
            ),
            base.numero(
                "percentual_tpm_fora_pico",
                "TPM fora do pico (% do pico)",
                passo=5,
                maximo=100,
            ),
        ],
    ),
]


# ---------- volumetria ----------

CAMPOS_HERDADOS = (
    "region",
    "tpm_por_if",
    "numero_ifs",
    "fator_pico",
    "response_time_s",
    "horas_por_dia",
    "dias_por_mes",
)


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
    """Regiao usada para buscar preco, considerando a heranca do ALB."""
    volumetria, _, _ = resolver_volumetria(entradas, config)
    return volumetria["region"]


# ---------- dimensionamento ----------

def _tasks_para(tps, entradas):
    """Tasks necessarias para um dado TPS, pelas duas restricoes."""
    vcpus = tps * (entradas["tempo_cpu_por_request_ms"] / 1000.0) / (entradas["utilizacao_alvo_cpu"] / 100.0)
    tasks_cpu = vcpus / entradas["vcpu_por_task"]
    em_voo = tps * entradas["_response_time_s"]
    tasks_concorrencia = em_voo / entradas["concorrencia_por_task"]
    return {
        "tps": tps,
        "vcpus_necessarias": vcpus,
        "requests_em_voo": em_voo,
        "tasks_cpu": tasks_cpu,
        "tasks_concorrencia": tasks_concorrencia,
        "tasks_brutas": max(tasks_cpu, tasks_concorrencia),
        "restricao": "CPU" if tasks_cpu >= tasks_concorrencia else "Concorrência",
    }


def _aplicar_asg(faixa, entradas):
    """Arredonda para cima e respeita o minimo e o maximo do ASG."""
    tasks = max(1, int(math.ceil(faixa["tasks_brutas"])))
    tasks = max(tasks, int(entradas["tasks_minimas"]))
    no_teto = tasks > int(entradas["tasks_maximas"])
    faixa["tasks"] = min(tasks, int(entradas["tasks_maximas"]))
    faixa["no_teto"] = no_teto
    return faixa


def calcular(entradas, recurso_cfg, preco, config=None):
    """Dimensiona as tasks e calcula o custo mensal do Fargate."""
    volumetria, herdado, avisos = resolver_volumetria(entradas, config)

    tpm_total = volumetria["tpm_por_if"] * volumetria["numero_ifs"] * volumetria["fator_pico"]
    tps_pico = tpm_total / 60.0
    horas_por_dia = volumetria["horas_por_dia"]
    dias_por_mes = volumetria["dias_por_mes"]
    horas_mes = horas_por_dia * dias_por_mes

    horas_pico_dia = min(entradas["horas_pico_por_dia"], horas_por_dia)
    horas_fora_dia = max(0.0, horas_por_dia - horas_pico_dia)
    tps_fora = tps_pico * (entradas["percentual_tpm_fora_pico"] / 100.0)

    # response time vem da volumetria (herdada ou propria)
    entradas_calc = dict(entradas, _response_time_s=volumetria["response_time_s"])

    pico = _aplicar_asg(_tasks_para(tps_pico, entradas_calc), entradas)
    fora = _aplicar_asg(_tasks_para(tps_fora, entradas_calc), entradas)

    if pico["no_teto"]:
        avisos.append(
            "O pico exige %s tasks, acima do maximo de %s do ASG: o custo esta limitado ao teto e "
            "o response time alvo nao seria atendido."
            % (fmt_int(math.ceil(pico["tasks_brutas"])), fmt_int(entradas["tasks_maximas"]))
        )

    avisos.extend(fargate_comum.validar_combo(entradas))

    tarifa = fargate_comum.precos_da_task(entradas, preco)
    preco_vcpu = tarifa["preco_vcpu"]
    preco_gb = tarifa["preco_gb"]
    preco_storage = tarifa["preco_storage"]
    storage_cobrado_gb = tarifa["storage_cobrado_gb"]
    custo_task_hora = tarifa["custo_task_hora"]

    task_horas_mes = dias_por_mes * (horas_pico_dia * pico["tasks"] + horas_fora_dia * fora["tasks"])
    vcpu_horas_mes = task_horas_mes * entradas["vcpu_por_task"]
    gb_horas_mes = task_horas_mes * entradas["memoria_gb_por_task"]
    storage_gb_horas_mes = task_horas_mes * storage_cobrado_gb

    custo_vcpu = vcpu_horas_mes * preco_vcpu
    custo_memoria = gb_horas_mes * preco_gb
    custo_storage = storage_gb_horas_mes * preco_storage
    total = custo_vcpu + custo_memoria + custo_storage

    moeda = preco.moeda
    requests_mes = tpm_total * horas_pico_dia * 60.0 * dias_por_mes + (
        tps_fora * 60.0 * horas_fora_dia * 60.0 * dias_por_mes
    )

    return {
        "total_mes": total,
        "moeda": moeda,
        # Numeros que outros recursos consomem (ex.: CloudWatch)
        "expostos": {
            "tasks_pico": pico["tasks"],
            "tasks_fora": fora["tasks"],
            "task_horas_mes": task_horas_mes,
            "unidades_mes": requests_mes,
            "requests_mes": requests_mes,
            "servicos": 1,
            # Carga que estas tasks jogam no Aurora.
            "consultas_aurora_s": tps_pico * entradas.get("consultas_aurora_por_transacao", 1.0),
        },
        "resumo": [
            {"rotulo": "TPM total", "valor": fmt_int(tpm_total)},
            {"rotulo": "Tasks no pico", "valor": fmt_int(pico["tasks"])},
            {"rotulo": "Restrição", "valor": pico["restricao"]},
            {"rotulo": "vCPU-horas/mês", "valor": fmt_num(vcpu_horas_mes, 0)},
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
                    pico["restricao"],
                    " · fora do pico: %s" % fmt_int(fora["tasks"]) if horas_fora_dia > 0 else "",
                ),
            ),
            cartao("TPM total / TPS", fmt_int(tpm_total), fmt_num(tps_pico, 1) + " TPS"),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas,
            volumetria,
            herdado,
            pico,
            fora,
            preco,
            {
                "tpm_total": tpm_total,
                "tps_pico": tps_pico,
                "tps_fora": tps_fora,
                "horas_pico_dia": horas_pico_dia,
                "horas_fora_dia": horas_fora_dia,
                "dias_por_mes": dias_por_mes,
                "horas_mes": horas_mes,
                "requests_mes": requests_mes,
                "task_horas_mes": task_horas_mes,
                "vcpu_horas_mes": vcpu_horas_mes,
                "gb_horas_mes": gb_horas_mes,
                "storage_gb_horas_mes": storage_gb_horas_mes,
                "storage_cobrado_gb": storage_cobrado_gb,
                "custo_task_hora": custo_task_hora,
                "custo_vcpu": custo_vcpu,
                "custo_memoria": custo_memoria,
                "custo_storage": custo_storage,
                "total": total,
                "preco_vcpu": preco_vcpu,
                "preco_gb": preco_gb,
                "preco_storage": preco_storage,
            },
        ),
    }


def _secoes(entradas, volumetria, herdado, pico, fora, preco, v):
    moeda = preco.moeda
    arq_rotulo = dict(ARQUITETURAS)[entradas["arquitetura"]]

    def linhas_restricao(faixa):
        return [
            linha(
                [
                    celula("CPU"),
                    celula(
                        "%s TPS × %s ms / %s%% = %s vCPU"
                        % (
                            fmt_num(faixa["tps"], 1),
                            fmt_num(entradas["tempo_cpu_por_request_ms"], 0),
                            fmt_num(entradas["utilizacao_alvo_cpu"], 0),
                            fmt_num(faixa["vcpus_necessarias"], 1),
                        )
                    ),
                    celula(fmt_num(faixa["tasks_cpu"], 2), num=True),
                ],
                classe="dominante" if faixa["restricao"] == "CPU" else None,
            ),
            linha(
                [
                    celula("Concorrência"),
                    celula(
                        "%s TPS × %s s = %s em voo / %s por task"
                        % (
                            fmt_num(faixa["tps"], 1),
                            fmt_num(volumetria["response_time_s"], 2),
                            fmt_num(faixa["requests_em_voo"], 0),
                            fmt_int(entradas["concorrencia_por_task"]),
                        )
                    ),
                    celula(fmt_num(faixa["tasks_concorrencia"], 2), num=True),
                ],
                classe="dominante" if faixa["restricao"] == "Concorrência" else None,
            ),
            linha(
                [
                    celula("Tasks provisionadas"),
                    celula(
                        "arredondado para cima, ASG entre %s e %s"
                        % (fmt_int(entradas["tasks_minimas"]), fmt_int(entradas["tasks_maximas"]))
                    ),
                    celula(fmt_int(faixa["tasks"]), num=True),
                ],
                classe="total",
            ),
        ]

    secoes = [
        base.tabela(
            "Volumetria aplicada",
            None,
            [
                linha([
                    celula("Origem"),
                    celula("herdada do ALB" if herdado else "própria deste recurso"),
                ]),
                linha([celula("Região"), celula(volumetria["region"])]),
                linha([celula("TPM por IF"), celula(fmt_int(volumetria["tpm_por_if"]), num=True)]),
                linha([celula("Número de IFs"), celula(fmt_int(volumetria["numero_ifs"]), num=True)]),
                linha([celula("Fator de pico"), celula(fmt_num(volumetria["fator_pico"], 2), num=True)]),
                linha([celula("TPM total"), celula(fmt_int(v["tpm_total"]), num=True)]),
                linha([celula("TPS no pico"), celula(fmt_num(v["tps_pico"], 2), num=True)]),
                linha([celula("Response time alvo"), celula(fmt_num(volumetria["response_time_s"], 2) + " s", num=True)]),
                linha([celula("Janela"), celula("%s h/dia × %s dias" % (fmt_num(volumetria["horas_por_dia"], 0), fmt_int(v["dias_por_mes"])))]),
            ],
        ),
        base.tabela(
            "Dimensionamento no pico",
            ["Restrição", "Cálculo", "Tasks"],
            linhas_restricao(pico),
            explica="As tasks atendem a restrição mais exigente. A linha destacada é a que dimensiona o serviço.",
        ),
    ]

    if v["horas_fora_dia"] > 0:
        secoes.append(
            base.tabela(
                "Dimensionamento fora do pico",
                ["Restrição", "Cálculo", "Tasks"],
                linhas_restricao(fora),
                explica="%s h/dia a %s%% do TPM de pico."
                % (fmt_num(v["horas_fora_dia"], 0), fmt_num(entradas["percentual_tpm_fora_pico"], 0)),
            )
        )

    secoes += [
        base.tabela(
            "Task e perfil do ASG",
            None,
            [
                linha([celula("Arquitetura"), celula(arq_rotulo)]),
                linha([
                    celula("Tamanho da task"),
                    celula("%s vCPU · %s GB" % (fmt_num(entradas["vcpu_por_task"], 2), fmt_num(entradas["memoria_gb_por_task"], 1))),
                ]),
                linha([
                    celula("Armazenamento efêmero"),
                    celula(
                        "%s GB (%s GB cobrados)"
                        % (fmt_num(entradas["armazenamento_efemero_gb"], 0), fmt_num(v["storage_cobrado_gb"], 0))
                    ),
                ]),
                linha([celula("Custo por task-hora"), celula(fmt_moeda(v["custo_task_hora"], moeda, 4), num=True)]),
                linha([
                    celula("Tasks no pico / fora do pico"),
                    celula("%s / %s" % (fmt_int(pico["tasks"]), fmt_int(fora["tasks"])), num=True),
                ]),
                linha([
                    celula("Horas de pico / fora do pico"),
                    celula(
                        "%s h / %s h por dia"
                        % (fmt_num(v["horas_pico_dia"], 0), fmt_num(v["horas_fora_dia"], 0))
                    ),
                ]),
                linha([celula("Task-horas no mês"), celula(fmt_num(v["task_horas_mes"], 0), num=True)]),
                linha([celula("Requests no mês"), celula(fmt_int(v["requests_mes"]), num=True)]),
            ],
        ),
        base.tabela(
            "Composição do custo",
            ["Item", "Quantidade", "Preço unitário", "Total mês"],
            [
                linha([
                    celula("vCPU-horas"),
                    celula(fmt_num(v["vcpu_horas_mes"], 0) + " vCPU-h", num=True),
                    celula(fmt_moeda(v["preco_vcpu"], moeda, 4) + "/vCPU-h", num=True),
                    celula(fmt_moeda(v["custo_vcpu"], moeda), num=True),
                ]),
                linha([
                    celula("GB-horas de memória"),
                    celula(fmt_num(v["gb_horas_mes"], 0) + " GB-h", num=True),
                    celula(fmt_moeda(v["preco_gb"], moeda, 4) + "/GB-h", num=True),
                    celula(fmt_moeda(v["custo_memoria"], moeda), num=True),
                ]),
                linha([
                    celula("Armazenamento efêmero"),
                    celula(fmt_num(v["storage_gb_horas_mes"], 0) + " GB-h", num=True),
                    celula(fmt_moeda(v["preco_storage"], moeda, 6) + "/GB-h", num=True),
                    celula(fmt_moeda(v["custo_storage"], moeda), num=True),
                ]),
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
                linha([celula("vCPU-hora (%s)" % arq_rotulo), celula(fmt_moeda(v["preco_vcpu"], moeda, 4), num=True)]),
                linha([celula("GB-hora (%s)" % arq_rotulo), celula(fmt_moeda(v["preco_gb"], moeda, 4), num=True)]),
                linha([celula("Armazenamento efêmero GB-hora"), celula(fmt_moeda(v["preco_storage"], moeda, 6), num=True)]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "O Fargate cobra vCPU e memória por segundo de execução da task, com mínimo de 1 minuto; "
                "aqui o custo é agregado por hora provisionada.",
                "A espera pela resposta do Aurora não consome CPU: é a restrição de concorrência que "
                "sustenta o response time alvo, não o tempo de CPU.",
                "O ASG leva de alguns segundos a poucos minutos para subir tasks; a utilização alvo de CPU "
                "é a folga que absorve a demanda enquanto a escala acontece.",
                "Não estão inclusos: Data Transfer, ECR (armazenamento e pull das imagens), CloudWatch "
                "(métricas, logs e alarmes do ASG) e o custo do Aurora consultado.",
            ],
        ),
    ]
    return secoes
