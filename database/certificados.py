"""database/certificados.py — Certificado de nómina adjunto: archivo, evidencia y propuesta de importación."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from services.certificados import NOMINAS, TITULO_CERTIFICADO

from database.base import BASE_DIR, DB_PATH, connect, now_iso

# Los PDF se guardan fuera de la base, uno por contenido: adjuntos/<audit_id>/<sha256>.pdf
ADJUNTOS_DIR = BASE_DIR / "adjuntos"


def _import_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for clave in (*NOMINAS, "advertencias"):
        data[clave] = json.loads(data.pop(f"{clave}_json"))
    return data


def save_certificate(
    audit_id: int,
    archivos: list[tuple[str, bytes]],
    analisis: dict[str, Any],
    user_id: int,
    db_path: Path | str = DB_PATH,
    adjuntos_dir: Path = ADJUNTOS_DIR,
) -> int:
    """Guarda los PDF [(nombre, contenido)], registra cada uno como evidencia
    de Supercias y deja una sola propuesta de ambas nóminas pendiente de
    revisión. Reemplaza la propuesta pendiente anterior (se revisa una a la
    vez). Con varios archivos, archivo, sha256 y ruta guardan uno por línea."""
    ts = now_iso()
    conteo = ", ".join(f"{len(analisis[n])} {n}" for n in NOMINAS)
    detectados = f"Detectados: {conteo}" if len(archivos) == 1 else f"Detectados entre los {len(archivos)} PDF: {conteo}"
    guardados = []  # (nombre, sha256, ruta)
    for archivo, pdf in archivos:
        sha256 = hashlib.sha256(pdf).hexdigest()
        ruta = Path(str(audit_id)) / f"{sha256}.pdf"
        destino = adjuntos_dir / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        if not destino.exists():
            destino.write_bytes(pdf)
        guardados.append((archivo, sha256, str(ruta)))

    with connect(db_path) as conn:
        conn.execute(
            "UPDATE nomina_imports SET estado = 'descartado' WHERE audit_id = ? AND estado = 'pendiente'",
            (audit_id,),
        )
        cur = conn.execute(
            """
            INSERT INTO nomina_imports
                (audit_id, archivo, sha256, ruta, administradores_json, accionistas_json,
                 advertencias_json, fecha_certificado, subido_por, subido_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, *("\n".join(columna) for columna in zip(*guardados)),
             *(json.dumps(analisis[clave], ensure_ascii=False) for clave in (*NOMINAS, "advertencias")),
             analisis["fecha_certificado"] or None, user_id, ts),
        )
        for archivo, sha256, _ in guardados:
            # El mismo archivo subido dos veces no duplica la evidencia.
            ya_registrado = conn.execute(
                "SELECT 1 FROM sources WHERE audit_id = ? AND notes LIKE ?", (audit_id, f"%SHA-256: {sha256}%"),
            ).fetchone()
            if not ya_registrado:
                conn.execute(
                    "INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at) "
                    "VALUES (?, ?, '', 'Supercias', ?, ?, ?)",
                    (audit_id, f"{TITULO_CERTIFICADO} (PDF adjunto)",
                     f"Archivo: {archivo} · SHA-256: {sha256} · {detectados}", user_id, ts),
                )
        return int(cur.lastrowid)


def get_certificate_import(audit_id: int, import_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        return _import_dict(conn.execute(
            "SELECT * FROM nomina_imports WHERE id = ? AND audit_id = ?", (import_id, audit_id),
        ).fetchone())


def _pending_certificate(conn: sqlite3.Connection, audit_id: int) -> dict[str, Any] | None:
    """Propuesta pendiente de revisión del expediente, si la hay."""
    return _import_dict(conn.execute(
        "SELECT * FROM nomina_imports WHERE audit_id = ? AND estado = 'pendiente' ORDER BY id DESC LIMIT 1",
        (audit_id,),
    ).fetchone())


def close_certificate_import(
    audit_id: int, import_id: int, estado: str, db_path: Path | str = DB_PATH,
) -> None:
    """Cierra una propuesta pendiente como 'importado' o 'descartado'. El PDF
    y su evidencia se conservan en ambos casos."""
    if estado not in ("importado", "descartado"):
        raise ValueError("Estado de certificado no válido")
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE nomina_imports SET estado = ? WHERE id = ? AND audit_id = ? AND estado = 'pendiente'",
            (estado, import_id, audit_id),
        )
        if cur.rowcount == 0:
            raise ValueError("El certificado ya fue revisado o no existe")
