"""
views/admin/dashboard.py — Panel general del jefe auditor.
"""
from __future__ import annotations

import sqlite3

from database import list_admin_audits
from ui.components import badge
from ui.helpers import esc, form_value
from ui.icons import SVG_ARROW_RIGHT, SVG_BRIEFCASE, SVG_CHECK, SVG_CLOCK, SVG_FILE
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str) -> str:
    """Genera el HTML del dashboard administrativo completo."""
    audits = list_admin_audits()

    total = len(audits)
    en_prog = sum(1 for a in audits if a["status"] == "en_investigacion")
    pendiente = sum(1 for a in audits if a["status"] == "pendiente")
    con_ruc = sum(1 for a in audits if a["ruc"])
    con_avance = total - pendiente


    stat_cards = f"""
    <div class="grid mb-16">
      <div class="stat-card blue col-3">
        <div class="stat-content">
          <div>
            <div class="stat-icon-wrap">{SVG_BRIEFCASE}</div>
            <div class="stat-value">{total}</div>
          </div>
        </div>
        <div class="stat-label mt-0">Total auditorías</div>
        <div class="stat-bg-shape" style="background:var(--accent-base);"></div>
      </div>
      <div class="stat-card amber col-3">
        <div class="stat-content">
          <div>
            <div class="stat-icon-wrap">{SVG_CLOCK}</div>
            <div class="stat-value">{en_prog + pendiente}</div>
          </div>
        </div>
        <div class="stat-label mt-0">Pendientes / en investigación</div>
        <div class="stat-bg-shape" style="background:#F59E0B;"></div>
      </div>
      <div class="stat-card blue col-3">
        <div class="stat-content">
          <div>
            <div class="stat-icon-wrap">{SVG_ARROW_RIGHT}</div>
            <div class="stat-value">{con_ruc}</div>
          </div>
        </div>
        <div class="stat-label mt-0">Con RUC registrado</div>
        <div class="stat-bg-shape" style="background:#8B5CF6;"></div>
      </div>
      <div class="stat-card green col-3">
        <div class="stat-content">
          <div>
            <div class="stat-icon-wrap">{SVG_CHECK}</div>
            <div class="stat-value">{con_avance}</div>
          </div>
        </div>
        <div class="stat-label mt-0">Con avance</div>
        <div class="stat-bg-shape" style="background:#10B981;"></div>
      </div>
    </div>
    """

    rows_html = ""
    for a in audits:
        if a["auditor_deleted_at"]:
            auditor_html = f"""
              <strong>{esc(a['auditor_name'])}</strong>
              <span class="assignment-state"><span class="badge badge-red">Baja definitiva</span></span>
            """
        elif not a["auditor_active"]:
            auditor_html = f"""
              <strong>{esc(a['auditor_name'])}</strong>
              <span class="assignment-state"><span class="badge badge-amber">Inactivo</span></span>
            """
        else:
            auditor_html = esc(a["auditor_name"])

        rows_html += f"""
        <tr>
          <td class="td-company">
            <strong>{esc(a['company_name'])}</strong>
            <span>RUC: {esc(a['ruc'] or '—')}</span>
          </td>
          <td>{esc(a['period'])}</td>
          <td>{auditor_html}</td>
          <td>{badge(a['status'])}</td>
          <td>
            <a class="btn btn-sm" href="/admin/audit?audit_id={a['id']}">{SVG_ARROW_RIGHT} Ver expediente</a>
          </td>
        </tr>
        """

    if not rows_html:
        rows_html = '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:32px;">No existen auditorías registradas.</td></tr>'

    filter_js = """
    <script>
    document.getElementById('audit-filter').addEventListener('change', function() {
      const val = this.value;
      document.querySelectorAll('#audit-table tbody tr').forEach(function(tr) {
        if (!val) { tr.style.display = ''; return; }
        const badge = tr.querySelector('.badge');
        const classes = badge ? badge.className : '';
        tr.style.display = (val === 'pending' && classes.includes('badge-gray')) ||
                            (val === 'progress' && classes.includes('badge-amber')) ? '' : 'none';
      });
    });
    </script>
    """

    content = f"""
    <div class="panel-header mb-0">
      <div>
        <h1 class="page-title">Vista general</h1>
        <p class="page-subtitle muted">Seguimiento de expedientes de auditoría en modo lectura.</p>
      </div>
      <div class="actions mt-0">
        <a class="btn btn-primary" href="/admin/companies">{SVG_FILE} + Asignar empresa</a>
      </div>
    </div>

    {stat_cards}

    <div class="panel">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
        <h2 class="mb-0">Auditorías asignadas</h2>
        <select id="audit-filter" style="width:auto;padding:8px 12px;font-size:13px;border-radius:20px;">
          <option value="">Todos los estados</option>
          <option value="pending">Pendiente</option>
          <option value="progress">En investigación</option>
        </select>
      </div>
      <div class="table-wrap">
        <table id="audit-table">
          <thead>
            <tr>
              <th>Empresa</th>
              <th>Período</th>
              <th>Auditor</th>
              <th>Estado</th>
              <th>Acción</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>
    {filter_js}
    """

    flash = form_value(query, "msg")
    return layout("Vista general", user, content, flash, active_path=active_path)
