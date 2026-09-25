"""
views/auditor/dashboard.py — Panel de asignaciones del auditor.
"""
from __future__ import annotations

import sqlite3

from database import get_audit_context, list_auditor_audits
from services.company_search import source_map_from_context
from ui.components import badge
from ui.helpers import esc, form_value
from ui.icons import SVG_ARROW_RIGHT, SVG_SEARCH
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str) -> str:
    """Genera el HTML del panel de auditorías asignadas al auditor."""
    audits = list_auditor_audits(user["id"])

    cards_html = ""
    for a in audits:
        # Mismo avance que decide en el expediente si el resumen puede generarse.
        readiness = source_map_from_context(a, get_audit_context(a["id"]))["readiness"]
        pct = readiness["required_percent"]
        requisitos = f'{readiness["required_completed"]} de {readiness["required_total"]} requisitos'
        has_ruc = bool(a["ruc"])
        next_label = "Buscar por RUC" if not has_ruc or a["status"] == "pendiente" else "Continuar expediente"
        next_icon = SVG_SEARCH if next_label == "Buscar por RUC" else SVG_ARROW_RIGHT
        ruc_line = f"RUC: {esc(a['ruc'])}" if has_ruc else "RUC pendiente de validar"

        # Estilo de tarjeta creativa en lugar de fila de tabla
        cards_html += f"""
        <div class="stat-card col-6" style="padding: 24px; display: flex; flex-direction: column; justify-content: space-between;">
          <div>
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
              <div>
                <h3 style="margin: 0; font-size: 16px; color: var(--ink);">{esc(a['company_name'])}</h3>
                <span style="font-size: 13px; color: var(--muted);">{ruc_line} • Período: {esc(a['period'])}</span>
              </div>
              {badge(a['status'])}
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:12px;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--bg);font-size:13px;color:var(--ink-2);">
              {SVG_SEARCH}
              <span>Primer paso: validar el RUC y abrir la ficha de investigación inicial.</span>
            </div>
            
            <div style="margin-top: 16px; margin-bottom: 16px;">
              <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px;">
                <span>Requisitos obligatorios</span>
                <span>{pct}%</span>
              </div>
              <div class="mini-progress" title="{requisitos}" style="height: 6px; background: var(--line); border-radius: 4px; overflow: hidden;">
                <div class="mini-progress-fill" style="width:{pct}%; height: 100%; background: var(--grad-primary); transition: width 0.3s ease;"></div>
              </div>
            </div>
          </div>
          
          <div style="margin-top: 20px;">
            <a class="btn btn-primary" href="/auditor/radar?audit_id={a['id']}&tab=sri" style="width: 100%;">
              {next_icon} {next_label}
            </a>
          </div>
        </div>
        """

    if not cards_html:
        cards_html = f"""
        <div class="col-12 panel" style="text-align:center; padding: 48px 24px; color: var(--muted);">
          <h3 style="color: var(--muted);">Todo al día</h3>
          <p>No tienes empresas asignadas pendientes en este momento.</p>
        </div>
        """

    content = f"""
    <div class="panel-header mb-0">
      <div>
        <h1 class="page-title">Mis expedientes</h1>
        <p class="page-subtitle muted">
          Seleccione una empresa para iniciar o continuar la investigación.
        </p>
      </div>
    </div>

    <div class="grid" style="margin-top: 24px;">
      {cards_html}
    </div>
    """

    flash = form_value(query, "msg")
    return layout("Mis expedientes", user, content, flash, active_path=active_path)
