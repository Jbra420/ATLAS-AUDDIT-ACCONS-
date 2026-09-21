"""Tab de captura y consulta de socios o accionistas."""
from __future__ import annotations
from providers.supercias import SuperciasProvider
from services.company_search import find_certificate_evidence
from ui.helpers import csrf_input, esc
from ui.components import people_avatar as _avatar
from ui.icons import SVG_CHECK, SVG_EXTERNAL, SVG_SAVE, SVG_TRASH


def _assisted_flow_panel(audit_id: int, audit, sources: list) -> str:
    """Ver tab_admins._assisted_flow_panel: mismo flujo, para el certificado
    de nómina de accionistas/socios (documento oficial distinto al de
    administradores)."""
    ruc = audit["ruc"] or ""
    company_name = audit["company_name"]
    cert_link = next(
        (lnk for lnk in SuperciasProvider().get_links(ruc, company_name)
         if "certificado" in lnk.name.lower()),
        None,
    )
    evidence = find_certificate_evidence(sources, "accionista") or find_certificate_evidence(sources, "socio")
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


def build(
    audit_id: int,
    audit,
    shareholders: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
    sources: list | None = None,
) -> str:  # noqa: ARG001
    rows = "".join(
        f'<tr>'
        f'<td style="font-size:12px;color:var(--muted);width:40px;">{esc(str(s["numero"] or "—"))}</td>'
        f'<td>{_avatar(s["nombre"])}{esc(s["nombre"])}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(s["identificacion"] or "—")}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          <form method="post" action="/auditor/radar/shareholder">
            {csrf_input(csrf_token)}
            <input type="hidden" name="audit_id" value="{audit_id}">
            <input type="hidden" name="shareholder_id" value="{s['id']}">
            <input type="hidden" name="action" value="delete">
            <button type="submit" class="btn-icon-danger" title="Eliminar accionista" aria-label="Eliminar accionista">{SVG_TRASH}</button>
          </form>
        </td>''')
        + f'</tr>'
        for s in shareholders
    ) if shareholders else (
        f'<tr><td colspan="{3 if read_only else 4}" style="color:var(--muted-2);font-style:italic;">'
        'Sin accionistas registrados. Consulte Supercias.</td></tr>'
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria</span>
        <h4>Registrar socio o accionista</h4>
      </div>
      <form method="post" action="/auditor/radar/shareholder">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="action" value="add">
        <div class="grid">
          <div class="col-3"><label>Número</label><input name="numero" type="number" min="1" placeholder="Automático"></div>
          <div class="col-4"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="Cédula, RUC o pasaporte"></div>
          <div class="col-5"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    assisted_panel = "" if read_only else _assisted_flow_panel(audit_id, audit, sources or [])
    return f"""
    {assisted_panel}
    <div class="people-section-head">
      <div><h3>Nómina de socios / accionistas</h3><p>{len(shareholders)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>#</th><th>Nombre</th><th>Identificación</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {editor}
    """
