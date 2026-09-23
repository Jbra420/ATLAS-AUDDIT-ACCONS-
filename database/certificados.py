"""database/certificados.py — Certificados de nómina adjuntos: archivo, evidencia y propuesta de importación."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from services.certificados import TIPOS_CERTIFICADO

from database.base import BASE_DIR, DB_PATH, connect, now_iso

# Los PDF se guardan fuera de la base, uno por contenido: adjuntos/<audit_id>/<sha256>.pdf
ADJUNTOS_DIR = BASE_DIR / "adjuntos"


def _import_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["filas"] = json.loads(data.pop("filas_json"))
    data["advertencias"] = json.loads(data.pop("advertencias_json"))
    return data


def save_certificate(
    audit_id: int,
    tipo: str,
    archivo: str,
    pdf: bytes,
    analisis: dict[str, Any],
    user_id: int,
    db_path: Path | str = DB_PATH,
    adjuntos_dir: Path = ADJUNTOS_DIR,
) -> int:
    """Guarda el PDF, lo registra como evidencia de Supercias y deja la
    propuesta del analizador pendiente de revisión. Reemplaza la propuesta
    pendiente anterior del mismo tipo (solo se revisa un certificado a la vez).
    """
    if tipo not in TIPOS_CERTIFICADO:
        raise ValueError("Tipo de certificado no reconocido")
    sha256 = hashlib.sha256(pdf).hexdigest()
    ruta = Path(str(audit_id)) / f"{sha256}.pdf"
    destino = adjuntos_dir / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        destino.write_bytes(pdf)

    ts = now_iso()
    titulo = f"{TIPOS_CERTIFICADO[tipo]} (PDF adjunto)"
    notas = f"Archivo: {archivo} · SHA-256: {sha256} · {len(analisis['filas'])} fila(s) detectada(s)"
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE certificate_imports SET estado = 'descartado' "
            "WHERE audit_id = ? AND tipo = ? AND estado = 'pendiente'",
            (audit_id, tipo),
        )
        cur = conn.execute(
            """
            INSERT INTO certificate_imports
                (audit_id, tipo, archivo, sha256, ruta, filas_json, advertencias_json,
                 fecha_certificado, subido_por, subido_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, tipo, archivo, sha256, str(ruta), json.dumps(analisis["filas"], ensure_ascii=False),
             json.dumps(analisis["advertencias"], ensure_ascii=False), analisis["fecha_certificado"] or None,
             user_id, ts),
        )
        # El mismo archivo subido dos veces no duplica la evidencia.
        ya_registrado = conn.execute(
            "SELECT 1 FROM sources WHERE audit_id = ? AND notes LIKE ?", (audit_id, f"%SHA-256: {sha256}%"),
        ).fetchone()
        if not ya_registrado:
            conn.execute(
                "INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at) "
                "VALUES (?, ?, '', 'Supercias', ?, ?, ?)",
                (audit_id, titulo, notas, user_id, ts),
            )
        return int(cur.lastrowid)


def get_certificate_import(audit_id: int, import_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        return _import_dict(conn.execute(
            "SELECT * FROM certificate_imports WHERE id = ? AND audit_id = ?", (import_id, audit_id),
        ).fetchone())


def _pending_certificates(conn: sqlite3.Connection, audit_id: int) -> dict[str, dict[str, Any]]:
    """Propuesta pendiente de revisión por tipo ("administradores", "accionistas")."""
    rows = conn.execute(
        "SELECT * FROM certificate_imports WHERE audit_id = ? AND estado = 'pendiente' ORDER BY id",
        (audit_id,),
    )
    return {row["tipo"]: _import_dict(row) for row in rows}


def close_certificate_import(
    audit_id: int, import_id: int, estado: str, db_path: Path | str = DB_PATH,
) -> None:
    """Cierra una propuesta pendiente como 'importado' o 'descartado'. El PDF
    y su evidencia se conservan en ambos casos."""
    if estado not in ("importado", "descartado"):
        raise ValueError("Estado de certificado no válido")
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE certificate_imports SET estado = ? WHERE id = ? AND audit_id = ? AND estado = 'pendiente'",
            (estado, import_id, audit_id),
        )
        if cur.rowcount == 0:
            raise ValueError("El certificado ya fue revisado o no existe")
