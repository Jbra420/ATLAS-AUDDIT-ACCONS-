"""
views/admin/audit_detail.py — Supervisión de expediente por el jefe auditor.

El jefe ve el mismo Radar Empresarial que usa el auditor, pero en modo lectura.
No ejecuta búsquedas, verificaciones, edición de datos ni generación de resumen.
"""
from __future__ import annotations

import sqlite3

from views.auditor.radar import page as radar_page


def render(user: sqlite3.Row, query: dict, active_path: str) -> str:
    """Genera el expediente completo en modo supervisión."""
    return radar_page.render(user, query, active_path)
