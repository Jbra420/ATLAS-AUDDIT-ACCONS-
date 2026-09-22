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
from services.company_search import build_source_map, find_source_check
from services.dossier import build_dossier_model
from services.normalizacion import anio_fiscal_sugerido
from services.rowutil import row_get
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
    SVG_FILE,
    SVG_INFO,
    SVG_MAP_PIN,
    SVG_RADAR,
    SVG_SEARCH,
    SVG_USERS,
)
from ui.layout import layout


def _tab_href(audit_id: int, tab_id: str, read_only: bool) -> str:
    route = "/admin/audit" if read_only else "/auditor/radar"
    return f"{route}?audit_id={audit_id}&tab={tab_id}#radar-tabs-main"


def _render_readiness_panel(readiness: dict, read_only: bool, audit_id: int) -> str:
    blockers = readiness.get("blockers", [])
    warnings = readiness.get("warnings", [])
    ready = bool(readiness.get("ready"))

    def item_html(item: dict, item_class: str) -> str:
        target_tab = item["tab"]
        return f"""
          <li class="readiness-item {item_class}">
            <span><strong>{esc(item["label"])}</strong><small>{esc(item["source"])}</small></span>
            <a href="{_tab_href(audit_id, target_tab, read_only)}"
               onclick="return switchTab('{target_tab}')">
              Revisar {SVG_ARROW_RIGHT}
            </a>
          </li>
        """

    blocker_items = "".join(item_html(item, "is-blocker") for item in blockers)
    warning_items = "".join(item_html(item, "is-warning") for item in warnings)
    if ready:
        title = "Validación mínima completa"
        description = (
            "El auditor puede generar el resumen preliminar."
            if not read_only else
            "El expediente cumple los requisitos para que el auditor genere el resumen."
        )
        icon = SVG_CHECK
        status_class = "is-ready"
    else:
        title = f"{len(blockers)} requisito(s) pendiente(s)"
        description = (
            "Complete los datos obligatorios antes de generar el resumen."
            if not read_only else
            "El auditor debe completar estos datos antes de generar el resumen."
        )
        icon = SVG_ALERT
        status_class = "is-blocked"

    blockers_column = ""
    if blockers:
        blockers_column = f"""
        <div class="readiness-group">
          <span class="readiness-group-title">Obligatorios</span>
          <ul>{blocker_items}</ul>
        </div>
        """
    warnings_column = ""
    if warnings:
        warnings_column = f"""
        <div class="readiness-group">
          <span class="readiness-group-title">Recomendados</span>
          <ul>{warning_items}</ul>
        </div>
        """

    return f"""
    <div class="readiness-panel {status_class}">
      <div class="readiness-head">
        <span class="readiness-icon">{icon}</span>
        <div>
          <h3>{esc(title)}</h3>
          <p>{esc(description)}</p>
        </div>
        <div class="readiness-count">
          <strong>{readiness.get('required_completed', 0)}/{readiness.get('required_total', 0)}</strong>
          <span>requisitos</span>
        </div>
      </div>
      <div class="readiness-groups">
        {blockers_column}
        {warnings_column}
      </div>
    </div>
    """


