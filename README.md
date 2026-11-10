# Calculadora AWS — Aggregated Polling

Servidor web local, em Python puro (somente biblioteca padrão), que dimensiona e
precifica recursos AWS a partir da volumetria das instituições financeiras (IFs)
que consomem as APIs.

Os preços unitários vêm da **AWS Price List API**
(`pricing.us-east-1.amazonaws.com`), com cache local e fallback para os valores de
`config.json`.

## Executar

```bash
python3 server.py                 # usa a porta de config.json e abre o navegador
python3 server.py --port 9000     # sobrescreve a porta
python3 server.py --no-browser    # não abre o navegador
python3 server.py --config outro.json
```

A porta vem de `config.json → server.port`.

## Telas

| Rota | Tela |
| --- | --- |
| `#/` | **Proposta**: one-page de apresentação — o que é o Aggregated Polling, volumetria, arquitetura, para onde vai a despesa e oportunidades de redução |
| `#/totais` | **Totais**: painel de simulação, custo mensal somado, um cartão por recurso e o comparativo |
| `#/investimento` | **Investimento inicial**: lista de atividades, valor da hora, cronograma em Gantt e esforço por comunidade |
| `#/beneficios` | **Benefícios**: economia esperada com a adoção, a partir do volume de requisições de saldo |
| `#/slots` | **Slots**: por que o modelo de slot variável, comparado ao de slots fixos |
| `#/recurso/<id>` | Tela do recurso: parâmetros à esquerda, detalhamento à direita |

## Tela de Proposta

A tela reúne os quatro números da proposta, cada um levando à tela onde ele é
calculado:

| Item | Vai para |
| --- | --- |
| Despesas mensais com infraestrutura (em R$) | `#/totais` |
| Benefício mensal esperado | `#/beneficios` |
| Investimento inicial | `#/investimento` |
| Duração da implantação | `#/investimento` |

Nada ali é digitado à mão: `/api/overview` monta tudo a partir dos recursos
registrados, do investimento e dos benefícios.

Todos os valores da Proposta ficam **em reais**, para serem comparáveis entre si: a
despesa de infraestrutura é convertida pela cotação do dólar e o valor original em
USD aparece na linha de apoio do cartão.

## Retorno do investimento

Abaixo dos cartões, o gráfico de **caixa acumulado** e, ao lado, o **payback** em
meses. O modelo tem dois estágios (`roi.py`):

```
meses 1..D (implantação)   −investimento / D por mês, sem benefício
mês D+1 em diante          +benefício mensal − despesa de infraestrutura
```

O payback é o mês em que o acumulado cruza zero, interpolado dentro do mês. Com os
valores atuais: 4 meses de implantação desembolsando R$ 335.360,00, depois
R$ 24.677,49/mês de resultado — **payback em 17,6 meses** (13,6 meses de
operação), caixa de R$ 158.189,81 em 24 meses e ROI de 47% no período.

O horizonte do gráfico se ajusta ao payback (mínimo 12, máximo 60 meses). Se o
benefício não superar a despesa de infraestrutura, não há payback e a tela diz
isso em vez de desenhar uma projeção que nunca cruza zero.

A área usa o par **divergente** azul/vermelho (polos frio/quente, ΔE 32,3 na visão
normal e 21,6 sob CVD), com o zero marcado e a faixa de implantação sombreada — o
sinal do caixa é polaridade, não identidade, por isso diverging e não categórico.

As **oportunidades de redução** são cenários declarados em `OPORTUNIDADES`
(`recursos/__init__.py`) como overrides; o endpoint recalcula o total de cada um e
a economia é a diferença contra a configuração atual — nenhum valor é estimado à
mão. Para acrescentar um cenário, basta mais uma entrada na lista.

Decisões de visualização, em todas as telas: valores de destaque são **hero
figures** (não gráficos de uma barra só); magnitudes por categoria usam **barras
ranqueadas em uma única cor** (`#2a78d6`, validada para banda de luminosidade,
croma e contraste ≥ 3:1), porque uma cor por categoria seria decoração quando há
uma só medida; e a divisão fixo/variável dos benefícios usa **ênfase** — a parcela
variável na cor de destaque, a fixa em cinza de contexto (`#52616f`, separação
ΔE 16,1). Toda barra carrega rótulo e valor ao lado, sem depender de hover.

O endpoint `/api/overview` continua devolvendo também indicadores de volumetria,
arquitetura, distribuição de despesa por recurso e oportunidades de redução com
economia calculada — prontos para novos itens na tela.

Os cartões da tela de Totais são links para a tela do recurso. Recursos ainda sem
modelo de custo aparecem como **planejados** e não somam no total.

## Simulação na tela de Totais

A tela de Totais tem um painel com os campos de maior impacto — **TPM por IF**,
**número de IFs**, **registros por página** e **response time** — para simular
cenários sem abrir cada recurso. Ao alterar um valor, todos os recursos são
recalculados e cada cartão mostra o delta contra o que está gravado em
`config.json`.

O primeiro controle é a **cotação do dólar**, usada para converter o custo da AWS
em reais. Ela é buscada automaticamente na AwesomeAPI (USD-BRL, cotação de venda),
com cache de 6 horas e fallback para o valor de `config.json → cambio.usd_brl`.
Alterar o campo simula outra cotação; **Salvar nos recursos** grava o valor e passa
a usá-lo no lugar da consulta automática (`buscar_automatico: false`). Para voltar
à cotação automática, basta religar essa chave no `config.json`.

