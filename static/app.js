const app = document.getElementById("app");
const nav = document.getElementById("nav");

let catalogo = { recursos: [], regioes_disponiveis: [] };
let debounce = null;

// ---------- helpers de DOM ----------

function el(tag, attrs = {}, filhos = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "texto") node.textContent = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const filho of [].concat(filhos)) {
    if (filho) node.appendChild(filho);
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, filhos = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "texto") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const filho of [].concat(filhos)) if (filho) node.appendChild(filho);
  return node;
}

function secaoTitulo(texto) {
  return el("h2", { texto });
}

async function api(rota, corpo) {
  const opcoes = corpo
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) }
    : {};
  const resp = await fetch(rota, opcoes);
  const dados = await resp.json().catch(() => ({ erro: "Resposta inválida do servidor." }));
  if (!resp.ok || dados.erro) throw new Error(dados.erro || "Falha na requisição.");
  return dados;
}

function caixaErro(msg) {
  return el("div", { class: "erro", texto: msg });
}

function caixaAvisos(avisos) {
  if (!avisos || !avisos.length) return null;
  return el("div", { class: "aviso", texto: avisos.join(" ") });
}

// ---------- navegação ----------

// As telas de recurso não entram no menu: chega-se a elas pelos cartões da
// calculadora e pelos links da proposta. Estando numa delas, "Calculadora" fica ativa.
const MENU = [
  { id: "proposta", rotulo: "Proposta", href: "#/" },
  { id: "calculadora", rotulo: "Calculadora", href: "#/totais" },
  { id: "investimento", rotulo: "Investimento", href: "#/investimento" },
  { id: "beneficios", rotulo: "Benefícios", href: "#/beneficios" },
  { id: "slots", rotulo: "Slots", href: "#/slots" },
];

function renderNav(ativo) {
  const secao = MENU.some((item) => item.id === ativo) ? ativo : "calculadora";
  nav.innerHTML = "";
  for (const item of MENU) {
    nav.appendChild(
      el("a", {
        href: item.href,
        class: "nav-item" + (item.id === secao ? " atual" : ""),
        texto: item.rotulo,
      })
    );
  }
}

// ---------- tela de proposta (one-page) ----------

async function telaProposta() {
  renderNav("proposta");
  app.className = "";
  app.innerHTML = "";
  app.appendChild(el("p", { class: "carregando", texto: "Calculando…" }));

  let v;
  try {
    v = await api("/api/overview");
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(caixaErro(String(e.message || e)));
    return;
  }

  app.innerHTML = "";

  const inv = v.investimento;
  const itens = [
    el("a", { class: "item-proposta", href: "#/totais", title: "Abrir a calculadora" }, [
      el("span", { class: "rotulo", texto: "Despesas mensais com infraestrutura" }),
      el("strong", { texto: v.total_mes_brl_fmt }),
      el("span", {
        class: "nota",
        texto: `${v.total_ano_brl_fmt}/ano · ${v.total_mes_fmt} a ${v.cambio.valor_fmt}`,
      }),
      el("span", { class: "chamada", texto: "Abrir a calculadora →" }),
    ]),
  ];

  if (inv) {
    itens.push(
      el(
        "a",
        { class: "item-proposta secundario", href: "#/investimento", title: "Abrir o investimento inicial" },
        [
          el("span", { class: "rotulo", texto: "Investimento inicial" }),
          el("strong", { texto: inv.valor_total_fmt }),
          el("span", {
            class: "nota",
            texto: `${inv.total_horas_fmt} × ${inv.valor_hora_fmt}/h · ${inv.itens.length} atividades`,
          }),
          el("span", { class: "chamada", texto: "Abrir o investimento →" }),
        ]
      ),
      el(
        "a",
        { class: "item-proposta secundario", href: "#/investimento", title: "Abrir o investimento inicial" },
        [
          el("span", { class: "rotulo", texto: "Duração da implantação" }),
          el("strong", { texto: inv.meses + (inv.meses === 1 ? " mês" : " meses") }),
          el("span", {
            class: "nota",
            texto: `${inv.duracao_dias_fmt} · ${inv.dias_uteis_por_mes} dias úteis/mês`,
          }),
          el("span", { class: "chamada", texto: "Abrir o cronograma →" }),
        ]
      )
    );
  }

  const ben = v.beneficios;
  if (ben) {
    itens.splice(
      1,
      0,
      el(
        "a",
        { class: "item-proposta beneficio", href: "#/beneficios", title: "Abrir os benefícios" },
        [
          el("span", { class: "rotulo", texto: "Benefício mensal esperado" }),
          el("strong", { texto: ben.beneficio_mes_fmt }),
          el("span", {
            class: "nota",
            texto: `${ben.beneficio_ano_fmt}/ano · ${ben.percentual_reducao}% de redução`,
          }),
          el("span", { class: "chamada", texto: "Abrir os benefícios →" }),
        ]
      )
    );
  }

  app.appendChild(el("div", { class: "proposta" }, itens));

  // Parâmetros que sustentam os números acima, antes do gráfico. Os rótulos aqui
  // são os da proposta, mais curtos que os das telas de cálculo.
  const ROTULOS_PROPOSTA = { response_time_s: "SLA", tpm_por_if: "TPM" };
  const destaques = ["registros_por_pagina", "response_time_s", "tpm_por_if", "slot_variavel"]
    .map((id) => (v.indicadores || []).find((i) => i.id === id))
    .filter(Boolean)
    .map((i) => Object.assign({}, i, { rotulo: ROTULOS_PROPOSTA[i.id] || i.rotulo }));

  if (destaques.length) {
    app.appendChild(
      el("section", { class: "painel volumetria" }, [
        secaoTitulo("Premissas"),
        el(
          "div",
          { class: "indicadores" },
          destaques.map((i) =>
            el(
              "a",
              {
                class: "indicador",
                href: i.href || "#/totais",
                title: i.href ? "Ver o comparativo de slots" : "Ajustar na calculadora",
              },
              [
                el("span", { class: "rotulo", texto: i.rotulo }),
                el("strong", { texto: i.valor }),
                i.nota ? el("span", { class: "nota", texto: i.nota }) : null,
              ]
            )
          )
        ),
      ])
    );
  }

  if (v.roi) app.appendChild(renderRoi(v.roi));
}