def _render_source_map(source_map: dict, read_only: bool, audit_id: int) -> str:
    totals = source_map["totals"]
    readiness = source_map["readiness"]

    # Identify individual card statuses (assuming only 2 cards: SRI and Supercias)
    sri_card = next((c for c in source_map["cards"] if c["key"] == "sri"), None)
    sup_card = next((c for c in source_map["cards"] if c["key"] == "supercias"), None)

    def step_html(num: int, card: dict | None, tab_id: str, title: str, btn_txt: str) -> str:
        if not card:
            return ""
        st = card["status"]  # 'complete', 'partial', 'pending'
        icon = SVG_CHECK if st == 'complete' else (SVG_CLOCK if st == 'partial' else SVG_ALERT)
        color_cls = f"step-{st}"

        found = card["completed"]
        tot = card["total"]

        return f"""
        <div class="step-item {color_cls}">
          <div class="step-indicator">
            <span class="step-num">{num}</span>
            <span class="step-icon">{icon}</span>
          </div>
          <div class="step-content">
            <div class="step-header">
              <h4>{esc(title)}</h4>
              <span class="step-badge {color_cls}">{esc(card["status_label"])}</span>
            </div>
            <p class="step-meta">{found} de {tot} datos validados</p>
            <a class="btn-step-action" href="{_tab_href(audit_id, tab_id, read_only)}"
               onclick="return switchTab('{tab_id}')">
              {esc(btn_txt)} {SVG_ARROW_RIGHT}
            </a>
          </div>
        </div>
        """

    # Generamos los 3 pasos: SRI, Supercias, Resumen
    step1 = step_html(1, sri_card, "sri", "Validación SRI", "Ir a SRI")
    step2 = step_html(2, sup_card, "supercias", "Societario (Supercias)", "Ir a Supercias")

    # Paso 3 (Resumen) depende de que los otros 2 estén completos
    is_ready = readiness["ready"]
    s3_st = "complete" if is_ready else "pending"
    s3_icon = SVG_CHECK if is_ready else SVG_RADAR
    s3_label = "Listo para generar" if is_ready else "Faltan datos obligatorios"
    s3_btn = "Abrir resumen" if is_ready else "Revisar pendientes"
    first_pending_tab = (
        readiness.get("blockers", [{}])[0].get("tab", "resumen")
        if readiness.get("blockers") else "resumen"
    )
    s3_tab = "resumen" if is_ready else first_pending_tab

    step3 = f"""
        <div class="step-item step-{s3_st}">
          <div class="step-indicator">
            <span class="step-num">3</span>
            <span class="step-icon">{s3_icon}</span>
          </div>
          <div class="step-content">
            <div class="step-header">
              <h4>Resumen Final</h4>
              <span class="step-badge step-{s3_st}">{s3_label}</span>
            </div>
            <p class="step-meta">Generación del dossier automático</p>
            <a class="btn-step-action" href="{_tab_href(audit_id, s3_tab, read_only)}"
               onclick="return switchTab('{s3_tab}')">
              {s3_btn} {SVG_ARROW_RIGHT}
            </a>
          </div>
        </div>
    """

    ready_icon = SVG_CHECK if is_ready else SVG_ALERT
    ready_color = "status-ready" if is_ready else "status-warning"
    ready_text = "Expediente listo" if is_ready else "Requiere atención"

    return f"""
    <section class="radar-workflow-stepper">
      <div class="workflow-header">
        <div class="workflow-titles">
          <span class="workflow-eyebrow">Progreso de la Auditoría</span>
          <h2>Flujo de validación del expediente</h2>
        </div>
        <div class="workflow-global-status {ready_color}">
          {ready_icon}
          <span>{ready_text}</span>
          <div class="workflow-meter">
            <span style="width: {readiness['required_percent']}%;"></span>
          </div>
        </div>
      </div>

      <div class="stepper-container">
        {step1}
        <div class="step-connector"></div>
        {step2}
        <div class="step-connector"></div>
        {step3}
      </div>
      {_render_readiness_panel(readiness, read_only, audit_id)}
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
    source_map_panel = _render_source_map(source_map, is_read_only, audit_id)

    # ── Indicadores financieros ───────────────────────────────────────────
    indicators = compute_indicators(dict(snapshot) if snapshot else None)
    dossier = build_dossier_model(
        audit, research, profile, location, admins, shareholders,
        docs, snapshot, indicators, source_map, sources,
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
    from .tab_documentos import build as build_documentos
    from .tab_resumen import build as build_resumen

    # Fuentes guiadas: SRI y Supercias (portal) se resuelven aquí para pasarle
    # a cada tab solo su propia fila de source_checks; el resto (Supercias —
    # documentos, SERCOP, búsqueda web) queda para el tab "Documentos".
    sri_check = find_source_check(src_checks, ("sri",))
    supercias_check = find_source_check(src_checks, ("supercias",), ("documento",))
    resolved_ids = {row["id"] for row in (sri_check, supercias_check) if row}
    other_checks = [c for c in src_checks if c["id"] not in resolved_ids]

    tab_sri = build_sri(
        audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok,
        source_check=sri_check,
    )
    tab_supercias = build_supercias(
        audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok,
        source_check=supercias_check,
    )
    tab_ubicacion = build_ubicacion(audit_id, audit, location, read_only=is_read_only, csrf_token=csrf_tok)
    tab_admins = build_admins(
        audit_id, audit, admins, read_only=is_read_only, csrf_token=csrf_tok, sources=sources,
    )
    tab_accionistas = build_accionistas(
        audit_id, audit, shareholders, read_only=is_read_only, csrf_token=csrf_tok, sources=sources,
    )
    tab_financiero = build_financiero(
        audit_id, indicators, read_only=is_read_only, csrf_token=csrf_tok,
        anio_sugerido=anio_fiscal_sugerido(row_get(profile, "ultimo_anio_balance")),
    )
    tab_documentos = build_documentos(
        audit_id, docs, other_checks, sources, read_only=is_read_only, csrf_token=csrf_tok,
    )
    tab_resumen = build_resumen(
        audit_id,
        research,
        dossier,
        readiness=source_map["readiness"],
        read_only=is_read_only,
        csrf_token=csrf_tok,
    )

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
        Valide el RUC y ejecute la búsqueda automática para cargar la ficha inicial de investigación.
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
          <div class="radar-search-actions">
            <button type="submit" class="btn-radar-validate" data-running-label="Validando...">
              {SVG_CHECK} Validar RUC
            </button>
            <button type="submit" class="btn-radar-search"
                    formaction="/auditor/radar/investigate" data-running-label="Buscando...">
              {SVG_SEARCH} Iniciar búsqueda
            </button>
          </div>
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
        ("documentos",   "Documentos",     SVG_FILE,     tab_documentos),
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
    function switchTab(id, updateUrl = true, scrollToTabs = true) {
      const pane = document.getElementById('tab-' + id);
      const btn  = document.getElementById('tab-btn-' + id);
      if (!pane || !btn) return true;

      document.querySelectorAll('.radar-tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.radar-tab-btn').forEach(b => b.classList.remove('active'));
      pane.classList.add('active');
      btn.classList.add('active');
      if (updateUrl) {
        const url = new URL(window.location);
        url.searchParams.set('tab', id);
        url.hash = 'radar-tabs-main';
        window.history.replaceState({tab: id}, '', url);
      }
      if (scrollToTabs) {
        const tabs = document.getElementById('radar-tabs-main');
        window.requestAnimationFrame(() => {
          tabs.scrollIntoView({behavior: 'smooth', block: 'start'});
          btn.focus({preventScroll: true});
        });
      }
      return false;
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

    const researchForm = document.querySelector('.radar-search-form');
    if (researchForm) {
      researchForm.addEventListener('submit', function(event) {
        const submitter = event.submitter;
        if (!submitter) return;
        window.requestAnimationFrame(() => {
          submitter.disabled = true;
          submitter.textContent = submitter.dataset.runningLabel || 'Procesando...';
        });
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
