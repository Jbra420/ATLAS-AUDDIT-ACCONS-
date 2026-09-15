"""views/auditor/radar/tab_fuentes.py — Tab de fuentes guiadas de consulta."""
from __future__ import annotations
from database import SOURCE_TYPES
from ui.helpers import esc, csrf_input
from ui.icons import SVG_EXTERNAL, SVG_SAVE, SOURCE_ICONS


def _src_icon(fuente: str) -> str:
    """Devuelve el ícono SVG correspondiente a la fuente, usando el mapa centralizado."""
    fuente_lower = fuente.lower()
    for key, icon in SOURCE_ICONS.items():
        if key.lower() in fuente_lower:
            return icon
    return SVG_EXTERNAL


def _source_option_html() -> str:
    return "".join(f'<option value="{esc(option)}">{esc(option)}</option>' for option in SOURCE_TYPES)


def _evidence_form(audit_id: int, csrf_token: str = "") -> str:
    return f"""
    <div class="evidence-capture-panel">
      <div class="evidence-capture-head">
        <div>
          <span class="source-map-eyebrow">Registro de evidencia</span>
          <h3>Bitacora de consulta del auditor</h3>
        </div>
      </div>
      <form method="post" action="/auditor/source" class="evidence-form">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <div class="grid">
          <div class="col-4">
            <label>Fuente</label>
            <select name="source_type" required>{_source_option_html()}</select>
          </div>
          <div class="col-4">
            <label>Titulo de evidencia</label>
            <input name="title" required placeholder="Consulta SERCOP / noticia / certificado">
          </div>
          <div class="col-4">
            <label>URL o referencia</label>
            <input name="url" placeholder="https://...">
          </div>
          <div class="col-6">
            <label>Resultado o hallazgo clave</label>
            <input name="finding" placeholder="No registra contratos publicos / sin alertas visibles">
          </div>
          <div class="col-6">
            <label>Notas internas</label>
            <input name="notes" placeholder="Fecha de consulta, criterio usado o limitacion">
          </div>
          <div class="col-12">
            <label>Evidencia textual</label>
            <textarea name="evidence_text" placeholder="Texto copiado desde SRI, Supercias, SERCOP o busqueda web..."></textarea>
          </div>
        </div>
        <div class="evidence-guide-grid">
          <div class="evidence-guide-card">
            <strong>SERCOP</strong>
            <span>Contratos, proveedor del Estado, inhabilitaciones o ausencia de registros.</span>
          </div>
          <div class="evidence-guide-card">
            <strong>Busqueda web general</strong>
            <span>Noticias, sanciones, referencias comerciales o coincidencias relevantes.</span>
          </div>
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} Registrar evidencia</button>
        </div>
      </form>
    </div>
    """


def _evidence_log(sources: list) -> str:
    items = ""
    for src in sources:
        url = src["url"] or ""
        notes = src["notes"] or ""
        items += f"""
        <article class="evidence-log-item">
          <div class="evidence-log-icon">{_src_icon(src["source_type"] or "")}</div>
          <div class="evidence-log-body">
            <div class="evidence-log-top">
              <span class="evidence-log-type">{esc(src["source_type"] or "Fuente")}</span>
              <span class="evidence-log-date">{esc(src["created_at"] or "")}</span>
            </div>
            <h4>{esc(src["title"])}</h4>
            {f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(url)}</a>' if url else ''}
            {f'<pre>{esc(notes)}</pre>' if notes else '<p class="evidence-log-empty">Sin notas adicionales.</p>'}
          </div>
        </article>
        """
    if not items:
        items = '<div class="evidence-empty">Todavia no hay evidencia registrada para este expediente.</div>'
    return f"""
    <div class="evidence-log-panel">
      <div class="evidence-log-head">
        <h3>Bitacora de evidencia registrada</h3>
        <span>{len(sources)} registro(s)</span>
      </div>
      <div class="evidence-log-list">{items}</div>
    </div>
    """


def build(audit_id: int, src_checks: list, sources: list | None = None, read_only: bool = False, csrf_token: str = "") -> str:
    sources = sources or []
    src_cards = ""
    for sc in src_checks:
        consulted  = sc["estado"] == "consultada"
        card_cls   = "source-check-card consulted" if consulted else "source-check-card"
        status_cls = "source-check-status consulted" if consulted else "source-check-status pending"
        status_txt = "✓ Consultada" if consulted else "□ Pendiente"
        accion_val = "revertir" if consulted else "consultar"
        btn_text   = "Marcar pendiente" if consulted else "Marcar consultada"
        obs_input  = '' if consulted else '<input name="observacion" class="source-form-obs" placeholder="Observación (opcional)">'
        form_html = "" if read_only else f"""
            <form method="post" action="/auditor/radar/source-check" class="source-check-actions">
              {csrf_input(csrf_token)}
              <input type="hidden" name="audit_id" value="{audit_id}">
              <input type="hidden" name="check_id" value="{sc['id']}">
              <input type="hidden" name="accion" value="{accion_val}">
              {obs_input}
              <button type="submit" class="btn btn-sm {'btn-primary' if not consulted else ''}">{btn_text}</button>
            </form>
        """
        src_cards += f"""
        <div class="{card_cls}">
          <div class="source-check-icon">{_src_icon(sc['fuente'])}</div>
          <div class="source-check-info">
            <div class="source-check-name">{esc(sc['fuente'])}</div>
            <div class="source-check-uso">{esc(sc['uso'] or '')}</div>
            <div class="{status_cls}">{status_txt}</div>
            {f'<div class="source-check-obs">{esc(sc["observacion"])}</div>' if sc["observacion"] else ""}
            {form_html}
          </div>
        </div>"""

    return f"""
    <div class="sources-tab-header">
      <h3>Fuentes guiadas de consulta</h3>
      <span>{'Modo solo lectura' if read_only else 'Trabajo del auditor'}</span>
    </div>
    <div class="source-check-grid">{src_cards if src_cards else '<p style="color:var(--muted-2);">No hay fuentes configuradas.</p>'}</div>
    {'' if read_only else _evidence_form(audit_id, csrf_token)}
    {_evidence_log(sources)}
    """
