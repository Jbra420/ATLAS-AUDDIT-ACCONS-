"""database/trazabilidad.py — Registro del historial por dato (data_provenance): fuente y fecha de consulta de cada cambio."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from database.base import DB_PATH, _text, connect, now_iso


def _record_provenance(
    conn: sqlite3.Connection,
    audit_id: int,
    bloque: str,
    campo: str,
    anterior: Any,
    nuevo: Any,
    fuente: str,
    fecha_consulta: str,
    user_id: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO data_provenance (
            audit_id, bloque, campo, valor_anterior, valor_nuevo,
            fuente, fecha_consulta, registrado_por, registrado_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (audit_id, bloque, campo, anterior, nuevo, fuente, fecha_consulta, user_id, now_iso()),
    )


def _record_row_changes(
    conn: sqlite3.Connection,
    audit_id: int,
    before: sqlite3.Row | None,
    after: sqlite3.Row | None,
    campos: tuple[str, ...],
    bloque_de: Any,
    fuente_de: Any,
    fecha_consulta: str,
    user_id: int | None,
) -> None:
    """Registra en data_provenance cada campo de campos cuyo valor cambió.

    También registra un valor que no cambió pero nunca tuvo trazabilidad (datos
    capturados antes de la Fase 3): así, volver a consultar la fuente le da su
    primera fuente y fecha en lugar de dejarlo sin rastro para siempre.

    bloque_de y fuente_de reciben el nombre del campo y devuelven su bloque y
    su fuente; un bloque vacío significa que el campo no se historiza.
    """
    if after is None:
        return
    traced = {
        (row["bloque"], row["campo"])
        for row in conn.execute(
            "SELECT DISTINCT bloque, campo FROM data_provenance WHERE audit_id = ?", (audit_id,)
        )
    }
    for campo in campos:
        anterior = _text(before[campo]) if before is not None else ""
        nuevo = _text(after[campo])
        bloque = bloque_de(campo)
        untraced = bool(nuevo) and (bloque, campo) not in traced
        if bloque and (anterior != nuevo or untraced):
            _record_provenance(
                conn, audit_id, bloque, campo, anterior or None, nuevo or None,
                fuente_de(campo), fecha_consulta, user_id,
            )


def list_provenance(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    """Historial de trazabilidad del expediente en orden cronológico."""
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM data_provenance WHERE audit_id = ? ORDER BY id", (audit_id,)
        ))


def _update_fields(
    conn: sqlite3.Connection,
    table: str,
    audit_id: int,
    data: dict,
    allowed: tuple[str, ...],
) -> None:
    """Actualiza solo las columnas presentes en data que estén en allowed.

    Las columnas ausentes conservan su valor: guardar una pestaña no debe
    vaciar los datos de otra. table y allowed son constantes del módulo,
    nunca entrada del usuario.
    """
    fields = [k for k in allowed if k in data]
    ts = now_iso()
    conn.execute(
        f"INSERT OR IGNORE INTO {table} (audit_id, updated_at) VALUES (?, ?)",  # noqa: S608
        (audit_id, ts),
    )
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE {table} SET {assignments}, updated_at = ? WHERE audit_id = ?",  # noqa: S608
        (*(str(data[k]).strip() for k in fields), ts, audit_id),
    )
