"""ROI do Aggregated Polling: fluxo de caixa acumulado e payback.

Modelo em dois estagios:

  implantacao (meses 1..D)   desembolso do investimento, distribuido por igual
                             entre os meses do cronograma; sem beneficio ainda
  operacao (de D+1 em diante)  beneficio mensal menos a despesa de infraestrutura

O payback e o mes em que o acumulado cruza zero, interpolado dentro do mes.
"""

import math

from recursos import base

MOEDA = "BRL"
HORIZONTE_MINIMO = 12
HORIZONTE_MAXIMO = 60
FOLGA_APOS_PAYBACK = 6


def calcular(despesa_mensal, beneficio_mensal, investimento, meses_implantacao, horizonte=None):
    """Serie do acumulado mes a mes, payback e indicadores de retorno."""
    meses_implantacao = max(0, int(meses_implantacao or 0))
    resultado_mensal = beneficio_mensal - despesa_mensal
    desembolso_mes = (investimento / meses_implantacao) if meses_implantacao else 0.0

    def fluxo_do_mes(mes):
        if mes <= meses_implantacao:
            return -desembolso_mes
        return resultado_mensal

    # Horizonte: cobre o payback com folga, dentro de limites legíveis.
    if horizonte is None:
        if resultado_mensal > 0:
            meses_para_pagar = investimento / resultado_mensal
            horizonte = int(meses_implantacao + meses_para_pagar + FOLGA_APOS_PAYBACK) + 1
        else:
            horizonte = 24
    horizonte = max(HORIZONTE_MINIMO, min(HORIZONTE_MAXIMO, horizonte))

    serie = []
    acumulado = 0.0
    payback_mes = None
    for mes in range(1, horizonte + 1):
        fluxo = fluxo_do_mes(mes)
        anterior = acumulado
        acumulado += fluxo
        if payback_mes is None and anterior < 0 <= acumulado and fluxo > 0:
            # fração do mês em que o acumulado cruza zero
            payback_mes = (mes - 1) + (-anterior / fluxo)
        serie.append({
            "mes": mes,
            "fluxo": fluxo,
            "acumulado": acumulado,
            "acumulado_fmt": base.fmt_moeda(acumulado, MOEDA),
            "fase": "implantação" if mes <= meses_implantacao else "operação",
        })

    pago = payback_mes is not None
    # Em meses fechados o payback arredonda para cima: no mês anterior o caixa
    # ainda está negativo.
    payback_meses = math.ceil(payback_mes) if pago else None
    payback_meses_operacao = math.ceil(payback_mes - meses_implantacao) if pago else None
    return {
        "moeda": MOEDA,
        "despesa_mensal": despesa_mensal,
        "despesa_mensal_fmt": base.fmt_moeda(despesa_mensal, MOEDA),
        "beneficio_mensal": beneficio_mensal,
        "beneficio_mensal_fmt": base.fmt_moeda(beneficio_mensal, MOEDA),
        "resultado_mensal": resultado_mensal,
        "resultado_mensal_fmt": base.fmt_moeda(resultado_mensal, MOEDA),
        "investimento": investimento,
        "investimento_fmt": base.fmt_moeda(investimento, MOEDA),
        "meses_implantacao": meses_implantacao,
        "horizonte": horizonte,
        "serie": serie,
        "payback_mes": payback_mes,
        "payback_meses": payback_meses,
        "payback_meses_fmt": (base.fmt_int(payback_meses) if pago else "—"),
        "payback_fmt": (base.fmt_int(payback_meses) + " meses") if pago else "não se paga",
        "payback_apos_golive": (payback_mes - meses_implantacao) if pago else None,
        "payback_apos_golive_fmt": (
            base.fmt_int(payback_meses_operacao) + " meses de operação" if pago else "—"
        ),
        "acumulado_final": serie[-1]["acumulado"] if serie else 0.0,
        "acumulado_final_fmt": base.fmt_moeda(serie[-1]["acumulado"] if serie else 0.0, MOEDA),
        "roi_percentual": ((serie[-1]["acumulado"] / investimento * 100) if investimento and serie else 0.0),
        "avisos": (
            []
            if resultado_mensal > 0
            else [
                "O benefício mensal (%s) não supera a despesa de infraestrutura (%s): "
                "sem resultado positivo, não há payback."
                % (base.fmt_moeda(beneficio_mensal, MOEDA), base.fmt_moeda(despesa_mensal, MOEDA))
            ]
        ),
    }
