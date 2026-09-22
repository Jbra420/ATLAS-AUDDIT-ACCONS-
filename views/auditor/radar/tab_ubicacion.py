"""views/auditor/radar/tab_ubicacion.py — Tab de ubicación y domicilio."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
from ui.icons import SVG_MAP_PIN, SVG_SAVE
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_UBICACION, etiqueta_traza, ultimo_por_campo
from ui.components import fecha_consulta_field, provenance_history

# (campo, etiqueta) — bloque 3 del levantamiento.
CAMPOS = (
    ("provincia", "Provincia"),
    ("canton", "Cantón"),
    ("ciudad", "Ciudad"),
    ("calle", "Calle principal"),
    ("numero", "Número"),
    ("interseccion", "Intersección"),
    ("barrio", "Barrio"),
    ("referencia", "Referencia"),
)
ETIQUETAS = dict(CAMPOS)


def _lval(location, key):
    return str(row_get(location, key, "")).strip()


def build(audit_id, audit, location, read_only: bool = False, csrf_token: str = "",
          provenance: list | None = None):
    lv = lambda k: _lval(location, k)
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_UBICACION]
    trazas = ultimo_por_campo(historial)
    addr_parts = [lv("calle"), lv("numero"), lv("interseccion"), lv("barrio"), lv("ciudad"), lv("provincia")]
    full_addr = ", ".join(p for p in addr_parts if p) or "Dirección pendiente de confirmar"

    def loc_field(label, key):
        v = lv(key)
        cls = "pending" if not v else ""
        trace = etiqueta_traza(trazas.get((BLOQUE_UBICACION, key))) if v else ""
        trace_html = f'<small class="info-card-trace">{esc(trace)}</small>' if trace else ""
        return (
            f'<div class="location-field"><label>{label}</label>'
            f'<span class="{cls}">{esc(v or "Pendiente")}</span>{trace_html}</div>'
        )

    edit_block = "" if read_only else f"""
    <hr class="section-divider">
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">✎ Editar ubicación</summary>
      <form method="post" action="/auditor/radar/profile">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="return_tab" value="ubicacion">
        <div class="grid">
          <div class="col-6"><label>Provincia</label><input name="provincia" value="{esc(lv('provincia'))}"></div>
          <div class="col-6"><label>Cantón</label><input name="canton" value="{esc(lv('canton'))}"></div>
          <div class="col-6"><label>Ciudad</label><input name="ciudad" value="{esc(lv('ciudad'))}"></div>
          <div class="col-6"><label>Calle principal</label><input name="calle" value="{esc(lv('calle'))}"></div>
          <div class="col-6"><label>Número</label><input name="numero" value="{esc(lv('numero'))}" placeholder="S/N"></div>
          <div class="col-6"><label>Intersección</label><input name="interseccion" value="{esc(lv('interseccion'))}"></div>
          <div class="col-6"><label>Barrio</label><input name="barrio" value="{esc(lv('barrio'))}"></div>
          <div class="col-6"><label>Referencia</label><input name="referencia" value="{esc(lv('referencia'))}" placeholder="Facilita la visita al cliente"></div>
          {fecha_consulta_field("col-6")}
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Guardar ubicación</button>
        </div>
      </form>
    </details>
    """

    fields_html = "".join(loc_field(label, key) for key, label in CAMPOS)
    return f"""
    <div class="location-card">
      <div class="location-header">{SVG_MAP_PIN}<h3>Domicilio registrado</h3></div>
      <div class="location-grid">
        {fields_html}
      </div>
      <div class="location-full-addr">{SVG_MAP_PIN} {esc(full_addr)}</div>
    </div>
    {edit_block}
    {provenance_history(historial, ETIQUETAS, "Historial de ubicación")}
    """