O número de IFs serve para avaliar crescimento. Com os defaults atuais:

| IFs | Total/mês | Observação |
| --- | --- | --- |
| 30 | US$ 52.541,57 | |
| 60 | US$ 91.966,51 | configuração atual |
| 120 | US$ 170.816,39 | |
| 300 | US$ 390.850,39 | API GW passa da cota de 10.000 req/s; ECS de consulta bate o teto de 500 tasks |
| 600 | US$ 753.350,69 | ECS de consulta travado no teto — o response time não seria atendido |

Os avisos de cada recurso aparecem na tela de Totais, então os limites de
capacidade surgem junto com o custo.

A simulação propaga para os recursos que herdam a volumetria do ALB: o backend
aplica os ajustes sobre uma cópia da configuração antes de calcular
(`recursos.config_efetivo`), então mexer no TPM do ALB move também o API Gateway e
o ECS de consulta. Nada é gravado até clicar em **Salvar nos recursos**.

Quais campos aparecem no painel é definido por `CONTROLES_GLOBAIS`, em
`recursos/__init__.py`, como pares `(recurso, campo)` — o formulário é montado a
partir dos metadados do próprio campo, então não há duplicação de rótulo nem de
validação.

## Arquivos

| Arquivo | Papel |
| --- | --- |
| `config.json` | Porta, API de preços, regiões e, por recurso, defaults / limites / preços de fallback |
| `server.py` | Servidor HTTP e API JSON |
| `investimento.py` | Investimento inicial: esforço, custo em reais e cronograma por dias úteis |
| `beneficios.py` | Benefícios: economia mensal esperada sobre a parcela variável do custo |
| `cambio.py` | Cotação USD→BRL: consulta pública, cache e fallback |
| `roi.py` | Fluxo de caixa acumulado e payback a partir dos três números da proposta |
| `recursos/__init__.py` | Registro de recursos, cálculo individual e totais |
| `recursos/base.py` | Formatação pt-BR, declaração de campos, normalização e seções de resultado |
| `recursos/alb.py` | Application Load Balancer: campos, modelo de LCU e detalhamento |
| `recursos/dynamodb_comum.py` | Preços, escolhas e blocos de capacidade comuns às tabelas DynamoDB |
| `recursos/fargate_comum.py` | Tamanhos de task, preços e custo por task-hora comuns aos ECS |
| `recursos/ecs_consumidor.py` | Base dos consumidores Kafka que gravam no Aurora |
| `recursos/aurora.py`, `kafka_sink.py`, `s3_staging.py`, `glue_carga.py` | Recursos da cadeia de ingestão |
| `aws_pricing.py` | AWS Price List API genérica (offer code por recurso), cache e fallback |
| `static/` | Página web: roteador por hash e renderização dirigida por metadados |
| `.cache/` | Offer files da AWS em cache (renovado a cada 24 h) |

## Como adicionar um recurso (DynamoDB, ECS, ...)

A página não tem HTML por recurso: o formulário e o detalhamento são montados a
partir do que o módulo declara. Para somar um recurso novo:

1. Crie `recursos/<id>.py` expondo:

   ```python
   ID, NOME, DESCRICAO, OFFER_CODE, STATUS   # metadados
   CAMPOS                                    # grupos de campos (base.grupo/numero/escolha/...)
   extrair_precos(offer, region) -> dict | None
   calcular(entradas, recurso_cfg, preco, config=None) -> dict
   region_efetiva(entradas, config) -> str   # opcional
   ```

   `config` é a configuração inteira, o que permite a um recurso herdar valores de
   outro (o ECS de consulta herda a volumetria do ALB). Quando a região também pode
   vir de outro recurso, declare `region_efetiva` para que o preço seja buscado na
   região certa.

   Um recurso pode publicar números para os outros devolvendo `expostos` no
   resultado — é como o CloudWatch obtém tasks, task-horas e unidades processadas
   dos dois ECS, chamando `recursos.calcular` para cada um. Os ECS expõem
   `tasks_pico`, `tasks_fora`, `task_horas_mes`, `unidades_mes` e `servicos`.

   `calcular` devolve `total_mes`, `moeda`, `resumo` (linhas do cartão na tela de
   totais), `cartoes`, `secoes` (tabelas e listas via `base.tabela` / `base.lista`)
   e, opcionalmente, `avisos` e `medidas`.

2. Registre o módulo em `ATIVOS`, em `recursos/__init__.py`, e remova o item
   correspondente de `PLANEJADOS`.

3. Acrescente a seção do recurso em `config.json → recursos.<id>` com
   `habilitado`, `entradas` (os defaults) e `precos_fallback` por região.

Preços escalonados por volume (API Gateway, por exemplo) usam
`aws_pricing.faixas_on_demand(offer, sku)` no extrator e
`base.custo_escalonado(faixas, quantidade)` no cálculo, que devolve o total e a
parcela que caiu em cada faixa.

`OFFER_CODE` é o offer code da Price List API — `AmazonDynamoDB`, `AmazonECS`,
`AWSELB` etc. O `extrair_precos` recebe o offer file já baixado e em cache, e
devolve o dicionário de preços que o `calcular` vai consumir (ou `None` quando não
encontrar os SKUs, caindo no fallback do `config.json`).

