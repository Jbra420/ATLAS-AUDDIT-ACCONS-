"""views/auditor/radar/tab_sri.py — Tab de identidad tributaria (SRI)."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
from ui.icons import SVG_EXTERNAL, SVG_SAVE
from providers.sri import SriProvider
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_SRI, etiqueta_traza, ultimo_por_campo
from ui.components import (
    fecha_consulta_field,
    info_card as _ic,
    provenance_history,
    source_check_control,
)

# (campo, etiqueta, clase CSS de la tarjeta) — bloque 1 del levantamiento.
CAMPOS = (
    ("razon_social_sri", "Razón social (SRI)", "full-width"),
    ("estado_contribuyente", "Estado contribuyente", ""),
    ("tipo_contribuyente", "Tipo contribuyente", ""),
    ("regimen", "Régimen", ""),
    ("agente_retencion", "Agente de retención", ""),
    ("fecha_inicio_actividades", "Inicio de actividades", ""),
    ("obligado_contabilidad", "Obligado contabilidad", ""),
    ("contribuyente_especial", "Contribuyente especial", ""),
    ("contribuyente_fantasma", "Contribuyente fantasma", ""),
    ("transacciones_inexistentes", "Transacciones inexistentes", ""),
    ("fecha_actualizacion", "Última actualización", ""),
    ("representante_legal_sri", "Representante legal (SRI)", ""),
    ("actividad_economica", "Actividad económica", "full-width"),
)
ETIQUETAS = {campo: etiqueta for campo, etiqueta, _css in CAMPOS} | {"categoria": "Clase de contribuyente (código)"}


def _pval(profile, key: str) -> str:
    return str(row_get(profile, key, "")).strip()


def _si_no_select(name: str, current: str) -> str:
    options = "".join(
        f'<option value="{value}"{" selected" if current == value else ""}>{label}</option>'
        for value, label in (("", "Sin consultar"), ("NO", "No"), ("SI", "Sí"))
    )
    return f'<select name="{name}">{options}</select>'


def build(
    audit_id: int,
    audit: object,
    profile: object,
    research: object,
    read_only: bool = False,
    csrf_token: str = "",
    source_check: object | None = None,
    provenance: list | None = None,
) -> str:
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    pv = lambda k: _pval(profile, k)
    check_html = "" if read_only else source_check_control(audit_id, source_check, csrf_token, return_tab="sri")
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_SRI]
    trazas = ultimo_por_campo(historial)

    sri_links_html = ""
    if not read_only:
        for lnk in SriProvider().get_links(ruc, company_name)[:2]:
            sri_links_html += f'<a class="btn-ext-link" href="{esc(lnk.url)}" target="_blank" rel="noopener">{SVG_EXTERNAL} {esc(lnk.name)}</a>'

    edit_block = "" if read_only else f"""
    <div class="external-links-row">{sri_links_html}</div>
    <hr class="section-divider">
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">
        ✎ Editar datos SRI manualmente
      </summary>
      <form method="post" action="/auditor/radar/profile">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="return_tab" value="sri">
        <div class="grid">
          <div class="col-12"><label>Razón social (SRI)</label>
            <input name="razon_social_sri" value="{esc(pv('razon_social_sri'))}"></div>
          <div class="col-6"><label>Estado contribuyente</label>
            <input name="estado_contribuyente" value="{esc(pv('estado_contribuyente'))}" placeholder="ACTIVO / SUSPENDIDO"></div>
          <div class="col-6"><label>Tipo contribuyente</label>
            <input name="tipo_contribuyente" value="{esc(pv('tipo_contribuyente'))}" placeholder="SOCIEDAD"></div>
          <div class="col-6"><label>Régimen</label>
            <input name="regimen" value="{esc(pv('regimen'))}" placeholder="GENERAL"></div>
          <div class="col-6"><label>Obligado contabilidad</label>
            <input name="obligado_contabilidad" value="{esc(pv('obligado_contabilidad'))}" placeholder="SI / NO"></div>
          <div class="col-6"><label>Agente retención</label>
            <input name="agente_retencion" value="{esc(pv('agente_retencion'))}" placeholder="SI / NO"></div>
          <div class="col-6"><label>Contribuyente especial</label>
            <input name="contribuyente_especial" value="{esc(pv('contribuyente_especial'))}" placeholder="SI / NO"></div>
          <div class="col-6"><label>Contribuyente fantasma</label>
            {_si_no_select("contribuyente_fantasma", pv("contribuyente_fantasma"))}</div>
          <div class="col-6"><label>Transacciones inexistentes</label>
            {_si_no_select("transacciones_inexistentes", pv("transacciones_inexistentes"))}</div>
          <div class="col-6"><label>Fecha inicio actividades</label>
            <input name="fecha_inicio_actividades" value="{esc(pv('fecha_inicio_actividades'))}"></div>
          <div class="col-6"><label>Representante legal (SRI)</label>
            <input name="representante_legal_sri" value="{esc(pv('representante_legal_sri'))}"></div>
          <div class="col-12"><label>Actividad económica</label>
            <input name="actividad_economica" value="{esc(pv('actividad_economica'))}"></div>
          {fecha_consulta_field()}
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar SRI</button>
        </div>
      </form>
    </details>
    """

    cards = "".join(
        _ic(etiqueta, pv(campo), css, trace=etiqueta_traza(trazas.get((BLOQUE_SRI, campo))))
        for campo, etiqueta, css in CAMPOS
    )
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Identidad tributaria — SRI</h3>
      {check_html}
    </div>
    <div class="info-grid">
      {cards}
    </div>
    {edit_block}
    {provenance_history(historial, ETIQUETAS, "Historial de datos SRI")}
    """
