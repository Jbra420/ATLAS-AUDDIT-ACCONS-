"""
ui/helpers.py — Utilidades HTML y de formulario reutilizables en toda la app Atlas.
"""
from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    """HTML-escapa un valor para inserción segura en templates."""
    return html.escape("" if value is None else str(value), quote=True)


def form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    """Extrae el primer valor de un campo POST/GET, con valor por defecto."""
    values = form.get(key)
    if not values:
        return default
    return values[0].strip()


def _now() -> str:
    """Retorna la fecha/hora actual en formato ISO sin microsegundos."""
    from datetime import datetime
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def csrf_input(csrf_token: str) -> str:
    """Genera el hidden input que lleva el CSRF token en cada formulario POST."""
    return f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}">'
