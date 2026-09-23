"""views/auditor/radar/tab_supercias.py — Tab de estado societario (Supercias)."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_EXTERNAL
from providers.supercias import SuperciasProvider
from services.normalizacion import clasificar_situacion_legal, clasificar_tipo_compania, con_valor_oficial
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_SUPERCIAS, etiqueta_traza, ultimo_por_campo
from ui.components import (
    edit_panel,
    form_field,
    info_card as _ic,
    provenance_history,
    source_check_control,
)

# (campo, etiqueta, clase CSS) — bloque 2 del levantamiento y datos del Directorio.
CAMPOS = (
    ("razon_social_supercias", "Razón social (Supercias)", "full-width"),
    ("expediente_supercias", "Expediente Supercias", ""),
    ("fecha_constitucion", "Fecha constitución", ""),
    ("tipo_compania", "Tipo de compañía", ""),
    ("situacion_legal", "Situación legal", ""),
    ("plazo_social", "Plazo social", ""),
    ("oficina_control", "Oficina de control", ""),
    ("nacionalidad", "Nacionalidad", ""),
    ("representante_legal", "Representante legal", ""),
    ("representante_cargo", "Cargo del representante", ""),
    ("telefono", "Teléfono", ""),
    ("capital_suscrito", "Capital suscrito", ""),
    ("ciiu_nivel1", "CIIU nivel 1", ""),
    ("ciiu_nivel6", "CIIU nivel 6", ""),
    ("ultimo_anio_balance", "Último año de balance", ""),
    ("objeto_social", "Objeto social", "full-width"),
)
ETIQUETAS = {campo: etiqueta for campo, etiqueta, _css in CAMPOS}

# Campos que se muestran como "categoría (texto oficial)".
_CLASIFICADORES = {
    "tipo_compania": clasificar_tipo_compania,
    "situacion_legal": clasificar_situacion_legal,
}

def _pval(profile, key):
    return str(row_get(profile, key, "")).strip()

def build(
    audit_id, audit, profile, research,
    read_only: bool = False, csrf_token: str = "", source_check: object | None = None,
    provenance: list | None = None,
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

    edit_fields = "".join(
        form_field(
            campo, etiqueta, pv(campo), col="col-12" if css else "col-6",
            placeholder="AAAA-MM-DD" if campo == "plazo_social" else "", textarea=campo == "objeto_social",
        )
        for campo, etiqueta, css in CAMPOS
    )
    edit_block = "" if read_only else f"""
    <div class="external-links-row">{links_html}</div>
    <hr class="section-divider">
    {edit_panel("Editar datos Supercias", csrf_token, audit_id, "supercias", edit_fields, "Guardar Supercias")}
    """
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_SUPERCIAS]
    trazas = ultimo_por_campo(historial)

    def _valor(campo: str) -> str:
        clasificar = _CLASIFICADORES.get(campo)
        return con_valor_oficial(clasificar(pv(campo)), pv(campo)) if clasificar else pv(campo)

    cards = "".join(
        _ic(etiqueta, _valor(campo), css, trace=etiqueta_traza(trazas.get((BLOQUE_SUPERCIAS, campo))))
        for campo, etiqueta, css in CAMPOS
    )
    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Estado societario — Supercias</h3>
      <div style="display:flex;align-items:center;gap:8px;">{fuente_badge}{check_html}</div>
    </div>
    <div class="info-grid">
      {cards}
    </div>
    {edit_block}
    {provenance_history(historial, ETIQUETAS, "Historial de datos Supercias")}
    """
