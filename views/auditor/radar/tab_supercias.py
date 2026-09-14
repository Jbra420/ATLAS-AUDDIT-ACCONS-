"""views/auditor/radar/tab_supercias.py — Tab de estado societario (Supercias)."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_EXTERNAL, SVG_SAVE
from providers.supercias import SuperciasProvider

def _pval(profile, key):
    if profile:
        v = profile[key]
        return (v or "").strip()
    return ""

def _ic(label, value, css_extra=""):
    v = value.strip() if value else ""
    val_cls = "info-card-value" if v else "info-card-value pending"
    val_text = esc(v) if v else "Pendiente de confirmar"
    return f'<div class="info-card {css_extra}"><div class="info-card-label">{esc(label)}</div><div class="{val_cls}">{val_text}</div></div>'

def build(audit_id, audit, profile, research, read_only: bool = False):
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    pv = lambda k: _pval(profile, k)
    links_html = ""
    if not read_only:
        for lnk in SuperciasProvider().get_links(ruc, company_name)[:2]:
            links_html += f'<a class="btn-ext-link" href="{esc(lnk.url)}" target="_blank" rel="noopener">{SVG_EXTERNAL} {esc(lnk.name)}</a>'
    sri_pass = "".join(f'<input type="hidden" name="{k}" value="{esc(pv(k))}">' for k in ["ruc","razon_social","estado_contribuyente","tipo_contribuyente","regimen","categoria","obligado_contabilidad","agente_retencion","contribuyente_especial","fecha_inicio_actividades","fecha_actualizacion","actividad_economica","representante_legal","plazo_social"])
    edit_block = "" if read_only else f"""
    <div class="external-links-row">{links_html}</div>
    <hr class="section-divider">
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">✎ Editar datos Supercias</summary>
      <form method="post" action="/auditor/radar/profile">
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="return_tab" value="supercias">
        <div class="grid">
          <div class="col-6"><label>Situación legal</label><input name="situacion_legal" value="{esc(pv('situacion_legal'))}"></div>
          <div class="col-6"><label>Tipo de compañía</label><input name="tipo_compania" value="{esc(pv('tipo_compania'))}"></div>
          <div class="col-6"><label>Nacionalidad</label><input name="nacionalidad" value="{esc(pv('nacionalidad'))}"></div>
          <div class="col-6"><label>Fecha constitución</label><input name="fecha_constitucion" value="{esc(pv('fecha_constitucion'))}"></div>
          <div class="col-6"><label>Expediente Supercias</label><input name="expediente_supercias" value="{esc(pv('expediente_supercias'))}"></div>
          <div class="col-6"><label>Oficina de control</label><input name="oficina_control" value="{esc(pv('oficina_control'))}"></div>
          <div class="col-12"><label>Objeto social</label><textarea name="objeto_social" style="min-height:60px;">{esc(pv('objeto_social'))}</textarea></div>
          {sri_pass}
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar Supercias</button>
        </div>
      </form>
    </details>
    """
    return f"""
    <h3 style="margin:0 0 16px;font-size:15px;">Estado societario — Supercias</h3>
    <div class="info-grid">
      {_ic("Situación legal", pv("situacion_legal"))}
      {_ic("Tipo de compañía", pv("tipo_compania"))}
      {_ic("Nacionalidad", pv("nacionalidad"))}
      {_ic("Fecha constitución", pv("fecha_constitucion"))}
      {_ic("Expediente Supercias", pv("expediente_supercias"))}
      {_ic("Oficina de control", pv("oficina_control"))}
      {_ic("Plazo social", pv("plazo_social"))}
      {_ic("Objeto social", pv("objeto_social"), "full-width")}
    </div>
    {edit_block}
    """
