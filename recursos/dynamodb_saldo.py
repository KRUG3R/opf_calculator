"""DynamoDB - Saldo agregado: tabela lida pelas consultas das IFs e alimentada pelo Kafka.

Dois fluxos batem nesta tabela:

  ECS de consulta  cada transacao da IF faz uma Query no GSI if_id/last_balance_change
                   e le uma pagina de registros;
  ECS de carga     para cada evento com consentimento, le o resource_id pelo GSI
                   account_id (1 item) e grava o registro.

O consumo e calculado por alvo de capacidade, porque tabela e GSIs sao cobrados
separadamente:

  tabela base (+ LSI)   as leituras e escritas do LSI consomem a capacidade da tabela
  GSI account_id        capacidade propria
  GSI if_id / last_balance_change   capacidade propria

Leitura: cada consulta le N itens; o DynamoDB arredonda o total da consulta para
blocos de 4 KB, e a leitura eventualmente consistente custa metade.
Escrita: cada alteracao consome blocos de 1 KB na tabela e replica nos indices que
projetam os atributos alterados; quando a sort key do GSI muda, a replicacao custa
duas escritas (remove a entrada antiga e insere a nova).
"""

import math

from . import base, dynamodb_comum
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha
from .dynamodb_comum import (
    BLOCO_ESCRITA,
    BLOCO_LEITURA,
    CLASSES_TABELA,
    CONSISTENCIAS,
    MODOS_CAPACIDADE,
    PROJECOES,
    SEGUNDOS_POR_DIA,
    extrair_precos,
)

ID = "dynamodb-saldo"
NOME = "DynamoDB — Saldo agregado"
NOME_CURTO = "Dynamo Saldo"
DESCRICAO = "Tabela de saldo agregado lida pelas consultas das IFs."
OFFER_CODE = "AmazonDynamoDB"
ORDEM = 50
STATUS = "ativo"

