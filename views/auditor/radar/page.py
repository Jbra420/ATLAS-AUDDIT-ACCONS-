"""
views/auditor/radar/page.py — Orquestador del Radar Empresarial.

Este módulo delega la construcción de cada tab a su propio archivo.
El método render() ensambla todos los tabs y construye la página final.
"""
from __future__ import annotations

import sqlite3

from database import (
    get_audit,
    get_audit_context,
)
from services.financial import compute_indicators
from services.company_search import build_source_map
from services.dossier import build_dossier_model
from services.activity_timeline import build_activity_timeline
from ui.components import ruc_banner_html
from ui.helpers import esc, form_value, csrf_input
from ui.icons import (
    SVG_ALERT,
    SVG_ARROW_RIGHT,
    SVG_BUILDING,
    SVG_CHECK,
    SVG_CLOCK,
    SVG_DOLLAR,
    SVG_DOWNLOAD,
    SVG_EXTERNAL,
    SVG_FILE,
    SVG_INFO,
    SVG_MAP_PIN,
    SVG_RADAR,
    SVG_SEARCH,
    SOURCE_ICONS,
    SVG_USERS,
)
from ui.layout import layout



def _render_source_map(source_map: dict, read_only: bool) -> str:
    totals = source_map["totals"]
    cards_html = ""
    for card in source_map["cards"]:
        found = "".join(
            f'<li><span>{esc(item["label"])}</span><strong>{esc(item["value"])}</strong></li>'
            for item in card["found"]
        )
        missing_txt = ", ".join(card["missing"]) if card["missing"] else "Sin pendientes criticos"
        cards_html += f"""
        <article class="source-map-card source-map-{esc(card['status'])}">
          <div class="source-map-card-head">
            <div class="source-map-icon">{SOURCE_ICONS.get(card["key"], SVG_INFO)}</div>
            <div>
              <h3>{esc(card["title"])}</h3>
              <span class="source-map-count">{card["completed"]}/{card["total"]} datos clave</span>
            </div>
            <span class="source-map-status status-{esc(card['status'])}">{esc(card["status_label"])}</span>
          </div>
          <ul class="source-map-found">
            {found if found else '<li class="source-map-empty">Sin datos confirmados todavia</li>'}
          </ul>
          <div class="source-map-missing">
            <span>Pendiente</span>
            <p>{esc(missing_txt)}</p>
          </div>
          <button type="button" class="source-map-link" onclick="switchTab('{esc(card['tab'])}')">
            {SVG_ARROW_RIGHT} {esc('Ver fuente' if read_only else 'Abrir fuente')}
          </button>
        </article>
        """

    ready_label = "Base suficiente para resumen" if totals["ready_for_summary"] else "Resumen aun requiere soporte"
    ready_icon = SVG_CHECK if totals["ready_for_summary"] else SVG_ALERT
    return f"""
    <section class="source-map-panel">
      <div class="source-map-header">
        <div>
          <span class="source-map-eyebrow">Mapa de busqueda empresarial</span>
          <h2>Estado de fuentes para interpretar la empresa</h2>
        </div>
        <div class="source-map-readiness">
          {ready_icon}
          <span>{esc(ready_label)}</span>
        </div>
      </div>
      <div class="source-map-meter" aria-label="Avance de fuentes">
        <span style="width: {totals['percent']}%;"></span>
      </div>
      <div class="source-map-meta">
        <span>{totals["completed_fields"]}/{totals["total_fields"]} datos clave</span>
        <span>{totals["complete_cards"]} completas</span>
        <span>{totals["partial_cards"]} en avance</span>
        <span>{totals["pending_cards"]} pendientes</span>
      </div>
      <div class="source-map-grid">{cards_html}</div>
    </section>
    """


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
    """Genera el HTML completo del Radar Empresarial."""
    audit_id = int(form_value(query, "audit_id", "0"))
    audit = get_audit(audit_id, user)
    if not audit:
        return layout(
            "Acceso denegado", user,
            '<div class="error-msg">Auditoría no disponible o no asignada.</div>',
            active_path="/auditor",
        )

    # ── Cargar datos (una sola conexión para todo el expediente) ───────────
    ctx = get_audit_context(audit_id)
    research = ctx["research"]
    profile, location = ctx["profile"], ctx["location"]
    admins, shareholders = ctx["admins"], ctx["shareholders"]
    docs, snapshot = ctx["docs"], ctx["snapshot"]
    src_checks, sources = ctx["source_checks"], ctx["sources"]
    is_read_only = user["role"] == "admin"
    readonly_class = "readonly-mode" if is_read_only else ""
    source_map = build_source_map(
        audit, research, profile, location, admins, shareholders,
        docs, snapshot, src_checks, sources,
    )
    source_map_panel = _render_source_map(source_map, is_read_only)

    # ── Indicadores financieros ───────────────────────────────────────────
    indicators = compute_indicators(dict(snapshot) if snapshot else None)
    dossier = build_dossier_model(
        audit, research, profile, location, admins, shareholders,
        docs, snapshot, indicators, source_map, sources,
    )
    timeline = build_activity_timeline(
        audit, research, profile, location, docs, snapshot, src_checks, sources,
    )

    # ── Progress ──────────────────────────────────────────────────────────
    company_name = audit["company_name"]
    ruc = audit["ruc"]
    active_tab = form_value(query, "tab", "sri")

    csrf_tok = csrf_token  # recibido desde el server con el token de la sesión activa

    # ── Importar tabs desde sus propios módulos ───────────────────────────
    from .tab_sri import build as build_sri
    from .tab_supercias import build as build_supercias
    from .tab_ubicacion import build as build_ubicacion
    from .tab_admins import build as build_admins
    from .tab_accionistas import build as build_accionistas
    from .tab_financiero import build as build_financiero
    from .tab_resumen import build as build_resumen

    tab_sri = build_sri(audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok)
    tab_supercias = build_supercias(audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok)
    tab_ubicacion = build_ubicacion(audit_id, audit, location, read_only=is_read_only, csrf_token=csrf_tok)
    tab_admins = build_admins(audit_id, audit, admins, read_only=is_read_only)
    tab_accionistas = build_accionistas(audit_id, audit, shareholders, read_only=is_read_only)
    tab_financiero = build_financiero(audit_id, indicators, read_only=is_read_only, csrf_token=csrf_tok)
    tab_resumen = build_resumen(audit_id, research, dossier, read_only=is_read_only, csrf_token=csrf_tok)

    # Panel lateral de señales eliminado a petición del usuario para mejor uso del espacio horizontal.

    # ── Panel de búsqueda ─────────────────────────────────────────────────
    ruc_banner = ruc_banner_html(ruc)
    if is_read_only:
        search_panel = f"""
    <div class="radar-search-panel">
      <div class="radar-search-eyebrow"><span>Supervisión de expediente</span></div>
      <h1 class="radar-search-title">Vista de solo lectura</h1>
      <p class="radar-search-subtitle">
        El jefe auditor puede ver el avance completo del auditor asignado, sin ejecutar consultas ni modificar información.
      </p>
      {ruc_banner}
      <div class="radar-search-fields">
        <div>
          <label>RUC de la empresa</label>
          <input value="{esc(ruc or 'Pendiente')}" readonly>
        </div>
        <div>
          <label>Razón social registrada</label>
          <input value="{esc(company_name)}" readonly>
        </div>
      </div>
    </div>
    """
    else:
        search_panel = f"""
    <div class="radar-search-panel">
      <div class="radar-search-eyebrow"><span>Búsqueda inicial por RUC</span></div>
      <h1 class="radar-search-title">Buscar información de la empresa</h1>
      <p class="radar-search-subtitle">
        Confirme el RUC asignado o ingrese el RUC de la empresa para iniciar la ficha de investigación.
      </p>
      {ruc_banner}
      <form method="post" action="/auditor/radar/search" class="radar-search-form">
        {csrf_input(csrf_tok)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <div class="radar-search-fields">
          <div>
            <label for="rs_ruc">RUC de la empresa</label>
            <input id="rs_ruc" name="search_ruc" value="{esc(ruc or '')}"
                   placeholder="1234567890001" maxlength="13" inputmode="numeric"
                   pattern="\\d{{13}}" required>
            <div class="radar-ruc-hint" id="ruc-hint-text">
              <span class="ruc-status-warn">Ingrese el RUC de 13 dígitos</span>
            </div>
          </div>
          <div>
            <label>Razón social registrada</label>
            <input value="{esc(company_name)}" readonly>
          </div>
          <button type="submit" class="btn-radar-search">{SVG_SEARCH} Validar RUC</button>
        </div>
      </form>
    </div>
    """

    # ── Ensamble de tabs ──────────────────────────────────────────────────
    tabs = [
        ("sri",          "SRI",            SVG_DOLLAR,   tab_sri),
        ("supercias",    "Supercias",      SVG_BUILDING, tab_supercias),
        ("ubicacion",    "Ubicación",      SVG_MAP_PIN,  tab_ubicacion),
        ("admins",       "Administradores",SVG_USERS,    tab_admins),
        ("accionistas",  "Accionistas",    SVG_USERS,    tab_accionistas),
        ("indicadores",  "Financiero",     SVG_DOLLAR,   tab_financiero),
        ("resumen",      "Resumen",        SVG_RADAR,    tab_resumen),
    ]

    nav_html = ""
    pane_html = ""
    for tab_id, tab_label, tab_icon, tab_content in tabs:
        is_active = tab_id == active_tab
        nav_html += f'<button class="radar-tab-btn{" active" if is_active else ""}" onclick="switchTab(\'{tab_id}\')" id="tab-btn-{tab_id}">{tab_icon} {esc(tab_label)}</button>'  # noqa: E501
        pane_html += f'<div class="radar-tab-pane{" active" if is_active else ""}" id="tab-{tab_id}">{tab_content}</div>'

    tabs_block = f"""
    <div class="radar-tabs" id="radar-tabs-main">
      <div class="radar-tab-nav">{nav_html}</div>
      {pane_html}
    </div>
    """

    # ── Barra de acción flotante ────────────────────────────────────
    if is_read_only:
        action_bar_right = f"""
          <span class="badge badge-gray">{SVG_INFO} Modo solo lectura</span>
          <a class="btn btn-sm" href="/export/summary?audit_id={audit_id}" title="Descargar resumen en .txt">
            {SVG_DOWNLOAD} Exportar .txt
          </a>
          <a class="btn btn-sm" href="/export/csv?audit_id={audit_id}" title="Descargar ficha en .csv">
            {SVG_DOWNLOAD} Exportar .csv
          </a>
          <a class="btn btn-sm" href="/export/dossier?audit_id={audit_id}" title="Descargar ficha final en .txt">
            {SVG_DOWNLOAD} Ficha final
          </a>
        """
    else:
        action_bar_right = f"""
          <button type="button" class="btn btn-sm" onclick="switchTab('resumen')" title="Ir al resumen">
            {SVG_RADAR} Ver resumen
          </button>
          <a class="btn btn-sm" href="/export/summary?audit_id={audit_id}" title="Descargar resumen en .txt">
            {SVG_DOWNLOAD} Exportar .txt
          </a>
          <a class="btn btn-sm" href="/export/csv?audit_id={audit_id}" title="Descargar ficha en .csv">
            {SVG_DOWNLOAD} Exportar .csv
          </a>
          <a class="btn btn-sm" href="/export/dossier?audit_id={audit_id}" title="Descargar ficha final en .txt">
            {SVG_DOWNLOAD} Ficha final
          </a>
        """
    action_bar = f"""
    <div class="radar-action-bar">
      <div class="radar-action-bar-inner">
        <div class="radar-action-left">
          {SVG_INFO}
          <span>{esc(company_name)} · RUC: {esc(ruc or '—')}</span>
        </div>
        <div class="radar-action-right">
          {action_bar_right}
        </div>
      </div>
    </div>
    """

    # ── JS para tabs ──────────────────────────────────────────────────────
    tab_js = """
    <script>
    function switchTab(id, updateUrl = true) {
      document.querySelectorAll('.radar-tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.radar-tab-btn').forEach(b => b.classList.remove('active'));
      const pane = document.getElementById('tab-' + id);
      const btn  = document.getElementById('tab-btn-' + id);
      if (pane) pane.classList.add('active');
      if (btn)  btn.classList.add('active');
      if (updateUrl) {
        const url = new URL(window.location);
        url.searchParams.set('tab', id);
        window.history.replaceState({tab: id}, '', url);
      }
    }
    window.addEventListener('popstate', (e) => {
       const urlParams = new URLSearchParams(window.location.search);
       const tab = urlParams.get('tab') || 'sri';
       switchTab(tab, false);
    });

    const rucInput = document.getElementById('rs_ruc');
    const rucHint  = document.getElementById('ruc-hint-text');
    if (rucInput && rucHint) {
      rucInput.addEventListener('input', function() {
        const v = this.value.replace(/\\D/g,'');
        this.value = v;
        if (v.length === 13) {
          rucHint.innerHTML = '<span class="ruc-status-valid">✓ RUC de 13 dígitos listo</span>';
          this.classList.add('ruc-valid'); this.classList.remove('ruc-invalid');
        } else if (v.length > 0) {
          rucHint.innerHTML = `<span class="ruc-status-warn">${v.length}/13 dígitos</span>`;
          this.classList.remove('ruc-valid','ruc-invalid');
        } else {
          rucHint.innerHTML = '<span class="ruc-status-warn">Ingrese el RUC de 13 dígitos</span>';
          this.classList.remove('ruc-valid','ruc-invalid');
        }
      });
    }
    </script>
    """

    # ── Contenido final ───────────────────────────────────────────────────
    content = f"""
    <div class="{readonly_class}">
      {search_panel}
      {source_map_panel}
      <div class="radar-layout">
        <div style="min-width: 0;">
          {tabs_block}
          {action_bar}
        </div>
      </div>
    </div>
    {tab_js}
    """

    flash = form_value(query, "msg")
    err = form_value(query, "err")
    flash_msg = flash or (f"Error: {err}" if err else "")
    active = "/admin" if is_read_only else "/auditor"
    return layout("Expediente", user, content, flash_msg, active_path=active)
