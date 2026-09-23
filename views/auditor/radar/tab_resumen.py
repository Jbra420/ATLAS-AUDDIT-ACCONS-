"""views/auditor/radar/tab_resumen.py — Tab de resumen y exportaciones."""
from __future__ import annotations
from ui.components import modal
from ui.helpers import esc, hidden_inputs
from ui.icons import SVG_DOWNLOAD, SVG_RADAR, SVG_SAVE


def _tab_href(audit_id: int, tab_id: str, read_only: bool) -> str:
    route = "/admin/audit" if read_only else "/auditor/radar"
    return f"{route}?audit_id={audit_id}&tab={tab_id}#radar-tabs-main"


def _metric(label: str, value: str, hint: str = "") -> str:
    hint_html = f"<span>{esc(hint)}</span>" if hint else ""
    return f"""
      <article class="dossier-metric">
        <strong>{esc(value)}</strong>
        <label>{esc(label)}</label>
        {hint_html}
      </article>
    """


def _list_items(items: list[str]) -> str:
    return "".join(f"<li>{esc(item)}</li>" for item in items)


PENDING_MODAL_ID = "summaryPendingModal"


def _pending_items(title: str, items: list[dict], audit_id: int) -> str:
    """Lista de puntos faltantes; "Completar" cierra el modal y abre la pestaña."""
    if not items:
        return ""
    rows = "".join(
        f"""
        <li class="summary-check-item">
          <span><strong>{esc(item['label'])}</strong><small>{esc(item['source'])}</small></span>
          <a href="{_tab_href(audit_id, item['tab'], False)}"
             onclick="closeModal('{PENDING_MODAL_ID}'); return switchTab('{item['tab']}')">Completar</a>
        </li>
        """
        for item in items
    )
    return f"""
      <div>
        <span class="summary-check-title">{esc(title)} ({len(items)})</span>
        <ul class="summary-check-list">{rows}</ul>
      </div>
    """


def _pending_modal(readiness: dict, audit_id: int, csrf_token: str) -> str:
    """Modal previo a generar el resumen: muestra lo que falta y permite
    continuar igual. Lo pendiente queda en la sección 10 del resumen."""
    blockers, warnings = readiness.get("blockers", []), readiness.get("warnings", [])
    return modal(PENDING_MODAL_ID, "Faltan datos del levantamiento", f"""
      <p class="modal-desc">
        Puede completarlos ahora o generar el resumen igual: los puntos pendientes
        quedarán listados en su sección «Pendientes de validación».
      </p>
      <div class="summary-pending-groups">
        {_pending_items("Obligatorios pendientes", blockers, audit_id)}
        {_pending_items("Recomendaciones", warnings, audit_id)}
      </div>
      <form method="post" action="/auditor/radar/summary" class="modal-actions">
        {hidden_inputs(csrf_token, audit_id=audit_id, confirmar_pendientes="1")}
        <button type="button" class="btn" onclick="closeModal('{PENDING_MODAL_ID}')">Volver y completar</button>
        <button type="submit" class="btn btn-primary">{SVG_RADAR} Generar de todas formas</button>
      </form>
    """, wide=True)


