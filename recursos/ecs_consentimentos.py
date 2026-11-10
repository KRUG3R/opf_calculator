"""ECS Fargate - Consentimentos: consome o tópico de consentimentos e mantém a tabela no Aurora."""

from . import ecs_consumidor, fargate_comum

ID = "ecs-consentimentos"
NOME = "ECS Fargate — Consentimentos"
NOME_CURTO = "ECS Consentimentos"
DESCRICAO = "Consome o tópico de consentimentos e atualiza a tabela no Aurora."
OFFER_CODE = fargate_comum.OFFER_CODE
ORDEM = 80
STATUS = "ativo"

CAMPOS = ecs_consumidor.construir_campos("o tópico de consentimentos", "a tabela de consentimentos")
extrair_precos = fargate_comum.extrair_precos
region_efetiva = ecs_consumidor.region_efetiva


def calcular(entradas, recurso_cfg, preco, config=None):
    return ecs_consumidor.calcular(entradas, preco, "consentimentos")