## Recursos já declarados

| Ordem | Id | Recurso | Papel |
| --- | --- | --- | --- |
| 10 | `api-gateway` | API Gateway | Porta de entrada das APIs das IFs |
| 20 | `alb` | Application Load Balancer | Distribui as chamadas para as tasks |
| 30 | `ecs-consulta` | ECS Fargate — Consulta | Atende as consultas lendo o Aurora |
| 40 | `aurora` | Aurora PostgreSQL | Saldos, consentimentos e resources |
| 50 | `kafka-sink` | Kafka Sink → S3 | Entrega os eventos de saldo no bucket |
| 60 | `s3-staging` | S3 — Staging | Guarda os arquivos até a carga |
| 70 | `glue-carga` | AWS Glue — Carga | Lê o S3 e carrega o Aurora |
| 80 | `ecs-consentimentos` | ECS Fargate — Consentimentos | Consome o tópico e atualiza a tabela |
| 90 | `ecs-resources` | ECS Fargate — Resources | Consome o tópico e atualiza a tabela |
| 100 | `cloudwatch` | CloudWatch | Métricas e alarmes do auto scaling |

```
                    ┌─ API GW → ALB → ECS Consulta ─┐
IFs ────────────────┤                               ├──→ Aurora
                    └───────────────────────────────┘        ▲
                                                             │
Kafka saldos ──→ Kafka Sink ──→ S3 ──→ Glue ─────────────────┤
Kafka consentimentos ──→ ECS Consentimentos ─────────────────┤
Kafka resources ──────→ ECS Resources ───────────────────────┘
```

## Estratégia anterior

A primeira modelagem — DynamoDB de saldo agregado, DynamoDB de controle de
paginação e um ECS consumindo o Kafka direto para o DynamoDB — continua no
projeto: os módulos `dynamodb_saldo.py`, `dynamodb_paginacao.py` e `ecs_carga.py`
seguem em `recursos/`, e suas seções em `config.json` estão com
`habilitado: false`. Para voltar a ela, reimporte os módulos em `ATIVOS`
(`recursos/__init__.py`) e reative as seções.

Todos têm modelo de custo. Para declarar um recurso futuro antes de modelá-lo,
acrescente-o a `PLANEJADOS` em `recursos/__init__.py`: ele aparece nas telas com o
selo *planejado* e não soma no total.

As telas seguem o fluxo da chamada (`ordem`), não o status: API GW → ALB → ECS de
consulta → DynamoDB. O ECS de carga alimenta as tabelas fora do caminho da
requisição e o CloudWatch observa os ECSs.

Cada módulo pode expor `NOME_CURTO` para a navegação (mantendo o nome completo nos
cartões) e `ORDEM` para a posição no fluxo.

---

# Recurso: Application Load Balancer

## Parâmetros de entrada

Ficam em `config.json → recursos.alb.entradas` e podem ser alterados na página sem
reiniciar o servidor. O botão **Salvar como default** grava os valores atuais de
volta no arquivo.

| Parâmetro | Default | Significado |
| --- | --- | --- |
| `region` | `sa-east-1` | Região de preço |
| `tpm_por_if` | 2500 | Transações por minuto **de cada IF** |
| `numero_ifs` | 1 | Quantidade de IFs; o TPM total é `tpm_por_if × numero_ifs` |
| `registros_por_pagina` | 500 | Registros por página na resposta |
| `response_time_s` | 1,5 | Tempo de resposta médio, usado nas conexões ativas |
| `modo_payload_entrada` | `por_exemplo_json` | `por_exemplo_json` (mede o exemplo colado) ou `manual` (KB) |
| `exemplo_payload_entrada` | JSON de consulta | Exemplo do corpo da requisição; o tamanho é medido dinamicamente |
| `payload_entrada_kb` | 2,0 | Payload de entrada, no modo `manual` |
| `modo_payload_saida` | `por_exemplo_json` | `por_exemplo_json`, `por_registro` (registros × KB/registro) ou `por_request` (valor fixo) |
| `exemplo_payload_saida_envelope` | JSON do `aggregated-polling` | Envelope da resposta **com o array de registros vazio**; contado 1× por resposta |
| `exemplo_payload_saida_registro` | UUID de `resourceIds` | Exemplo de **um** registro; multiplicado por `registros_por_pagina` |
| `minificar_json` | `true` | Mede o JSON compacto (como trafega) em vez de indentado |
| `payload_saida_por_registro_kb` | 1,0 | KB por registro, no modo `por_registro` |
| `payload_saida_kb` | 500 | Payload de saída fixo, no modo `por_request` |
| `reuso_conexao` | 1 | Requests por conexão TCP (1 = nova conexão por request) |
| `regras_por_request` | 10 | Regras do listener avaliadas por request (as 10 primeiras são gratuitas) |
| `overhead_http_kb` | 0,8 | Headers e overhead de protocolo por request |
| `horas_por_dia` / `dias_por_mes` | 24 / 30 | Janela de funcionamento (720 h/mês) |
| `fator_pico` | 1,0 | Multiplicador sobre o TPM total para cenário de pico |
| `zonas_disponibilidade` | 2 | Informativo: não altera o preço do ALB |

## Modelo de cálculo