def _render_dossier(audit_id: int, dossier: dict | None) -> str:
    if not dossier:
        return ""

    metrics = dossier.get("metrics", {})
    identity = "".join(
        f'<div class="dossier-kv"><span>{esc(item["label"])}</span><strong>{esc(item["value"])}</strong></div>'
        for item in dossier.get("identity", [])
    )
    source_rows = "".join(
        f"""
        <tr>
          <td>{esc(row["title"])}</td>
          <td>{esc(row["status"])}</td>
          <td>{esc(row["completed"])}</td>
          <td>{esc(row["missing"])}</td>
        </tr>
        """
        for row in dossier.get("source_status", [])
    )
    evidence_rows = "".join(
        f"""
        <li>
          <strong>{esc(row["title"])}</strong>
          <span>{esc(row["type"])} · {esc(row["notes"])}</span>
        </li>
        """
        for row in dossier.get("evidence", [])
    ) or "<li><strong>Sin evidencias registradas</strong><span>Agregue enlaces o notas desde la pestaña Documentos.</span></li>"
    financial = "".join(
        f'<div class="dossier-kv"><span>{esc(item["label"])}</span><strong>{esc(item["value"])}</strong></div>'
        for item in dossier.get("financial", [])
    )
    return f"""
    <section class="dossier-panel">
      <div class="dossier-head">
        <div>
          <span class="dossier-eyebrow">Cierre preliminar</span>
          <h3>Ficha final de resultados</h3>
          <p>{esc(dossier.get("closing", ""))}</p>
        </div>
        <a class="btn btn-sm" href="/export/dossier?audit_id={audit_id}" title="Descargar ficha final en .txt">
          {SVG_DOWNLOAD} Exportar ficha final
        </a>
      </div>
      <div style="background-color: var(--color-warning-light, #fff3cd); color: var(--color-warning-dark, #856404); padding: 12px; border-radius: 6px; border: 1px solid var(--color-warning-border, #ffeeba); margin-bottom: 20px; font-size: 13px; display: flex; align-items: center; gap: 8px;">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
        <span><strong>Nota importante:</strong> Esta ficha es una lectura técnica preliminar. <strong>No sustituye el Informe Final de Auditoría.</strong></span>
      </div>

      <div class="dossier-metrics">
        {_metric("Avance de fuentes", f'{metrics.get("source_percent", 0)}%')}
        {_metric("Fuentes completas", str(metrics.get("completed_sources", 0)), f'{metrics.get("partial_sources", 0)} en avance')}
        {_metric("Evidencias", str(metrics.get("evidence_count", 0)), "registros de soporte")}
        {_metric("Riesgos", str(metrics.get("risk_count", 0)), f'{metrics.get("pending_count", 0)} pendientes')}
      </div>
      <div class="dossier-grid">
        <article class="dossier-section">
          <h4>Identificación</h4>
          <div class="dossier-kv-grid">{identity}</div>
        </article>
        <article class="dossier-section">
          <h4>Indicadores clave</h4>
          <div class="dossier-kv-grid">{financial}</div>
        </article>
      </div>
      <article class="dossier-section">
        <h4>Estado de fuentes</h4>
        <div class="dossier-table-wrap">
          <table class="dossier-table">
            <thead><tr><th>Fuente</th><th>Estado</th><th>Avance</th><th>Pendiente</th></tr></thead>
            <tbody>{source_rows}</tbody>
          </table>
        </div>
      </article>
      <div class="dossier-grid">
        <article class="dossier-section">
          <h4>Evidencia registrada</h4>
          <ul class="dossier-evidence">{evidence_rows}</ul>
        </article>
        <article class="dossier-section">
          <h4>Riesgos y pendientes</h4>
          <div class="dossier-lists">
            <div><span>Riesgos</span><ul>{_list_items(dossier.get("risks", []))}</ul></div>
            <div><span>Pendientes</span><ul>{_list_items(dossier.get("pending", []))}</ul></div>
          </div>
        </article>
      </div>
    </section>
    """


