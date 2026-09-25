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


def form_id(form: dict[str, list[str]], key: str) -> int:
    """Identificador numérico de un campo; 0 si falta o no es un entero
    positivo (ningún registro tiene id 0, así que el acceso se deniega)."""
    value = form_value(form, key)
    return int(value) if value.isdigit() else 0


def _now() -> str:
    """Retorna la fecha/hora actual en formato ISO sin microsegundos."""
    from datetime import datetime
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def csrf_input(csrf_token: str) -> str:
    """Genera el hidden input que lleva el CSRF token en cada formulario POST."""
    return f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}">'


def hidden_inputs(csrf_token: str, **fields: object) -> str:
    """CSRF + un hidden input por campo: el encabezado común de todo formulario POST."""
    return csrf_input(csrf_token) + "".join(
        f'<input type="hidden" name="{name}" value="{esc(value)}">' for name, value in fields.items()
    )
