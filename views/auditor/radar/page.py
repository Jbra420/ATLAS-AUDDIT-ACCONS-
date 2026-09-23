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
from services.company_search import find_source_check, source_map_from_context
from services.dossier import build_dossier_model
from services.normalizacion import anio_fiscal_sugerido
from services.rowutil import row_get
from services.ruc_validator import validate_ruc
from ui.helpers import esc, form_value, hidden_inputs
from ui.icons import (
    SVG_BUILDING,
    SVG_DOLLAR,
    SVG_DOWNLOAD,
    SVG_FILE,
    SVG_INFO,
    SVG_MAP_PIN,
    SVG_RADAR,
    SVG_REFRESH,
    SVG_SEARCH,
    SVG_USERS,
)
from ui.layout import layout


def _render_search_bar(
    audit_id: int, company_name: str, ruc: str | None, read_only: bool,
    csrf_token: str, sri_check, supercias_check, financial_years: int,
) -> str:
    has_ruc = bool(ruc)
    valid_ruc = validate_ruc(ruc)[0] if has_ruc else False
    ruc_state = (
        '<span class="radar-lookup-ruc-state is-valid">RUC válido</span>' if valid_ruc else
        '<span class="radar-lookup-ruc-state is-invalid">RUC por verificar</span>' if has_ruc else
        '<span class="radar-lookup-ruc-state">RUC pendiente</span>'
    )
    ruc_line = (
        f'<span class="radar-lookup-ruc">RUC <strong>{esc(ruc)}</strong></span>{ruc_state}'
        if has_ruc else ruc_state
    )

    sri_ready = bool(sri_check and sri_check["estado"] == "consultada")
    supercias_ready = bool(supercias_check and supercias_check["estado"] == "consultada")

    def source_state(label: str, ready: bool, detail: str = "consultado") -> str:
        state = detail if ready else "pendiente"
        css = "is-ready" if ready else "is-pending"
        return (
            f'<span class="radar-lookup-source {css}">'
            f'<span class="radar-lookup-dot" aria-hidden="true"></span>'
            f'{esc(label)} {esc(state)}</span>'
        )

    sources = ""
    if has_ruc:
        years_label = f'{financial_years} año(s) disponible(s)' if financial_years else "pendiente"
        sources = f"""
      <div class="radar-lookup-sources" aria-label="Estado de las fuentes">
        {source_state('SRI', sri_ready)}
        {source_state('Supercias', supercias_ready, 'consultada')}
        {source_state('Financiero', financial_years > 0, years_label)}
      </div>"""

    if read_only:
        action = '<span class="radar-lookup-readonly">Solo lectura</span>'
    else:
        ruc_field = (
            f'<input type="hidden" name="search_ruc" value="{esc(ruc)}">' if has_ruc else
            """<div class="radar-lookup-entry">
              <label for="rs_ruc">RUC de la empresa</label>
              <input id="rs_ruc" name="search_ruc" placeholder="13 dígitos" maxlength="13"
                     inputmode="numeric" pattern="[0-9]{13}" required
                     aria-describedby="ruc-hint-text" autocomplete="off">
              <span id="ruc-hint-text" class="radar-lookup-hint" aria-live="polite">Ingrese 13 dígitos</span>
            </div>"""
        )
        button_icon = SVG_REFRESH if sri_ready or supercias_ready or financial_years else SVG_SEARCH
        button_label = "Actualizar búsqueda" if sri_ready or supercias_ready or financial_years else "Iniciar búsqueda"
        refresh_available = bool(sri_ready or supercias_ready or financial_years)
        refresh_attribute = 'data-confirm-refresh="true"' if refresh_available else ""
        refresh_dialog = f"""
      <dialog id="radar-refresh-dialog" class="radar-refresh-dialog"
              aria-labelledby="radar-refresh-title" aria-describedby="radar-refresh-description">
        <div class="radar-refresh-content">
          <span class="radar-refresh-icon" aria-hidden="true">{SVG_INFO}</span>
          <h2 id="radar-refresh-title">Actualizar datos de la empresa</h2>
          <p id="radar-refresh-description">
            Atlas volverá a consultar los catálogos locales para este RUC.
          </p>
          <div class="radar-refresh-note">
            <strong>Antes de continuar</strong>
            <span>La consulta puede reemplazar ajustes manuales en SRI, Supercias y datos financieros.</span>
          </div>
          <div class="radar-refresh-actions">
            <form method="dialog"><button type="submit" class="radar-refresh-cancel" autofocus>Cancelar</button></form>
            <button type="button" id="radar-refresh-confirm" class="radar-refresh-confirm">Actualizar datos</button>
          </div>
        </div>
      </dialog>""" if refresh_available else ""
        action = f"""
      <form method="post" action="/auditor/radar/investigate" class="radar-search-form">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        {ruc_field}
        <button type="submit" class="btn-radar-search" data-running-label="Buscando..." {refresh_attribute}>
          {button_icon}<span>{button_label}</span>
        </button>
      </form>{refresh_dialog}"""

    return f"""
    <section class="radar-lookup" aria-label="Búsqueda de empresa">
      <div class="radar-lookup-main">
        <div class="radar-lookup-identity">
          <span class="radar-lookup-kicker">Expediente de auditoría</span>
          <h1>{esc(company_name)}</h1>
          <div class="radar-lookup-meta">{ruc_line}</div>
        </div>
        {action}
      </div>
      {sources}
    </section>"""


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
    provenance = ctx["provenance"]
    is_read_only = user["role"] == "admin"
    readonly_class = "readonly-mode" if is_read_only else ""
    source_map = source_map_from_context(audit, ctx)

    # ── Indicadores financieros ───────────────────────────────────────────
    indicators = compute_indicators(dict(snapshot) if snapshot else None)
    dossier = build_dossier_model(
        audit, research, profile, location, admins, shareholders,
        snapshot, indicators, source_map, sources,
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
    # a cada tab solo su propia fila de source_checks.
    sri_check = find_source_check(src_checks, ("sri",))
    supercias_check = find_source_check(src_checks, ("supercias",), ("documento",))

    tab_sri = build_sri(
        audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok,
        source_check=sri_check, provenance=provenance,
        alertas=source_map["validacion"]["alertas"],
    )
    tab_supercias = build_supercias(
        audit_id, audit, profile, research, read_only=is_read_only, csrf_token=csrf_tok,
        source_check=supercias_check, provenance=provenance,
    )
    tab_ubicacion = build_ubicacion(
        audit_id, audit, location, read_only=is_read_only, csrf_token=csrf_tok, provenance=provenance,
    )
    tab_admins = build_admins(
        audit_id, audit, admins, read_only=is_read_only, csrf_token=csrf_tok, sources=sources,
        provenance=provenance, certificado=ctx["certificado"],
    )
    tab_accionistas = build_accionistas(
        audit_id, audit, shareholders, read_only=is_read_only, csrf_token=csrf_tok, sources=sources,
        provenance=provenance, certificado=ctx["certificado"],
    )
    fin_anio = form_value(query, "fin_anio")
    tab_financiero = build_financiero(
        audit_id, indicators, read_only=is_read_only, csrf_token=csrf_tok,
        anio_sugerido=anio_fiscal_sugerido(row_get(profile, "ultimo_anio_balance")),
        financial=ctx["financial"],
        ruc=ruc or "",
        # Ejercicio a editar elegido en la pestaña; se ignora si no es un año válido.
        anio_edicion=int(fin_anio) if fin_anio.isdigit() and 1990 <= int(fin_anio) <= 2100 else None,
        provenance=provenance,
    )
    tab_documentos = build_documentos(
        audit_id, sources, read_only=is_read_only, csrf_token=csrf_tok,
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

    # ── Búsqueda y estado de fuentes ──────────────────────────────────────
    search_panel = _render_search_bar(
        audit_id, company_name, ruc, is_read_only, csrf_tok,
        sri_check, supercias_check, len(ctx["financial"]["years"]),
    )

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
          rucHint.textContent = '13 dígitos ingresados; se validará al buscar';
        } else if (v.length > 0) {
          rucHint.textContent = `${v.length}/13 dígitos`;
        } else {
          rucHint.textContent = 'Ingrese 13 dígitos';
        }
      });
    }

    const researchForm = document.querySelector('.radar-search-form');
    if (researchForm) {
      const refreshDialog = document.getElementById('radar-refresh-dialog');
      const searchButton = researchForm.querySelector('.btn-radar-search');
      researchForm.addEventListener('submit', function(event) {
        if (researchForm.dataset.submitting === 'true') {
          event.preventDefault();
          return;
        }
        const submitter = event.submitter || searchButton;
        if (!submitter) return;
        if (submitter.dataset.confirmRefresh === 'true' &&
            researchForm.dataset.refreshConfirmed !== 'true' && refreshDialog) {
          event.preventDefault();
          refreshDialog.showModal();
          return;
        }
        researchForm.dataset.submitting = 'true';
        window.requestAnimationFrame(() => {
          submitter.disabled = true;
          submitter.querySelector('span').textContent = submitter.dataset.runningLabel || 'Procesando...';
        });
      });
      document.getElementById('radar-refresh-confirm')?.addEventListener('click', function() {
        refreshDialog.close();
        researchForm.dataset.refreshConfirmed = 'true';
        researchForm.requestSubmit(searchButton);
      });
    }
    </script>
    """

    # ── Contenido final ───────────────────────────────────────────────────
    content = f"""
    <div class="{readonly_class}">
      {search_panel}
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
