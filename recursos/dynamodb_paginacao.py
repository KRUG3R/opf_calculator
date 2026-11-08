"""DynamoDB - Controle de paginacao: tabela que guarda o cursor de cada consulta paginada.

Modelo do fluxo: cada sessao de paginacao cria um item (o token) na primeira
pagina e, nas paginas seguintes, le o token para retomar de onde parou e atualiza
o cursor. Os itens sao efemeros e saem por TTL, cuja exclusao nao e cobrada.

  sessoes/s        = TPS / paginas_por_sessao
  leituras/s       = TPS - sessoes/s        (a primeira pagina nao le token)
  escritas/s       = sessoes/s (criacao) + leituras/s (atualizacao do cursor)
  itens vivos      = sessoes/s x TTL
"""

from . import base, dynamodb_comum
from .base import cartao, celula, fmt_int, fmt_moeda, fmt_num, linha
from .dynamodb_comum import (
    BLOCO_ESCRITA,
    BLOCO_LEITURA,
    CLASSES_TABELA,
    CONSISTENCIAS,
    MODOS_CAPACIDADE,
    extrair_precos,
    unidades_por_bloco,
)

ID = "dynamodb-paginacao"
NOME = "DynamoDB — Controle de paginação"
NOME_CURTO = "Dynamo Paginação"
DESCRICAO = "Tabela que controla a paginação das consultas."
OFFER_CODE = dynamodb_comum.OFFER_CODE
ORDEM = 60
STATUS = "ativo"

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
        "Fluxo de paginação",
        [
            base.numero(
                "paginas_por_sessao",
                "Páginas por sessão de paginação",
                passo=0.5,
                minimo=1,
                ajuda="Quantas páginas a IF percorre em média antes de abandonar o cursor.",
            ),
            base.booleano(
                "atualiza_cursor_por_pagina",
                "Atualiza o cursor a cada página servida",
                ajuda="Desligue se o token for imutável e a posição vier na própria requisição.",
            ),
            base.numero(
                "ttl_minutos",
                "TTL do token (minutos)",
                minimo=1,
                ajuda="Define quantos itens ficam vivos ao mesmo tempo. A exclusão por TTL é gratuita.",
            ),
            base.escolha(
                "consistencia_leitura",
                "Consistência da leitura do token",
                CONSISTENCIAS,
                ajuda="A paginação lê o que acabou de escrever: a leitura forte evita cursor desatualizado.",
            ),
        ],
    ),
    base.grupo(
        "Item",
        [
            base.texto_longo(
                "exemplo_item",
                "Exemplo de 1 token",
                linhas=12,
                ajuda="O tamanho é a soma de nome + valor dos atributos.",
                medida="item",
            ),
            base.numero("overhead_item_bytes", "Overhead por item (bytes)"),
            base.booleano(
                "guardar_pagina_no_item",
                "Guardar os ids da página dentro do token",
                ajuda="Em vez de só o cursor. Multiplica o tamanho do item pelo tamanho da página.",
            ),
            base.numero(
                "bytes_por_id",
                "Bytes por id guardado",
                passo=1,
                visivel_quando={"guardar_pagina_no_item": True},
            ),
            base.booleano(
                "herdar_itens_da_pagina",
                "Ids por token = registros por página do ALB",
                visivel_quando={"guardar_pagina_no_item": True},
            ),
            base.numero(
                "ids_por_token",
                "Ids por token",
                visivel_quando={"herdar_itens_da_pagina": False},
            ),
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
                visivel_quando={"modo_capacidade": "provisionado"},
            ),
            base.booleano(
                "pitr_habilitado",
                "Point-in-time recovery (PITR)",
                ajuda="Raramente faz sentido em tabela efêmera com TTL curto.",
            ),
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


def resolver_volumetria(entradas, config):
    avisos = []
    volumetria = {chave: entradas[chave] for chave in CAMPOS_HERDADOS}
    volumetria["ids_por_token"] = entradas["ids_por_token"]
    herdado = False

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
    if entradas.get("herdar_itens_da_pagina") and entradas.get("guardar_pagina_no_item"):
        if entradas_alb and "registros_por_pagina" in entradas_alb:
            volumetria["ids_por_token"] = entradas_alb["registros_por_pagina"]
        else:
            avisos.append("Sem registros_por_pagina do ALB; usando os ids por token deste recurso.")
    return volumetria, herdado, avisos


def region_efetiva(entradas, config):
    volumetria, _, _ = resolver_volumetria(entradas, config)
    return volumetria["region"]


# ---------- calculo ----------

def calcular(entradas, recurso_cfg, preco, config=None):
    volumetria, herdado, avisos = resolver_volumetria(entradas, config)

    # ---- tamanho do token ----
    medida_item = base.medir_json(entradas.get("exemplo_item"), True)
    tamanho_atributos = base.tamanho_atributos_dynamodb(entradas.get("exemplo_item"))
    if tamanho_atributos is None:
        tamanho_atributos = medida_item["bytes"]
        if medida_item["bytes"]:
            avisos.append("O exemplo de token nao e um objeto JSON valido; medido como texto bruto.")

    bytes_da_pagina = 0.0
    if entradas["guardar_pagina_no_item"]:
        bytes_da_pagina = volumetria["ids_por_token"] * entradas["bytes_por_id"]
    # Capacidade usa o tamanho do item; o overhead de ~100 B entra no armazenamento.
    tamanho_item = tamanho_atributos + bytes_da_pagina
    tamanho_item_storage = tamanho_item + entradas["overhead_item_bytes"]

    # ---- fluxo ----
    tpm_total = volumetria["tpm_por_if"] * volumetria["numero_ifs"] * volumetria["fator_pico"]
    tps = tpm_total / 60.0
    horas_mes = volumetria["horas_por_dia"] * volumetria["dias_por_mes"]
    segundos_mes = horas_mes * 3600.0

    paginas_por_sessao = entradas["paginas_por_sessao"]
    sessoes_s = tps / paginas_por_sessao
    leituras_s = max(0.0, tps - sessoes_s)  # a primeira pagina de cada sessao nao le token

    fator_consistencia = 0.5 if entradas["consistencia_leitura"] == "eventual" else 1.0
    unidades_leitura_item = unidades_por_bloco(tamanho_item, BLOCO_LEITURA) * fator_consistencia
    unidades_escrita_item = unidades_por_bloco(tamanho_item, BLOCO_ESCRITA)

    rru_s = leituras_s * unidades_leitura_item
    escritas_criacao_s = sessoes_s
    escritas_cursor_s = leituras_s if entradas["atualiza_cursor_por_pagina"] else 0.0
    wru_s = (escritas_criacao_s + escritas_cursor_s) * unidades_escrita_item

    # ---- armazenamento ----
    itens_vivos = sessoes_s * entradas["ttl_minutos"] * 60.0
    storage_gb = itens_vivos * tamanho_item_storage / base.GB

    # ---- precos ----
    faixas = dynamodb_comum.seletor_de_faixas(preco, entradas)
    custos = []
    total = 0.0

    if entradas["modo_capacidade"] == "on_demand":
        rru_mes = rru_s * segundos_mes
        wru_mes = wru_s * segundos_mes
        custo_rru, _ = base.custo_escalonado(faixas("rru"), rru_mes)
        custo_wru, _ = base.custo_escalonado(faixas("wru"), wru_mes)
        custos.append(("Read request units", fmt_int(rru_mes) + " RRU", custo_rru))
        custos.append(("Write request units", fmt_int(wru_mes) + " WRU", custo_wru))
        total += custo_rru + custo_wru
        capacidade = {"rru_mes": rru_mes, "wru_mes": wru_mes}
    else:
        import math

        alvo = entradas["utilizacao_alvo_provisionado"] / 100.0
        rcu = math.ceil(rru_s / alvo)
        wcu = math.ceil(wru_s / alvo)
        custo_rcu, _ = base.custo_escalonado(faixas("rcu_hora"), rcu * horas_mes)
        custo_wcu, _ = base.custo_escalonado(faixas("wcu_hora"), wcu * horas_mes)
        custos.append(("RCU provisionadas", fmt_int(rcu * horas_mes) + " RCU-h", custo_rcu))
        custos.append(("WCU provisionadas", fmt_int(wcu * horas_mes) + " WCU-h", custo_wcu))
        total += custo_rcu + custo_wcu
        capacidade = {"rcu": rcu, "wcu": wcu}

    custo_storage, _ = base.custo_escalonado(faixas("storage_gb_mes"), storage_gb)
    custos.append(("Armazenamento", fmt_num(storage_gb, 3) + " GB-mês", custo_storage))
    total += custo_storage

    if entradas["pitr_habilitado"]:
        custo_pitr, _ = base.custo_escalonado(preco.valores.get("pitr_gb_mes") or [], storage_gb)
        custos.append(("PITR (backup contínuo)", fmt_num(storage_gb, 3) + " GB-mês", custo_pitr))
        total += custo_pitr

    moeda = preco.moeda
    requests_mes = tpm_total * horas_mes * 60.0

    return {
        "total_mes": total,
        "moeda": moeda,
        "medidas": {"item": dict(medida_item, bytes=tamanho_atributos)},
        "resumo": [
            {"rotulo": "Sessões/s", "valor": fmt_num(sessoes_s, 1)},
            {"rotulo": "Tamanho do token", "valor": fmt_num(tamanho_item, 0) + " B"},
            {"rotulo": "Itens vivos", "valor": fmt_int(itens_vivos)},
            {"rotulo": "Escrita", "valor": fmt_num(wru_s, 0) + " un/s"},
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
                "Unidades de escrita",
                fmt_num(wru_s, 0),
                "por segundo · %s sessões/s" % fmt_num(sessoes_s, 0),
            ),
            cartao(
                "Unidades de leitura",
                fmt_num(rru_s, 0),
                "por segundo · " + dict(CONSISTENCIAS)[entradas["consistencia_leitura"]],
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
                "sessoes_s": sessoes_s,
                "leituras_s": leituras_s,
                "escritas_criacao_s": escritas_criacao_s,
                "escritas_cursor_s": escritas_cursor_s,
                "tamanho_atributos": tamanho_atributos,
                "bytes_da_pagina": bytes_da_pagina,
                "tamanho_item": tamanho_item,
                "unidades_leitura_item": unidades_leitura_item,
                "unidades_escrita_item": unidades_escrita_item,
                "rru_s": rru_s,
                "wru_s": wru_s,
                "itens_vivos": itens_vivos,
                "storage_gb": storage_gb,
                "capacidade": capacidade,
                "custos": custos,
                "total": total,
            },
        ),
    }


