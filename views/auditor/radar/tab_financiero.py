"""views/auditor/radar/tab_financiero.py — Tab de información financiera por año fiscal.

Bloque 6 del levantamiento:
  1. Parámetro previo obligatorio: el año fiscal de los estados financieros.
  2. Casilleros del Estado de Situación Financiera (1, 2, 3) y del Estado de
     Resultados Integral (401, 403, 501, 502, 707) de ese año.
  3. Comparativo entre ejercicios del mismo RUC.
"""
from __future__ import annotations

from datetime import date

from services.financial import CASILLEROS, ETIQUETAS_FINANCIERAS, comparativo, formato_moneda
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_FINANCIERO
from ui.components import fecha_consulta_field, provenance_history
from ui.helpers import csrf_input, esc
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


def _year_step(audit_id: int, anio: int | None, anio_sugerido: dict | None, ruc: str,
               read_only: bool, csrf_token: str) -> str:
    """Paso 1: registrar el año fiscal (parámetro previo obligatorio)."""
    if len(ruc or "") != 13:
        return _notice("alert-medium", "Registre el RUC del expediente antes de cargar información financiera.")
    sugerencia = ""
    if anio_sugerido and anio_sugerido["anio"] != anio:
        sugerencia = _notice(
            "alert-info",
            f'<strong>Año fiscal sugerido: {anio_sugerido["anio"]}</strong> — último balance presentado '
            f'según el Directorio de Compañías. Descargue en Supercias los documentos económicos con '
            f'fecha de corte {esc(anio_sugerido["fecha_corte"])} y confirme el año antes de registrar cifras.',
        )
    if read_only:
        estado = f"Año fiscal {anio}" if anio else "Año fiscal pendiente de registrar"
        return f'<p style="font-size:13px;color:var(--muted);margin:0 0 12px;">{estado}.</p>{sugerencia}'
    valor = anio or (anio_sugerido["anio"] if anio_sugerido else "")
    return f"""
    {sugerencia}
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Paso previo obligatorio</span>
        <h4>Año fiscal de los estados financieros</h4>
      </div>
      <p style="font-size:13px;color:var(--muted);margin:0 0 12px;">
        Si la auditoría se ejecuta en {date.today().year} sobre el ejercicio {date.today().year - 1},
        se descargan los documentos con fecha {date.today().year - 1}-12-31.
      </p>
      <form method="post" action="/auditor/radar/financial-year">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <div class="grid">
          <div class="col-4"><label>Año fiscal *</label>
            <input name="anio_fiscal" type="number" min="1990" max="{date.today().year}" value="{esc(str(valor))}" required></div>
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} {"Cambiar" if anio else "Registrar"} año fiscal</button>
        </div>
      </form>
    </section>
    """


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
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="anio_fiscal" value="{anio_edicion}">
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
    return f"""
    <form method="get" action="/auditor/radar" class="fin-year-switch">
      <input type="hidden" name="audit_id" value="{audit_id}">
      <input type="hidden" name="tab" value="indicadores">
      <label>Registrar o editar otro ejercicio</label>
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
    origen = row_get(snapshot, "origen")

    if anio is None:
        estado_badge = '<span class="badge badge-gray">Año fiscal pendiente</span>'
    elif origen == "anual":
        estado_badge = f'<span class="badge badge-green">Ejercicio {anio}</span>'
    else:
        estado_badge = f'<span class="badge badge-gray">Ejercicio {anio} sin cifras</span>'

    sin_anio_html = _notice(
        "alert-medium",
        "Las cifras que se muestran fueron registradas sin año fiscal. "
        + ("Regístrelas como ejercicio del año fiscal elegido para usarlas en el levantamiento."
           if anio else "Registre el año fiscal y confírmelas contra los documentos económicos."),
    ) if origen == "sin_anio" else ""

    catalog_notice = ""
    catalog_years = [y for y in years if "Estados financieros por ramo" in (row_get(y, "fuente") or "")]
    if catalog_years:
        lista = ", ".join(str(y["anio_fiscal"]) for y in catalog_years)
        catalog_notice = _notice(
            "alert-info",
            f"Cifras del reporte local de Supercias disponibles para {esc(lista)}. "
            "Confirme el año fiscal y contraste los importes con los documentos económicos originales."
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

    edit_html = ""
    if not read_only and anio is not None:
        anio_edicion = anio_edicion or anio
        fila = por_anio.get(anio_edicion)
        desde_sin_anio = fila is None and anio_edicion == anio and financial.get("legacy") is not None
        if desde_sin_anio:
            fila = financial.get("legacy")
        edit_html = (
            '<hr class="section-divider">'
            + _other_year_selector(audit_id, anio_edicion)
            + _figures_form(audit_id, anio_edicion, fila, desde_sin_anio, csrf_token)
        )

    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_FINANCIERO]
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
      <h3 style="margin:0;font-size:15px;">Información financiera</h3>
      {estado_badge}
    </div>
    {_year_step(audit_id, anio, anio_sugerido, ruc, read_only, csrf_token)}
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
