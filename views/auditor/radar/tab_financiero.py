"""views/auditor/radar/tab_financiero.py — Tab de información financiera por año fiscal.

Bloque 6 del levantamiento:
  1. Ejercicio mostrado: el registrado con cifras o, si no, el más reciente
     con cifras del RUC. No se pide el año antes de mostrar la información.
  2. Casilleros del Estado de Situación Financiera (1, 2, 3) y del Estado de
     Resultados Integral (401, 403, 501, 502, 707) de ese año.
  3. Comparativo entre ejercicios del mismo RUC.
"""
from __future__ import annotations

from datetime import date

from services.financial import (
    CASILLEROS, ETIQUETAS_FINANCIERAS, comparativo, compute_indicators, formato_moneda,
)
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_FINANCIERO
from ui.components import fecha_consulta_field, provenance_history
from ui.helpers import hidden_inputs, esc
from ui.icons import SVG_ALERT, SVG_DOLLAR, SVG_SAVE


def _kpi(color: str, label: str, value: str, sublabel: str = "") -> str:
    val_cls = "kpi-value" if value != "—" else "kpi-value pending"
    return (
        f'<div class="kpi-card kpi-{color}">'
        f'<div class="kpi-label">{SVG_DOLLAR} {esc(label)}</div>'
        f'<div class="{val_cls}">{esc(value)}</div>'
        + (f'<div class="kpi-sublabel">{esc(sublabel)}</div>' if sublabel else "")
        + "</div>"
    )


def _ind_card(label: str, value: str, threshold: float | None = None,
              raw: float | None = None, low_is_bad: bool = True) -> str:
    cls = "ind-neutral"
    if raw is not None and threshold is not None:
        if low_is_bad:
            cls = "ind-ok" if raw >= threshold else "ind-warn"
        else:
            cls = "ind-ok" if raw <= threshold else "ind-bad"
    return (
        f'<div class="kpi-indicator {cls}">'
        f'<div class="kpi-indicator-val">{esc(value)}</div>'
        f'<div class="kpi-indicator-name">{esc(label)}</div>'
        f"</div>"
    )


def _notice(css: str, html: str) -> str:
    return f'<div class="fin-alert {css}">{SVG_ALERT}<div>{html}</div></div>'


def _money(value) -> str:
    return formato_moneda(value)


def _history_labels(historial: list) -> dict[str, str]:
    labels = {"anio_fiscal": "Año fiscal de la auditoría"}
    for row in historial:
        campo = row_get(row, "campo")
        anio, _, key = campo.partition(".")
        if key:
            etiqueta = ETIQUETAS_FINANCIERAS.get(key, "Fecha de aprobación de la junta")
            labels[campo] = f"{anio} · {etiqueta}"
    return labels


def _year_status(anio: int | None, years: list, anio_sugerido: dict | None, ruc: str) -> str:
    """Ejercicio mostrado. No se pide el año: se muestran las cifras disponibles."""
    if len(ruc or "") != 13:
        return _notice("alert-medium", "Registre el RUC del expediente antes de cargar información financiera.")
    if years:
        disponibles = ", ".join(str(y["anio_fiscal"]) for y in years)
        sin_cifras = "" if anio in {int(y["anio_fiscal"]) for y in years} else " — sin cifras registradas"
        return (
            f'<p style="font-size:13px;color:var(--muted);margin:0 0 12px;">'
            f"Ejercicio mostrado: <strong>{anio}</strong> (EEFF al {anio}-12-31){sin_cifras}. "
            f"Ejercicios con cifras: {esc(disponibles)}.</p>"
        )
    detalle = (
        f' Descargue en Supercias los documentos económicos con fecha de corte '
        f'{esc(anio_sugerido["fecha_corte"])} (último balance presentado) y registre las cifras.'
        if anio_sugerido else ""
    )
    return _notice("alert-info", "No hay cifras financieras disponibles para este RUC." + detalle)