```
TPM total = tpm_por_if × numero_ifs × fator_pico
TPS       = TPM total / 60
horas/mês = horas_por_dia × dias_por_mes
bytes/request = payload_entrada + payload_saida + overhead_http
```

### Payload medido a partir de um exemplo JSON

No modo `por_exemplo_json` o tamanho não é informado em KB: é **medido** sobre os
exemplos colados, em UTF-8, com `minificar_json` reserializando o JSON na forma
compacta (é assim que ele trafega; o valor "como colado" também é exibido).

```
payload_saida = envelope + registros_por_pagina × registro + (registros_por_pagina − 1)
```

O envelope é o corpo da resposta com o array de registros **vazio** (ele não
escala com a paginação) e o último termo são as vírgulas que separam os itens do
array. Mudar `registros_por_pagina` na página recalcula o payload na hora.

Se o exemplo não for JSON válido, o texto é medido como está e aparece um aviso —
o cálculo não quebra.

### LCUs

O ALB cobra uma taxa fixa por hora mais **LCUs** (Load Balancer Capacity Units).
A LCU faturada em cada hora é o **máximo** entre quatro dimensões:

| Dimensão | Cálculo | Limite por LCU |
| --- | --- | --- |
| Novas conexões | `TPS / reuso_conexao` | 25 conexões/s |
| Conexões ativas | `TPS × response_time` (Lei de Little) | 3.000 conexões/min |
| Bytes processados | `TPS × 3600 × bytes/request` | 1 GB/h |
| Avaliações de regra | `TPS × max(0, regras − 10)` | 1.000 avaliações/s |

```
Custo = horas/mês × preço_ALB_hora + horas/mês × LCU × preço_LCU_hora
```

Os limites ficam em `config.json → recursos.alb.lcu_limites` e podem ser ajustados
se a AWS mudar a definição de LCU. 1 GB = 1.024³ bytes.

### Ordem de grandeza (sa-east-1, $0,034/ALB-h e $0,011/LCU-h)

Com o exemplo do `aggregated-polling` (envelope 404 B + registros de 38 B):

| Cenário | KB/request | LCUs | Gargalo | US$/mês |
| --- | --- | --- | --- | --- |
| 1 IF, 2.500 TPM, 500 registros | 20,43 | 2,92 | bytes | 47,63 |
| 5.000 registros por página | 191,82 | 27,44 | bytes | 241,80 |
| Registro = objeto de saldo (125 B) | 62,91 | 9,00 | bytes | 95,76 |
| 50 IFs, reuso de conexão 10 | 20,43 | 146,15 | bytes | 1.181,96 |

O tamanho do registro é a alavanca principal: um `resourceIds` de UUIDs mantém o
custo na casa das dezenas de dólares, enquanto registros completos de centenas de
bytes multiplicam o total. Compactar a resposta (gzip) ou reduzir a página ataca
exatamente a dimensão que está cobrando.

## Fora do escopo do total

Data Transfer Out para a internet, NAT Gateway, WAF, certificados, e o custo dos
targets (EC2/ECS/Lambda) atrás do ALB.

---

# Recurso: API Gateway

Uma rota que recebe as chamadas das IFs e encaminha para o ALB. Os defaults ficam
em `config.json → recursos.api-gateway.entradas` e a volumetria é herdada do ALB
por padrão (`herdar_volumetria_do_alb`), igual ao ECS de consulta.

## Parâmetros

| Parâmetro | Default | Significado |
| --- | --- | --- |
| `herdar_volumetria_do_alb` | `true` | Usa região, TPM por IF, nº de IFs, fator de pico e janela do ALB |
| `tipo_api` | `http` | `http` (HTTP API) ou `rest` (REST API, ~2,7× mais caro por request) |
| `integracao` | `vpc_link_alb` | VPC Link privado para o ALB, ou integração HTTP pública |
| `numero_rotas` | 1 | Informativo: o preço não varia com o número de rotas |
| `requests_por_transacao` | 1,0 | Requests no API GW por transação da IF (preflights, retries) |
| `cache_gb` | `nenhum` | Cache dedicado, cobrado por hora — só existe em REST API |
| `limite_rps_conta` | 10000 | Cota de requests/s da conta; o pico é comparado com ela |

## Modelo de cálculo

```
requests/mês = TPM total × 60 × horas_por_dia × dias_por_mes × requests_por_transacao
custo = escalonado(faixas do tipo de API, requests/mês) + horas/mês × preço do cache
```

O preço por request **cai por faixa de volume mensal**, e a calculadora aplica a
progressão faixa por faixa (a tabela "Faixas de preço aplicadas" mostra quanto
caiu em cada uma). Em sa-east-1, publicação 2026-10-13:

| Tipo | Faixas |
| --- | --- |
| HTTP API | $1,59/milhão até 300 M; $1,43/milhão acima |
| REST API | $4,25/milhão até 1 bi; $3,61/milhão até 20 bi; $2,29/milhão acima |

O preço não depende do número de rotas nem do tamanho do payload (limite de 10 MB
por request).

### Avisos que a calculadora emite

- **REST API com VPC Link para o ALB**: a REST API só faz integração privada com
  NLB. Para chegar ao ALB seria preciso um NLB na frente, ou usar HTTP API.
- **Pico acima da cota**: se o pico de requests/s passar de `limite_rps_conta`, as
  chamadas excedentes recebem 429 e é preciso pedir aumento de cota.

