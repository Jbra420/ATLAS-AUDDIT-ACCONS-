"""views/auditor/radar/tab_documentos.py — Tab de documentos económicos."""
from __future__ import annotations
from ui.helpers import esc, csrf_input
from ui.icons import SVG_FILE


def build(audit_id: int, audit: object, docs: list, read_only: bool = False, csrf_token: str = "") -> str:
    docs_reviewed = sum(1 for d in docs if d["estado"] == "revisado")
    doc_items = ""
    for d in docs:
        reviewed = d["estado"] == "revisado"
        item_cls = "doc-item doc-reviewed" if reviewed else "doc-item"
        btn_cls = "btn-doc-review reviewed" if reviewed else "btn-doc-review"
        btn_text = "✓ Revisado" if reviewed else "Marcar revisado"
        accion = "revertir" if reviewed else "revisar"
        action_html = (
            f'<span class="{btn_cls}">{btn_text if reviewed else "Pendiente"}</span>'
            if read_only else
            f"""
            <form method="post" action="/auditor/radar/document" style="display:inline;">
              {csrf_input(csrf_token)}
              <input type="hidden" name="audit_id" value="{audit_id}">
              <input type="hidden" name="doc_id" value="{d['id']}">
              <input type="hidden" name="accion" value="{accion}">
              <button type="submit" class="{btn_cls}">{btn_text}</button>
            </form>
            """
        )
        doc_items += f"""
        <div class="{item_cls}">
          <div class="doc-icon">{SVG_FILE}</div>
          <div class="doc-info">
            <div class="doc-name">{esc(d['nombre'])}</div>
            <div class="doc-meta">Período: {esc(d['fecha'] or '—')}</div>
          </div>
          <div class="doc-actions">
            {action_html}
          </div>
        </div>"""

    return f"""
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Documentos económicos</h3>
      <span style="font-size:12px;color:var(--muted);">{docs_reviewed}/{len(docs)} revisados</span>
    </div>
    <div class="doc-list">{doc_items if doc_items else '<p style="color:var(--muted-2);">No hay documentos registrados.</p>'}</div>
    """
