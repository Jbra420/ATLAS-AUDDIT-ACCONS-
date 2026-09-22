"""
services/financial.py — Cálculo de indicadores financieros para Atlas Radar Empresarial.

Toma un snapshot financiero y retorna indicadores calculados con alertas automáticas.
No consulta servicios externos. Todo es local y determinístico.

Indicadores calculados:
  - Ingresos totales  = cod.401 + cod.403
  - Gastos totales    = cod.501 + cod.502
  - Razón endeudamiento = pasivo / activo
  - Margen neto       = utilidad / ingresos totales
  - Patrimonio/Activo = patrimonio / activo
"""
from __future__ import annotations

import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Umbrales de alerta
# ---------------------------------------------------------------------------

UMBRAL_ENDEUDAMIENTO = 0.60  # > 60% → endeudamiento alto
UMBRAL_MARGEN_NETO_BAJO = 0.05  # < 5% → rentabilidad baja


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """División segura. Retorna None si denominador es 0 o None."""
    if denominator is None or denominator == 0 or numerator is None:
        return None
    return numerator / denominator


def _fmt(value: float | None, decimals: int = 2, prefix: str = "") -> str:
    """Formatea un valor numérico para mostrar al usuario."""
    if value is None:
        return "—"
    return f"{prefix}{value:,.{decimals}f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def compute_indicators(snapshot: sqlite3.Row | dict | None) -> dict:
    """
    Calcula indicadores financieros a partir de un snapshot.

    Args:
        snapshot: sqlite3.Row o dict con los campos del snapshot financiero.
                  Puede ser None si no hay datos.

    Returns:
        dict con indicadores calculados, valores formateados y lista de alertas.
    """
    if snapshot is None:
        return _empty_indicators()

    def _get(key: str) -> float | None:
        try:
            v = snapshot[key]
            return float(v) if v is not None else None
        except (KeyError, TypeError, ValueError):
            return None

    activo = _get("activo_total")
    pasivo = _get("pasivo_total")
    patrimonio = _get("patrimonio_neto")
    ingresos_401 = _get("ingresos_401")
    ingresos_403 = _get("otros_ingresos_403")
    costo_501 = _get("costo_ventas_501")
    gastos_502 = _get("gastos_502")
    utilidad = _get("utilidad_neta_707")

    # ── Cálculos derivados ────────────────────────────────────────────────
    ingresos_totales = (
        (ingresos_401 or 0) + (ingresos_403 or 0)
        if (ingresos_401 is not None or ingresos_403 is not None)
        else None
    )
    gastos_totales = (
        (costo_501 or 0) + (gastos_502 or 0)
        if (costo_501 is not None or gastos_502 is not None)
        else None
    )
    razon_endeudamiento = _safe_div(pasivo, activo)
    margen_neto = _safe_div(utilidad, ingresos_totales)
    patrimonio_sobre_activo = _safe_div(patrimonio, activo)

    # ── Alertas automáticas ───────────────────────────────────────────────
    alertas: list[dict] = []

    if razon_endeudamiento is not None and razon_endeudamiento > UMBRAL_ENDEUDAMIENTO:
        alertas.append({
            "tipo": "alto",
            "campo": "Endeudamiento",
            "mensaje": (
                f"Razón de endeudamiento {_pct(razon_endeudamiento)} supera el umbral "
                f"del {int(UMBRAL_ENDEUDAMIENTO*100)}%. Evaluar capacidad de pago."
            ),
        })

    if margen_neto is not None and margen_neto < UMBRAL_MARGEN_NETO_BAJO:
        alertas.append({
            "tipo": "medio",
            "campo": "Rentabilidad",
            "mensaje": (
                f"Margen neto {_pct(margen_neto)} por debajo del "
                f"{int(UMBRAL_MARGEN_NETO_BAJO*100)}%. Revisar estructura de costos."
            ),
        })

    if utilidad is not None and utilidad < 0:
        alertas.append({
            "tipo": "alto",
            "campo": "Pérdida neta",
            "mensaje": f"La empresa reporta pérdida neta de {_fmt(utilidad, prefix='$')}. Riesgo de continuidad.",
        })

    if patrimonio is not None and activo is not None and patrimonio < 0:
        alertas.append({
            "tipo": "alto",
            "campo": "Patrimonio negativo",
            "mensaje": "El patrimonio neto es negativo. Indica pérdidas acumuladas superiores al capital.",
        })

    if activo is None and pasivo is None:
        alertas.append({
            "tipo": "info",
            "campo": "Sin datos financieros",
            "mensaje": "No se han registrado datos financieros. Consultar documentos en Supercias.",
        })

    return {
        # Valores raw
        "activo_total": activo,
        "pasivo_total": pasivo,
        "patrimonio_neto": patrimonio,
        "ingresos_401": ingresos_401,
        "otros_ingresos_403": ingresos_403,
        "costo_ventas_501": costo_501,
        "gastos_502": gastos_502,
        "utilidad_neta_707": utilidad,
        # Derivados
        "ingresos_totales": ingresos_totales,
        "gastos_totales": gastos_totales,
        "razon_endeudamiento": razon_endeudamiento,
        "margen_neto": margen_neto,
        "patrimonio_sobre_activo": patrimonio_sobre_activo,
        # Formateados
        "fmt_activo": _fmt(activo, prefix="$"),
        "fmt_pasivo": _fmt(pasivo, prefix="$"),
        "fmt_patrimonio": _fmt(patrimonio, prefix="$"),
        "fmt_ingresos_totales": _fmt(ingresos_totales, prefix="$"),
        "fmt_gastos_totales": _fmt(gastos_totales, prefix="$"),
        "fmt_utilidad": _fmt(utilidad, prefix="$"),
        "fmt_endeudamiento": _pct(razon_endeudamiento),
        "fmt_margen_neto": _pct(margen_neto),
        "fmt_patrimonio_activo": _pct(patrimonio_sobre_activo),
        # Alertas
        "alertas": alertas,
        "tiene_datos": activo is not None or pasivo is not None,
    }


