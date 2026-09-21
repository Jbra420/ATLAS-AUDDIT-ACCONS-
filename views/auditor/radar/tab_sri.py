"""views/auditor/radar/tab_sri.py — Tab de identidad tributaria (SRI)."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
from ui.icons import SVG_EXTERNAL, SVG_SAVE
from providers.sri import SriProvider
from services.rowutil import row_get
from ui.components import info_card as _ic, source_check_control


def _pval(profile, key: str) -> str:
    return str(row_get(profile, key, "")).strip()


def build(
    audit_id: int,
    audit: object,
    profile: object,
    research: object,
    read_only: bool = False,
    csrf_token: str = "",
    source_check: object | None = None,
) -> str:
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    pv = lambda k: _pval(profile, k)
    check_html = "" if read_only else source_check_control(audit_id, source_check, csrf_token, return_tab="sri")

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
          <div class="col-6"><label>Fecha inicio actividades</label>
            <input name="fecha_inicio_actividades" value="{esc(pv('fecha_inicio_actividades'))}"></div>
          <div class="col-6"><label>Representante legal</label>
            <input name="representante_legal" value="{esc(pv('representante_legal'))}"></div>
          <div class="col-12"><label>Actividad económica</label>
            <input name="actividad_economica" value="{esc(pv('actividad_economica'))}"></div>
          <input type="hidden" name="ruc" value="{esc(ruc)}">
          <input type="hidden" name="razon_social" value="{esc(company_name)}">
          {"".join(f'<input type="hidden" name="{k}" value="{esc(pv(k))}">' for k in ["expediente_supercias","nacionalidad","tipo_compania","situacion_legal","fecha_constitucion","plazo_social","oficina_control","objeto_social","categoria","fecha_actualizacion"])}
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar SRI</button>
        </div>
      </form>
    </details>
    """

    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Identidad tributaria — SRI</h3>
      {check_html}
    </div>
    <div class="info-grid">
      {_ic("Estado contribuyente", pv("estado_contribuyente"))}
      {_ic("Tipo contribuyente", pv("tipo_contribuyente"))}
      {_ic("Régimen", pv("regimen"))}
      {_ic("Obligado contabilidad", pv("obligado_contabilidad"))}
      {_ic("Agente de retención", pv("agente_retencion"))}
      {_ic("Contribuyente especial", pv("contribuyente_especial"))}
      {_ic("Inicio de actividades", pv("fecha_inicio_actividades"))}
      {_ic("Última actualización", pv("fecha_actualizacion"))}
      {_ic("Representante legal", pv("representante_legal"))}
      {_ic("Actividad económica", pv("actividad_economica"), "full-width")}
    </div>
    {edit_block}
    """