### Ordem de grandeza (60 IFs × 2.500 TPM = 6,48 bilhões de requests/mês)

| Cenário | US$/mês |
| --- | --- |
| HTTP API (default) | 9.314,40 |
| REST API | 24.032,80 |
| REST API + cache de 6,05 GB | 24.209,20 |
| 2 requests por transação (HTTP API) | 18.580,80 |

Neste volume o API Gateway é o **maior item da conta** — mais caro que o ALB e o
ECS somados — e a escolha entre HTTP API e REST API vale US$ 14,7 mil por mês.
Como o custo é linear no número de requests, `requests_por_transacao` é a variável
mais sensível: cada request extra por transação custa outros ~US$ 9 mil/mês.

## Fora do escopo do total

Data Transfer Out, WAF, certificados, custom domain e os recursos por trás da
integração.

---

# Recurso: ECS Fargate — Consulta

Tasks que atendem as consultas vindas do ALB: leem o DynamoDB e devolvem a página
de slots dentro do response time alvo. Os defaults ficam em
`config.json → recursos.ecs-consulta.entradas`.

## Volumetria herdada do ALB

Com `herdar_volumetria_do_alb` (default) o recurso usa a região, o TPM por IF, o
número de IFs, o fator de pico, o response time e a janela **do ALB** — não há
número duplicado entre as duas telas. Desligando a herança, os campos próprios
aparecem no formulário e passam a valer.

## Parâmetros

| Parâmetro | Default | Significado |
| --- | --- | --- |
| `herdar_volumetria_do_alb` | `true` | Usa a volumetria do ALB em vez dos campos próprios |
| `arquitetura` | `x86` | `x86` (Intel/AMD) ou `arm` (Graviton, ~20% mais barato) |
| `vcpu_por_task` | 0,5 | vCPU de cada task (tamanhos válidos: 0,25 a 16) |
| `memoria_gb_por_task` | 1,0 | Memória de cada task; a faixa válida depende da vCPU |
| `armazenamento_efemero_gb` | 20 | Os primeiros 20 GB por task são gratuitos |
| `tempo_cpu_por_request_ms` | 25 | **Só CPU**; a espera pelo DynamoDB não entra aqui |
| `utilizacao_alvo_cpu` | 60 | Alvo do target tracking do ASG, em % |
| `concorrencia_por_task` | 50 | Consultas em voo que uma task sustenta no response time |
| `tasks_minimas` / `tasks_maximas` | 2 / 500 | Limites do Auto Scaling |
| `horas_pico_por_dia` | 24 | Horas no TPM de pico; no restante vale o percentual abaixo |
| `percentual_tpm_fora_pico` | 30 | TPM fora do pico, como % do pico |

## Modelo de cálculo

O número de tasks é o **máximo entre duas restrições**, no mesmo espírito das LCUs
do ALB:

| Restrição | Cálculo |
| --- | --- |
| CPU | `vCPUs = TPS × tempo_cpu_por_request / utilizacao_alvo_cpu`, depois `/ vcpu_por_task` |
| Concorrência | `requests em voo = TPS × response_time` (Lei de Little), depois `/ concorrencia_por_task` |

O resultado é arredondado para cima e preso entre `tasks_minimas` e
`tasks_maximas`. Se o pico exigir mais que o teto do ASG, o custo é limitado ao
teto e aparece um aviso de que o response time não seria atendido.

O Auto Scaling é modelado por faixa horária:

```
task-horas/mês = dias × (horas_pico × tasks_pico + horas_fora × tasks_fora)
custo = task-horas × (vcpu_por_task × preço_vCPU-h + memória_GB × preço_GB-h)
      + task-horas × GB efêmero cobrado × preço_storage-h
```

Com `horas_pico_por_dia = 24` a faixa fora do pico não existe e o serviço roda
dimensionado para o pico o mês inteiro.

### Ordem de grandeza (sa-east-1, 60 IFs × 2.500 TPM = 2.500 TPS)

Preços de 2026-10-19: vCPU-h $0,0696 e GB-h $0,0076 (x86); $0,0557 e $0,00612 (ARM).

| Cenário | Tasks no pico | Restrição | US$/mês |
| --- | --- | --- | --- |
| Default (0,5 vCPU / 1 GB, x86) | 209 | CPU | 6.380,35 |
| Mesmo cenário em ARM/Graviton | 209 | CPU | 5.111,81 |
| Tasks de 1 vCPU / 2 GB | 105 | CPU | 6.410,88 |
| Tempo de CPU de 5 ms | 75 | Concorrência | 2.289,60 |
| Concorrência de 10 por task (CPU 5 ms) | 375 | Concorrência | 11.448,00 |
| Pico em 10 h/dia, 20% fora do pico | 209 | CPU | 3.406,42 |

Com 25 ms de CPU por request a conta é **limitada por CPU**, não por concorrência:
são 104 vCPU no pico. Dobrar a task de 0,5 para 1 vCPU quase não muda o total
(o custo é por vCPU-hora, não por task), mas reduzir o tempo de CPU por request
ou migrar para Graviton mexe direto no valor. A concorrência só passa a mandar
quando o processamento é barato — aí o que sustenta o response time é ter
requisições em voo suficientes, e `concorrencia_por_task` vira a alavanca.

## Fora do escopo do total