# Alvos de capacidade: (id, rotulo, prefixo dos campos)
ALVOS = [
    ("tabela", "Tabela base (+ LSI id_eq3)"),
    ("gsi_account", "GSI account_id"),
    ("gsi_if", "GSI if_id / last_balance_change"),
]

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
        "Item e volume de dados",
        [
            base.numero("numero_registros", "Número de registros na tabela"),
            base.texto_longo(
                "exemplo_item",
                "Exemplo de 1 item",
                linhas=10,
                ajuda="O tamanho é a soma de nome + valor dos atributos, sem a sintaxe do JSON.",
                medida="item",
            ),
            base.numero(
                "overhead_item_bytes",
                "Overhead por item (bytes)",
                ajuda="Metadados internos do DynamoDB, ~100 bytes por item.",
            ),
            base.numero(
                "tamanho_chaves_bytes",
                "Tamanho das chaves projetadas (bytes)",
                ajuda="Usado nos índices com projeção KEYS_ONLY.",
            ),
        ],
    ),
    base.grupo(
        "Índices",
        [
            base.escolha("lsi_eq3_projecao", "LSI id_eq3 — projeção", PROJECOES),
            base.escolha("gsi_account_projecao", "GSI account_id — projeção", PROJECOES),
            base.escolha("gsi_if_projecao", "GSI if_id / last_balance_change — projeção", PROJECOES),
            base.booleano(
                "gsi_if_sort_key_muda",
                "A sort key do GSI if_id muda em cada atualização",
                ajuda="last_balance_change é a sort key: cada alteração custa 2 escritas no índice.",
            ),
        ],
    ),
    base.grupo(
        "Carga vinda dos ECS",
        [
            base.booleano(
                "herdar_carga_dos_ecs",
                "Herdar a carga dos recursos ECS",
                ajuda="Leituras do ECS de consulta (TPM das IFs) e leituras e escritas do ECS de carga.",
            ),
            base.escolha("consistencia_leitura", "Consistência da leitura", CONSISTENCIAS),
        ],
    ),
    base.grupo(
        "Leituras do ECS de consulta",
        [
            base.booleano(
                "herdar_itens_da_pagina",
                "Itens por consulta = registros por página do ALB",
            ),
            base.numero(
                "itens_por_consulta",
                "Itens por consulta",
                visivel_quando={"herdar_itens_da_pagina": False},
            ),
            base.numero("consultas_gsi_if_por_transacao", "Consultas no GSI if_id por transação", passo=0.1),
            base.numero("consultas_tabela_por_transacao", "Consultas na tabela base por transação", passo=0.1),
            base.numero("itens_por_consulta_tabela", "Itens por consulta na tabela base", passo=1),
            base.numero("consultas_lsi_por_transacao", "Consultas no LSI id_eq3 por transação", passo=0.1),
        ],
    ),
    base.grupo(
        "Leituras e escritas do ECS de carga",
        [
            base.numero(
                "eventos_com_consentimento_s",
                "Eventos com consentimento por segundo",
                passo=1,
                ajuda="Herdado do ECS de carga quando a herança está ligada.",
                visivel_quando={"herdar_carga_dos_ecs": False},
            ),
            base.numero(
                "consultas_gsi_account_por_evento",
                "Consultas no GSI account_id por evento",
                passo=0.1,
                ajuda="Busca do resource_id a partir do account_id.",
            ),
            base.numero(
                "itens_por_consulta_gsi_account",
                "Itens por consulta no GSI account_id",
                passo=1,
            ),
            base.numero(
                "escritas_por_evento",
                "Escritas na tabela por evento",
                passo=0.1,
                ajuda="1 = cada evento com consentimento grava um registro.",
            ),
            base.numero(
                "atualizacoes_por_registro_por_dia",
                "Atualizações por registro por dia",
                passo=0.1,
                ajuda="Usado só quando a herança dos ECS está desligada.",
                visivel_quando={"herdar_carga_dos_ecs": False},
            ),
            base.numero("novos_registros_por_dia", "Novos registros por dia"),
        ],
    ),
    base.grupo(
        "Capacidade e extras",
        [
            base.escolha("modo_capacidade", "Modo de capacidade", MODOS_CAPACIDADE),
            base.escolha("classe_tabela", "Classe da tabela", CLASSES_TABELA),
            base.numero(
                "utilizacao_alvo_provisionado",
                "Utilização alvo (%)",
                passo=5,
                minimo=1,
                maximo=100,
                ajuda="Alvo do auto scaling da capacidade provisionada.",
                visivel_quando={"modo_capacidade": "provisionado"},
            ),
            base.booleano("pitr_habilitado", "Point-in-time recovery (PITR)"),
            base.booleano("streams_habilitado", "DynamoDB Streams (para o ECS de carga)"),
            base.booleano(
                "aplicar_free_tier",
                "Aplicar o free tier (25 GB / 25 RCU / 25 WCU)",
                ajuda="O free tier é por conta: ligue em um recurso só.",
            ),
        ],
    ),
]


# ---------- volumetria ----------

CAMPOS_HERDADOS = ("region", "tpm_por_if", "numero_ifs", "fator_pico", "horas_por_dia", "dias_por_mes")


def _carga_do_ecs_carga(config):
    """Leituras e escritas por segundo que o ECS de carga joga nesta tabela."""
    if not (config or {}).get("recursos", {}).get("ecs-carga", {}).get("habilitado", False):
        return None
    from . import calcular as calcular_recurso  # lazy: o pacote ainda esta carregando no import

    try:
        resultado = calcular_recurso(config, "ecs-carga")
    except (ValueError, KeyError):
        return None
    expostos = resultado.get("expostos") or {}
    if "eventos_com_consentimento_s" not in expostos:
        return None
    return {
        "nome": resultado.get("nome", "ECS de carga"),
        "eventos_s": expostos.get("eventos_s_pico", 0.0),
        "eventos_com_consentimento_s": expostos["eventos_com_consentimento_s"],
    }