def _empty_indicators() -> dict:
    """Retorna estructura vacía de indicadores cuando no hay snapshot."""
    return {
        "activo_total": None, "pasivo_total": None, "patrimonio_neto": None,
        "ingresos_401": None, "otros_ingresos_403": None,
        "costo_ventas_501": None, "gastos_502": None, "utilidad_neta_707": None,
        "ingresos_totales": None, "gastos_totales": None,
        "razon_endeudamiento": None, "margen_neto": None, "patrimonio_sobre_activo": None,
        "fmt_activo": "—", "fmt_pasivo": "—", "fmt_patrimonio": "—",
        "fmt_ingresos_totales": "—", "fmt_gastos_totales": "—", "fmt_utilidad": "—",
        "fmt_endeudamiento": "—", "fmt_margen_neto": "—", "fmt_patrimonio_activo": "—",
        "alertas": [{
            "tipo": "info",
            "campo": "Sin datos financieros",
            "mensaje": "No se han cargado datos financieros. Consulte documentos en Supercias.",
        }],
        "tiene_datos": False,
    }


# ---------------------------------------------------------------------------
# Resumen textual de indicadores (para el resumen estructurado)
# ---------------------------------------------------------------------------

def indicators_summary_text(indicators: dict) -> str:
    """Genera texto estructurado de indicadores para incluir en el resumen."""
    if not indicators["tiene_datos"]:
        return "  Sin datos financieros registrados. Pendiente de confirmar."

    lines = [
        f"  Activo total          : {indicators['fmt_activo']}",
        f"  Pasivo total          : {indicators['fmt_pasivo']}",
        f"  Patrimonio neto       : {indicators['fmt_patrimonio']}",
        f"  Ingresos totales      : {indicators['fmt_ingresos_totales']}",
        f"  Gastos totales        : {indicators['fmt_gastos_totales']}",
        f"  Utilidad neta         : {indicators['fmt_utilidad']}",
        "",
        f"  Razón endeudamiento   : {indicators['fmt_endeudamiento']}",
        f"  Margen neto           : {indicators['fmt_margen_neto']}",
        f"  Patrimonio / Activo   : {indicators['fmt_patrimonio_activo']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Levantamiento de información — casilleros por año fiscal
# ---------------------------------------------------------------------------

# (campo, etiqueta, casillero, admite negativos). Los casilleros son los del
# formulario de Supercias indicados en el requisito; el de la utilidad antes
# de participación e impuestos está por confirmar y no se muestra número.
CASILLEROS = (
    ("activo_total", "Total activo", "1", False),
    ("pasivo_total", "Total pasivo", "2", False),
    ("patrimonio_neto", "Patrimonio neto", "3", True),
    ("ingresos_401", "Ingresos de actividades ordinarias", "401", False),
    ("otros_ingresos_403", "Otros ingresos", "403", False),
    ("costo_ventas_501", "Costo de ventas y producción", "501", False),
    ("gastos_502", "Gastos", "502", False),
    ("utilidad_antes_part_imp", "Utilidad antes de participación e impuestos", "", True),
    ("utilidad_neta_707", "Ganancia (pérdida) neta del período", "707", True),
)
CAMPOS_FINANCIEROS = tuple(campo for campo, _l, _c, _n in CASILLEROS)
ETIQUETAS_FINANCIERAS = {
    campo: (f"{etiqueta} (casillero {casillero})" if casillero else f"{etiqueta} (casillero por confirmar)")
    for campo, etiqueta, casillero, _n in CASILLEROS
}


def _num(row: Any, key: str) -> float | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    return float(value) if value is not None else None


def _sum_or_none(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def filas_comparativo(row: Any) -> list[tuple[str, float | None, bool]]:
    """Filas del estado financiero de un año en el orden del requisito:
    (etiqueta, valor, es_total_calculado). Los totales de ingresos y gastos
    se calculan; los demás valores son los casilleros registrados."""
    v = {campo: _num(row, campo) for campo in CAMPOS_FINANCIEROS}
    return [
        (ETIQUETAS_FINANCIERAS["activo_total"], v["activo_total"], False),
        (ETIQUETAS_FINANCIERAS["pasivo_total"], v["pasivo_total"], False),
        (ETIQUETAS_FINANCIERAS["patrimonio_neto"], v["patrimonio_neto"], False),
        (ETIQUETAS_FINANCIERAS["ingresos_401"], v["ingresos_401"], False),
        (ETIQUETAS_FINANCIERAS["otros_ingresos_403"], v["otros_ingresos_403"], False),
        ("Total ingresos (401 + 403)", _sum_or_none(v["ingresos_401"], v["otros_ingresos_403"]), True),
        (ETIQUETAS_FINANCIERAS["costo_ventas_501"], v["costo_ventas_501"], False),
        (ETIQUETAS_FINANCIERAS["gastos_502"], v["gastos_502"], False),
        ("Total gastos (501 + 502)", _sum_or_none(v["costo_ventas_501"], v["gastos_502"]), True),
        (ETIQUETAS_FINANCIERAS["utilidad_antes_part_imp"], v["utilidad_antes_part_imp"], False),
        (ETIQUETAS_FINANCIERAS["utilidad_neta_707"], v["utilidad_neta_707"], False),
    ]


def comparativo(estados: list[Any], anio_base: int | None, max_anios: int = 5) -> dict:
    """Comparativo entre ejercicios de un mismo RUC.

    estados son filas de financial_statements. anio_base es el año fiscal de
    la auditoría; la variación se calcula contra el ejercicio anterior más
    cercano que esté registrado. Si no hay dos ejercicios, no hay variación.
    """
    por_anio = {int(e["anio_fiscal"]): e for e in estados or []}
    anios = sorted(por_anio, reverse=True)[:max_anios]
    base = anio_base if anio_base in por_anio else (anios[0] if anios else None)
    previo = max((a for a in por_anio if base is not None and a < base), default=None)

    filas = []
    columnas = {a: filas_comparativo(por_anio[a]) for a in anios}
    base_rows = filas_comparativo(por_anio[base]) if base is not None else []
    previo_rows = filas_comparativo(por_anio[previo]) if previo is not None else []
    for i, (etiqueta, _v, calculado) in enumerate(filas_comparativo({})):
        valores = [columnas[a][i][1] for a in anios]
        variacion = variacion_pct = None
        if base_rows and previo_rows:
            actual, anterior = base_rows[i][1], previo_rows[i][1]
            if actual is not None and anterior is not None:
                variacion = actual - anterior
                variacion_pct = _safe_div(variacion, abs(anterior))
        filas.append({
            "etiqueta": etiqueta,
            "calculado": calculado,
            "valores": valores,
            "variacion": variacion,
            "variacion_pct": variacion_pct,
        })
    return {"anios": anios, "base": base, "previo": previo, "filas": filas}