/** Fluxo de caixa acumulado: área divergente em torno do zero, com o payback marcado. */
function renderRoi(r) {
  const L = 76, R = 34, T = 16, B = 30;
  const W = 1000, H = 250;
  const area = { x0: L, x1: W - R, y0: T, y1: H - B };

  const valores = r.serie.map((p) => p.acumulado);
  const maxAbsAcima = Math.max(0, ...valores);
  const minAbaixo = Math.min(0, ...valores);
  const faixa = maxAbsAcima - minAbaixo || 1;
  const n = r.serie.length;

  const x = (mes) => area.x0 + ((mes - 1) / Math.max(n - 1, 1)) * (area.x1 - area.x0);
  const y = (valor) => area.y1 - ((valor - minAbaixo) / faixa) * (area.y1 - area.y0);
  const yZero = y(0);

  const pontos = r.serie.map((p) => `${x(p.mes).toFixed(1)},${y(p.acumulado).toFixed(1)}`);
  const linha = "M" + pontos.join("L");
  const areaPath = `${linha}L${x(n).toFixed(1)},${yZero.toFixed(1)}L${x(1).toFixed(1)},${yZero.toFixed(1)}Z`;

  const fmtEixo = (valor) =>
    (valor / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + "k";

  const marcasX = [];
  const passo = n > 24 ? 6 : n > 12 ? 3 : 2;
  for (let mes = 1; mes <= n; mes += passo) marcasX.push(mes);
  if (marcasX[marcasX.length - 1] !== n) marcasX.push(n);

  const grafico = svg(
    "svg",
    {
      viewBox: `0 0 ${W} ${H}`,
      class: "grafico-roi",
      role: "img",
      "aria-label": `Fluxo de caixa acumulado ao longo de ${n} meses; payback em ${r.payback_fmt}`,
    },
    [
      svg("defs", {}, [
        svg("clipPath", { id: "roi-acima" }, [
          svg("rect", { x: area.x0, y: area.y0, width: area.x1 - area.x0, height: Math.max(yZero - area.y0, 0) }),
        ]),
        svg("clipPath", { id: "roi-abaixo" }, [
          svg("rect", { x: area.x0, y: yZero, width: area.x1 - area.x0, height: Math.max(area.y1 - yZero, 0) }),
        ]),
      ]),
      // faixa da implantação
      r.meses_implantacao
        ? svg("rect", {
            x: area.x0,
            y: area.y0,
            width: Math.max(x(r.meses_implantacao) - area.x0, 0),
            height: area.y1 - area.y0,
            class: "roi-fase",
          })
        : null,
      svg("path", { d: areaPath, class: "roi-area negativa", "clip-path": "url(#roi-abaixo)" }),
      svg("path", { d: areaPath, class: "roi-area positiva", "clip-path": "url(#roi-acima)" }),
      svg("line", { x1: area.x0, x2: area.x1, y1: yZero, y2: yZero, class: "roi-zero" }),
      svg("path", { d: linha, class: "roi-linha" }),
      r.payback_mes
        ? svg("g", {}, [
            svg("line", {
              x1: x(r.payback_mes),
              x2: x(r.payback_mes),
              y1: area.y0,
              y2: area.y1,
              class: "roi-payback-linha",
            }),
            svg("circle", { cx: x(r.payback_mes), cy: yZero, r: 5, class: "roi-payback-ponto" }),
          ])
        : null,
      // eixos
      svg("text", { x: area.x0 - 8, y: y(maxAbsAcima) + 4, class: "roi-eixo", "text-anchor": "end", texto: fmtEixo(maxAbsAcima) }),
      svg("text", { x: area.x0 - 8, y: yZero + 4, class: "roi-eixo", "text-anchor": "end", texto: "0" }),
      svg("text", { x: area.x0 - 8, y: y(minAbaixo) + 4, class: "roi-eixo", "text-anchor": "end", texto: fmtEixo(minAbaixo) }),
      ...marcasX.map((mes, i) =>
        svg("text", {
          x: x(mes),
          y: H - 10,
          class: "roi-eixo",
          "text-anchor": i === marcasX.length - 1 ? "end" : "middle",
          texto: "Mês " + mes,
        })
      ),
      // alvos de hover, um por mês
      ...r.serie.map((p) =>
        svg("rect", {
          x: x(p.mes) - (area.x1 - area.x0) / (2 * Math.max(n - 1, 1)),
          y: area.y0,
          width: (area.x1 - area.x0) / Math.max(n - 1, 1),
          height: area.y1 - area.y0,
          class: "roi-alvo",
        }, [svg("title", { texto: `Mês ${p.mes} (${p.fase}): ${p.acumulado_fmt}` })])
      ),
    ]
  );

  return el("section", { class: "painel roi" }, [
    el("div", { class: "roi-grafico" }, [
      secaoTitulo("Retorno do investimento"),
      el("p", {
        class: "explica",
        texto: `Caixa acumulado: ${r.meses_implantacao} meses de implantação desembolsando ${r.investimento_fmt}, depois ${r.resultado_mensal_fmt}/mês de resultado (benefício ${r.beneficio_mensal_fmt} − infraestrutura ${r.despesa_mensal_fmt}).`,
      }),
      grafico,
      el("div", { class: "legenda" }, [
        el("span", {}, [el("i", { class: "swatch negativo" }), el("span", { texto: "Caixa negativo" })]),
        el("span", {}, [el("i", { class: "swatch positivo" }), el("span", { texto: "Caixa positivo" })]),
        el("span", {}, [el("i", { class: "swatch fase" }), el("span", { texto: "Implantação" })]),
      ]),
    ]),
    el("a", { class: "roi-payback", href: "#/investimento", title: "Abrir o investimento inicial" }, [
      el("span", { class: "rotulo", texto: "Payback" }),
      el("strong", { texto: r.payback_meses_fmt }),
      el("span", { class: "unidade", texto: r.payback_mes ? "meses" : r.payback_fmt }),
      el("span", { class: "nota", texto: r.payback_mes ? r.payback_apos_golive_fmt : "" }),
      el("div", { class: "roi-detalhes" }, [
        el("div", {}, [el("span", { texto: "Resultado mensal" }), el("b", { texto: r.resultado_mensal_fmt })]),
        el("div", {}, [
          el("span", { texto: `Caixa em ${r.horizonte} meses` }),
          el("b", { texto: r.acumulado_final_fmt }),
        ]),
        el("div", {}, [
          el("span", { texto: "ROI no período" }),
          el("b", { texto: r.roi_percentual.toFixed(0) + "%" }),
        ]),
      ]),
    ]),
  ]);
}

// ---------- tela de totais ----------

// Ajustes feitos no painel de simulação, por recurso: {alb: {tpm_por_if: 5000}}.
let simulacao = {};

function fmtMoeda(v, moeda = "USD") {
  const simbolo = moeda === "USD" ? "US$" : moeda;
  return `${simbolo} ${Math.abs(v).toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function delta(atual, base, moeda) {
  if (base === undefined || base === null) return null;
  const diff = atual - base;
  if (Math.abs(diff) < 0.005) return el("span", { class: "delta igual", texto: "sem mudança" });
  const pct = base ? (diff / base) * 100 : 0;
  const sinal = diff > 0 ? "+" : "−";
  return el("span", {
    class: "delta " + (diff > 0 ? "sobe" : "desce"),
    texto: `${sinal}${fmtMoeda(diff, moeda)} (${sinal}${Math.abs(pct).toFixed(1)}%)`,
  });
}

async function telaTotais() {
  renderNav("calculadora");
  app.className = "";
  app.innerHTML = "";

  const painel = el("section", { class: "painel largo" });
  const areaSimulacao = el("div", { id: "simulacao" });
  const areaResultados = el("div", { id: "resultados" }, [
    el("p", { class: "carregando", texto: "Calculando…" }),
  ]);
  painel.appendChild(areaSimulacao);
  painel.appendChild(areaResultados);
  app.appendChild(painel);

  let debounceTotais = null;
  let controlesMontados = false;

  async function atualizar() {
    const statusEl = document.getElementById("status-simulacao");
    if (statusEl) statusEl.textContent = "Calculando…";
    let t;
    try {
      t = await api("/api/totals", Object.keys(simulacao).length ? simulacao : undefined);
    } catch (e) {
      areaResultados.innerHTML = "";
      areaResultados.appendChild(caixaErro(String(e.message || e)));
      return;
    }
    if (!controlesMontados) {
      montarControles(t);
      controlesMontados = true;
    }
    renderResultados(t);
    const status = document.getElementById("status-simulacao");
    if (status) {
      status.textContent = t.baseline
        ? "Simulação ativa — os valores do config.json não foram alterados."
        : "Valores do config.json.";
    }
  }

  function montarControles(t) {
    areaSimulacao.innerHTML = "";
    if (!t.controles || !t.controles.length) return;

    areaSimulacao.appendChild(secaoTitulo("Simulação"));

    const form = el("form", { class: "controles", id: "form-simulacao" });
    for (const controle of t.controles) {
      const label = renderCampo(controle.campo);
      const entrada = label.querySelector("input, select, textarea");
      entrada.dataset.recurso = controle.recurso;
      entrada.value = controle.valor;
      label.appendChild(
        el("small", { texto: `${controle.recurso_nome} · ${controle.campo.nome}` })
      );
      form.appendChild(label);
    }
    areaSimulacao.appendChild(form);

    areaSimulacao.appendChild(
      el("div", { class: "acoes" }, [
        el("button", {
          type: "button",
          class: "secundario",
          texto: "Salvar nos recursos",
          onclick: salvarSimulacao,
        }),
        el("button", {
          type: "button",
          class: "secundario",
          texto: "Restaurar do config.json",
          onclick: () => {
            simulacao = {};
            for (const campo of form.elements) {
              const controle = t.controles.find((c) => c.campo.nome === campo.name);
              if (controle) campo.value = controle.valor;
            }
            atualizar();
          },
        }),
        el("span", { class: "status", id: "status-simulacao" }),
      ])
    );

    form.addEventListener("input", () => {
      simulacao = {};
      for (const campo of form.elements) {
        if (!campo.name || !campo.dataset.recurso) continue;
        const recurso = campo.dataset.recurso;
        const valor = campo.type === "number" ? Number(campo.value) : campo.value;
        if (recurso === "cambio") {
          simulacao.cambio = valor; // cotação, não é um recurso
          continue;
        }
        simulacao[recurso] = simulacao[recurso] || {};
        simulacao[recurso][campo.name] = valor;
      }
      clearTimeout(debounceTotais);
      const status = document.getElementById("status-simulacao");
      if (status) status.textContent = "…";
      debounceTotais = setTimeout(atualizar, 350);
    });
  }

  async function salvarSimulacao() {
    const status = document.getElementById("status-simulacao");
    try {
      for (const [recurso, entradas] of Object.entries(simulacao)) {
        if (recurso === "cambio") {
          await api("/api/exchange/save", { usd_brl: entradas });
          continue;
        }
        await api("/api/resources/" + encodeURIComponent(recurso) + "/defaults", entradas);
      }
      simulacao = {};
      controlesMontados = false; // remonta com os novos valores gravados
      await atualizar();
      const novoStatus = document.getElementById("status-simulacao");
      if (novoStatus) novoStatus.textContent = "Valores gravados em config.json.";
    } catch (e) {
      areaResultados.prepend(caixaErro(String(e.message || e)));
    }
  }

  function renderResultados(t) {
    areaResultados.innerHTML = "";
    const base = t.baseline;

    const avisos = caixaAvisos(t.avisos);
    if (avisos) areaResultados.appendChild(avisos);

    areaResultados.appendChild(
      el("div", { class: "cartoes" }, [
        el("div", { class: "cartao destaque" }, [
          el("span", { class: "rotulo", texto: "Custo mensal total" }),
          el("strong", { texto: t.total_mes_fmt }),
          el("span", { class: "nota", texto: `${t.total_hora_fmt}/h · ${t.total_ano_fmt}/ano` }),
          base
            ? el("div", { class: "comparacao" }, [
                el("span", { class: "nota", texto: `config: ${base.total_mes_fmt}` }),
                delta(t.total_mes, base.total_mes, t.moeda),
              ])
            : null,
        ]),
        el("div", { class: "cartao" }, [
          el("span", { class: "rotulo", texto: "Custo mensal em reais" }),
          el("strong", { texto: t.total_mes_brl_fmt }),
          el("span", {
            class: "nota",
            texto: `${t.total_ano_brl_fmt}/ano · dólar a ${t.cambio.valor_fmt}`,
          }),
          el("span", { class: "nota fonte-cambio", texto: t.cambio.fonte }),
        ]),
      ])
    );

    areaResultados.appendChild(secaoTitulo("Recursos"));
    areaResultados.appendChild(
      el("p", { class: "explica", texto: "Clique em um recurso para abrir sua tela e ajustar os parâmetros." })
    );

    const grade = el("div", { class: "grade-recursos" });
    for (const r of t.recursos) {
      const planejado = r.status === "planejado";
      const erro = r.status === "erro";
      const baseRecurso = base ? base.por_recurso[r.id] : undefined;
      grade.appendChild(
        el(
          planejado ? "div" : "a",
          {
            class: "cartao-recurso" + (planejado ? " futuro" : "") + (erro ? " com-erro" : ""),
            href: planejado ? null : "#/recurso/" + r.id,
          },
          [
            el("div", { class: "cabeca" }, [
              el("strong", { texto: r.nome }),
              el("span", {
                class: "selo " + r.status,
                texto: { ativo: "ativo", planejado: "planejado", desabilitado: "desabilitado", erro: "erro" }[r.status],
              }),
            ]),
            el("p", { class: "descricao", texto: r.descricao || "" }),
            erro ? el("p", { class: "erro-inline", texto: r.erro }) : null,
            r.total_mes_fmt
              ? el("div", { class: "valor" }, [
                  el("strong", { texto: r.total_mes_fmt }),
                  el("span", { class: "nota", texto: "/mês" + (r.region ? " · " + r.region : "") }),
                ])
              : el("div", { class: "valor vazio", texto: planejado ? "modelo a definir" : "—" }),
            baseRecurso !== undefined ? delta(r.total_mes, baseRecurso, t.moeda) : null,
            r.resumo && r.resumo.length
              ? el(
                  "ul",
                  { class: "mini-resumo" },
                  r.resumo.map((i) => el("li", {}, [el("span", { texto: i.rotulo }), el("b", { texto: i.valor })]))
                )
              : null,
            r.participacao
              ? el("div", { class: "barra", title: (r.participacao * 100).toFixed(1) + "% do total" }, [
                  el("span", { style: `width:${(r.participacao * 100).toFixed(1)}%` }),
                ])
              : null,
          ]
        )
      );
    }
    areaResultados.appendChild(grade);

    const ativos = t.recursos.filter((r) => r.total_mes_fmt);
    if (ativos.length) {
      areaResultados.appendChild(secaoTitulo("Comparativo"));
      const colunas = [
        el("th", { texto: "Recurso" }),
        el("th", { texto: "Região" }),
        el("th", { texto: "Custo mês" }),
        el("th", { texto: "Em reais" }),
        el("th", { texto: "Participação" }),
      ];
      if (base) colunas.push(el("th", { texto: "vs. config" }));

      const tbody = el("tbody");
      for (const r of ativos) {
        const celulas = [
          el("td", {}, [el("a", { href: "#/recurso/" + r.id, texto: r.nome })]),
          el("td", { texto: r.region || "—" }),
          el("td", { class: "num", texto: r.total_mes_fmt }),
          el("td", { class: "num", texto: r.total_mes_brl_fmt || "—" }),
          el("td", { class: "num", texto: (r.participacao * 100).toFixed(1) + "%" }),
        ];
        if (base) {
          const d = delta(r.total_mes, base.por_recurso[r.id], t.moeda);
          celulas.push(el("td", { class: "num" }, [d]));
        }
        tbody.appendChild(el("tr", {}, celulas));
      }
      const totalCelulas = [
        el("td", { texto: "Total" }),
        el("td", { texto: "" }),
        el("td", { class: "num", texto: t.total_mes_fmt }),
        el("td", { class: "num", texto: t.total_mes_brl_fmt }),
        el("td", { class: "num", texto: "100,0%" }),
      ];
      if (base) totalCelulas.push(el("td", { class: "num" }, [delta(t.total_mes, base.total_mes, t.moeda)]));
      tbody.appendChild(el("tr", { class: "total" }, totalCelulas));

      areaResultados.appendChild(
        el("table", { class: "tabela larga" }, [el("thead", {}, [el("tr", {}, colunas)]), tbody])
      );
    }
  }

  atualizar();
}

// ---------- tela de investimento inicial ----------

async function telaInvestimento() {
  renderNav("investimento");
  app.className = "";
  app.innerHTML = "";
  app.appendChild(el("p", { class: "carregando", texto: "Carregando…" }));

  let dados;
  try {
    dados = await api("/api/investment");
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(caixaErro(String(e.message || e)));
    return;
  }

  let estado = {
    valor_hora: dados.valor_hora,
    dias_uteis_por_mes: dados.dias_uteis_por_mes,
    itens: dados.itens.map((i) => ({
      id: i.id,
      comunidade: i.comunidade,
      atividade: i.atividade,
      horas: i.horas,
      dias_uteis: i.dias_uteis,
      predecessora: i.predecessora,
    })),
  };
  let debounceInv = null;
  let proximoId = estado.itens.length + 1;

  app.innerHTML = "";
  const painelLista = el("section", { class: "painel" });
  const painelResultado = el("section", { class: "painel" });
  app.appendChild(painelLista);
  app.appendChild(painelResultado);

  function novoId() {
    let candidato;
    do {
      candidato = "a" + proximoId++;
    } while (estado.itens.some((i) => i.id === candidato));
    return candidato;
  }

  function agendarCalculo() {
    clearTimeout(debounceInv);
    debounceInv = setTimeout(calcular, 350);
  }

  async function calcular(resultadoPronto) {
    let d = resultadoPronto;
    if (!d) {
      try {
        d = await api("/api/investment", estado);
      } catch (e) {
        painelResultado.innerHTML = "";
        painelResultado.appendChild(caixaErro(String(e.message || e)));
        return;
      }
    }
    renderResultado(d);
    atualizarLinhas(d);
  }

  // --- lista editável ---

  function renderLista() {
    painelLista.innerHTML = "";
    painelLista.appendChild(el("h2", { class: "titulo-recurso", texto: "Investimento inicial" }));

    painelLista.appendChild(
      el("div", { class: "parametros-inv" }, [
        el("label", {}, [
          el("span", { class: "rotulo-campo", texto: "Valor da hora (R$)" }),
          el("input", {
            type: "number",
            step: "0.01",
            min: "0",
            value: estado.valor_hora,
            oninput: (ev) => {
              estado.valor_hora = Number(ev.target.value);
              agendarCalculo();
            },
          }),
        ]),
        el("label", {}, [
          el("span", { class: "rotulo-campo", texto: "Dias úteis por mês" }),
          el("input", {
            type: "number",
            step: "1",
            min: "1",
            value: estado.dias_uteis_por_mes,
            oninput: (ev) => {
              estado.dias_uteis_por_mes = Number(ev.target.value);
              agendarCalculo();
            },
          }),
        ]),
      ])
    );

    const corpo = el("tbody", { id: "corpo-itens" });
    estado.itens.forEach((item, indice) => corpo.appendChild(renderLinha(item, indice)));

    painelLista.appendChild(
      el("div", { class: "rolagem" }, [
        el("table", { class: "tabela larga tabela-itens" }, [
          el("thead", {}, [
            el("tr", {}, [
              el("th", { texto: "Comunidade" }),
              el("th", { texto: "Atividade" }),
              el("th", { texto: "Horas" }),
              el("th", { texto: "Dias úteis" }),
              el("th", { texto: "Predecessora" }),
              el("th", { class: "num", texto: "Custo" }),
              el("th", { texto: "" }),
            ]),
          ]),
          corpo,
        ]),
      ])
    );

    painelLista.appendChild(
      el("div", { class: "acoes" }, [
        el("button", {
          type: "button",
          texto: "Adicionar atividade",
          onclick: () => {
            estado.itens.push({
              id: novoId(),
              comunidade: "",
              atividade: "",
              horas: 0,
              dias_uteis: 0,
              predecessora: "",
            });
            renderLista();
            calcular();
          },
        }),
        el("button", { type: "button", class: "secundario", texto: "Salvar", onclick: salvar }),
        el("button", {
          type: "button",
          class: "secundario",
          texto: "Restaurar do config.json",
          onclick: () => telaInvestimento(),
        }),
        el("span", { class: "status", id: "status-inv" }),
      ])
    );
  }

  function renderLinha(item, indice) {
    const campo = (chave, tipo, extra = {}) =>
      el("input", Object.assign({
        type: tipo,
        value: item[chave],
        oninput: (ev) => {
          item[chave] = tipo === "number" ? Number(ev.target.value) : ev.target.value;
          agendarCalculo();
        },
      }, extra));

    const select = el("select", {
      dataset: { predecessora: item.id },
      onchange: (ev) => {
        item.predecessora = ev.target.value;
        agendarCalculo();
      },
    });
    preencherPredecessoras(select, item);

    return el("tr", { dataset: { linha: item.id } }, [
      el("td", {}, [campo("comunidade", "text")]),
      el("td", {}, [campo("atividade", "text")]),
      el("td", {}, [campo("horas", "number", { step: "1", min: "0" })]),
      el("td", {}, [campo("dias_uteis", "number", { step: "1", min: "0" })]),
      el("td", {}, [select]),
      el("td", { class: "num custo-linha", texto: "—" }),
      el("td", {}, [
        el("button", {
          type: "button",
          class: "remover",
          title: "Remover atividade",
          texto: "×",
          onclick: () => {
            estado.itens.splice(indice, 1);
            for (const outro of estado.itens) {
              if (outro.predecessora === item.id) outro.predecessora = "";
            }
            renderLista();
            calcular();
          },
        }),
      ]),
    ]);
  }

  function preencherPredecessoras(select, item) {
    const atual = item.predecessora;
    select.innerHTML = "";
    select.appendChild(el("option", { value: "", texto: "— nenhuma —" }));
    for (const outro of estado.itens) {
      if (outro.id === item.id) continue;
      select.appendChild(
        el("option", { value: outro.id, texto: outro.atividade || outro.id })
      );
    }
    select.value = atual;
  }

  function atualizarLinhas(d) {
    for (const calculado of d.itens) {
      const linha = painelLista.querySelector(`[data-linha="${calculado.id}"]`);
      if (!linha) continue;
      const celula = linha.querySelector(".custo-linha");
      if (celula) celula.textContent = calculado.custo_fmt;
    }
    // os rótulos das predecessoras acompanham o nome das atividades
    for (const item of estado.itens) {
      const select = painelLista.querySelector(`[data-predecessora="${item.id}"]`);
      if (select) preencherPredecessoras(select, item);
    }
  }

  async function salvar() {
    const status = document.getElementById("status-inv");
    try {
      const d = await api("/api/investment/save", estado);
      calcular(d);
      if (status) status.textContent = "Gravado em config.json.";
    } catch (e) {
      painelResultado.prepend(caixaErro(String(e.message || e)));
    }
  }

  // --- resultado: cartões, gantt e comunidades ---

  function renderResultado(d) {
    painelResultado.innerHTML = "";

    const avisos = caixaAvisos(d.avisos);
    if (avisos) painelResultado.appendChild(avisos);

    painelResultado.appendChild(
      el("div", { class: "cartoes" }, [
        el("div", { class: "cartao destaque" }, [
          el("span", { class: "rotulo", texto: "Investimento inicial" }),
          el("strong", { texto: d.valor_total_fmt }),
          el("span", { class: "nota", texto: `${d.total_horas_fmt} × ${d.valor_hora_fmt}/h` }),
        ]),
        el("div", { class: "cartao" }, [
          el("span", { class: "rotulo", texto: "Esforço total" }),
          el("strong", { texto: d.total_horas_fmt }),
          el("span", { class: "nota", texto: `${d.itens.length} atividades` }),
        ]),
        el("div", { class: "cartao" }, [
          el("span", { class: "rotulo", texto: "Duração" }),
          el("strong", { texto: d.meses + (d.meses === 1 ? " mês" : " meses") }),
          el("span", {
            class: "nota",
            texto: `${d.duracao_dias_fmt} · ${d.dias_uteis_por_mes} dias úteis/mês`,
          }),
        ]),
      ])
    );

    painelResultado.appendChild(secaoTitulo("Cronograma"));
    painelResultado.appendChild(renderGantt(d));

    if (d.comunidades.length) {
      painelResultado.appendChild(secaoTitulo("Esforço por comunidade"));
      painelResultado.appendChild(
        el(
          "div",
          { class: "barras" },
          d.comunidades.map((c) =>
            el("div", { class: "barra-linha" }, [
              el("span", { class: "barra-nome", texto: c.comunidade }),
              el("span", { class: "barra-trilha" }, [
                el("span", {
                  class: "barra-preenchimento",
                  style: `width:${Math.max(c.proporcao_da_maior * 100, 0.6).toFixed(1)}%`,
                }),
              ]),
              el("span", { class: "barra-valor", texto: c.custo_fmt }),
              el("span", { class: "barra-pct", texto: c.horas_fmt }),
            ])
          )
        )
      );
    }
  }

  function renderGantt(d) {
    if (!d.itens.length || !d.duracao_dias) {
      return el("p", { class: "explica", texto: "Informe dias úteis nas atividades para ver o cronograma." });
    }
    const totalDias = d.meses * d.dias_uteis_por_mes;
    const larguraMes = 100 / d.meses;

    const cabecalho = el(
      "div",
      { class: "gantt-meses" },
      Array.from({ length: d.meses }, (_, i) =>
        el("span", { class: "gantt-mes", style: `width:${larguraMes}%`, texto: "Mês " + (i + 1) })
      )
    );

    const linhas = d.itens.map((item) => {
      const esquerda = (item.inicio / totalDias) * 100;
      const largura = Math.max((item.dias_uteis / totalDias) * 100, 0.8);
      return el("div", { class: "gantt-linha" }, [
        el("span", { class: "gantt-rotulo", texto: item.atividade || item.id }),
        el("span", { class: "gantt-trilha", style: `--meses:${d.meses}` }, [
          el("span", {
            class: "gantt-barra",
            style: `left:${esquerda.toFixed(2)}%;width:${largura.toFixed(2)}%`,
            title: `${item.atividade || item.id}: dia ${Math.round(item.inicio) + 1} ao ${Math.round(item.fim)} · ${item.dias_uteis} dias úteis`,
          }, [
            el("span", { class: "gantt-dias", texto: Math.round(item.dias_uteis) + "d" }),
          ]),
        ]),
      ]);
    });

    return el("div", { class: "gantt" }, [
      el("div", { class: "gantt-linha cabecalho" }, [
        el("span", { class: "gantt-rotulo", texto: "" }),
        cabecalho,
      ]),
      ...linhas,
    ]);
  }

  renderLista();
  calcular(dados);
}

// ---------- tela de slots: fixos x variáveis ----------

const SLOTS_ATALHOS = [
  { minutos: 5, rotulo: "5 min" },
  { minutos: 15, rotulo: "15 min" },
  { minutos: 60, rotulo: "1 hora" },
  { minutos: 1440, rotulo: "24 horas" },
];

function telaSlots() {
  renderNav("slots");
  app.className = "";
  app.innerHTML = "";

  let janela = 5;
  const pagina = el("div", { class: "slots" });
  app.appendChild(pagina);

  // --- o que é um slot ---
  pagina.appendChild(
    el("section", { class: "painel" }, [
      el("h2", { class: "titulo-recurso", texto: "Slot fixo × slot variável" }),
      el("p", {
        class: "intro",
        texto:
          "No Aggregated Polling, um slot é a janela de tempo usada para identificar quais recursos mudaram. A diferença entre os dois modelos está em quem define essa janela: no modelo fixo ela é predefinida pelo transmissor; no variável, o consumidor pede os últimos N minutos a partir de agora.",
      }),
      el("div", { class: "comparativo-modelos" }, [
        el("div", { class: "modelo fixo" }, [
          el("span", { class: "selo-modelo", texto: "Slot fixo" }),
          el("p", { texto: "Janelas predefinidas, gerenciadas uma a uma pelo transmissor." }),
          el("code", { texto: "GET /aggregated-polling/v1/balances  → percorrer slot a slot" }),
        ]),
        el("div", { class: "modelo variavel" }, [
          el("span", { class: "selo-modelo", texto: "Slot variável" }),
          el("p", { texto: "Janela relativa ao momento da consulta, definida pelo consumidor." }),
          el("code", { texto: "GET /aggregated-polling/v1/balances?slots=5" }),
        ]),
      ]),
    ])
  );

  // --- simulador de janela ---
  const painelJanela = el("section", { class: "painel" });
  pagina.appendChild(painelJanela);

  function renderJanela() {
    painelJanela.innerHTML = "";
    painelJanela.appendChild(secaoTitulo("Quantas requisições para cobrir a janela"));
    painelJanela.appendChild(
      el("p", { class: "explica", texto: "Com slots de 1 minuto, cada minuto da janela é um slot a percorrer." })
    );

    painelJanela.appendChild(
      el("div", { class: "janela-controles" }, [
        el("label", {}, [
          el("span", { class: "rotulo-campo", texto: "Janela consultada (minutos)" }),
          el("input", {
            type: "number",
            min: "1",
            max: "1440",
            step: "1",
            value: janela,
            oninput: (ev) => {
              const valor = Math.max(1, Math.min(1440, Number(ev.target.value) || 1));
              janela = valor;
              renderJanela();
            },
          }),
        ]),
        el(
          "div",
          { class: "atalhos" },
          SLOTS_ATALHOS.map((a) =>
            el("button", {
              type: "button",
              class: "secundario" + (janela === a.minutos ? " ativo" : ""),
              texto: a.rotulo,
              onclick: () => {
                janela = a.minutos;
                renderJanela();
              },
            })
          )
        ),
      ])
    );

    painelJanela.appendChild(
      el("div", { class: "placar" }, [
        el("div", { class: "placar-lado fixo" }, [
          el("span", { class: "rotulo", texto: "Slots fixos" }),
          el("strong", { texto: janela.toLocaleString("pt-BR") }),
          el("span", { class: "unidade", texto: janela === 1 ? "slot a percorrer" : "slots a percorrer" }),
          renderBlocosSlots(janela),
        ]),
        el("div", { class: "placar-lado variavel" }, [
          el("span", { class: "rotulo", texto: "Slot variável" }),
          el("strong", { texto: "1" }),
          el("span", { class: "unidade", texto: "requisição" }),
          el("code", { class: "chamada-api", texto: `?slots=${janela}` }),
        ]),
      ])
    );
  }

  /** Blocos de 1 minuto; acima de 60 mostra uma amostra com reticências. */
  function renderBlocosSlots(n) {
    const mostrar = Math.min(n, 60);
    const blocos = Array.from({ length: mostrar }, () => el("i", { class: "bloco-slot" }));
    const caixa = el("div", { class: "blocos-slot" }, blocos);
    if (n > mostrar) {
      caixa.appendChild(el("span", { class: "resto", texto: `… +${(n - mostrar).toLocaleString("pt-BR")}` }));
    }
    return caixa;
  }

  renderJanela();

  // --- linha do tempo comparativa ---
  pagina.appendChild(
    el("section", { class: "painel" }, [
      secaoTitulo("A mesma janela nos dois modelos"),
      el("p", {
        class: "explica",
        texto:
          "Consulta às 10h05 pedindo os últimos 5 minutos. A Conta A teve o saldo alterado duas vezes: às 10h01 e às 10h03.",
      }),
      renderLinhaDoTempo(),
      el("div", { class: "legenda" }, [
        el("span", {}, [el("i", { class: "swatch fixo" }), el("span", { texto: "Slots fixos de 1 minuto" })]),
        el("span", {}, [el("i", { class: "swatch variavel-cor" }), el("span", { texto: "Slot variável (slots=5)" })]),
        el("span", {}, [el("i", { class: "swatch evento" }), el("span", { texto: "Alteração de saldo" })]),
      ]),
    ])
  );

  // --- deduplicação ---
  pagina.appendChild(
    el("section", { class: "painel" }, [
      secaoTitulo("O que cada modelo devolve"),
      el("div", { class: "retornos" }, [
        el("div", { class: "retorno fixo" }, [
          el("span", { class: "selo-modelo", texto: "Slot fixo" }),
          el("ul", {}, [
            el("li", {}, [el("code", { texto: "slot 10h01" }), el("span", { texto: "Conta A" })]),
            el("li", { class: "vazio" }, [el("code", { texto: "slot 10h02" }), el("span", { texto: "—" })]),
            el("li", {}, [el("code", { texto: "slot 10h03" }), el("span", { texto: "Conta A" })]),
            el("li", { class: "vazio" }, [el("code", { texto: "slot 10h04" }), el("span", { texto: "—" })]),
            el("li", { class: "vazio" }, [el("code", { texto: "slot 10h05" }), el("span", { texto: "—" })]),
          ]),
          el("p", { class: "conclusao", texto: "A Conta A aparece 2 vezes — o receptor precisa deduplicar." }),
        ]),
        el("div", { class: "retorno variavel" }, [
          el("span", { class: "selo-modelo", texto: "Slot variável" }),
          el("ul", {}, [
            el("li", {}, [el("code", { texto: "10h00 → 10h05" }), el("span", { texto: "Conta A" })]),
          ]),
          el("p", { class: "conclusao", texto: "A Conta A aparece 1 vez, consolidada na janela." }),
        ]),
      ]),
      el("p", {
        class: "nota-dedup",
        texto:
          "Identificado o recurso alterado, o receptor consulta a API de Balances para obter o saldo atual — uma vez só, tenha a conta mudado uma ou dez vezes. Repetir o mesmo recurso não acrescenta informação, só chamadas.",
      }),
    ])
  );

  // --- tabela comparativa ---
  const linhasComparativo = [
    ["Janela de consulta", "Intervalos predefinidos", "Relativo ao momento atual"],
    ["Gerenciamento", "Controle individual dos slots", "Consulta dinâmica"],
    ["Consulta de 5 minutos", "5 slots de 1 minuto", "slots=5"],
    ["Consulta de 24 horas", "1.440 slots de 1 minuto", "slots=1440"],
    ["Múltiplas alterações", "Mesmo recurso em slots distintos", "Consolidado em uma ocorrência"],
    ["Processamento do receptor", "Pode exigir deduplicação", "Reduz duplicidade no retorno"],
    ["Flexibilidade", "Limitada à estrutura de slots", "Permite diferentes janelas"],
    ["Complexidade de consulta", "Maior", "Menor"],
  ];
  pagina.appendChild(
    el("section", { class: "painel" }, [
      secaoTitulo("Comparativo"),
      el("table", { class: "tabela larga comparativo-slots" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { texto: "Critério" }),
            el("th", { texto: "Slot fixo" }),
            el("th", { texto: "Slot variável" }),
          ]),
        ]),
        el(
          "tbody",
          {},
          linhasComparativo.map(([criterio, fixo, variavel]) =>
            el("tr", {}, [
              el("td", { texto: criterio }),
              el("td", { class: "col-fixo", texto: fixo }),
              el("td", { class: "col-variavel", texto: variavel }),
            ])
          )
        ),
      ]),
    ])
  );

  // --- pontos técnicos ---
  const pontos = [
    ["Deduplicação de recursos", "O retorno deve trazer identificadores únicos dentro da janela: uma conta com várias alterações aparece uma vez só."],
    ["Sobreposição de consultas", "Janelas relativas se sobrepõem entre consultas consecutivas. O receptor precisa tratar recursos já processados."],
    ["Consistência temporal", "Definir com clareza o momento de referência e evitar lacunas em caso de atraso, indisponibilidade ou processamento assíncrono."],
    ["Retenção do histórico", "O transmissor continua precisando guardar o que mudou no período — só não precisa organizar isso em slots fixos."],
    ["Limite máximo de consulta", "Definir um teto para o parâmetro slots, considerando capacidade, volume de alterações, retenção e tempo de resposta."],
  ];
  pagina.appendChild(
    el("section", { class: "painel" }, [
      secaoTitulo("Pontos técnicos a considerar"),
      el(
        "div",
        { class: "pontos-tecnicos" },
        pontos.map(([titulo, texto], i) =>
          el("div", { class: "ponto" }, [
            el("span", { class: "numero", texto: String(i + 1) }),
            el("div", {}, [el("strong", { texto: titulo }), el("p", { texto })]),
          ])
        )
      ),
    ])
  );

  // --- recomendação ---
  pagina.appendChild(
    el("section", { class: "painel recomendacao" }, [
      secaoTitulo("Recomendação"),
      el("p", {
        class: "destaque-recomendacao",
        texto:
          "Para identificar recursos alterados e recuperar seu estado atual, o modelo de slots variáveis é mais simples e mais flexível.",
      }),
      el(
        "div",
        { class: "beneficios-slots" },
        [
          ["Simplicidade", "Períodos maiores sem percorrer cada slot."],
          ["Flexibilidade", "O consumidor define o intervalo via slots."],
          ["Eficiência", "Consolida múltiplas alterações do mesmo recurso."],
          ["Menor redundância", "Evita chamadas desnecessárias à API de Balances."],
          ["Recuperação fácil", "24 horas em uma requisição: slots=1440."],
        ].map(([titulo, texto]) =>
          el("div", { class: "beneficio-slot" }, [
            el("strong", { texto: titulo }),
            el("span", { texto }),
          ])
        )
      ),
    ])
  );
}

/** Linha do tempo 10h00–10h05 com as duas leituras da mesma janela. */
function renderLinhaDoTempo() {
  const W = 1000, H = 230;
  const x0 = 120, x1 = 968;
  const minutos = 5;
  const x = (min) => x0 + (min / minutos) * (x1 - x0);
  // Posições dentro do minuto em que a alteração ocorreu (10h01 e 10h03).
  const eventos = [1.4, 3.4];

  const faixaFixos = [];
  for (let i = 0; i < minutos; i++) {
    faixaFixos.push(
      svg("rect", {
        x: x(i) + 2,
        y: 46,
        width: x(i + 1) - x(i) - 4,
        height: 38,
        rx: 5,
        class: "slot-fixo-bloco",
      })
    );
    faixaFixos.push(
      svg("text", {
        x: (x(i) + x(i + 1)) / 2,
        y: 70,
        class: "slot-rotulo-bloco",
        "text-anchor": "middle",
        texto: `slot 10h0${i}`,
      })
    );
  }

  return svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "linha-tempo", role: "img",
    "aria-label": "Linha do tempo de 10h00 a 10h05 comparando cinco slots fixos de um minuto com um único slot variável" }, [
    // rótulos das faixas
    svg("text", { x: x0 - 14, y: 70, class: "faixa-rotulo", "text-anchor": "end", texto: "Slots fixos" }),
    svg("text", { x: x0 - 14, y: 148, class: "faixa-rotulo", "text-anchor": "end", texto: "Slot variável" }),
    ...faixaFixos,
    // slot variável: uma janela contínua
    svg("rect", { x: x(0) + 2, y: 124, width: x(5) - x(0) - 4, height: 38, rx: 5, class: "slot-variavel-bloco" }),
    svg("text", { x: (x(0) + x(5)) / 2, y: 148, class: "slot-rotulo-variavel", "text-anchor": "middle",
      texto: "slots=5  ·  10h00 → 10h05  ·  uma requisição" }),
    // eventos de alteração
    ...eventos.flatMap((min) => {
      const minuto = Math.floor(min);
      return [
        svg("line", { x1: x(min), x2: x(min), y1: 38, y2: 172, class: "evento-linha" }),
        svg("circle", { cx: x(min), cy: 38, r: 6, class: "evento-ponto" },
          [svg("title", { texto: `Alteração de saldo da Conta A no minuto 10h0${minuto}` })]),
        svg("text", { x: x(min), y: 26, class: "evento-rotulo", "text-anchor": "middle", texto: `10h0${minuto}` }),
      ];
    }),
    // eixo de tempo
    svg("line", { x1: x0, x2: x1, y1: 186, y2: 186, class: "eixo-tempo" }),
    ...Array.from({ length: minutos + 1 }, (_, i) =>
      svg("g", {}, [
        svg("line", { x1: x(i), x2: x(i), y1: 182, y2: 190, class: "eixo-tempo" }),
        svg("text", { x: x(i), y: 208, class: "roi-eixo", "text-anchor": "middle", texto: `10h0${i}` }),
      ])
    ),
    svg("text", { x: x1, y: 26, class: "evento-rotulo agora", "text-anchor": "end", texto: "consulta às 10h05" }),
  ]);
}

// ---------- tela de benefícios ----------

async function telaBeneficios() {
  renderNav("beneficios");
  app.className = "duas-colunas";
  app.innerHTML = "";
  app.appendChild(el("p", { class: "carregando", texto: "Carregando…" }));

  let d;
  try {
    d = await api("/api/benefits");
  } catch (e) {
    app.className = "";
    app.innerHTML = "";
    app.appendChild(caixaErro(String(e.message || e)));
    return;
  }

  app.innerHTML = "";
  const painelEntradas = el("section", { class: "painel" });
  const painelResultado = el("section", { class: "painel" });
  app.appendChild(painelEntradas);
  app.appendChild(painelResultado);

  let debounceBen = null;
  const form = el("form", { id: "form-beneficios" });

  const campos = [
    { nome: "volume_requisicoes_mes", rotulo: "Volume de requisições de saldo por mês", passo: 1 },
    { nome: "custo_unitario", rotulo: "Custo unitário por requisição (R$)", passo: 0.00001 },
    { nome: "percentual_custo_fixo", rotulo: "Custo fixo (%)", passo: 1, max: 100 },
    { nome: "percentual_custo_variavel", rotulo: "Custo variável (%)", passo: 1, max: 100 },
    {
      nome: "percentual_reducao",
      rotulo: "Redução esperada com o Aggregated Polling (%)",
      passo: 1,
      max: 100,
    },
  ];

  for (const campo of campos) {
    form.appendChild(
      el("label", {}, [
        el("span", { class: "rotulo-campo", texto: campo.rotulo }),
        el("input", {
          type: "number",
          name: campo.nome,
          step: campo.passo,
          min: "0",
          max: campo.max || null,
          value: d.entradas[campo.nome],
        }),
      ])
    );
  }

  form.appendChild(
    el("label", {}, [
      el("span", { class: "rotulo-campo", texto: "Racional da redução esperada" }),
      el("small", { texto: "Como o percentual foi estimado: premissas, medições, fontes." }),
      el("textarea", { name: "racional", rows: 10, texto: d.racional || "" }),
    ])
  );

  painelEntradas.appendChild(el("h2", { class: "titulo-recurso", texto: "Benefícios" }));
  painelEntradas.appendChild(form);
  const status = el("p", { class: "status", id: "status-ben" });
  painelEntradas.appendChild(
    el("div", { class: "acoes" }, [
      el("button", { type: "button", texto: "Recalcular", onclick: () => calcular() }),
      el("button", { type: "button", class: "secundario", texto: "Salvar", onclick: salvar }),
      el("button", {
        type: "button",
        class: "secundario",
        texto: "Restaurar do config.json",
        onclick: () => telaBeneficios(),
      }),
    ])
  );
  painelEntradas.appendChild(status);

  function coletar() {
    const dados = {};
    for (const campo of form.elements) {
      if (!campo.name) continue;
      dados[campo.name] = campo.type === "number" ? Number(campo.value) : campo.value;
    }
    return dados;
  }

  async function calcular(pronto) {
    let resultado = pronto;
    if (!resultado) {
      status.textContent = "Calculando…";
      try {
        resultado = await api("/api/benefits", coletar());
      } catch (e) {
        painelResultado.innerHTML = "";
        painelResultado.appendChild(caixaErro(String(e.message || e)));
        status.textContent = "";
        return;
      }
    }
    render(resultado);
    status.textContent = "Atualizado " + new Date().toLocaleTimeString("pt-BR");
  }

  async function salvar() {
    try {
      const resultado = await api("/api/benefits/save", coletar());
      render(resultado);
      status.textContent = "Gravado em config.json.";
    } catch (e) {
      painelResultado.prepend(caixaErro(String(e.message || e)));
    }
  }

  function render(b) {
    painelResultado.innerHTML = "";

    const avisos = caixaAvisos(b.avisos);
    if (avisos) painelResultado.appendChild(avisos);

    painelResultado.appendChild(
      el("div", { class: "cartoes" }, [
        el("div", { class: "cartao destaque" }, [
          el("span", { class: "rotulo", texto: "Benefício mensal" }),
          el("strong", { texto: b.beneficio_mes_fmt }),
          el("span", { class: "nota", texto: b.beneficio_ano_fmt + "/ano" }),
        ]),
        el("div", { class: "cartao" }, [
          el("span", { class: "rotulo", texto: "Custo atual das requisições" }),
          el("strong", { texto: b.custo_total_mes_fmt }),
          el("span", { class: "nota", texto: b.volume_fmt + " requisições/mês" }),
        ]),
        el("div", { class: "cartao" }, [
          el("span", { class: "rotulo", texto: "Requisições evitadas" }),
          el("strong", { texto: b.requisicoes_evitadas_fmt }),
          el("span", { class: "nota", texto: b.percentual_reducao + "% do volume atual" }),
        ]),
      ])
    );

    painelResultado.appendChild(secaoTitulo("Como o benefício é calculado"));
    painelResultado.appendChild(
      el("table", { class: "tabela larga" }, [
        el(
          "tbody",
          {},
          b.passos.map((p) =>
            el("tr", { class: p.destaque ? "total" : null }, [
              el("td", { texto: p.rotulo }),
              el("td", { texto: p.detalhe }),
              el("td", { class: "num", texto: p.valor }),
            ])
          )
        ),
      ])
    );

    painelResultado.appendChild(secaoTitulo("Composição do custo atual"));
    painelResultado.appendChild(
      el("div", { class: "barra-partes" }, [
        el("span", {
          class: "parte fixo",
          style: `width:${b.percentual_custo_fixo}%`,
          title: `Custo fixo: ${b.custo_fixo_mes_fmt} (${b.percentual_custo_fixo}%)`,
        }),
        el("span", {
          class: "parte variavel",
          style: `width:${b.percentual_custo_variavel}%`,
          title: `Custo variável: ${b.custo_variavel_mes_fmt} (${b.percentual_custo_variavel}%)`,
        }),
      ])
    );
    painelResultado.appendChild(
      el("div", { class: "legenda" }, [
        el("span", {}, [
          el("i", { class: "swatch fixo" }),
          el("span", {
            texto: `Fixo — ${b.custo_fixo_mes_fmt} (${b.percentual_custo_fixo}%), não cai com o volume`,
          }),
        ]),
        el("span", {}, [
          el("i", { class: "swatch variavel" }),
          el("span", {
            texto: `Variável — ${b.custo_variavel_mes_fmt} (${b.percentual_custo_variavel}%), base do benefício`,
          }),
        ]),
      ])
    );

    if (b.racional && b.racional.trim()) {
      painelResultado.appendChild(secaoTitulo("Racional da redução esperada"));
      painelResultado.appendChild(el("p", { class: "racional", texto: b.racional }));
    }
  }

  form.addEventListener("input", () => {
    clearTimeout(debounceBen);
    status.textContent = "…";
    debounceBen = setTimeout(() => calcular(), 400);
  });

  calcular(d);
}

// ---------- tela de recurso ----------

function campoVisivel(campo, entradas) {
  const cond = campo.visivel_quando;
  if (!cond) return true;
  return Object.entries(cond).every(([chave, valor]) => entradas[chave] === valor);
}

function renderCampo(campo) {
  const label = el("label", { dataset: { campo: campo.nome } });
  label.appendChild(el("span", { class: "rotulo-campo", texto: campo.rotulo }));
  if (campo.ajuda) label.appendChild(el("small", { texto: campo.ajuda }));

  let entrada;
  if (campo.tipo === "numero") {
    entrada = el("input", {
      type: "number",
      name: campo.nome,
      step: campo.passo ?? 1,
      min: campo.minimo ?? null,
      max: campo.maximo ?? null,
    });
  } else if (campo.tipo === "escolha") {
    entrada = el(
      "select",
      { name: campo.nome },
      campo.opcoes.map((o) => el("option", { value: o.valor, texto: o.rotulo }))
    );
  } else if (campo.tipo === "regiao") {
    entrada = el(
      "select",
      { name: campo.nome },
      catalogo.regioes_disponiveis.map((r) => el("option", { value: r, texto: r }))
    );
  } else if (campo.tipo === "texto_longo") {
    entrada = el("textarea", { name: campo.nome, rows: campo.linhas || 6, spellcheck: "false" });
  } else if (campo.tipo === "booleano") {
    label.className = "inline";
    label.innerHTML = "";
    entrada = el("input", { type: "checkbox", name: campo.nome });
    label.appendChild(entrada);
    label.appendChild(el("span", { texto: campo.rotulo }));
    return label;
  }

  label.appendChild(entrada);
  if (campo.medida) {
    label.appendChild(el("span", { class: "medida", dataset: { medida: campo.medida } }));
  }
  return label;
}

function renderSecao(secao) {
  const bloco = document.createDocumentFragment();
  bloco.appendChild(secaoTitulo(secao.titulo));
  if (secao.explica) bloco.appendChild(el("p", { class: "explica", texto: secao.explica }));

  if (secao.tipo === "lista") {
    bloco.appendChild(el("ul", { class: "notas" }, secao.itens.map((i) => el("li", { texto: i }))));
    return bloco;
  }

  const tbody = el(
    "tbody",
    {},
    secao.linhas.map((l) =>
      el(
        "tr",
        { class: l.classe || null },
        l.celulas.map((c) => el("td", { class: c.num ? "num" : null, texto: c.texto }))
      )
    )
  );
  const tabela = el("table", { class: "tabela" + (secao.colunas ? " larga" : "") });
  if (secao.colunas) {
    tabela.appendChild(
      el("thead", {}, [el("tr", {}, secao.colunas.map((c) => el("th", { texto: c })))])
    );
  }
  tabela.appendChild(tbody);
  bloco.appendChild(tabela);
  return bloco;
}

async function telaRecurso(recursoId) {
  renderNav(recursoId);
  app.className = "duas-colunas";
  app.innerHTML = "";
  app.appendChild(el("p", { class: "carregando", texto: "Carregando…" }));

  let detalhe;
  try {
    detalhe = await api("/api/resources/" + encodeURIComponent(recursoId));
  } catch (e) {
    app.className = "";
    app.innerHTML = "";
    app.appendChild(caixaErro(String(e.message || e)));
    return;
  }

  app.innerHTML = "";

  if (detalhe.status === "planejado") {
    app.className = "";
    const painel = el("section", { class: "painel largo" }, [
      el("h2", { texto: detalhe.nome }),
      el("p", { class: "explica", texto: detalhe.descricao || "" }),
      el("div", { class: "aviso", texto:
        "Recurso já previsto, mas o modelo de custo ainda não foi definido. " +
        "Quando os parâmetros estiverem fechados, ele entra como um módulo em recursos/ e passa a somar nos totais." }),
      el("p", {}, [el("a", { href: "#/", texto: "← Voltar aos totais" })]),
    ]);
    app.appendChild(painel);
    return;
  }

  // Painel de entradas
  const painelEntradas = el("section", { class: "painel" });
  const form = el("form", { id: "form" });
  for (const grupo of detalhe.campos) {
    form.appendChild(secaoTitulo(grupo.titulo));
    for (const campo of grupo.campos) form.appendChild(renderCampo(campo));
  }
  painelEntradas.appendChild(el("h2", { texto: detalhe.nome, class: "titulo-recurso" }));
  painelEntradas.appendChild(form);
  painelEntradas.appendChild(
    el("div", { class: "acoes" }, [
      el("button", { type: "button", texto: "Recalcular", onclick: () => calcular() }),
      el("button", {
        type: "button",
        class: "secundario",
        texto: "Salvar como default",
        onclick: salvarDefaults,
      }),
      el("button", {
        type: "button",
        class: "secundario",
        texto: "Restaurar do config.json",
        onclick: () => telaRecurso(recursoId),
      }),
    ])
  );
  const status = el("p", { class: "status", id: "status" });
  painelEntradas.appendChild(status);

  const painelResultado = el("section", { class: "painel", id: "resultado" });

  app.appendChild(painelEntradas);
  app.appendChild(painelResultado);

  // ---- estado do formulário ----

  function preencher(entradas) {
    for (const [chave, valor] of Object.entries(entradas)) {
      const campo = form.elements[chave];
      if (!campo) continue;
      if (campo.type === "checkbox") campo.checked = Boolean(valor);
      else campo.value = valor;
    }
    aplicarVisibilidade();
  }

  function coletar() {
    const dados = {};
    for (const campo of form.elements) {
      if (!campo.name) continue;
      if (campo.type === "checkbox") dados[campo.name] = campo.checked;
      else if (campo.type === "number") dados[campo.name] = Number(campo.value);
      else dados[campo.name] = campo.value;
    }
    return dados;
  }

  function aplicarVisibilidade() {
    const atuais = coletar();
    for (const grupo of detalhe.campos) {
      for (const campo of grupo.campos) {
        const label = form.querySelector(`[data-campo="${campo.nome}"]`);
        if (label) label.classList.toggle("oculto", !campoVisivel(campo, atuais));
      }
    }
  }

  async function calcular() {
    status.textContent = "Calculando…";
    let dados;
    try {
      dados = await api("/api/resources/" + encodeURIComponent(recursoId) + "/calculate", coletar());
    } catch (e) {
      painelResultado.innerHTML = "";
      painelResultado.appendChild(caixaErro(String(e.message || e)));
      status.textContent = "";
      return;
    }
    renderResultado(dados);
    status.textContent = "Atualizado " + new Date().toLocaleTimeString("pt-BR");
  }

  function renderResultado(d) {
    painelResultado.innerHTML = "";

    const avisos = caixaAvisos(d.avisos);
    if (avisos) painelResultado.appendChild(avisos);

    painelResultado.appendChild(
      el(
        "div",
        { class: "cartoes" },
        d.cartoes.map((c) =>
          el("div", { class: "cartao" + (c.destaque ? " destaque" : "") }, [
            el("span", { class: "rotulo", texto: c.rotulo }),
            el("strong", { texto: c.valor }),
            c.nota ? el("span", { class: "nota", texto: c.nota }) : null,
          ])
        )
      )
    );

    for (const secao of d.secoes) painelResultado.appendChild(renderSecao(secao));

    // legendas de tamanho ao lado dos textareas de exemplo
    for (const [chave, m] of Object.entries(d.medidas || {})) {
      const span = form.querySelector(`[data-medida="${chave}"]`);
      if (!span) continue;
      const rotulo = chave.charAt(0).toUpperCase() + chave.slice(1);
      span.classList.remove("ruim");
      if (!m || !m.bytes) {
        span.textContent = rotulo + ": vazio";
      } else if (m.json_valido === false) {
        span.textContent = `${rotulo}: ${formatarBytes(m.bytes)} — JSON inválido, medido como texto bruto`;
        span.classList.add("ruim");
      } else {
        const sufixo = m.minificado ? ` (minificado; ${formatarBytes(m.bytes_original)} como colado)` : "";
        span.textContent = `${rotulo}: ${formatarBytes(m.bytes)}${sufixo}`;
      }
    }
  }

  async function salvarDefaults() {
    try {
      await api("/api/resources/" + encodeURIComponent(recursoId) + "/defaults", coletar());
      status.textContent = "Defaults gravados em config.json.";
    } catch (e) {
      painelResultado.prepend(caixaErro(String(e.message || e)));
    }
  }

  form.addEventListener("input", () => {
    aplicarVisibilidade();
    clearTimeout(debounce);
    status.textContent = "…";
    debounce = setTimeout(calcular, 350);
  });
  form.addEventListener("change", aplicarVisibilidade);

  preencher(detalhe.entradas);
  calcular();
}

function formatarBytes(v) {
  if (v >= 1024 * 1024) return (v / (1024 * 1024)).toFixed(2).replace(".", ",") + " MB";
  if (v >= 1024) return (v / 1024).toFixed(2).replace(".", ",") + " KB";
  return Math.round(v).toLocaleString("pt-BR") + " B";
}

// ---------- roteador ----------

function rotear() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const partes = hash.split("/").filter(Boolean);
  if (partes[0] === "recurso" && partes[1]) telaRecurso(decodeURIComponent(partes[1]));
  else if (partes[0] === "totais") telaTotais();
  else if (partes[0] === "investimento") telaInvestimento();
  else if (partes[0] === "beneficios") telaBeneficios();
  else if (partes[0] === "slots") telaSlots();
  else telaProposta();
}

window.addEventListener("hashchange", rotear);

(async () => {
  try {
    catalogo = await api("/api/resources");
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(caixaErro(String(e.message || e)));
    return;
  }
  rotear();
})();