def resolver_volumetria(entradas, config):
    avisos = []
    volumetria = {chave: entradas[chave] for chave in CAMPOS_HERDADOS}
    volumetria["registros_por_pagina"] = entradas["itens_por_consulta"]
    volumetria["eventos_com_consentimento_s"] = entradas["eventos_com_consentimento_s"]
    volumetria["origem_carga"] = None
    herdado = False

    if entradas.get("herdar_carga_dos_ecs"):
        carga = _carga_do_ecs_carga(config)
        if carga:
            volumetria["eventos_com_consentimento_s"] = carga["eventos_com_consentimento_s"]
            volumetria["origem_carga"] = carga
        else:
            avisos.append(
                "Heranca da carga ligada, mas o ECS de carga nao esta disponivel; usando os eventos por segundo deste recurso."
            )

    entradas_alb = ((config or {}).get("recursos", {}).get("alb") or {}).get("entradas")
    if entradas.get("herdar_volumetria_do_alb"):
        if entradas_alb:
            for chave in CAMPOS_HERDADOS:
                if chave in entradas_alb:
                    volumetria[chave] = entradas_alb[chave]
            herdado = True
        else:
            avisos.append(
                "Heranca de volumetria ligada, mas o ALB nao esta configurado; usando os valores proprios deste recurso."
            )
    if entradas.get("herdar_itens_da_pagina"):
        if entradas_alb and "registros_por_pagina" in entradas_alb:
            volumetria["registros_por_pagina"] = entradas_alb["registros_por_pagina"]
        else:
            avisos.append("Sem registros_por_pagina do ALB; usando os itens por consulta deste recurso.")
    return volumetria, herdado, avisos


def region_efetiva(entradas, config):
    volumetria, _, _ = resolver_volumetria(entradas, config)
    return volumetria["region"]


# ---------- calculo ----------

def _tamanho_projetado(projecao, tamanho_item, tamanho_chaves):
    return tamanho_item if projecao == "all" else tamanho_chaves


