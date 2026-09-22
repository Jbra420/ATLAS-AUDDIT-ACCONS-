"""views/auditor/radar/tab_supercias.py — Tab de estado societario (Supercias)."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
from ui.icons import SVG_EXTERNAL, SVG_SAVE
from providers.supercias import SuperciasProvider
from services.normalizacion import clasificar_situacion_legal, clasificar_tipo_compania, con_valor_oficial
from services.rowutil import row_get
from ui.components import info_card as _ic, source_check_control

def _pval(profile, key):
    return str(row_get(profile, key, "")).strip()

def build(
    audit_id, audit, profile, research,
    read_only: bool = False, csrf_token: str = "", source_check: object | None = None,
):
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    pv = lambda k: _pval(profile, k)
    check_html = "" if read_only else source_check_control(audit_id, source_check, csrf_token, return_tab="supercias")
    links_html = ""
    if not read_only:
        for lnk in SuperciasProvider().get_links(ruc, company_name)[:2]:
            links_html += f'<a class="btn-ext-link" href="{esc(lnk.url)}" target="_blank" rel="noopener">{SVG_EXTERNAL} {esc(lnk.name)}</a>'

    fuente = pv("supercias_fuente")
    fecha_cat = pv("supercias_catalogo_fecha")
    fuente_badge = ""
    if fuente == "catalogo_local":
        fuente_badge = (
            f'<span class="badge badge-gray" title="Datos cargados desde el Directorio de Compañías '
            f'(catálogo local importado con scripts/update_supercias_catalog.py)">'
            f'Catálogo local{f" · corte {esc(fecha_cat)}" if fecha_cat else ""}</span>'
        )

    edit_block = "" if read_only else f"""
    <div class="external-links-row">{links_html}</div>
    <hr class="section-divider">
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">✎ Editar datos Supercias</summary>
      <form method="post" action="/auditor/radar/profile">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="return_tab" value="supercias">
        <div class="grid">
          <div class="col-12"><label>Razón social (Supercias)</label><input name="razon_social_supercias" value="{esc(pv('razon_social_supercias'))}"></div>
          <div class="col-6"><label>Situación legal</label><input name="situacion_legal" value="{esc(pv('situacion_legal'))}"></div>
          <div class="col-6"><label>Tipo de compañía</label><input name="tipo_compania" value="{esc(pv('tipo_compania'))}"></div>
          <div class="col-6"><label>Nacionalidad</label><input name="nacionalidad" value="{esc(pv('nacionalidad'))}"></div>
          <div class="col-6"><label>Fecha constitución</label><input name="fecha_constitucion" value="{esc(pv('fecha_constitucion'))}"></div>
          <div class="col-6"><label>Expediente Supercias</label><input name="expediente_supercias" value="{esc(pv('expediente_supercias'))}"></div>
          <div class="col-6"><label>Oficina de control</label><input name="oficina_control" value="{esc(pv('oficina_control'))}"></div>
          <div class="col-6"><label>Plazo social</label><input name="plazo_social" value="{esc(pv('plazo_social'))}" placeholder="AAAA-MM-DD"></div>
          <div class="col-6"><label>Representante legal</label><input name="representante_legal" value="{esc(pv('representante_legal'))}"></div>
          <div class="col-6"><label>Cargo del representante</label><input name="representante_cargo" value="{esc(pv('representante_cargo'))}"></div>
          <div class="col-6"><label>Teléfono</label><input name="telefono" value="{esc(pv('telefono'))}"></div>
          <div class="col-6"><label>Capital suscrito</label><input name="capital_suscrito" value="{esc(pv('capital_suscrito'))}"></div>
          <div class="col-6"><label>CIIU nivel 1</label><input name="ciiu_nivel1" value="{esc(pv('ciiu_nivel1'))}"></div>
          <div class="col-6"><label>CIIU nivel 6</label><input name="ciiu_nivel6" value="{esc(pv('ciiu_nivel6'))}"></div>
          <div class="col-6"><label>Último año de balance</label><input name="ultimo_anio_balance" value="{esc(pv('ultimo_anio_balance'))}"></div>
          <div class="col-12"><label>Objeto social</label><textarea name="objeto_social" style="min-height:60px;">{esc(pv('objeto_social'))}</textarea></div>
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar Supercias</button>
        </div>
      </form>
    </details>
    """
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Estado societario — Supercias</h3>
      <div style="display:flex;align-items:center;gap:8px;">{fuente_badge}{check_html}</div>
    </div>
    <div class="info-grid">
      {_ic("Razón social (Supercias)", pv("razon_social_supercias"), "full-width")}
      {_ic("Situación legal", con_valor_oficial(clasificar_situacion_legal(pv("situacion_legal")), pv("situacion_legal")))}
      {_ic("Tipo de compañía", con_valor_oficial(clasificar_tipo_compania(pv("tipo_compania")), pv("tipo_compania")))}
      {_ic("Nacionalidad", pv("nacionalidad"))}
      {_ic("Fecha constitución", pv("fecha_constitucion"))}
      {_ic("Expediente Supercias", pv("expediente_supercias"))}
      {_ic("Oficina de control", pv("oficina_control"))}
      {_ic("Plazo social", pv("plazo_social"))}
      {_ic("Representante legal", pv("representante_legal"))}
      {_ic("Cargo del representante", pv("representante_cargo"))}
      {_ic("Teléfono", pv("telefono"))}
      {_ic("Capital suscrito", pv("capital_suscrito"))}
      {_ic("CIIU nivel 1", pv("ciiu_nivel1"))}
      {_ic("CIIU nivel 6", pv("ciiu_nivel6"))}
      {_ic("Último año de balance", pv("ultimo_anio_balance"))}
      {_ic("Objeto social", pv("objeto_social"), "full-width")}
    </div>
    {edit_block}
    """
