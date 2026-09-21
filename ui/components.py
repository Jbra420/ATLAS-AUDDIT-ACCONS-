"""
ui/components.py — Componentes HTML reutilizables de la interfaz Atlas.
Depende de ui/icons y ui/helpers únicamente. Nunca importa de views/.
"""
from __future__ import annotations

from database import AUDIT_STATUSES
from services.ruc_validator import validate_ruc
from services.rowutil import row_get
from ui.helpers import csrf_input, esc
from ui.icons import (
    SVG_ALERT,
    SVG_CHECK,
    SVG_CIRCLE,
    SVG_CLOCK,
    SVG_CROSS,
    SVG_INFO,
)


def badge(status: str) -> str:
    """Genera un badge HTML coloreado para un estado de auditoría."""
    css_map = {
        "pendiente": "badge-gray",
        "en_investigacion": "badge-amber",
    }
    icon_map = {
        "pendiente": SVG_CIRCLE,
        "en_investigacion": SVG_CLOCK,
    }
    css = css_map.get(status, "badge-gray")
    icon = icon_map.get(status, SVG_CIRCLE)
    label = AUDIT_STATUSES.get(status, status)
    return f'<span class="badge {css}">{icon} {esc(label)}</span>'


def avatar_initials(name: str) -> str:
    """Extrae las iniciales de un nombre completo para mostrar en el avatar."""
    parts = (name or "?").split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "?"


def people_avatar(name: str) -> str:
    """Avatar circular con iniciales para filas de tablas de personas (admins, accionistas)."""
    initials = "".join(p[0] for p in (name or "").strip().split()[:2]).upper()
    return f'<span class="people-avatar">{esc(initials)}</span>'


def info_card(label: str, value: str, css_extra: str = "") -> str:
    """Tarjeta de dato de solo lectura usada en los tabs del Radar Empresarial (SRI, Supercias, etc.)."""
    v = value.strip() if value else ""
    val_cls = "info-card-value" if v else "info-card-value pending"
    val_text = esc(v) if v else "Pendiente de confirmar"
    return (
        f'<div class="info-card {css_extra}">'
        f'<div class="info-card-label">{esc(label)}</div>'
        f'<div class="{val_cls}">{val_text}</div>'
        f'</div>'
    )


def source_check_control(
    audit_id: int, check: object | None, csrf_token: str = "", return_tab: str = "sri",
) -> str:
    """Badge + botón toggle para marcar una fuente guiada (source_checks) como consultada.

    Usado en tab_sri, tab_supercias y tab_documentos del Radar Empresarial.
    'check' es la fila de source_checks ya resuelta por page.py (o None si esa
    fuente no está configurada para el expediente, en cuyo caso no se dibuja nada).
    """
    if not check:
        return ""
    consulted = row_get(check, "estado") == "consultada"
    badge_html = (
        f'<span class="badge {"badge-green" if consulted else "badge-gray"}">'
        f'{"Consultada" if consulted else "Pendiente de consultar"}</span>'
    )
    accion = "revertir" if consulted else "consultar"
    btn_text = "Marcar pendiente" if consulted else "Marcar consultada"
    obs_input = (
        "" if consulted else
        '<input name="observacion" placeholder="Observación (opcional)" class="source-check-obs-input">'
    )
    return f"""
    <div class="source-check-inline">
      {badge_html}
      <form method="post" action="/auditor/radar/source-check" class="source-check-inline-form">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="check_id" value="{row_get(check, 'id')}">
        <input type="hidden" name="accion" value="{accion}">
        <input type="hidden" name="return_tab" value="{esc(return_tab)}">
        {obs_input}
        <button type="submit" class="btn btn-sm">{btn_text}</button>
      </form>
    </div>
    """


def ruc_banner_html(ruc: str | None) -> str:
    """Genera el banner de estado del RUC (válido / advertencia / inválido / no registrado)."""
    if not ruc:
        return f'<div class="ruc-banner neutral">{SVG_INFO} <span>El RUC no ha sido registrado. El jefe debe añadirlo antes de iniciar la investigación.</span></div>'  # noqa: E501

    valid, warn, msg = validate_ruc(ruc)
    if warn:
        css, icon = "warning", SVG_ALERT
    elif valid:
        css, icon = "valid", SVG_CHECK
    else:
        css, icon = "invalid", SVG_CROSS

    return f'<div class="ruc-banner {css}">{icon} <div><span class="ruc-val">{esc(ruc)}</span> <span style="margin-left:8px">{esc(msg)}</span></div></div>'  # noqa: E501
