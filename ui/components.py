"""
ui/components.py — Componentes HTML reutilizables de la interfaz Atlas.
Depende de ui/icons y ui/helpers únicamente. Nunca importa de views/.
"""
from __future__ import annotations

import sqlite3

from database import AUDIT_STATUSES, STAGE_FIELDS
from services.ruc_validator import validate_ruc
from ui.helpers import esc
from ui.icons import (
    SVG_ALERT,
    SVG_ARROW_RIGHT,
    SVG_CHECK,
    SVG_CIRCLE,
    SVG_CLOCK,
    SVG_CROSS,
    SVG_INFO,
    SVG_RETURN,
)


def badge(status: str) -> str:
    """Genera un badge HTML coloreado para un estado de auditoría."""
    css_map = {
        "pendiente": "badge-gray",
        "en_investigacion": "badge-amber",
        "listo_revision": "badge-blue",
        "devuelto": "badge-red",
        "revisado": "badge-green",
    }
    icon_map = {
        "pendiente": SVG_CIRCLE,
        "en_investigacion": SVG_CLOCK,
        "listo_revision": SVG_ARROW_RIGHT,
        "devuelto": SVG_RETURN,
        "revisado": SVG_CHECK,
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


def render_progress_track(progress: dict) -> str:
    """Renderiza la barra de progreso de la investigación."""
    stages = progress["stages"]
    labels = dict(STAGE_FIELDS)
    keys = list(labels.keys())

    current_idx = -1
    for i, k in enumerate(keys):
        if not stages[k]:
            current_idx = i
            break

    items = []
    for i, key in enumerate(keys):
        done = stages[key]
        is_current = i == current_idx
        css = "done" if done else ("current" if is_current else "")

        if done:
            icon = SVG_CHECK
        elif is_current:
            icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="4"></circle></svg>'  # noqa: E501
        else:
            icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle></svg>'  # noqa: E501

        items.append(f"""
        <div class="progress-step {css}">
          <div class="step-dot">{icon}</div>
          <div class="step-label">{labels[key]}</div>
        </div>
        """)

    pct = progress["percent"]
    return f"""
    <div class="progress-container">
      <div class="progress-header">
        <span>Progreso de la investigación</span>
        <span class="pct">{pct}%</span>
      </div>
      <div class="progress-track">{''.join(items)}</div>
    </div>
    """


def ruc_banner_html(ruc: str | None) -> str:
    """Genera el banner de estado del RUC (válido / inválido / no registrado)."""
    if not ruc:
        return f'<div class="ruc-banner neutral">{SVG_INFO} <span>El RUC no ha sido registrado. El jefe debe añadirlo antes de iniciar la investigación.</span></div>'  # noqa: E501

    valid, msg = validate_ruc(ruc)
    if "⚠" in msg:
        css = "warning"
        icon = SVG_ALERT
        msg = msg.replace("⚠ ", "")
    elif valid:
        css = "valid"
        icon = SVG_CHECK
        msg = msg.replace("✓ ", "")
    else:
        css = "invalid"
        icon = SVG_CROSS
        msg = msg.replace("✗ ", "")

    return f'<div class="ruc-banner {css}">{icon} <div><span class="ruc-val">{esc(ruc)}</span> <span style="margin-left:8px">{esc(msg)}</span></div></div>'  # noqa: E501