def calcular(entradas, recurso_cfg, preco, config=None):
    volumetria, herdado, avisos = resolver_volumetria(entradas, config)

    # ---- tamanho do item ----
    medida_item = base.medir_json(entradas.get("exemplo_item"), True)
    tamanho_atributos = base.tamanho_atributos_dynamodb(entradas.get("exemplo_item"))
    if tamanho_atributos is None:
        tamanho_atributos = medida_item["bytes"]
        if medida_item["bytes"]:
            avisos.append("O exemplo de item nao e um objeto JSON valido; medido como texto bruto.")
    # O overhead de ~100 B por item e de armazenamento (indexacao); a capacidade de
    # leitura e escrita e cobrada sobre o tamanho do item: nomes + valores.
    tamanho_item = tamanho_atributos
    tamanho_chaves = entradas["tamanho_chaves_bytes"]
    overhead = entradas["overhead_item_bytes"]

    projetado = {
        "tabela": tamanho_item,
        "lsi": _tamanho_projetado(entradas["lsi_eq3_projecao"], tamanho_item, tamanho_chaves),
        "gsi_account": _tamanho_projetado(entradas["gsi_account_projecao"], tamanho_item, tamanho_chaves),
        "gsi_if": _tamanho_projetado(entradas["gsi_if_projecao"], tamanho_item, tamanho_chaves),
    }
    # No armazenamento cada item carrega o overhead, na tabela e em cada indice.
    projetado_storage = {nome: bytes_ + overhead for nome, bytes_ in projetado.items()}

    # ---- volumetria de leitura ----
    tpm_total = volumetria["tpm_por_if"] * volumetria["numero_ifs"] * volumetria["fator_pico"]
    tps = tpm_total / 60.0
    horas_mes = volumetria["horas_por_dia"] * volumetria["dias_por_mes"]
    itens_por_consulta = volumetria["registros_por_pagina"]

    fator_consistencia = 0.5 if entradas["consistencia_leitura"] == "eventual" else 1.0

    def unidades_leitura(itens, bytes_por_item):
        """Unidades de uma consulta: total arredondado para blocos de 4 KB."""
        if itens <= 0:
            return 0.0
        blocos = math.ceil((itens * bytes_por_item) / BLOCO_LEITURA)
        return blocos * fator_consistencia

    # Cada destino tem a sua origem de carga: as consultas das IFs vem do ECS de
    # consulta (taxa = TPS), e a busca do resource_id vem do ECS de carga
    # (taxa = eventos com consentimento por segundo).
    eventos_s = volumetria["eventos_com_consentimento_s"]
    consultas = {
        "tabela": ("consulta", tps, entradas["consultas_tabela_por_transacao"], entradas["itens_por_consulta_tabela"], projetado["tabela"]),
        "lsi": ("consulta", tps, entradas["consultas_lsi_por_transacao"], itens_por_consulta, projetado["lsi"]),
        "gsi_account": ("carga", eventos_s, entradas["consultas_gsi_account_por_evento"], entradas["itens_por_consulta_gsi_account"], projetado["gsi_account"]),
        "gsi_if": ("consulta", tps, entradas["consultas_gsi_if_por_transacao"], itens_por_consulta, projetado["gsi_if"]),
    }

    leitura = {}
    for nome, (origem, taxa, qtd_consultas, itens, bytes_item) in consultas.items():
        unidades = unidades_leitura(itens, bytes_item)
        leitura[nome] = {
            "origem": origem,
            "taxa": taxa,
            "consultas_por_transacao": qtd_consultas,
            "itens_por_consulta": itens,
            "bytes_por_item": bytes_item,
            "unidades_por_consulta": unidades,
            "unidades_por_segundo": taxa * qtd_consultas * unidades,
        }

    # O LSI consome a capacidade da tabela base.
    rru_s = {
        "tabela": leitura["tabela"]["unidades_por_segundo"] + leitura["lsi"]["unidades_por_segundo"],
        "gsi_account": leitura["gsi_account"]["unidades_por_segundo"],
        "gsi_if": leitura["gsi_if"]["unidades_por_segundo"],
    }

    # ---- volumetria de escrita ----
    if entradas.get("herdar_carga_dos_ecs") and volumetria["origem_carga"]:
        # Cada evento com consentimento grava o registro de saldo.
        atualizacoes_s = eventos_s * entradas["escritas_por_evento"]
        origem_escrita = "ECS de carga"
    else:
        atualizacoes_s = (
            entradas["numero_registros"] * entradas["atualizacoes_por_registro_por_dia"] / SEGUNDOS_POR_DIA
        )
        origem_escrita = "numero_registros × atualizações/dia"
    novos_s = entradas["novos_registros_por_dia"] / SEGUNDOS_POR_DIA

    def unidades_escrita(bytes_item):
        return float(math.ceil(bytes_item / BLOCO_ESCRITA)) if bytes_item > 0 else 0.0

    # Quais indices sao tocados por uma atualizacao de saldo:
    # - LSI e GSI account_id so quando projetam o atributo alterado (projecao ALL);
    #   com KEYS_ONLY as chaves nao mudam, entao nao ha replicacao.
    # - GSI if_id sempre, porque last_balance_change e a sort key.
    lsi_no_update = entradas["lsi_eq3_projecao"] == "all"
    gsi_account_no_update = entradas["gsi_account_projecao"] == "all"
    fator_gsi_if = 2.0 if entradas["gsi_if_sort_key_muda"] else 1.0

    escrita = {
        "tabela": {
            "unidades_por_item": unidades_escrita(projetado["tabela"]),
            "atualizacoes_s": atualizacoes_s,
            "novos_s": novos_s,
            "fator": 1.0,
        },
        "lsi": {
            "unidades_por_item": unidades_escrita(projetado["lsi"]),
            "atualizacoes_s": atualizacoes_s if lsi_no_update else 0.0,
            "novos_s": novos_s,
            "fator": 1.0,
        },
        "gsi_account": {
            "unidades_por_item": unidades_escrita(projetado["gsi_account"]),
            "atualizacoes_s": atualizacoes_s if gsi_account_no_update else 0.0,
            "novos_s": novos_s,
            "fator": 1.0,
        },
        "gsi_if": {
            "unidades_por_item": unidades_escrita(projetado["gsi_if"]),
            "atualizacoes_s": atualizacoes_s,
            "novos_s": novos_s,
            "fator": fator_gsi_if,
        },
    }
    for dados in escrita.values():
        dados["unidades_por_segundo"] = (
            (dados["atualizacoes_s"] * dados["fator"] + dados["novos_s"]) * dados["unidades_por_item"]
        )

    wru_s = {
        "tabela": escrita["tabela"]["unidades_por_segundo"] + escrita["lsi"]["unidades_por_segundo"],
        "gsi_account": escrita["gsi_account"]["unidades_por_segundo"],
        "gsi_if": escrita["gsi_if"]["unidades_por_segundo"],
    }

    # ---- armazenamento ----
    registros = entradas["numero_registros"]
    storage_bytes = {nome: registros * bytes_ for nome, bytes_ in projetado_storage.items()}
    storage_gb_total = sum(storage_bytes.values()) / base.GB

    # ---- precos ----
    faixas = dynamodb_comum.seletor_de_faixas(preco, entradas)

    segundos_mes = horas_mes * 3600.0
    custos = []
    total = 0.0

    if entradas["modo_capacidade"] == "on_demand":
        rru_mes = sum(rru_s.values()) * segundos_mes
        wru_mes = sum(wru_s.values()) * segundos_mes
        custo_rru, _ = base.custo_escalonado(faixas("rru"), rru_mes)
        custo_wru, _ = base.custo_escalonado(faixas("wru"), wru_mes)
        custos.append(("Read request units", fmt_int(rru_mes) + " RRU", custo_rru))
        custos.append(("Write request units", fmt_int(wru_mes) + " WRU", custo_wru))
        total += custo_rru + custo_wru
        capacidade = {"rru_mes": rru_mes, "wru_mes": wru_mes}
    else:
        alvo = entradas["utilizacao_alvo_provisionado"] / 100.0
        rcu = {k: math.ceil(v / alvo) for k, v in rru_s.items()}
        wcu = {k: math.ceil(v / alvo) for k, v in wru_s.items()}
        rcu_horas = sum(rcu.values()) * horas_mes
        wcu_horas = sum(wcu.values()) * horas_mes
        custo_rcu, _ = base.custo_escalonado(faixas("rcu_hora"), rcu_horas)
        custo_wcu, _ = base.custo_escalonado(faixas("wcu_hora"), wcu_horas)
        custos.append(("RCU provisionadas", fmt_int(rcu_horas) + " RCU-h", custo_rcu))
        custos.append(("WCU provisionadas", fmt_int(wcu_horas) + " WCU-h", custo_wcu))
        total += custo_rcu + custo_wcu
        capacidade = {"rcu": rcu, "wcu": wcu, "rcu_horas": rcu_horas, "wcu_horas": wcu_horas}

    custo_storage, _ = base.custo_escalonado(faixas("storage_gb_mes"), storage_gb_total)
    custos.append(("Armazenamento", fmt_num(storage_gb_total, 2) + " GB-mês", custo_storage))
    total += custo_storage

    custo_pitr = 0.0
    if entradas["pitr_habilitado"]:
        faixas_pitr = preco.valores.get("pitr_gb_mes") or []
        custo_pitr, _ = base.custo_escalonado(faixas_pitr, storage_gb_total)  # PITR nao tem variante IA
        if not faixas_pitr:
            avisos.append("Sem preco de PITR nesta regiao; o backup continuo nao foi somado.")
        custos.append(("PITR (backup contínuo)", fmt_num(storage_gb_total, 2) + " GB-mês", custo_pitr))
        total += custo_pitr

    custo_streams = 0.0
    registros_stream_mes = 0.0
    if entradas["streams_habilitado"]:
        registros_stream_mes = (atualizacoes_s + novos_s) * segundos_mes
        faixas_streams = preco.valores.get("streams_requests") or []
        if not entradas["aplicar_free_tier"]:
            faixas_streams = base.faixas_sem_gratuito(faixas_streams)
        custo_streams, _ = base.custo_escalonado(faixas_streams, registros_stream_mes)
        custos.append(("Streams (read requests)", fmt_int(registros_stream_mes) + " req", custo_streams))
        total += custo_streams

    moeda = preco.moeda
    unidade_leitura_rotulo = "RRU/s" if entradas["modo_capacidade"] == "on_demand" else "RCU"
    requests_mes = tpm_total * horas_mes * 60.0

    return {
        "total_mes": total,
        "moeda": moeda,
        "medidas": {"item": dict(medida_item, bytes=tamanho_atributos)},
        "resumo": [
            {"rotulo": "Registros", "valor": fmt_int(registros)},
            {"rotulo": "Leitura (consulta)", "valor": fmt_num(rru_s["gsi_if"] + rru_s["tabela"], 0) + " un/s"},
            {"rotulo": "Leitura (carga)", "valor": fmt_num(rru_s["gsi_account"], 0) + " un/s"},
            {"rotulo": "Escrita", "valor": fmt_num(sum(wru_s.values()), 0) + " un/s"},
        ],
        "cartoes": [
            cartao(
                "Custo mensal do DynamoDB",
                fmt_moeda(total, moeda),
                "%s/h · %s/ano"
                % (fmt_moeda(total / horas_mes if horas_mes else 0, moeda, 4), fmt_moeda(total * 12, moeda)),
                destaque=True,
            ),
            cartao(
                "Unidades de leitura",
                fmt_num(sum(rru_s.values()), 0),
                "%s · %s" % (unidade_leitura_rotulo, dict(CONSISTENCIAS)[entradas["consistencia_leitura"]]),
            ),
            cartao(
                "Unidades de escrita",
                fmt_num(sum(wru_s.values()), 0),
                "por segundo · %s registros" % fmt_int(registros),
            ),
        ],
        "avisos": avisos,
        "secoes": _secoes(
            entradas,
            volumetria,
            herdado,
            preco,
            {
                "tpm_total": tpm_total,
                "tps": tps,
                "horas_mes": horas_mes,
                "requests_mes": requests_mes,
                "itens_por_consulta": itens_por_consulta,
                "tamanho_atributos": tamanho_atributos,
                "tamanho_item": tamanho_item,
                "tamanho_chaves": tamanho_chaves,
                "projetado": projetado,
                "projetado_storage": projetado_storage,
                "leitura": leitura,
                "eventos_s": eventos_s,
                "origem_escrita": origem_escrita,
                "rru_s": rru_s,
                "escrita": escrita,
                "wru_s": wru_s,
                "atualizacoes_s": atualizacoes_s,
                "novos_s": novos_s,
                "storage_bytes": storage_bytes,
                "storage_gb_total": storage_gb_total,
                "capacidade": capacidade,
                "custos": custos,
                "total": total,
                "registros_stream_mes": registros_stream_mes,
            },
        ),
    }


