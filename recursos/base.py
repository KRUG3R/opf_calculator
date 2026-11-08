"""Blocos comuns aos recursos: formatacao pt-BR, declaracao de campos e normalizacao."""

import json

KB = 1024.0
GB = 1024.0 ** 3


# ---------- formatacao ----------

def fmt_num(valor, decimais=2):
    """1234.5 -> '1.234,50'"""
    texto = "{:,.{d}f}".format(float(valor), d=decimais)
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_int(valor):
    return fmt_num(valor, 0)


SIMBOLOS = {"USD": "US$", "BRL": "R$"}


def fmt_moeda(valor, moeda="USD", decimais=2):
    simbolo = SIMBOLOS.get(moeda, moeda)
    return "%s %s" % (simbolo, fmt_num(valor, decimais))


def fmt_bytes(valor):
    valor = float(valor)
    if valor >= 1024 * 1024:
        return fmt_num(valor / (1024 * 1024), 2) + " MB"
    if valor >= 1024:
        return fmt_num(valor / 1024, 2) + " KB"
    return fmt_int(valor) + " B"


# ---------- medicao de payloads de exemplo ----------

def medir_json(texto, minificar=True):
    """Mede o tamanho em bytes de um exemplo de payload.

    Se o texto for JSON valido e 'minificar' estiver ativo, mede a forma compacta
    (sem indentacao), que e o que normalmente trafega na rede. Caso contrario mede
    o texto exatamente como foi informado.
    """
    texto = (texto or "").strip()
    if not texto:
        return {"bytes": 0.0, "json_valido": None, "minificado": False, "bytes_original": 0.0}

    bytes_original = float(len(texto.encode("utf-8")))
    try:
        objeto = json.loads(texto)
    except ValueError:
        return {
            "bytes": bytes_original,
            "json_valido": False,
            "minificado": False,
            "bytes_original": bytes_original,
        }

    if not minificar:
        return {"bytes": bytes_original, "json_valido": True, "minificado": False, "bytes_original": bytes_original}

    compacto = json.dumps(objeto, separators=(",", ":"), ensure_ascii=False)
    return {
        "bytes": float(len(compacto.encode("utf-8"))),
        "json_valido": True,
        "minificado": True,
        "bytes_original": bytes_original,
    }


def tamanho_atributos_dynamodb(texto):
    """Tamanho de um item no DynamoDB: soma de nome + valor de cada atributo.

    O DynamoDB nao cobra a sintaxe do JSON, so os nomes e os valores dos atributos.
    Retorna None quando o texto nao e um objeto JSON valido.
    """
    try:
        objeto = json.loads((texto or "").strip())
    except ValueError:
        return None
    if not isinstance(objeto, dict):
        return None

    def tamanho_valor(valor):
        if isinstance(valor, bool) or valor is None:
            return 1
        if isinstance(valor, (int, float)):
            return len(("%g" % valor).replace("-", "").replace(".", "")) // 2 + 1
        if isinstance(valor, str):
            return len(valor.encode("utf-8"))
        if isinstance(valor, list):
            return 3 + sum(tamanho_valor(v) + 1 for v in valor)
        if isinstance(valor, dict):
            return 3 + sum(len(k.encode("utf-8")) + tamanho_valor(v) + 1 for k, v in valor.items())
        return 0

    return float(sum(len(k.encode("utf-8")) + tamanho_valor(v) for k, v in objeto.items()))


# ---------- precos: leitura de usagetype ----------

def usagetype_padrao(usagetype, sufixo, modificadores_ignorados=()):
    """True quando o usagetype e o do recurso padrao da regiao, sem variantes.

    O prefixo pode trazer a sigla da regiao ("SAE1-Fargate-GB-Hours") ou vir sem ela
    ("Fargate-GB-Hours"). Qualquer token extra antes do sufixo (Outposts, TS,
    Reserved...) indica uma variante que nao serve.
    """
    if not usagetype.endswith(sufixo):
        return False
    prefixo = usagetype[: -len(sufixo)].strip("-")
    if not prefixo:
        return True
    partes = prefixo.split("-")
    if len(partes) > 1:  # sigla da regiao + modificador
        return False
    return partes[0].upper() not in tuple(m.upper() for m in modificadores_ignorados)


# ---------- declaracao de campos ----------

def numero(nome, rotulo, passo=1, minimo=0, maximo=None, ajuda=None, visivel_quando=None):
    return {
        "nome": nome,
        "rotulo": rotulo,
        "tipo": "numero",
        "passo": passo,
        "minimo": minimo,
        "maximo": maximo,
        "ajuda": ajuda,
        "visivel_quando": visivel_quando,
    }


def escolha(nome, rotulo, opcoes, ajuda=None, visivel_quando=None):
    """opcoes: lista de (valor, rotulo)."""
    return {
        "nome": nome,
        "rotulo": rotulo,
        "tipo": "escolha",
        "opcoes": [{"valor": v, "rotulo": r} for v, r in opcoes],
        "ajuda": ajuda,
        "visivel_quando": visivel_quando,
    }


