"""Tab de captura y consulta de administradores de la empresa."""
from __future__ import annotations
from providers.supercias import SuperciasProvider
from services.company_search import find_certificate_evidence
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_ADMINISTRADORES
from ui.helpers import csrf_input, esc
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
    """Recuerda que el Directorio local no trae la nómina completa y guía al
    auditor a obtener el certificado oficial y registrarlo como evidencia
    antes de transcribir administradores. No hay parser automático de PDF:
    no existen aún muestras reales del certificado para validar uno
    (ver propuesta técnica, sección 11 — Administradores y accionistas)."""
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    cert_link = next(
        (lnk for lnk in SuperciasProvider().get_links(ruc, company_name)
         if "certificado" in lnk.name.lower()),
        None,
    )
    evidence = find_certificate_evidence(sources, "administrador")
    if evidence:
        status_html = (
            f'<span class="badge badge-green">{SVG_CHECK} Certificado registrado como evidencia</span>'
        )
    else:
        status_html = '<span class="badge badge-gray">Certificado aún no registrado</span>'
    link_html = (
        f'<a class="btn-ext-link" href="{esc(cert_link.url)}" target="_blank" rel="noopener">'
        f'{SVG_EXTERNAL} {esc(cert_link.name)}</a>'
        if cert_link else ""
    )
    docs_href = f"/auditor/radar?audit_id={audit_id}&tab=documentos#radar-tabs-main"
    return f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Flujo asistido — certificado oficial</span>
        <h4>Antes de registrar la nómina completa</h4>
      </div>
      <p style="font-size:13px;color:var(--muted);margin:0 0 12px;">
        El Directorio de Compañías solo trae el representante legal actual, no la nómina
        completa de administradores. Obtenga el certificado electrónico oficial (gratuito,
        sin registro previo), regístrelo como evidencia y luego transcriba cada administrador
        abajo. Atlas no extrae datos automáticamente del PDF: no hay muestras reales todavía
        para construir un lector confiable.
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


def _complete_form(audit_id: int, a, csrf_token: str) -> str:
    """Formulario por fila para completar identificación y nacionalidad sin
    volver a escribir nombre y cargo (p. ej. el registro que trajo el Directorio)."""
    return f"""
    <details class="row-edit">
      <summary>Completar</summary>
      <form method="post" action="/auditor/radar/administrator">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="administrator_id" value="{a['id']}">
        <input type="hidden" name="action" value="update">
        <label>Tipo de identificación</label>{_tipo_select(row_get(a, "tipo_identificacion") or "cedula")}
        <label>Identificación</label>
        <input name="identificacion" maxlength="32" value="{esc(row_get(a, 'identificacion') or '')}">
        <label>Nacionalidad</label>
        <input name="nacionalidad" maxlength="80" value="{esc(row_get(a, 'nacionalidad') or '')}">
        {fecha_consulta_field("")}
        <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Guardar</button>
      </form>
    </details>
    """


def build(
    audit_id: int,
    audit,
    admins: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
    sources: list | None = None,
    provenance: list | None = None,
) -> str:
    rows = "".join(
        f'<tr><td>{_avatar(a["nombre"])}{esc(a["nombre"])}</td>'
        f'<td><span class="cargo-badge">{esc(a["cargo"])}</span></td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(_identificacion_text(a))}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["nacionalidad"] or "—")}</td>'
        f'<td style="font-size:12px;color:var(--muted);">{esc(_fuente_text(a))}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          {_complete_form(audit_id, a, csrf_token)}
          <form method="post" action="/auditor/radar/administrator">
            {csrf_input(csrf_token)}
            <input type="hidden" name="audit_id" value="{audit_id}">
            <input type="hidden" name="administrator_id" value="{a['id']}">
            <input type="hidden" name="action" value="delete">
            <button type="submit" class="btn-icon-danger" title="Eliminar administrador" aria-label="Eliminar administrador">{SVG_TRASH}</button>
          </form>
        </td>''')
        + '</tr>'
        for a in admins
    ) if admins else (
        f'<tr><td colspan="{5 if read_only else 6}" style="color:var(--muted-2);font-style:italic;">'
        'Sin administradores registrados. Consulte Supercias.</td></tr>'
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria — Supercias, Administradores actuales</span>
        <h4>Registrar administrador</h4>
      </div>
      <p style="font-size:13px;color:var(--muted);margin:0 0 12px;">
        Se requiere al menos el gerente general y el presidente, cada uno con su identificación.
      </p>
      <form method="post" action="/auditor/radar/administrator">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="action" value="add">
        <div class="grid">
          <div class="col-4"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
          <div class="col-4"><label>Cargo *</label><input name="cargo" maxlength="120" required placeholder="Gerente general"></div>
          <div class="col-4"><label>Nacionalidad</label><input name="nacionalidad" maxlength="80"></div>
          <div class="col-4"><label>Tipo de identificación</label>{_tipo_select("cedula")}</div>
          <div class="col-4"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="10 dígitos si es cédula"></div>
          {fecha_consulta_field()}
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    assisted_panel = "" if read_only else _assisted_flow_panel(audit_id, audit, sources or [])
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_ADMINISTRADORES]
    return f"""
    {assisted_panel}
    <div class="people-section-head">
      <div><h3>Administradores registrados</h3><p>{len(admins)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>Nombre</th><th>Cargo</th><th>Identificación</th><th>Nacionalidad</th><th>Fuente</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {editor}
    {provenance_history(historial, {}, "Historial de administradores")}
    """