def _secoes(entradas, volumetria, herdado, preco, v):
    moeda = preco.moeda
    on_demand = entradas["modo_capacidade"] == "on_demand"
    rotulos_indice = {
        "tabela": "Tabela base",
        "lsi": "LSI id_eq3",
        "gsi_account": "GSI account_id",
        "gsi_if": "GSI if_id / last_balance_change",
    }
    projecoes = {
        "tabela": "item completo",
        "lsi": dict(PROJECOES)[entradas["lsi_eq3_projecao"]],
        "gsi_account": dict(PROJECOES)[entradas["gsi_account_projecao"]],
        "gsi_if": dict(PROJECOES)[entradas["gsi_if_projecao"]],
    }

    secoes = [
        base.tabela(
            "Volumetria aplicada",
            None,
            [
                linha([celula("Origem"), celula("herdada do ALB" if herdado else "própria deste recurso")]),
                linha([celula("Região"), celula(volumetria["region"])]),
                linha([celula("TPM total"), celula(fmt_int(v["tpm_total"]), num=True)]),
                linha([celula("TPS"), celula(fmt_num(v["tps"], 2), num=True)]),
                linha([celula("Itens por consulta"), celula(fmt_int(v["itens_por_consulta"]), num=True)]),
                linha([
                    celula("Carga do ECS de carga"),
                    celula(
                        "%s · %s eventos com consentimento/s"
                        % (volumetria["origem_carga"]["nome"], fmt_num(v["eventos_s"], 0))
                        if volumetria["origem_carga"]
                        else "%s eventos/s (valor próprio)" % fmt_num(v["eventos_s"], 0)
                    ),
                ]),
                linha([celula("Registros na tabela"), celula(fmt_int(entradas["numero_registros"]), num=True)]),
                linha([
                    celula("Janela"),
                    celula("%s h/dia × %s dias" % (fmt_num(volumetria["horas_por_dia"], 0), fmt_int(volumetria["dias_por_mes"]))),
                ]),
            ],
        ),
        base.tabela(
            "Tamanho do item e dos índices",
            ["Destino", "Projeção", "Bytes (capacidade)", "Armazenamento"],
            [
                linha([
                    celula(rotulos_indice[nome]),
                    celula(projecoes[nome]),
                    celula(fmt_num(v["projetado"][nome], 0) + " B", num=True),
                    celula(fmt_num(v["storage_bytes"][nome] / base.GB, 2) + " GB", num=True),
                ])
                for nome in ("tabela", "lsi", "gsi_account", "gsi_if")
            ]
            + [
                linha(
                    [
                        celula("Total"),
                        celula(""),
                        celula(""),
                        celula(fmt_num(v["storage_gb_total"], 2) + " GB", num=True),
                    ],
                    classe="total",
                )
            ],
            explica="A capacidade é cobrada sobre o tamanho do item (%s B de atributos); o armazenamento "
            "soma %s B de overhead por item, na tabela e em cada índice. KEYS_ONLY guarda só as chaves (%s B)."
            % (
                fmt_num(v["tamanho_atributos"], 0),
                fmt_num(entradas["overhead_item_bytes"], 0),
                fmt_num(v["tamanho_chaves"], 0),
            ),
        ),
        base.tabela(
            "Consumo de leitura",
            ["Destino", "Origem", "Taxa × consultas", "Itens × bytes", "Unidades/consulta", "Unidades/s"],
            [
                linha([
                    celula(rotulos_indice[nome]),
                    celula("ECS consulta" if d["origem"] == "consulta" else "ECS carga"),
                    celula(
                        "%s/s × %s"
                        % (fmt_num(d["taxa"], 0), fmt_num(d["consultas_por_transacao"], 2))
                    ),
                    celula("%s × %s B" % (fmt_int(d["itens_por_consulta"]), fmt_num(d["bytes_por_item"], 0))),
                    celula(fmt_num(d["unidades_por_consulta"], 1), num=True),
                    celula(fmt_num(d["unidades_por_segundo"], 1), num=True),
                ])
                for nome, d in (
                    ("tabela", v["leitura"]["tabela"]),
                    ("lsi", v["leitura"]["lsi"]),
                    ("gsi_account", v["leitura"]["gsi_account"]),
                    ("gsi_if", v["leitura"]["gsi_if"]),
                )
            ]
            + [
                linha(
                    [
                        celula("Total"),
                        celula(""),
                        celula(""),
                        celula(""),
                        celula(""),
                        celula(fmt_num(sum(v["rru_s"].values()), 1), num=True),
                    ],
                    classe="total",
                )
            ],
            explica="As consultas das IFs vêm do ECS de consulta (%s TPS); a busca do resource_id vem do ECS "
            "de carga (%s eventos com consentimento/s). O total de cada consulta é arredondado para blocos de "
            "4 KB e a leitura eventualmente consistente custa metade."
            % (fmt_num(v["tps"], 0), fmt_num(v["eventos_s"], 0)),
        ),
        base.tabela(
            "Consumo de escrita",
            ["Destino", "Alterações/s", "Unidades por item", "Fator", "Unidades/s"],
            [
                linha([
                    celula(rotulos_indice[nome]),
                    celula(fmt_num(d["atualizacoes_s"] + d["novos_s"], 1), num=True),
                    celula(fmt_num(d["unidades_por_item"], 0), num=True),
                    celula("2× (sort key muda)" if d["fator"] > 1 else "1×"),
                    celula(fmt_num(d["unidades_por_segundo"], 1), num=True),
                ])
                for nome, d in (
                    ("tabela", v["escrita"]["tabela"]),
                    ("lsi", v["escrita"]["lsi"]),
                    ("gsi_account", v["escrita"]["gsi_account"]),
                    ("gsi_if", v["escrita"]["gsi_if"]),
                )
            ]
            + [
                linha(
                    [
                        celula("Total"),
                        celula(""),
                        celula(""),
                        celula(""),
                        celula(fmt_num(sum(v["wru_s"].values()), 1), num=True),
                    ],
                    classe="total",
                )
            ],
            explica="%s escritas/s vindas de %s e %s novos registros/s. Índices KEYS_ONLY cujas chaves "
            "não mudam não são tocados pela atualização."
            % (fmt_num(v["atualizacoes_s"], 1), v["origem_escrita"], fmt_num(v["novos_s"], 2)),
        ),
    ]

    if not on_demand:
        cap = v["capacidade"]
        secoes.append(
            base.tabela(
                "Capacidade provisionada",
                ["Alvo", "RCU", "WCU"],
                [
                    linha([
                        celula(rotulos_indice[nome] if nome != "tabela" else "Tabela base (+ LSI)"),
                        celula(fmt_int(cap["rcu"][nome]), num=True),
                        celula(fmt_int(cap["wcu"][nome]), num=True),
                    ])
                    for nome in ("tabela", "gsi_account", "gsi_if")
                ],
                explica="Unidades por segundo divididas pela utilização alvo de %s%% e arredondadas para cima."
                % fmt_num(entradas["utilizacao_alvo_provisionado"], 0),
            )
        )

    linhas_custo = [
        linha([celula(nome), celula(quantidade, num=True), celula(fmt_moeda(valor, moeda), num=True)])
        for nome, quantidade, valor in v["custos"]
    ]
    linhas_custo += [
        linha([celula("Total mensal"), celula(""), celula(fmt_moeda(v["total"], moeda), num=True)], classe="total"),
        linha([
            celula("Custo por IF / mês"),
            celula(""),
            celula(fmt_moeda(v["total"] / volumetria["numero_ifs"], moeda), num=True),
        ]),
        linha([
            celula("Custo por milhão de transações"),
            celula(""),
            celula(fmt_moeda(v["total"] / v["requests_mes"] * 1e6 if v["requests_mes"] else 0, moeda, 4), num=True),
        ]),
    ]
    secoes.append(base.tabela("Composição do custo", ["Item", "Quantidade", "Total mês"], linhas_custo))

    ia = entradas["classe_tabela"] == "standard_ia"
    prefixo = "ia_" if ia else ""

    def primeiro_preco(chave, por_milhao=False):
        faixas = preco.valores.get(prefixo + chave) or preco.valores.get(chave) or []
        pagas = base.faixas_sem_gratuito(faixas)
        if not pagas:
            return "—"
        valor = pagas[0]["preco"] * (1e6 if por_milhao else 1)
        return fmt_moeda(valor, moeda, 4) + ("/milhão" if por_milhao else "")

    secoes += [
        base.tabela(
            "Preço unitário e fonte",
            None,
            [
                linha([celula("Região"), celula(preco.region)]),
                linha([celula("Classe da tabela"), celula(dict(CLASSES_TABELA)[entradas["classe_tabela"]])]),
                linha([celula("Modo"), celula(dict(MODOS_CAPACIDADE)[entradas["modo_capacidade"]])]),
                linha([celula("Leitura"), celula(primeiro_preco("rru" if on_demand else "rcu_hora", on_demand), num=True)]),
                linha([celula("Escrita"), celula(primeiro_preco("wru" if on_demand else "wcu_hora", on_demand), num=True)]),
                linha([celula("Armazenamento"), celula(primeiro_preco("storage_gb_mes") + "/GB-mês", num=True)]),
                linha([celula("PITR"), celula(primeiro_preco("pitr_gb_mes") + "/GB-mês", num=True)]),
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "O LSI compartilha a capacidade da tabela base; os dois GSIs têm capacidade própria.",
                "last_balance_change é a sort key do GSI if_id: cada atualização remove a entrada antiga e "
                "insere a nova, custando 2 escritas nesse índice.",
                "Índices com projeção KEYS_ONLY só são tocados quando uma chave muda — por isso o GSI "
                "account_id não consome escrita nas atualizações de saldo.",
                "O free tier (25 GB, 25 RCU e 25 WCU) é por conta, não por tabela; mantenha-o ligado em um "
                "único recurso da calculadora.",
                "Não estão inclusos: transações (TransactWrite custa 2×), DAX, backups on-demand, "
                "exports e Data Transfer entre regiões.",
            ],
        ),
    ]
    return secoes