Data Transfer, ECR (armazenamento e pull das imagens), CloudWatch (métricas, logs
e alarmes do ASG) e o DynamoDB consultado — cada um é um recurso próprio.

---

# Recurso: Aurora PostgreSQL

Base relacional que concentra saldos, consentimentos e resources. Recebe três
fluxos de escrita (Glue, ECS de consentimentos e ECS de resources) e serve as
consultas do ECS de consulta — toda essa carga é **herdada** dos recursos
vizinhos com `herdar_carga`.

| Modo | Como é cobrado |
| --- | --- |
| `serverless_v2` | ACU-hora, em cada instância (writer + réplicas) |
| `provisionado` | Hora de instância, por classe |

A ACU média é dimensionada pela carga: `(consultas/s + escritas/s) ÷
transacoes_por_acu_por_segundo`, presa entre o mínimo e o máximo configurados. Se
a carga passar do teto, a tela avisa que o banco satura.

| Classe | I/O | ACU-hora | Armazenamento |
| --- | --- | --- | --- |
| Standard | US$ 0,28/milhão | US$ 0,25 | US$ 0,19/GB-mês |
| I/O-Optimized | incluso | US$ 0,33 | US$ 0,428/GB-mês |

Quando o I/O passa de 25% da conta, a calculadora calcula o total na outra classe
e avisa se compensa. Com os defaults atuais, compensa: **US$ 3.273,90 → US$ 1.600,00**.

---

# Recurso: Kafka Sink → S3

Entrega os eventos de saldo do tópico para o bucket, em um de dois caminhos:

| Modo | Cobrança |
| --- | --- |
| `msk_connect` | MCU-hora provisionada (US$ 0,175), independente do volume |
| `data_channel` | US$ 0,01 por GB processado, sem capacidade a dimensionar |

O recurso também publica a volumetria que o resto da cadeia consome: eventos/mês,
GB gravados e **quantos arquivos** chegam ao bucket — cada partição fecha um
arquivo a cada flush, e é isso que vira PUT no S3.

O `intervalo_flush_segundos` troca custo por latência: flush curto gera muitos
arquivos pequenos, que encarecem o S3 e deixam o Glue mais lento. A tela avisa
quando o arquivo médio fica abaixo de 1 MB.

---

# Recurso: S3 — Staging

Bucket entre o sink e o Glue. Volume e número de arquivos vêm do sink; as
execuções que fazem LIST vêm do Glue.

```
PUT  = arquivos entregues pelo sink          US$ 7,00/milhão
GET  = arquivos × leituras_por_arquivo       US$ 0,56/milhão
LIST = execuções do Glue × LIST por execução US$ 0,56/milhão
armazenamento = GB do mês × retenção/30      US$ 0,0405/GB-mês
```

PUT custa 12,5× o GET, então o custo aqui é governado pelo número de arquivos —
não pelo volume de dados.

---

# Recurso: AWS Glue — Carga

Lê o S3 e carrega o Aurora, em modo **agendado** (a cada N minutos) ou
**streaming** (contínuo). No agendado paga-se DPU × duração de cada execução, com
mínimo de 1 minuto por execução; o intervalo é também a **latência do dado**.

A tela traz a comparação direta entre frequências, com o mesmo job (2 DPUs,
2 min por execução, Glue 6.0+):

| Frequência | Execuções/mês | DPU-horas | Latência | US$/mês |
| --- | --- | --- | --- | --- |
| A cada 1 min | 43.200 | 2.880 | até 1 min | 1.391,04 ⚠ |
| **A cada 5 min** | 8.640 | 576 | até 5 min | **278,21** |
| A cada 10 min | 4.320 | 288 | até 10 min | 139,10 |
| A cada 15 min | 2.880 | 192 | até 15 min | 92,74 |
| A cada 30 min | 1.440 | 96 | até 30 min | 46,37 |
| A cada 60 min | 720 | 48 | até 60 min | 23,18 |
| Glue Streaming | contínuo | 1.440 | segundos | 695,52 |

O ⚠ marca os intervalos menores que a duração da execução, em que os jobs se
sobrepõem. Note que **1 minuto custa o dobro do streaming** e entrega latência
pior: abaixo de ~3 minutos, o streaming passa a ser a opção melhor e mais barata.

Versões: Glue 6.0+ (Gen2) custa US$ 0,483/DPU-h contra US$ 0,69 da 4.0/5.0; o modo
Flex sai por US$ 0,315, em capacidade não garantida.

---

# Recursos: ECS Fargate — Consentimentos e Resources

Dois consumidores Kafka que mantêm as tabelas de consentimentos e de resources no
Aurora. Compartilham a lógica em `recursos/ecs_consumidor.py` e o mesmo trio de
restrições do ECS de consulta — CPU, concorrência de I/O e **teto de partições** —
além de publicarem suas escritas (`escritas_aurora_s`) para o Aurora somar na carga.

---

# Recurso: CloudWatch

Neste desenho o CloudWatch serve ao **auto scaling dos dois ECS**: os logs das
aplicações vão para o Datadog, que já está pago e fica fora desta conta.

As métricas que o target tracking usa — **CPU e memória do serviço** (`AWS/ECS`) e
**ALBRequestCountPerTarget** (`AWS/ApplicationELB`) — são publicadas pela AWS **sem
custo**. Sobram três itens pagos:

| Item | Como é calculado |
| --- | --- |
| Alarmes de escala | `serviços × políticas × alarmes_por_politica` (o target tracking cria 2: scale-out e scale-in) |
| Métricas customizadas de escala | Gatilhos publicados pela equipe, como o lag do consumidor Kafka |
| Coleta pelo Datadog | `GetMetricData`: ciclos × métricas por ciclo; ou `Metric Streams`: métricas × atualizações/min × minutos |

A seção de logs continua no formulário, desligada por `logs_no_cloudwatch`: se algo
voltar a escrever no CloudWatch Logs, basta ligar e informar os GB.

## Parâmetros

| Parâmetro | Default | Significado |
| --- | --- | --- |
| `numero_servicos` | 2 | Serviços ECS com auto scaling (consulta e carga) |
| `politicas_escala_por_servico` | 1 | Políticas de target tracking por serviço |
| `alarmes_por_politica` | 2 | Scale-out e scale-in |
| `metricas_padrao_observadas` | 6 | Informativo: gatilhos padrão, sem custo |
| `metricas_customizadas` | 2 | **Estimativa** — lag do consumidor Kafka no ECS de carga |
| `modo_coleta_externa` | `getmetricdata` | Como o Datadog obtém as métricas |
| `metricas_coletadas` / `intervalo_coleta_minutos` | 40 / 10 | Padrão da integração AWS do Datadog |
| `alarmes_operacionais` | 8 | **Estimativa** — fora do auto scaling |
| `alarmes_compostos` | 1 | **Estimativa** |
| `logs_no_cloudwatch` | `false` | Os logs vão para o Datadog |

### Ordem de grandeza (sa-east-1)

| Cenário | US$/mês |
| --- | --- |
| **Default (4 alarmes de escala, lag como gatilho)** | **5,31** |
| Escalar só por CPU (sem gatilho customizado) | 4,71 |
| Sem coleta pelo Datadog | 2,89 |
| 3 políticas de escala por serviço | 6,39 |
| Datadog coletando a cada 1 minuto | 27,09 |
| Metric Streams (1 update/min) | 10,15 |
| 40 alarmes operacionais | 9,63 |
| **Lag publicado por partição (256 métricas)** | **81,51** |
| *Logs residuais no CloudWatch (50 GB)* | *50,52* |

Com os logs no Datadog, o CloudWatch sai de US$ 5,8 mil para **US$ 5,31 por mês** —
deixa de ser um item da conta. A composição: US$ 2,42 de coleta pelo Datadog,
US$ 1,62 de alarmes, US$ 0,68 do composto e US$ 0,60 dos gatilhos customizados.

Três coisas que ainda podem mexer nesse número:

- **Lag por partição em vez de por tópico.** Publicar o lag de cada uma das 256
  partições como métrica própria custa US$ 76,80/mês em vez de US$ 0,60 — 15× o
  resto do recurso. Agregue por tópico ou por grupo de consumidores.
- **Intervalo de coleta do Datadog.** Cada métrica requisitada por `GetMetricData`
  é cobrada; de 10 min para 1 min, o item vai de US$ 2,42 para US$ 24,19.
- **Logs voltando ao CloudWatch.** A US$ 0,90/GB, 50 GB já custam 10× todo o
  resto do recurso.

## Fora do escopo do total

O custo do próprio Datadog (hosts, custom metrics, indexed logs), X-Ray/traces,
Synthetics, RUM e dashboards (os 3 primeiros são gratuitos). O free tier
(10 métricas, 10 alarmes, 5 GB) não é modelado: é por conta, não por recurso.

---

# Slots fixos × slots variáveis

Tela explicativa, sem parâmetros de custo: apresenta o modelo de **slot variável**
(`?slots=N`, janela relativa ao momento da consulta) frente ao de **slots fixos**
(janelas predefinidas, percorridas uma a uma).

O conteúdo é gráfico, em cinco blocos:

1. **Os dois modelos** lado a lado, com a chamada de API de cada um.
2. **Simulador de janela**: quantas requisições cobrem N minutos em cada modelo —
   N blocos de um minuto contra uma requisição. Atalhos para 5 min, 15 min, 1 hora
   e 24 horas (onde o contraste fica em 1.440 × 1).
3. **Linha do tempo** de 10h00 a 10h05 com a mesma janela nos dois modelos e duas
   alterações da Conta A (10h01 e 10h03) marcadas sobre os slots.
4. **O que cada modelo devolve**: a conta aparece duas vezes no fixo, uma no
   variável, com a explicação de por que a consolidação basta (o receptor vai
   consultar a API de Balances uma vez, tenha havido uma ou dez alterações).
5. **Comparativo**, **pontos técnicos** (deduplicação, sobreposição, consistência
   temporal, retenção, limite de consulta) e **recomendação**.

Cores seguem o mesmo critério das demais telas: ênfase no modelo proposto
(azul `#2a78d6`) contra o modelo atual em cinza de contexto (`#52616f`), e laranja
só para os eventos de alteração.

---

# Investimento inicial

Tela à parte dos recursos AWS: o esforço de implantação, em horas e em reais, com
o cronograma das atividades. Fica em `config.json → investimento_inicial`.

## A lista

Cada linha é uma atividade com **comunidade**, **atividade**, **horas** (esforço),
**dias úteis** (planejamento) e **atividade predecessora** (opcional). A coluna de
custo é calculada; linhas são adicionadas e removidas na própria tela.

