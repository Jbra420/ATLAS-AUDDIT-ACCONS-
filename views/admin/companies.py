"""
views/admin/companies.py — Directorio de empresas y asignación de auditorías.
"""
from __future__ import annotations

import sqlite3

from database import list_admin_audits, list_auditors
from ui.components import badge
from ui.helpers import esc, form_value
from ui.icons import SVG_ALERT, SVG_ARROW_RIGHT, SVG_INFO
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str) -> str:
    """Genera el HTML de la página de gestión de empresas."""
    auditors = list_auditors()
    audits = list_admin_audits()
    err = form_value(query, "err")

    options = "".join(
        f'<option value="{a["id"]}">{esc(a["full_name"])} ({esc(a["username"])})</option>'
        for a in auditors
    )
    if not options:
        options = '<option disabled>No hay auditores registrados</option>'

    rows_html = "".join(
        f"""<tr>
          <td class="td-company">
            <strong>{esc(a['company_name'])}</strong>
            <span>RUC: {esc(a['ruc'] or '—')}</span>
          </td>
          <td>{esc(a['period'])}</td>
          <td>{esc(a['auditor_name'])}</td>
          <td>{badge(a['status'])}</td>
          <td>
            <a class="btn btn-sm" href="/admin/audit?audit_id={a['id']}">{SVG_ARROW_RIGHT} Seguimiento</a>
          </td>
        </tr>"""
        for a in audits
    )

    content = f"""
    <div class="mb-16">
      <h1 class="page-title">Directorio de Empresas</h1>
      <p class="page-subtitle muted">Asigne nuevas empresas a los auditores para iniciar la investigación.</p>
    </div>
    {'<div class="error-msg">' + SVG_ALERT + ' ' + esc(err) + '</div>' if err else ''}

    <div class="grid">
      <div class="panel col-4">
        <h2>Registrar empresa</h2>
        <form method="post" action="/admin/companies">
          <label for="comp_name">Razón social *</label>
          <input id="comp_name" name="name" required placeholder="Ej. ACME Cía. Ltda.">

          <label for="comp_ruc">RUC</label>
          <input id="comp_ruc" name="ruc" placeholder="13 dígitos numéricos"
                 maxlength="13" pattern="\\d{{13}}"
                 title="El RUC debe tener exactamente 13 dígitos numéricos">
          <div class="field-hint">{SVG_INFO} Puede añadir el RUC más adelante.</div>

          <label for="comp_city">Ciudad</label>
          <input id="comp_city" name="city" placeholder="Ej. Quito">

          <label for="comp_activity">Actividad esperada</label>
          <input id="comp_activity" name="activity_hint" placeholder="Ej. Servicios de consultoría">

          <label for="comp_period">Período auditado *</label>
          <input id="comp_period" name="period" value="2026" required>

          <label for="auditor_sel">Auditor asignado *</label>
          <select id="auditor_sel" name="assigned_auditor_id" required>{options}</select>

          <div class="actions" style="margin-top:24px;">
            <button class="btn btn-primary" type="submit" style="width:100%">Asignar empresa</button>
          </div>
        </form>
      </div>
      <div class="panel col-8">
        <h2>Empresas registradas ({len(audits)})</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Empresa</th><th>Período</th><th>Auditor</th><th>Estado</th><th>Acción</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:24px;">No hay empresas asignadas.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>
    """

    flash = form_value(query, "msg")
    return layout("Empresas", user, content, flash, active_path=active_path)
