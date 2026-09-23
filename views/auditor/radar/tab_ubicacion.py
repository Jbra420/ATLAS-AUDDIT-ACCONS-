"""views/auditor/radar/tab_ubicacion.py — Tab de ubicación y domicilio."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_MAP_PIN
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_UBICACION, etiqueta_traza, ultimo_por_campo
from ui.components import edit_panel, form_field, provenance_history

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
PLACEHOLDERS = {"numero": "S/N", "referencia": "Facilita la visita al cliente"}


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

    edit_fields = "".join(
        form_field(campo, etiqueta, lv(campo), placeholder=PLACEHOLDERS.get(campo, ""))
        for campo, etiqueta in CAMPOS
    )
    edit_block = "" if read_only else f"""
    <hr class="section-divider">
    {edit_panel("Editar ubicación", csrf_token, audit_id, "ubicacion", edit_fields, "Guardar ubicación", "col-6")}
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