| Campo | Papel |
| --- | --- |
| `valor_hora` | Converte horas em reais. Default **R$ 262,00**, editável |
| `dias_uteis_por_mes` | Tamanho do mês no cronograma. Default **20** |
| `horas` | Esforço, que vira custo: `horas × valor_hora` |
| `dias_uteis` | Duração no calendário, independente do esforço |
| `predecessora` | A atividade que precisa terminar antes; vazio começa no dia zero |

Esforço e duração são separados de propósito: 320 horas podem ocupar 25 dias
úteis com parte do time, e é a duração que posiciona a barra no cronograma.

## Cronograma

O início de cada atividade é o fim da predecessora; sem predecessora, começa em
zero. A duração total é o maior fim, e os meses são genéricos — **Mês 1, Mês 2…**,
de 20 dias úteis cada.

Dependências circulares não quebram o cálculo: a atividade envolvida é posicionada
no início e aparece um aviso. Uma predecessora que não existe mais é recusada na
validação.

## API

| Rota | Método | Descrição |
| --- | --- | --- |
| `/api/investment` | GET | Lista, totais, cronograma e esforço por comunidade |
| `/api/investment` | POST | **Prévia**: recalcula a lista enviada sem gravar |
| `/api/investment/save` | POST | Grava a lista em `config.json` e devolve o recálculo |

```bash
curl -s -X POST http://127.0.0.1:8080/api/investment \
  -H 'Content-Type: application/json' \
  -d '{"valor_hora": 262, "itens": [
        {"id":"a1","comunidade":"Plataforma","atividade":"Infra","horas":160,"dias_uteis":15,"predecessora":""},
        {"id":"a2","comunidade":"Dados","atividade":"Carga","horas":320,"dias_uteis":25,"predecessora":"a1"}]}'
```

As atividades que vêm no `config.json` são **exemplos** derivados da arquitetura,
para substituir pelas reais.

---

# Benefícios

Economia esperada com a adoção do Aggregated Polling. Fica em
`config.json → beneficios`.

## Cálculo

```
custo total das requisições = volume × custo unitário
parcela variável            = custo total × % variável
benefício mensal            = parcela variável × % de redução
```

Ou, na forma direta: `volume × custo unitário × % de redução × % variável`.

A parcela **fixa** fica de fora de propósito: ela não cai quando o volume cai. A
tela mostra a cadeia passo a passo e a divisão fixo/variável, para a conta ficar
auditável.

| Parâmetro | Default | Significado |
| --- | --- | --- |
| `volume_requisicoes_mes` | 613.000.000 | Requisições de saldo por mês no modelo atual |
| `custo_unitario` | R$ 0,00042 | Custo de uma requisição de saldo |
| `percentual_custo_fixo` | 70 | Parcela que não varia com o volume |
| `percentual_custo_variavel` | 30 | Parcela que cai junto com o volume — base do benefício |
| `percentual_reducao` | 71 | Redução de volume esperada com o polling agregado |
| `racional` | texto | Como o percentual de redução foi estimado |

Com os defaults: R$ 257.460,00 de custo mensal, R$ 77.238,00 de parcela variável,
**R$ 54.838,98 de benefício mensal** (R$ 658.067,76/ano) e 435,2 milhões de
requisições evitadas.

Se fixo + variável não somarem 100%, a tela avisa — mas o cálculo continua, porque
pode haver uma terceira parcela fora do escopo.

## API

| Rota | Método | Descrição |
| --- | --- | --- |
| `/api/benefits` | GET | Benefício, cadeia de cálculo e racional |
| `/api/benefits` | POST | **Prévia**: recalcula os valores enviados sem gravar |
| `/api/benefits/save` | POST | Grava em `config.json` e devolve o recálculo |

---

# API JSON

| Rota | Método | Descrição |
| --- | --- | --- |
| `/api/resources` | GET | Catálogo de recursos (ativos e planejados) e regiões disponíveis |
| `/api/resources/<id>` | GET | Metadados, campos declarados e defaults do recurso |
| `/api/resources/<id>/calculate` | GET / POST | Detalhamento completo; no POST, as entradas enviadas são mescladas sobre os defaults |
| `/api/resources/<id>/defaults` | POST | Persiste as entradas do recurso em `config.json` |
| `/api/overview` | GET | Dados da tela de Proposta: indicadores, arquitetura, distribuição de despesa e oportunidades calculadas |
| `/api/totals` | GET / POST | Totais em USD e BRL, cotação e campos de simulação; no POST, `{"<id>": {entradas}}` aplica os ajustes (inclusive na herança) e devolve o `baseline`. A chave `cambio` simula outra cotação |
| `/api/exchange/save` | POST | Grava `{"usd_brl": x}` em `config.json` e desliga a busca automática |
| `/api/config` | GET | Configuração bruta |

```bash
curl -s -X POST http://127.0.0.1:8080/api/resources/alb/calculate \
  -H 'Content-Type: application/json' \
  -d '{"numero_ifs": 50, "reuso_conexao": 10}'

curl -s http://127.0.0.1:8080/api/totals

# simulação: dobra o TPM do ALB e recalcula todos os recursos
curl -s -X POST http://127.0.0.1:8080/api/totals \
  -H 'Content-Type: application/json' \
  -d '{"alb": {"tpm_por_if": 5000}}'
```
