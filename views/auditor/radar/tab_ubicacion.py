"""views/auditor/radar/tab_ubicacion.py — Tab de ubicación y domicilio."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_MAP_PIN, SVG_SAVE

def _lval(location, key):
    if location:
        v = location[key]
        return (v or "").strip()
    return ""

def build(audit_id, audit, location, read_only: bool = False):
    lv = lambda k: _lval(location, k)
    addr_parts = [lv("calle"), lv("numero"), lv("interseccion"), lv("barrio"), lv("ciudad"), lv("provincia")]
    full_addr = ", ".join(p for p in addr_parts if p) or "Dirección pendiente de confirmar"
    def loc_field(label, key):
        v = lv(key)
        cls = "pending" if not v else ""
        return f'<div class="location-field"><label>{label}</label><span class="{cls}">{esc(v or "Pendiente")}</span></div>'
    edit_block = "" if read_only else f"""
    <hr class="section-divider">
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">✎ Editar ubicación</summary>
      <form method="post" action="/auditor/radar/profile">
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="return_tab" value="ubicacion">
        <div class="grid">
          <div class="col-6"><label>Provincia</label><input name="provincia" value="{esc(lv('provincia'))}"></div>
          <div class="col-6"><label>Cantón</label><input name="canton" value="{esc(lv('canton'))}"></div>
          <div class="col-6"><label>Ciudad</label><input name="ciudad" value="{esc(lv('ciudad'))}"></div>
          <div class="col-6"><label>Calle</label><input name="calle" value="{esc(lv('calle'))}"></div>
          <div class="col-6"><label>Número</label><input name="numero" value="{esc(lv('numero'))}" placeholder="S/N"></div>
          <div class="col-6"><label>Intersección</label><input name="interseccion" value="{esc(lv('interseccion'))}"></div>
          <div class="col-6"><label>Barrio</label><input name="barrio" value="{esc(lv('barrio'))}"></div>
          <div class="col-6"><label>Referencia</label><input name="referencia" value="{esc(lv('referencia'))}"></div>
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar ubicación</button>
        </div>
      </form>
    </details>
    """

    return f"""
    <div class="location-card">
      <div class="location-header">{SVG_MAP_PIN}<h3>Domicilio registrado</h3></div>
      <div class="location-grid">
        {loc_field("Provincia","provincia")}
        {loc_field("Cantón","canton")}
        {loc_field("Ciudad","ciudad")}
        {loc_field("Calle","calle")}
        {loc_field("Número","numero")}
        {loc_field("Intersección","interseccion")}
      </div>
      <div class="location-full-addr">{SVG_MAP_PIN} {esc(full_addr)}</div>
    </div>
    {edit_block}
    """
