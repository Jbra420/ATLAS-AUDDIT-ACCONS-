"""views/auditor/radar/tab_resumen.py — Tab de resumen y exportaciones."""
from __future__ import annotations
from ui.helpers import esc
from ui.icons import SVG_DOWNLOAD, SVG_RADAR, SVG_SAVE


def build(audit_id: int, research: object, read_only: bool = False) -> str:
    summary_text = research["generated_summary"] or (
        "El auditor aún no ha generado el resumen estructurado."
        if read_only else
        "Presione 'Generar resumen' para crear el resumen estructurado."
    )
    actions_html = "" if read_only else f"""
      <div class="actions mt-0">
        <form method="post" action="/auditor/radar/summary" style="display:inline;">
          <input type="hidden" name="audit_id" value="{audit_id}">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_RADAR} Generar resumen</button>
        </form>
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
      <input type="hidden" name="audit_id" value="{audit_id}">
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
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
      <h3 style="margin:0;font-size:15px;">Resumen preliminar de investigación</h3>
      {actions_html}
    </div>
    <div class="summary-box-v3">{esc(summary_text)}</div>
    {observations_html}
    """
