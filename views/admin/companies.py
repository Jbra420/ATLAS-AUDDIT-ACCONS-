"""
views/admin/companies.py — Directorio de empresas y asignación de auditorías.
"""
from __future__ import annotations

import sqlite3

from database import list_admin_audits, list_auditors
from ui.components import badge
from ui.helpers import esc, form_value, csrf_input
from ui.icons import SVG_ALERT, SVG_ARROW_RIGHT
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
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

    rows_html = []
    for a in audits:
        if a["auditor_deleted_at"]:
            auditor_label = f"""
              <strong>{esc(a['auditor_name'])}</strong>
              <span class="assignment-state"><span class="badge badge-red">Baja definitiva</span> Reasignación disponible</span>
            """
        elif not a["auditor_active"]:
            auditor_label = f"""
              <strong>{esc(a['auditor_name'])}</strong>
              <span class="assignment-state"><span class="badge badge-amber">Inactivo</span> Reasignación disponible</span>
            """
        else:
            auditor_label = f"<strong>{esc(a['auditor_name'])}</strong>"
        
        # Build options for reassignment dropdown, excluding the current assigned auditor
        reassign_options = "".join(
            f'<option value="{aud["id"]}">{esc(aud["full_name"])}</option>'
            for aud in auditors if aud["id"] != a['assigned_auditor_id']
        )
        
        if reassign_options:
            reassign_form = f"""
            <form method="post" action="/admin/companies/reassign" style="display:inline; margin:0;" title="Reasignar">
              {csrf_input(csrf_token)}
              <input type="hidden" name="audit_id" value="{a['id']}">
              <select name="new_auditor_id" onchange="this.form.submit()" class="form-control form-control-sm" style="width:auto; display:inline-block; padding: 2px 4px; font-size: 12px;">
                <option value="" disabled selected>Reasignar...</option>
                {reassign_options}
              </select>
            </form>
            """
        else:
            reassign_form = ""

        rows_html.append(f"""<tr>
          <td class="td-company">
            <strong>{esc(a['company_name'])}</strong>
            <span>RUC: {esc(a['ruc'] or '—')}</span>
          </td>
          <td>{esc(a['period'])}</td>
          <td>{auditor_label}{reassign_form}</td>
          <td>{badge(a['status'])}</td>
          <td>
            <a class="btn btn-sm" href="/admin/audit?audit_id={a['id']}">{SVG_ARROW_RIGHT} Seguimiento</a>
          </td>
        </tr>""")
    
    rows_html = "".join(rows_html)

    content = f"""
    <div class="mb-16">
      <h1 class="page-title">Directorio de Empresas</h1>
      <p class="page-subtitle muted">Asigne nuevas empresas a los auditores para iniciar la investigación.</p>
    </div>
    {'<div class="error-msg toast">' + SVG_ALERT + ' ' + esc(err) + '</div>' if err else ''}

    <div class="grid">
      <div class="panel col-4">
        <h2>Registrar empresa</h2>
        <form method="post" action="/admin/companies" id="create-company-form">
          {csrf_input(csrf_token)}
          
          <div id="ruc-search-group">
            <label for="comp_ruc">RUC de la empresa *</label>
            <div style="display:flex; gap:8px;">
                <input id="comp_ruc" name="ruc" placeholder="13 dígitos numéricos" maxlength="13" pattern="\\d{{13}}" title="El RUC debe tener exactamente 13 dígitos numéricos" required style="flex:1;">
                <button type="button" id="btn-search-ruc" class="btn btn-secondary">Buscar</button>
            </div>
            <div class="field-hint" id="ruc-hint">Ingrese el RUC para autocompletar los datos.</div>
          </div>

          <div id="company-details" style="display:none; margin-top: 24px; border-top: 1px solid var(--color-border); padding-top: 16px;">
              <label for="comp_name">Razón social *</label>
              <input id="comp_name" name="name" required placeholder="Ej. ACME Cía. Ltda.">

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
          </div>
        </form>

        <script>
        document.addEventListener("DOMContentLoaded", () => {{
            const btnSearch = document.getElementById("btn-search-ruc");
            const inputRuc = document.getElementById("comp_ruc");
            const detailsDiv = document.getElementById("company-details");
            const hint = document.getElementById("ruc-hint");
            
            const inputName = document.getElementById("comp_name");
            const inputCity = document.getElementById("comp_city");
            const inputActivity = document.getElementById("comp_activity");

            btnSearch.addEventListener("click", async () => {{
                const ruc = inputRuc.value.trim();
                if (ruc.length !== 13) {{
                    hint.innerHTML = "<span style='color:var(--danger)'>El RUC debe tener 13 dígitos.</span>";
                    return;
                }}
                
                btnSearch.disabled = true;
                btnSearch.textContent = "Buscando...";
                hint.textContent = "Consultando datos...";
                
                try {{
                    const res = await fetch(`/api/lookup-ruc?ruc=${{ruc}}`);
                    const data = await res.json();
                    
                    if (!res.ok) {{
                        hint.innerHTML = `<span style='color:var(--danger)'>${{data.error || 'Error al buscar RUC'}}</span>`;
                        detailsDiv.style.display = "none";
                    }} else {{
                        hint.innerHTML = `<span style='color:var(--success)'>Datos encontrados. Verifique y asigne.</span>`;
                        inputName.value = data.name || "";
                        inputCity.value = data.city || "";
                        inputActivity.value = data.activity_hint || "";
                        detailsDiv.style.display = "block";
                        inputName.focus();
                    }}
                }} catch (e) {{
                    hint.innerHTML = "<span style='color:var(--danger)'>Error de conexión al buscar RUC.</span>";
                }} finally {{
                    btnSearch.disabled = false;
                    btnSearch.textContent = "Buscar";
                }}
            }});
            
            // La búsqueda automática se deshabilitó a petición del usuario.
            // El usuario debe hacer clic explícitamente en "Buscar".
        }});
        </script>
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
