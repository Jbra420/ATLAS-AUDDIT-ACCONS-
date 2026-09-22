"""views/auditor/radar/tab_financiero.py — Tab de indicadores financieros."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
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


def _anio_sugerido_html(anio_sugerido: dict | None) -> str:
    """Aviso del año fiscal sugerido por el Directorio de Compañías. Es una
    sugerencia: el auditor debe confirmar el año en Documentos económicos."""
    if not anio_sugerido:
        return ""
    return (
        f'<div class="fin-alert alert-info">{SVG_ALERT}<div>'
        f'<strong>Año fiscal sugerido: {anio_sugerido["anio"]}</strong> — último balance presentado '
        f'según el Directorio de Compañías. Descargue en Supercias los documentos económicos con '
        f'fecha de corte {esc(anio_sugerido["fecha_corte"])} y confirme el año antes de registrar cifras.'
        f'</div></div>'
    )


def build(
    audit_id: int,
    indicators: dict,
    read_only: bool = False,
    csrf_token: str = "",
    anio_sugerido: dict | None = None,
) -> str:
    fin_alerts_html = ""
    for alerta in indicators.get("alertas", []):
        css = "alert-high" if alerta["tipo"] == "alto" else (
            "alert-medium" if alerta["tipo"] == "medio" else "alert-info"
        )
        fin_alerts_html += f'<div class="fin-alert {css}">{SVG_ALERT}<div>{esc(alerta["mensaje"])}</div></div>'

    edit_block = "" if read_only else f"""
    <hr class="section-divider">
    <details>
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">
        ✎ Ingresar / actualizar datos financieros
      </summary>
      <form method="post" action="/auditor/radar/financial">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <div class="grid">
          <div class="col-4"><label>Activo total (Balance)</label>
            <input name="activo_total" type="number" step="0.01" value="{esc(str(indicators['activo_total'] or ''))}"></div>
          <div class="col-4"><label>Pasivo total (Balance)</label>
            <input name="pasivo_total" type="number" step="0.01" value="{esc(str(indicators['pasivo_total'] or ''))}"></div>
          <div class="col-4"><label>Patrimonio neto (Balance)</label>
            <input name="patrimonio_neto" type="number" step="0.01" value="{esc(str(indicators['patrimonio_neto'] or ''))}"></div>
          <div class="col-4"><label>Ingresos cod. 401</label>
            <input name="ingresos_401" type="number" step="0.01" value="{esc(str(indicators['ingresos_401'] or ''))}"></div>
          <div class="col-4"><label>Otros ingresos cod. 403</label>
            <input name="otros_ingresos_403" type="number" step="0.01" value="{esc(str(indicators['otros_ingresos_403'] or ''))}"></div>
          <div class="col-4"><label>Costo ventas cod. 501</label>
            <input name="costo_ventas_501" type="number" step="0.01" value="{esc(str(indicators['costo_ventas_501'] or ''))}"></div>
          <div class="col-4"><label>Gastos operac. cod. 502</label>
            <input name="gastos_502" type="number" step="0.01" value="{esc(str(indicators['gastos_502'] or ''))}"></div>
          <div class="col-4"><label>Utilidad neta cod. 707</label>
            <input name="utilidad_neta_707" type="number" step="0.01" value="{esc(str(indicators['utilidad_neta_707'] or ''))}"></div>
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar indicadores</button>
        </div>
      </form>
    </details>
    """

    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
      <h3 style="margin:0;font-size:15px;">Indicadores financieros</h3>
      {'<span class="badge badge-green">Datos cargados</span>' if indicators["tiene_datos"] else '<span class="badge badge-gray">Pendiente de datos</span>'}
    </div>
    {_anio_sugerido_html(anio_sugerido)}
    <div class="kpi-grid">
      {_kpi("blue",   "Activo total",    indicators["fmt_activo"],            "Estado de situación")}
      {_kpi("red",    "Pasivo total",    indicators["fmt_pasivo"],            "Estado de situación")}
      {_kpi("green",  "Patrimonio neto", indicators["fmt_patrimonio"],        "Activo − Pasivo")}
      {_kpi("purple", "Ingresos totales",indicators["fmt_ingresos_totales"],  "Cód. 401 + 403")}
      {_kpi("amber",  "Gastos totales",  indicators["fmt_gastos_totales"],    "Cód. 501 + 502")}
      {_kpi("indigo", "Utilidad neta",   indicators["fmt_utilidad"],          "Cód. 707")}
    </div>
    <h4 style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--muted);margin:0 0 12px;">Indicadores calculados</h4>
    <div class="kpi-indicator-grid">
      {_ind_card("Razón endeudamiento", indicators["fmt_endeudamiento"],
                 0.60, indicators["razon_endeudamiento"], low_is_bad=False)}
      {_ind_card("Margen neto", indicators["fmt_margen_neto"],
                 0.05, indicators["margen_neto"], low_is_bad=True)}
      {_ind_card("Patrimonio / Activo", indicators["fmt_patrimonio_activo"])}
    </div>
    {fin_alerts_html}
    {edit_block}
    """
