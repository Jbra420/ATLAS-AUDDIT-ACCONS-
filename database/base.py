"""database/base.py — Conexión SQLite y utilidades compartidas por todo el paquete."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


# Raíz del proyecto: las bases locales viven junto a app.py.
BASE_DIR = Path(__file__).resolve().parent.parent
# ATLAS_DB_PATH permite usar otra base (p. ej. una desechable para tests/test_http.py).
DB_PATH = Path(os.environ.get("ATLAS_DB_PATH") or BASE_DIR / "auddit.db")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _fetch_row(conn: sqlite3.Connection, table: str, audit_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM {table} WHERE audit_id = ?", (audit_id,)  # noqa: S608
    ).fetchone()


def _today() -> str:
    return datetime.now().date().isoformat()


def _touch_audit(conn: sqlite3.Connection, audit_id: int) -> None:
    conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))


def _optional_number(
    value: Any, label: str, minimum: float, maximum: float | None = None,
) -> float | None:
    """Número opcional del formulario (acepta coma decimal). Vacío -> None."""
    text = _text(value).replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} debe ser un número") from exc
    if number < minimum or (maximum is not None and number > maximum):
        rango = f"entre {minimum:g} y {maximum:g}" if maximum is not None else f"mayor o igual a {minimum:g}"
        raise ValueError(f"{label} debe estar {rango}")
    return number