def _secoes(entradas, volumetria, herdado, preco, v):
    moeda = preco.moeda
    on_demand = entradas["modo_capacidade"] == "on_demand"

    linhas_item = [
        linha([celula("Atributos do token"), celula(fmt_num(v["tamanho_atributos"], 0) + " B", num=True)]),
    ]
    if entradas["guardar_pagina_no_item"]:
        linhas_item.append(
            linha([
                celula("Ids da página no item"),
                celula(
                    "%s × %s B = %s B"
                    % (
                        fmt_int(volumetria["ids_por_token"]),
                        fmt_num(entradas["bytes_por_id"], 0),
                        fmt_num(v["bytes_da_pagina"], 0),
                    ),
                    num=True,
                ),
            ])
        )
    linhas_item += [
        linha(
            [celula("Tamanho do item (capacidade)"), celula(fmt_num(v["tamanho_item"], 0) + " B", num=True)],
            classe="total",
        ),
        linha([
            celula("Com overhead (armazenamento)"),
            celula(fmt_num(v["tamanho_item"] + entradas["overhead_item_bytes"], 0) + " B", num=True),
        ]),
        linha([
            celula("Unidades por leitura"),
            celula(fmt_num(v["unidades_leitura_item"], 1), num=True),
        ]),
        linha([
            celula("Unidades por escrita"),
            celula(fmt_num(v["unidades_escrita_item"], 1), num=True),
        ]),
    ]

    secoes = [
        base.tabela(
            "Volumetria aplicada",
            None,
            [
                linha([celula("Origem"), celula("herdada do ALB" if herdado else "própria deste recurso")]),
                linha([celula("Região"), celula(volumetria["region"])]),
                linha([celula("TPM total"), celula(fmt_int(v["tpm_total"]), num=True)]),
                linha([celula("Requisições de página por segundo"), celula(fmt_num(v["tps"], 2), num=True)]),
                linha([
                    celula("Janela"),
                    celula("%s h/dia × %s dias" % (fmt_num(volumetria["horas_por_dia"], 0), fmt_int(volumetria["dias_por_mes"]))),
                ]),
            ],
        ),
        base.tabela(
            "Fluxo de paginação",
            ["Evento", "Cálculo", "Por segundo"],
            [
                linha([
                    celula("Requisições de página"),
                    celula("TPM total / 60"),
                    celula(fmt_num(v["tps"], 1), num=True),
                ]),
                linha([
                    celula("Sessões de paginação"),
                    celula("requisições / %s páginas por sessão" % fmt_num(entradas["paginas_por_sessao"], 1)),
                    celula(fmt_num(v["sessoes_s"], 1), num=True),
                ]),
                linha([
                    celula("Leituras do token"),
                    celula("requisições − sessões (a 1ª página não lê)"),
                    celula(fmt_num(v["leituras_s"], 1), num=True),
                ]),
                linha([
                    celula("Escritas de criação"),
                    celula("1 por sessão"),
                    celula(fmt_num(v["escritas_criacao_s"], 1), num=True),
                ]),
                linha([
                    celula("Escritas de cursor"),
                    celula(
                        "1 por página servida"
                        if entradas["atualiza_cursor_por_pagina"]
                        else "desligado: token imutável"
                    ),
                    celula(fmt_num(v["escritas_cursor_s"], 1), num=True),
                ]),
                linha([
                    celula("Itens vivos (TTL)"),
                    celula("sessões/s × %s min" % fmt_num(entradas["ttl_minutos"], 0)),
                    celula(fmt_int(v["itens_vivos"]), num=True),
                ]),
            ],
        ),
        base.tabela("Tamanho do token", None, linhas_item),
        base.tabela(
            "Consumo de capacidade",
            ["Tipo", "Eventos/s", "Unidades por evento", "Unidades/s"],
            [
                linha([
                    celula("Leitura"),
                    celula(fmt_num(v["leituras_s"], 1), num=True),
                    celula(fmt_num(v["unidades_leitura_item"], 1), num=True),
                    celula(fmt_num(v["rru_s"], 1), num=True),
                ]),
                linha([
                    celula("Escrita"),
                    celula(fmt_num(v["escritas_criacao_s"] + v["escritas_cursor_s"], 1), num=True),
                    celula(fmt_num(v["unidades_escrita_item"], 1), num=True),
                    celula(fmt_num(v["wru_s"], 1), num=True),
                ]),
            ],
            explica="Leitura em blocos de 4 KB (metade do custo quando eventual), escrita em blocos de 1 KB. "
            "A exclusão por TTL não consome capacidade.",
        ),
    ]

    if not on_demand:
        secoes.append(
            base.tabela(
                "Capacidade provisionada",
                None,
                [
                    linha([celula("RCU"), celula(fmt_int(v["capacidade"]["rcu"]), num=True)]),
                    linha([celula("WCU"), celula(fmt_int(v["capacidade"]["wcu"]), num=True)]),
                ],
                explica="Unidades por segundo divididas pela utilização alvo de %s%%."
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

    faixas = dynamodb_comum.seletor_de_faixas(preco, entradas)

    def primeiro_preco(chave, por_milhao=False, ignorar_classe=False):
        obtidas = faixas(chave, ignorar_classe)
        if not obtidas:
            return "—"
        valor = obtidas[0]["preco"] * (1e6 if por_milhao else 1)
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
                linha([celula("Fonte"), celula(preco.fonte)]),
                linha([celula("Publicação do preço"), celula(preco.publicado_em or "—")]),
            ],
        ),
        base.lista(
            "Notas",
            [
                "A exclusão por TTL não consome capacidade de escrita: o token sai da tabela de graça.",
                "A escrita custa 5× a leitura por unidade, e aqui há ao menos uma escrita por página "
                "servida — por isso o custo é dominado por escrita, ao contrário da tabela de saldo.",
                "Guardar os ids da página dentro do token troca leitura do índice por tamanho de item: "
                "com 500 ids de 38 B o token passa de ~1 KB para ~20 KB, o que multiplica por 20 as "
                "unidades de escrita.",
                "O free tier (25 GB, 25 RCU e 25 WCU) é por conta, não por tabela.",
                "Premissas estimadas, não medidas: páginas por sessão, TTL do token e atualização do "
                "cursor a cada página. São os três campos que mais mexem no total.",
            ],
        ),
    ]
    return secoes
