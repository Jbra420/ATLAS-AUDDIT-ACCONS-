"""views/auditor/radar/tab_documentos.py — Documentos económicos y evidencia complementaria.

Reúne lo que antes vivía en tab_documentos.py + tab_fuentes.py (eliminados en
7b80504 sin reemplazo): el checklist de economic_documents, las fuentes
guiadas que no tienen su propio tab (Supercias — Documentos económicos,
SERCOP, Búsqueda web general) y la bitácora de evidencia libre. Reutiliza las
rutas ya existentes en core/server.py (/auditor/radar/document,
/auditor/radar/source-check, /auditor/source) y las clases CSS vigentes
(.people-table, .people-editor, .dossier-evidence) en vez de las bespoke que
ya fueron purgadas de static/atlas.css.
"""
from __future__ import annotations

from database import SOURCE_TYPES
from services.rowutil import row_get
from ui.components import source_check_control
from ui.helpers import esc, csrf_input
from ui.icons import SVG_EXTERNAL, SVG_SAVE, SOURCE_ICONS


def _src_icon(fuente: str) -> str:
    fuente_lower = (fuente or "").lower()
    for key, icon in SOURCE_ICONS.items():
        if key.lower() in fuente_lower:
            return icon
    return SVG_EXTERNAL


def _document_rows(audit_id: int, docs: list, read_only: bool, csrf_token: str) -> str:
    rows = ""
    for d in docs:
        reviewed = row_get(d, "estado") == "revisado"
        accion = "revertir" if reviewed else "revisar"
        btn_txt = "Revertir" if reviewed else "Marcar revisado"
        status_html = (
            f'<span class="badge {"badge-green" if reviewed else "badge-gray"}">'
            f'{"Revisado" if reviewed else "Pendiente"}</span>'
        )
        action_cell = "" if read_only else f'''
        <td class="people-table-actions">
          <form method="post" action="/auditor/radar/document">
            {csrf_input(csrf_token)}
            <input type="hidden" name="audit_id" value="{audit_id}">
            <input type="hidden" name="doc_id" value="{row_get(d, "id")}">
            <input type="hidden" name="accion" value="{accion}">
            <button type="submit" class="btn btn-sm">{btn_txt}</button>
          </form>
        </td>'''
        rows += f'''<tr>
          <td>{esc(row_get(d, "nombre"))}</td>
          <td style="font-size:13px;color:var(--muted);">{esc(row_get(d, "fecha") or "—")}</td>
          <td>{status_html}</td>
          {action_cell}
        </tr>'''
    if not rows:
        colspan = 3 if read_only else 4
        rows = (
            f'<tr><td colspan="{colspan}" style="color:var(--muted-2);font-style:italic;">'
            'Sin documentos registrados.</td></tr>'
        )
    return rows


def _check_rows(audit_id: int, checks: list, read_only: bool, csrf_token: str) -> str:
    rows = ""
    for c in checks:
        consulted = row_get(c, "estado") == "consultada"
        status_html = (
            f'<span class="badge {"badge-green" if consulted else "badge-gray"}">'
            f'{"Consultada" if consulted else "Pendiente"}</span>'
        )
        action_cell = (
            f'<td>{status_html}</td>' if read_only else
            f'<td>{source_check_control(audit_id, c, csrf_token, return_tab="documentos")}</td>'
        )
        rows += f'''<tr>
          <td>{_src_icon(row_get(c, "fuente"))} {esc(row_get(c, "fuente"))}</td>
          <td style="font-size:13px;color:var(--muted);">{esc(row_get(c, "uso") or "—")}</td>
          {action_cell}
        </tr>'''
    if not rows:
        rows = (
            '<tr><td colspan="3" style="color:var(--muted-2);font-style:italic;">'
            'No hay fuentes adicionales configuradas.</td></tr>'
        )
    return rows


def _source_option_html() -> str:
    return "".join(f'<option value="{esc(option)}">{esc(option)}</option>' for option in SOURCE_TYPES)


def _evidence_form(audit_id: int, csrf_token: str) -> str:
    return f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Registro de evidencia</span>
        <h4>Bitácora de consulta del auditor</h4>
      </div>
      <form method="post" action="/auditor/source">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <div class="grid">
          <div class="col-4"><label>Fuente</label><select name="source_type" required>{_source_option_html()}</select></div>
          <div class="col-4"><label>Título de evidencia</label><input name="title" required placeholder="Consulta SERCOP / noticia / certificado"></div>
          <div class="col-4"><label>URL o referencia</label><input name="url" placeholder="https://..."></div>
          <div class="col-6"><label>Resultado o hallazgo clave</label><input name="finding" placeholder="No registra contratos públicos / sin alertas visibles"></div>
          <div class="col-6"><label>Notas internas</label><input name="notes" placeholder="Fecha de consulta, criterio usado o limitación"></div>
          <div class="col-12"><label>Evidencia textual</label><textarea name="evidence_text" style="min-height:60px;" placeholder="Texto copiado desde SRI, Supercias, SERCOP o búsqueda web..."></textarea></div>
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar evidencia</button>
        </div>
      </form>
    </section>
    """


def _evidence_log(sources: list) -> str:
    items = "".join(
        f'''<li>
          <strong>{esc(row_get(s, "title"))}</strong>
          <span>{esc(row_get(s, "source_type") or "Fuente")} · {esc(row_get(s, "notes") or "Sin notas")}</span>
        </li>'''
        for s in sources
    ) or '<li><strong>Sin evidencias registradas</strong><span>Agregue enlaces o notas desde el formulario de arriba.</span></li>'
    return f"""
    <section class="dossier-section">
      <h4>Bitácora de evidencia registrada ({len(sources)})</h4>
      <ul class="dossier-evidence">{items}</ul>
    </section>
    """


def build(
    audit_id: int,
    docs: list,
    other_checks: list,
    sources: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
) -> str:
    reviewed = sum(1 for d in docs if row_get(d, "estado") == "revisado")
    docs_actions_header = "" if read_only else "<th>Acciones</th>"
    checks_actions_header = "Estado" if read_only else "Acciones"

    return f"""
    <div class="people-section-head">
      <div><h3>Documentos económicos</h3><p>{reviewed}/{len(docs)} documento(s) revisados.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>Documento</th><th>Período</th><th>Estado</th>{docs_actions_header}</tr></thead>
        <tbody>{_document_rows(audit_id, docs, read_only, csrf_token)}</tbody>
      </table>
    </div>

    <hr class="section-divider">
    <h3 style="margin:0 0 16px;font-size:15px;">Otras fuentes guiadas</h3>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>Fuente</th><th>Uso</th><th>{checks_actions_header}</th></tr></thead>
        <tbody>{_check_rows(audit_id, other_checks, read_only, csrf_token)}</tbody>
      </table>
    </div>

    <hr class="section-divider">
    {"" if read_only else _evidence_form(audit_id, csrf_token)}
    {_evidence_log(sources)}
    """
