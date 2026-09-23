"""views/auditor/radar/tab_documentos.py — Bitácora de evidencia del auditor.

Registra cada consulta hecha fuera de las otras pestañas (fuente, URL,
hallazgo y texto copiado) con la ruta /auditor/source. La evidencia alimenta
el Resumen y la ficha final.
"""
from __future__ import annotations

from database import SOURCE_TYPES
from services.rowutil import row_get
from ui.helpers import esc, hidden_inputs
from ui.icons import SVG_SAVE


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
        {hidden_inputs(csrf_token, audit_id=audit_id)}
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
    sources: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
) -> str:
    return f"""
    <div class="people-section-head">
      <div><h3>Bitácora de evidencia</h3><p>Registre cada consulta y su hallazgo como soporte del levantamiento.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    {"" if read_only else _evidence_form(audit_id, csrf_token)}
    {_evidence_log(sources)}
    """