def _figures_form(audit_id: int, anio_edicion: int, fila, desde_sin_anio: bool, csrf_token: str) -> str:
    """Formulario de casilleros de un solo ejercicio. El año va fijo (oculto):
    para editar otro ejercicio se recarga la pestaña con sus propias cifras."""
    def value(campo: str) -> str:
        v = row_get(fila, campo, None)
        return "" if v is None else f"{float(v):.2f}"

    minimo = ' min="0"'
    inputs = "".join(
        f'<div class="col-4"><label>{esc(ETIQUETAS_FINANCIERAS[campo])}</label>'
        f'<input name="{campo}" type="number" step="0.01"{"" if negativos else minimo} value="{value(campo)}"></div>'
        for campo, _label, _casillero, negativos in CASILLEROS
    )
    aviso = _notice(
        "alert-medium",
        f"Estas cifras se registraron antes de exigir el año fiscal y no tienen año asignado. "
        f"Verifíquelas contra los documentos económicos de {anio_edicion} antes de guardarlas como "
        f"ejercicio {anio_edicion}.",
    ) if desde_sin_anio else ""
    return f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Supercias — Documentos económicos · EEFF al {anio_edicion}-12-31</span>
        <h4>Casilleros del ejercicio {anio_edicion}</h4>
      </div>
      {aviso}
      <form method="post" action="/auditor/radar/financial">
        {hidden_inputs(csrf_token, audit_id=audit_id, anio_fiscal=anio_edicion)}
        <div class="grid">
          {inputs}
          <div class="col-4"><label>Fecha de aprobación (acta de junta)</label>
            <input name="fecha_junta_aprobacion" type="date" max="{date.today().isoformat()}" value="{esc(row_get(fila, 'fecha_junta_aprobacion') or '')}"></div>
          {fecha_consulta_field()}
        </div>
        <p style="font-size:12px;color:var(--muted);margin:8px 0 0;">
          Los totales de ingresos (401 + 403) y gastos (501 + 502) se calculan. Total ingresos menos
          total gastos no equivale al casillero 707: hay rubros intermedios como la participación de
          trabajadores y el impuesto a la renta.
        </p>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Guardar ejercicio {anio_edicion}</button>
        </div>
      </form>
    </section>
    """


def _other_year_selector(audit_id: int, anio_edicion: int) -> str:
    """Abre otro ejercicio completo: cifras, indicadores, comparativo y formulario."""
    return f"""
    <form method="get" action="/auditor/radar" class="fin-year-switch">
      <input type="hidden" name="audit_id" value="{audit_id}">
      <input type="hidden" name="tab" value="indicadores">
      <label>Ver o registrar otro ejercicio</label>
      <input name="fin_anio" type="number" min="1990" max="{date.today().year}" value="{anio_edicion}">
      <button type="submit" class="btn btn-sm">Abrir ejercicio</button>
    </form>
    """


def _comparative_table(estados: list, anio_base: int | None) -> str:
    comp = comparativo(estados, anio_base)
    if not comp["anios"]:
        return ""
    head = "".join(f"<th>{a}</th>" for a in comp["anios"])
    var_head = (
        f'<th>Variación {comp["base"]} vs {comp["previo"]}</th><th>%</th>' if comp["previo"] else ""
    )
    rows = ""
    for fila in comp["filas"]:
        cells = "".join(f'<td class="num">{_money(v)}</td>' for v in fila["valores"])
        var_cells = ""
        if comp["previo"]:
            pct = fila["variacion_pct"]
            var_cells = (
                f'<td class="num">{_money(fila["variacion"])}</td>'
                f'<td class="num">{"—" if pct is None else f"{pct * 100:.1f}%"}</td>'
            )
        style = ' style="font-weight:700;"' if fila["calculado"] else ""
        rows += f'<tr{style}><td>{esc(fila["etiqueta"])}</td>{cells}{var_cells}</tr>'
    nota = "" if comp["previo"] else (
        '<p style="font-size:12px;color:var(--muted);margin:8px 0 0;">'
        "Registre al menos dos ejercicios para ver la variación.</p>"
    )
    return f"""
    <h4 class="fin-section-title">Comparativo entre ejercicios</h4>
    <div class="table-wrap">
      <table class="fin-comparative">
        <thead><tr><th>Partida</th>{head}{var_head}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {nota}
    """


def build(
    audit_id: int,
    indicators: dict,
    read_only: bool = False,
    csrf_token: str = "",
    anio_sugerido: dict | None = None,
    financial: dict | None = None,
    ruc: str = "",
    anio_edicion: int | None = None,
    provenance: list | None = None,
) -> str:
    financial = financial or {}
    anio = financial.get("anio_fiscal")
    years = financial.get("years") or []
    snapshot = financial.get("snapshot")
    por_anio = {int(y["anio_fiscal"]): y for y in years}
    if anio_edicion and anio_edicion != anio:
        # Ejercicio abierto desde la pestaña: todo lo que se muestra es de ese año.
        anio = anio_edicion
        fila = por_anio.get(anio)
        snapshot = {**dict(fila), "origen": "anual"} if fila is not None else None
        indicators = compute_indicators(snapshot)
    origen = row_get(snapshot, "origen")

    if origen == "anual":
        estado_badge = f'<span class="badge badge-green">Ejercicio {anio}</span>'
    elif anio_edicion:
        estado_badge = f'<span class="badge badge-gray">Ejercicio {anio} sin cifras</span>'
    else:
        estado_badge = '<span class="badge badge-gray">Sin cifras por ejercicio</span>'

    sin_anio_html = _notice(
        "alert-medium",
        "Las cifras que se muestran fueron registradas sin año fiscal. "
        "Guárdelas como un ejercicio para usarlas en el levantamiento.",
    ) if origen == "sin_anio" else ""

    catalog_notice = ""
    catalog_years = [y for y in years if "Estados financieros por ramo" in (row_get(y, "fuente") or "")]
    if catalog_years:
        lista = ", ".join(str(y["anio_fiscal"]) for y in catalog_years)
        catalog_notice = _notice(
            "alert-info",
            f"Cifras del reporte local de Supercias disponibles para {esc(lista)}. "
            "Contraste los importes con los documentos económicos originales."
        )
    if origen == "anual" and snapshot and row_get(snapshot, "fuente"):
        catalog_notice += (
            f'<p style="font-size:12px;color:var(--muted);margin:0 0 12px;">'
            f'Fuente de cifras: {esc(row_get(snapshot, "fuente"))}; '
            f'consulta: {esc(row_get(snapshot, "fecha_consulta") or "sin fecha")}.</p>'
        )

    fin_alerts_html = "".join(
        _notice(
            "alert-high" if a["tipo"] == "alto" else ("alert-medium" if a["tipo"] == "medio" else "alert-info"),
            esc(a["mensaje"]),
        )
        for a in indicators.get("alertas", [])
    )

    selector_html = ""
    edit_html = ""
    if not read_only and len(ruc or "") == 13:
        anio_defecto = financial.get("anio_fiscal") or (
            anio_sugerido["anio"] if anio_sugerido else date.today().year - 1)
        anio_edicion = anio_edicion or anio_defecto
        fila = por_anio.get(anio_edicion)
        desde_sin_anio = (fila is None and anio_edicion == anio_defecto
                          and financial.get("legacy") is not None)
        if desde_sin_anio:
            fila = financial.get("legacy")
        selector_html = _other_year_selector(audit_id, anio_edicion)
        edit_html = (
            '<hr class="section-divider">'
            + _figures_form(audit_id, anio_edicion, fila, desde_sin_anio, csrf_token)
        )

    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_FINANCIERO]
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
      <h3 style="margin:0;font-size:15px;">Información financiera</h3>
      {estado_badge}
    </div>
    {_year_status(anio, years, anio_sugerido, ruc)}
    {selector_html}
    {catalog_notice}
    {sin_anio_html}
    <div class="kpi-grid">
      {_kpi("blue",   "Total activo",     indicators["fmt_activo"],           "Casillero 1")}
      {_kpi("red",    "Total pasivo",     indicators["fmt_pasivo"],           "Casillero 2")}
      {_kpi("green",  "Patrimonio neto",  indicators["fmt_patrimonio"],       "Casillero 3")}
      {_kpi("purple", "Total ingresos",   indicators["fmt_ingresos_totales"], "Casilleros 401 + 403")}
      {_kpi("amber",  "Total gastos",     indicators["fmt_gastos_totales"],   "Casilleros 501 + 502")}
      {_kpi("indigo", "Ganancia neta",    indicators["fmt_utilidad"],         "Casillero 707")}
    </div>
    <h4 class="fin-section-title">Indicadores calculados</h4>
    <div class="kpi-indicator-grid">
      {_ind_card("Razón endeudamiento", indicators["fmt_endeudamiento"],
                 0.60, indicators["razon_endeudamiento"], low_is_bad=False)}
      {_ind_card("Margen neto", indicators["fmt_margen_neto"],
                 0.05, indicators["margen_neto"], low_is_bad=True)}
      {_ind_card("Patrimonio / Activo", indicators["fmt_patrimonio_activo"])}
    </div>
    {fin_alerts_html}
    {_comparative_table(years, anio)}
    {edit_html}
    {provenance_history(historial, _history_labels(historial), "Historial de información financiera")}
    """
