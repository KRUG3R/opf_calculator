#!/usr/bin/env python3
"""Calculadora AWS - Aggregated Polling - servidor HTTP local (somente biblioteca padrao)."""

import argparse
import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import beneficios
import cambio
import investimento
import recursos
import roi
from recursos import base

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAX_BODY = 1024 * 1024


def carregar_config(caminho):
    with open(caminho, "r") as fp:
        return json.load(fp)


def salvar_entradas(caminho, recurso_id, entradas):
    """Persiste novos defaults de um recurso preservando o resto do arquivo."""
    config = carregar_config(caminho)
    config["recursos"][recurso_id]["entradas"].update(entradas)
    tmp = caminho + ".tmp"
    with open(tmp, "w") as fp:
        json.dump(config, fp, indent=2, ensure_ascii=False)
        fp.write("\n")
    os.replace(tmp, caminho)
    return config


def salvar_secao(caminho, secao, dados):
    """Persiste uma seção do config.json preservando o resto do arquivo."""
    config = carregar_config(caminho)
    config[secao] = dados
    tmp = caminho + ".tmp"
    with open(tmp, "w") as fp:
        json.dump(config, fp, indent=2, ensure_ascii=False)
        fp.write("\n")
    os.replace(tmp, caminho)
    return config


class Handler(BaseHTTPRequestHandler):
    server_version = "CalculadoraAWS/2.0"
    config_path = CONFIG_PATH

    def log_message(self, fmt, *args):
        print("[http] %s - %s" % (self.address_string(), fmt % args))

    # ---------- helpers ----------

    def _responder_json(self, payload, status=200):
        corpo = json.dumps(payload, ensure_ascii=False, default=float).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _ler_json(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0:
            return {}
        if tamanho > MAX_BODY:
            raise ValueError("Corpo da requisicao muito grande.")
        return json.loads(self.rfile.read(tamanho).decode("utf-8"))

    def _servir_estatico(self, caminho_rel):
        caminho = os.path.normpath(os.path.join(STATIC_DIR, caminho_rel))
        if not caminho.startswith(STATIC_DIR) or not os.path.isfile(caminho):
            self._responder_json({"erro": "Arquivo nao encontrado."}, 404)
            return
        tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
        with open(caminho, "rb") as fp:
            corpo = fp.read()
        self.send_response(200)
        self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _detalhe_recurso(self, config, recurso_id):
        """Metadados, campos e defaults de um recurso (sem calcular)."""
        modulo = recursos.obter(recurso_id)
        if modulo is None:
            planejado = recursos.obter_planejado(recurso_id)
            if planejado is None:
                return None
            return dict(planejado, campos=[], entradas={}, habilitado=False)
        recurso_cfg = recursos.config_do_recurso(config, recurso_id)
        return dict(
            recursos.meta(modulo),
            campos=modulo.CAMPOS,
            entradas=recurso_cfg["entradas"],
            habilitado=bool(recurso_cfg.get("habilitado", True)),
            regioes_disponiveis=config.get("regioes_disponiveis", []),
        )

    # ---------- rotas ----------

    def _rota(self):
        partes = [p for p in self.path.split("?")[0].split("/") if p]
        return partes

    def do_GET(self):
        partes = self._rota()
        caminho = "/" + "/".join(partes)

        if not partes:
            self._servir_estatico("index.html")
            return
        if partes[0] == "static":
            self._servir_estatico("/".join(partes[1:]))
            return

        try:
            config = carregar_config(self.config_path)

            if caminho == "/api/config":
                self._responder_json(config)
            elif caminho == "/api/resources":
                self._responder_json({
                    "recursos": recursos.listar(config),
                    "regioes_disponiveis": config.get("regioes_disponiveis", []),
                })
            elif caminho == "/api/investment":
                self._responder_json(investimento.calcular(config))
            elif caminho == "/api/benefits":
                self._responder_json(beneficios.calcular(config))
            elif caminho == "/api/overview":
                visao = recursos.visao_geral(config)
                visao["investimento"] = investimento.calcular(config)
                visao["beneficios"] = beneficios.calcular(config)
                visao["roi"] = roi.calcular(
                    despesa_mensal=visao["total_mes_brl"],
                    beneficio_mensal=visao["beneficios"]["beneficio_mes"],
                    investimento=visao["investimento"]["valor_total"],
                    meses_implantacao=visao["investimento"]["meses"],
                )
                self._responder_json(visao)
            elif caminho == "/api/totals":
                self._responder_json(recursos.calcular_totais(config))
            elif len(partes) == 3 and partes[:2] == ["api", "resources"]:
                detalhe = self._detalhe_recurso(config, partes[2])
                if detalhe is None:
                    self._responder_json({"erro": "Recurso nao encontrado."}, 404)
                else:
                    self._responder_json(detalhe)
            elif len(partes) == 4 and partes[:2] == ["api", "resources"] and partes[3] == "calculate":
                self._responder_json(recursos.calcular(config, partes[2]))
            else:
                self._responder_json({"erro": "Rota nao encontrada."}, 404)
        except (ValueError, KeyError, OSError) as exc:
            self._responder_json({"erro": str(exc)}, 400)

    def do_POST(self):
        partes = self._rota()
        try:
            corpo = self._ler_json()
        except ValueError as exc:
            self._responder_json({"erro": "JSON invalido: %s" % exc}, 400)
            return

        try:
            config = carregar_config(self.config_path)

            caminho = "/" + "/".join(partes)
            if caminho == "/api/totals":
                self._responder_json(recursos.calcular_totais(config, corpo))
            elif caminho == "/api/investment":
                # Prévia sem gravar: recalcula a lista recebida.
                config["investimento_inicial"] = investimento.normalizar(
                    corpo, config.get("investimento_inicial")
                )
                self._responder_json(investimento.calcular(config))
            elif caminho == "/api/benefits":
                # Prévia sem gravar.
                config["beneficios"] = beneficios.normalizar(corpo, config.get("beneficios"))
                self._responder_json(beneficios.calcular(config))
            elif caminho == "/api/exchange/save":
                cfg_cambio = dict(config.get("cambio") or cambio.PADROES)
                try:
                    valor = float(str(corpo.get("usd_brl")).replace(",", "."))
                except (TypeError, ValueError):
                    valor = 0
                if valor <= 0:
                    raise ValueError("'usd_brl' deve ser um número maior que zero.")
                cfg_cambio["usd_brl"] = valor
                # Um valor informado à mão passa a valer sobre a consulta automática.
                cfg_cambio["buscar_automatico"] = False
                salvar_secao(self.config_path, "cambio", cfg_cambio)
                self._responder_json({"ok": True, "usd_brl": valor})
            elif caminho == "/api/benefits/save":
                dados = beneficios.normalizar(corpo, config.get("beneficios"))
                salvar_secao(self.config_path, "beneficios", dados)
                config["beneficios"] = dados
                resultado = beneficios.calcular(config)
                resultado["ok"] = True
                self._responder_json(resultado)
            elif caminho == "/api/investment/save":
                dados = investimento.normalizar(corpo, config.get("investimento_inicial"))
                salvar_secao(self.config_path, "investimento_inicial", dados)
                config["investimento_inicial"] = dados
                resultado = investimento.calcular(config)
                resultado["ok"] = True
                self._responder_json(resultado)
            elif len(partes) == 4 and partes[:2] == ["api", "resources"]:
                recurso_id, acao = partes[2], partes[3]
                if acao == "calculate":
                    self._responder_json(recursos.calcular(config, recurso_id, corpo))
                elif acao == "defaults":
                    modulo = recursos.obter(recurso_id)
                    if modulo is None:
                        self._responder_json({"erro": "Recurso nao encontrado."}, 404)
                        return
                    recurso_cfg = recursos.config_do_recurso(config, recurso_id)
                    entradas = base.normalizar(modulo.CAMPOS, recurso_cfg["entradas"], corpo)
                    salvar_entradas(self.config_path, recurso_id, entradas)
                    self._responder_json({"ok": True, "entradas": entradas})
                else:
                    self._responder_json({"erro": "Rota nao encontrada."}, 404)
            else:
                self._responder_json({"erro": "Rota nao encontrada."}, 404)
        except (ValueError, KeyError, OSError) as exc:
            self._responder_json({"erro": str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser(description="Calculadora AWS - Aggregated Polling.")
    parser.add_argument("--config", default=CONFIG_PATH, help="caminho do arquivo de configuracao JSON")
    parser.add_argument("--port", type=int, help="sobrescreve a porta do arquivo de configuracao")
    parser.add_argument("--no-browser", action="store_true", help="nao abrir o navegador")
    args = parser.parse_args()

    config_path = os.path.abspath(args.config)
    config = carregar_config(config_path)
    srv_cfg = config.get("server", {})
    host = srv_cfg.get("host", "127.0.0.1")
    port = args.port or int(srv_cfg.get("port", 8777))

    Handler.config_path = config_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = "http://%s:%d/" % (host, port)
    print("Calculadora AWS - Aggregated Polling em %s" % url)
    print("Configuracao: %s" % config_path)
    print("Recursos: %s" % ", ".join(
        "%s (%s)" % (r["nome"], r["status"]) for r in recursos.listar(config)
    ))

    if srv_cfg.get("abrir_navegador", True) and not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
