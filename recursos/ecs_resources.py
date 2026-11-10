"""ECS Fargate - Resources: consome o tópico de resources e mantém a tabela no Aurora."""

from . import ecs_consumidor, fargate_comum

ID = "ecs-resources"
NOME = "ECS Fargate — Resources"
NOME_CURTO = "ECS Resources"
DESCRICAO = "Consome o tópico de resources e atualiza a tabela no Aurora."
OFFER_CODE = fargate_comum.OFFER_CODE
ORDEM = 90
STATUS = "ativo"

CAMPOS = ecs_consumidor.construir_campos("o tópico de resources", "a tabela de resources")
extrair_precos = fargate_comum.extrair_precos
region_efetiva = ecs_consumidor.region_efetiva


def calcular(entradas, recurso_cfg, preco, config=None):
    return ecs_consumidor.calcular(entradas, preco, "resources")