def build(
    audit_id: int,
    research: object,
    dossier: dict | None = None,
    readiness: dict | None = None,
    read_only: bool = False,
    csrf_token: str = "",
) -> str:
    readiness = readiness or {"blockers": [], "warnings": []}
    has_pending = bool(readiness.get("blockers") or readiness.get("warnings"))
    summary_text = research["generated_summary"] or (
        "El auditor aún no ha generado el resumen estructurado."
        if read_only else
        "Presione 'Generar resumen' para crear el resumen estructurado."
    )
    actions_html = pending_modal_html = ""
    if not read_only:
        # Con puntos pendientes, el botón abre el modal en vez de enviar.
        generate = (
            f"""<button type="button" class="btn btn-sm btn-primary" onclick="openModal('{PENDING_MODAL_ID}')">
              {SVG_RADAR} Generar resumen</button>"""
            if has_pending else
            f"""<form method="post" action="/auditor/radar/summary" style="display:inline;">
              {hidden_inputs(csrf_token, audit_id=audit_id)}
              <button type="submit" class="btn btn-sm btn-primary">{SVG_RADAR} Generar resumen</button>
            </form>"""
        )
        pending_modal_html = _pending_modal(readiness, audit_id, csrf_token) if has_pending else ""
        actions_html = f"""
      <div class="actions mt-0">
        {generate}
        <a class="btn btn-sm" href="/export/summary?audit_id={audit_id}">{SVG_DOWNLOAD} TXT</a>
        <a class="btn btn-sm" href="/export/csv?audit_id={audit_id}">{SVG_DOWNLOAD} CSV</a>
      </div>
        """
    observations_html = (
        f"""
    <hr class="section-divider">
    <h4 style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--muted);margin:0 0 12px;">
      Observaciones adicionales del auditor
    </h4>
    <div class="info-grid">
      <div class="info-card full-width"><div class="info-card-label">Observaciones</div><div class="info-card-value">{esc(research['observations'] or 'Pendiente de confirmar')}</div></div>
      <div class="info-card full-width"><div class="info-card-label">Riesgos identificados manualmente</div><div class="info-card-value">{esc(research['risk_flags'] or 'Pendiente de confirmar')}</div></div>
      <div class="info-card full-width"><div class="info-card-label">Evidencia textual pegada</div><div class="info-card-value">{esc(research['pasted_text'] or 'Pendiente de confirmar')}</div></div>
    </div>
        """
        if read_only else
        f"""
    <hr class="section-divider">
    <h4 style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:var(--muted);margin:0 0 12px;">
      Observaciones adicionales del auditor
    </h4>
    <form method="post" action="/auditor/radar">
      {hidden_inputs(csrf_token, audit_id=audit_id)}
      <div class="grid">
        <div class="col-6"><label>Observaciones</label>
          <textarea name="observations" style="min-height:80px;">{esc(research['observations'] or '')}</textarea></div>
        <div class="col-6"><label>Riesgos identificados manualmente</label>
          <textarea name="risk_flags" style="min-height:80px;">{esc(research['risk_flags'] or '')}</textarea></div>
        <div class="col-12"><label>Evidencia textual pegada (para señales)</label>
          <textarea name="pasted_text" style="min-height:80px;" placeholder="Pegue texto de las fuentes consultadas...">{esc(research['pasted_text'] or '')}</textarea></div>
      </div>
      <input type="hidden" name="commercial_name" value="{esc(research['commercial_name'] or '')}">
      <input type="hidden" name="economic_activity" value="{esc(research['economic_activity'] or '')}">
      <input type="hidden" name="legal_status" value="{esc(research['legal_status'] or '')}">
      <input type="hidden" name="representative" value="{esc(research['representative'] or '')}">
      <input type="hidden" name="address" value="{esc(research['address'] or '')}">
      <input type="hidden" name="tax_obligations" value="{esc(research['tax_obligations'] or '')}">
      <input type="hidden" name="public_contracting" value="{esc(research['public_contracting'] or '')}">
      <input type="hidden" name="supercias_info" value="{esc(research['supercias_info'] or '')}">
      <input type="hidden" name="sri_info" value="{esc(research['sri_info'] or '')}">
      <input type="hidden" name="sercop_info" value="{esc(research['sercop_info'] or '')}">
      <input type="hidden" name="submit_mode" value="save">
      <div class="actions" style="justify-content:flex-end;margin-top:12px;">
        <button type="submit" class="btn btn-sm">{SVG_SAVE} Guardar observaciones</button>
      </div>
    </form>
        """
    )

    return f"""
    {_render_dossier(audit_id, dossier)}
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Resumen preliminar de investigación</h3>
      {actions_html}
    </div>
    <div class="summary-box-v3">{esc(summary_text)}</div>
    {observations_html}
    {pending_modal_html}
    """
