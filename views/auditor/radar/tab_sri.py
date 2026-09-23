"""views/auditor/radar/tab_sri.py — Tab de identidad tributaria (SRI)."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_EXTERNAL
from providers.sri import SriProvider
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_SRI, etiqueta_traza, ultimo_por_campo
from ui.components import (
    edit_panel,
    form_field,
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
    ("ciiu_sri", "Código CIIU (SRI)", ""),
    ("actividad_economica", "Actividad económica", "full-width"),
)
ETIQUETAS = {campo: etiqueta for campo, etiqueta, _css in CAMPOS} | {"categoria": "Clase de contribuyente (código)"}
PLACEHOLDERS = {
    "estado_contribuyente": "ACTIVO / SUSPENDIDO",
    "tipo_contribuyente": "SOCIEDAD",
    "regimen": "GENERAL",
    "obligado_contabilidad": "SI / NO",
    "agente_retencion": "SI / NO",
    "contribuyente_especial": "SI / NO",
    "ciiu_sri": "I551001",
}
# Alertas del SRI que se capturan con un selector Sí / No / Sin consultar.
_SI_NO = {"contribuyente_fantasma", "transacciones_inexistentes"}


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

    edit_fields = "".join(
        form_field(
            campo, etiqueta, pv(campo), col="col-12" if css else "col-6",
            placeholder=PLACEHOLDERS.get(campo, ""),
        ) if campo not in _SI_NO else
        f'<div class="col-6"><label>{esc(etiqueta)}</label>{_si_no_select(campo, pv(campo))}</div>'
        for campo, etiqueta, css in CAMPOS if campo != "fecha_actualizacion"
    )
    edit_block = "" if read_only else f"""
    <div class="external-links-row">{sri_links_html}</div>
    <hr class="section-divider">
    {edit_panel("Editar datos SRI manualmente", csrf_token, audit_id, "sri", edit_fields, "Guardar SRI")}
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
