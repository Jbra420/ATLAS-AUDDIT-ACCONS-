"""Tab de captura y consulta de socios o accionistas."""
from __future__ import annotations
from providers.supercias import SuperciasProvider
from services.company_search import find_certificate_evidence
from services.financial import formato_moneda
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_ACCIONISTAS
from ui.helpers import hidden_inputs, esc
from ui.components import (
    fecha_consulta_field,
    fuente_text as _fuente_text,
    identificacion_select as _tipo_select,
    identificacion_text as _identificacion_text,
    people_avatar as _avatar,
    provenance_history,
)
from ui.icons import SVG_CHECK, SVG_EXTERNAL, SVG_SAVE, SVG_TRASH


def _assisted_flow_panel(audit_id: int, audit, sources: list) -> str:
    """Ver tab_admins._assisted_flow_panel: mismo flujo, para el certificado
    de nómina de accionistas/socios (documento oficial distinto al de
    administradores)."""
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    evidence = find_certificate_evidence(sources, "accionista") or find_certificate_evidence(sources, "socio")
    if evidence:
        status_html = (
            f'<span class="badge badge-green">{SVG_CHECK} Certificado registrado como evidencia</span>'
        )
    else:
        status_html = '<span class="badge badge-gray">Certificado aún no registrado</span>'
    link_html = "".join(
        f'<a class="btn-ext-link" href="{esc(lnk.url)}" target="_blank" rel="noopener">'
        f'{SVG_EXTERNAL} {esc(lnk.name)}</a>'
        for lnk in SuperciasProvider().nomina_links(ruc, company_name)
    )
    docs_href = f"/auditor/radar?audit_id={audit_id}&tab=documentos#radar-tabs-main"
    return f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Flujo asistido — certificado oficial</span>
        <h4>Antes de registrar la nómina completa</h4>
      </div>
      <p style="font-size:13px;color:var(--muted);margin:0 0 12px;">
        El Directorio de Compañías no incluye accionistas. Obtenga el certificado electrónico
        de nómina de accionistas/socios (gratuito, sin registro previo), regístrelo como
        evidencia y luego transcriba cada accionista abajo. Atlas no extrae datos
        automáticamente del PDF: no hay muestras reales todavía para construir un lector
        confiable.
      </p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
        {status_html}
        {link_html}
        <a class="btn-ext-link" href="{docs_href}" onclick="return switchTab('documentos')">
          {SVG_EXTERNAL} Registrar certificado como evidencia
        </a>
      </div>
    </section>
    <hr class="section-divider">
    """


_PERMITIDOS = ("cedula", "ruc", "pasaporte")


def _participacion_text(row) -> str:
    partes = []
    porcentaje = row_get(row, "participacion_porcentaje", None)
    capital = row_get(row, "capital", None)
    if porcentaje is not None:
        partes.append(f"{float(porcentaje):g} %")
    if capital is not None:
        partes.append(formato_moneda(float(capital)))
    return " · ".join(partes) or "—"


def _number_value(row, key: str) -> str:
    value = row_get(row, key, None)
    return "" if value is None else f"{float(value):g}"


def _complete_form(audit_id: int, s, csrf_token: str) -> str:
    """Formulario por fila para completar identificación, participación y
    beneficiario final sin volver a escribir el nombre."""
    return f"""
    <details class="row-edit">
      <summary>Completar</summary>
      <form method="post" action="/auditor/radar/shareholder">
        {hidden_inputs(csrf_token, audit_id=audit_id, shareholder_id=s['id'], action="update")}
        <label>Tipo de identificación</label>{_tipo_select(row_get(s, "tipo_identificacion") or "cedula", _PERMITIDOS)}
        <label>Identificación</label>
        <input name="identificacion" maxlength="32" value="{esc(row_get(s, 'identificacion') or '')}">
        <label>Participación (%)</label>
        <input name="participacion_porcentaje" type="number" step="0.0001" min="0" max="100" value="{_number_value(s, 'participacion_porcentaje')}">
        <label>Capital (USD)</label>
        <input name="capital" type="number" step="0.01" min="0" value="{_number_value(s, 'capital')}">
        <label>Beneficiario final</label>
        <input name="beneficiario_final" maxlength="160" value="{esc(row_get(s, 'beneficiario_final') or '')}">
        {fecha_consulta_field("")}
        <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Guardar</button>
      </form>
    </details>
    """


def build(
    audit_id: int,
    audit,
    shareholders: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
    sources: list | None = None,
    provenance: list | None = None,
) -> str:
    rows = "".join(
        f'<tr>'
        f'<td style="font-size:12px;color:var(--muted);width:40px;">{esc(str(s["numero"] or "—"))}</td>'
        f'<td>{_avatar(s["nombre"])}{esc(s["nombre"])}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(_identificacion_text(s))}</td>'
        f'<td style="font-size:13px;">{esc(_participacion_text(s))}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(row_get(s, "beneficiario_final") or "—")}</td>'
        f'<td style="font-size:12px;color:var(--muted);">{esc(_fuente_text(s))}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          {_complete_form(audit_id, s, csrf_token)}
          <form method="post" action="/auditor/radar/shareholder">
            {hidden_inputs(csrf_token, audit_id=audit_id, shareholder_id=s['id'], action="delete")}
            <button type="submit" class="btn-icon-danger" title="Eliminar accionista" aria-label="Eliminar accionista">{SVG_TRASH}</button>
          </form>
        </td>''')
        + f'</tr>'
        for s in shareholders
    ) if shareholders else (
        f'<tr><td colspan="{6 if read_only else 7}" style="color:var(--muted-2);font-style:italic;">'
        'Sin accionistas registrados. Consulte Supercias.</td></tr>'
    )
    porcentajes = [
        float(row_get(s, "participacion_porcentaje")) for s in shareholders
        if row_get(s, "participacion_porcentaje", None) is not None
    ]
    total_html = (
        f'<p style="font-size:13px;color:var(--muted);margin:8px 0 0;">'
        f'Suma de participaciones registradas: <strong>{sum(porcentajes):g} %</strong> '
        f'({len(porcentajes)} de {len(shareholders)} accionista(s) con porcentaje).</p>'
        if porcentajes else ""
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria — Supercias, Accionistas y Kárdex</span>
        <h4>Registrar socio o accionista</h4>
      </div>
      <form method="post" action="/auditor/radar/shareholder">
        {hidden_inputs(csrf_token, audit_id=audit_id, action="add")}
        <div class="grid">
          <div class="col-2"><label>Número</label><input name="numero" type="number" min="1" placeholder="Auto"></div>
          <div class="col-5"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
          <div class="col-2"><label>Tipo</label>{_tipo_select("cedula", _PERMITIDOS)}</div>
          <div class="col-3"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="Cédula, RUC o pasaporte"></div>
          <div class="col-3"><label>Participación (%)</label><input name="participacion_porcentaje" type="number" step="0.0001" min="0" max="100"></div>
          <div class="col-3"><label>Capital (USD)</label><input name="capital" type="number" step="0.01" min="0"></div>
          <div class="col-6"><label>Beneficiario final</label><input name="beneficiario_final" maxlength="160" placeholder="Persona natural que controla la participación"></div>
          {fecha_consulta_field()}
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    assisted_panel = "" if read_only else _assisted_flow_panel(audit_id, audit, sources or [])
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_ACCIONISTAS]
    return f"""
    {assisted_panel}
    <div class="people-section-head">
      <div><h3>Nómina de socios / accionistas</h3><p>{len(shareholders)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>#</th><th>Nombre</th><th>Identificación</th><th>Participación</th><th>Beneficiario final</th><th>Fuente</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {total_html}
    {editor}
    {provenance_history(historial, {}, "Historial de accionistas")}
    """