def texto_longo(nome, rotulo, linhas=6, ajuda=None, visivel_quando=None, medida=None):
    return {
        "nome": nome,
        "rotulo": rotulo,
        "tipo": "texto_longo",
        "linhas": linhas,
        "ajuda": ajuda,
        "visivel_quando": visivel_quando,
        "medida": medida,
    }


def booleano(nome, rotulo, ajuda=None, visivel_quando=None):
    return {
        "nome": nome,
        "rotulo": rotulo,
        "tipo": "booleano",
        "ajuda": ajuda,
        "visivel_quando": visivel_quando,
    }


def regiao(nome="region", rotulo="Região"):
    return {"nome": nome, "rotulo": rotulo, "tipo": "regiao"}


def grupo(titulo, campos):
    return {"titulo": titulo, "campos": campos}


def encontrar_campo(grupos, nome):
    for campo in todos_os_campos(grupos):
        if campo["nome"] == nome:
            return campo
    return None


def todos_os_campos(grupos):
    for g in grupos:
        for campo in g["campos"]:
            yield campo


# ---------- normalizacao ----------

def normalizar(grupos, defaults, recebidas):
    """Mescla as entradas recebidas sobre os defaults, convertendo pelo tipo declarado."""
    tipos = {c["nome"]: c for c in todos_os_campos(grupos)}
    entradas = dict(defaults)
    erros = []

    for chave, valor in (recebidas or {}).items():
        campo = tipos.get(chave)
        if campo is None or chave not in entradas:
            continue
        if campo["tipo"] == "numero":
            try:
                numero_valor = float(str(valor).replace(",", "."))
            except (TypeError, ValueError):
                erros.append("'%s' nao e um numero valido: %r" % (chave, valor))
                continue
            minimo = campo.get("minimo")
            maximo = campo.get("maximo")
            if minimo is not None and numero_valor < minimo:
                erros.append("'%s' deve ser >= %g." % (chave, minimo))
                continue
            if maximo is not None and numero_valor > maximo:
                erros.append("'%s' deve ser <= %g." % (chave, maximo))
                continue
            entradas[chave] = numero_valor
        elif campo["tipo"] == "booleano":
            entradas[chave] = valor if isinstance(valor, bool) else str(valor).lower() in ("1", "true", "on")
        elif campo["tipo"] == "escolha":
            validos = [o["valor"] for o in campo["opcoes"]]
            if valor not in validos:
                erros.append("'%s' deve ser um de: %s." % (chave, ", ".join(validos)))
                continue
            entradas[chave] = valor
        else:
            entradas[chave] = valor

    if erros:
        raise ValueError(" ".join(erros))
    return entradas


# ---------- precos escalonados ----------

def custo_escalonado(faixas, quantidade):
    """Aplica faixas de preco progressivas a uma quantidade.

    Retorna (total, detalhes), onde cada detalhe traz a parcela da quantidade que
    caiu naquela faixa e o subtotal correspondente.
    """
    total = 0.0
    detalhes = []
    for faixa in sorted(faixas, key=lambda f: f["inicio"]):
        inicio = faixa["inicio"]
        fim = faixa["fim"]
        if quantidade <= inicio:
            continue
        teto = quantidade if fim is None else min(quantidade, fim)
        parcela = max(0.0, teto - inicio)
        if parcela <= 0:
            continue
        subtotal = parcela * faixa["preco"]
        total += subtotal
        detalhes.append({
            "inicio": inicio,
            "fim": fim,
            "preco": faixa["preco"],
            "quantidade": parcela,
            "subtotal": subtotal,
        })
    return total, detalhes


def faixas_sem_gratuito(faixas):
    """Remove as faixas de preco zero (free tier), esticando a primeira faixa paga.

    O free tier do DynamoDB e por conta, nao por tabela: contar em cada recurso
    daria desconto repetido.
    """
    pagas = [f for f in sorted(faixas, key=lambda f: f["inicio"]) if f["preco"] > 0]
    if not pagas:
        return list(faixas)
    primeira = dict(pagas[0])
    primeira["inicio"] = 0.0
    return [primeira] + [dict(f) for f in pagas[1:]]


# ---------- secoes de resultado ----------

def celula(texto, num=False):
    return {"texto": texto, "num": num}


def linha(celulas, classe=None):
    return {"celulas": celulas, "classe": classe}


def tabela(titulo, colunas, linhas, explica=None):
    return {
        "tipo": "tabela",
        "titulo": titulo,
        "colunas": colunas,
        "linhas": linhas,
        "explica": explica,
    }


def lista(titulo, itens):
    return {"tipo": "lista", "titulo": titulo, "itens": itens}


def cartao(rotulo, valor, nota=None, destaque=False):
    return {"rotulo": rotulo, "valor": valor, "nota": nota, "destaque": destaque}
