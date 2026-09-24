"""database/investigacion.py — Notas de investigación, resumen preliminar, fuentes consultadas y fuentes guiadas."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from services.summary import generate_summary

from database.base import DB_PATH, connect, now_iso
from database.expedientes import _load_radar_context


SOURCE_TYPES = [
    "SRI",
    "Supercias",
    "SERCOP",
    "Documentos economicos",
    "Busqueda web general",
    "Otra fuente",
]


RESEARCH_FIELDS = [
    "commercial_name",
    "economic_activity",
    "legal_status",
    "representative",
    "address",
    "tax_obligations",
    "public_contracting",
    "supercias_info",
    "sri_info",
    "sercop_info",
    "observations",
    "risk_flags",
    "pasted_text",
]


def get_research(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if row:
            return row
        conn.execute(
            "INSERT INTO research_notes (audit_id, updated_at) VALUES (?, ?)",
            (audit_id, now_iso()),
        )
        return conn.execute(
            "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
        ).fetchone()


def _fetch_audit(conn: sqlite3.Connection, audit_id: int) -> sqlite3.Row:
    audit = conn.execute(
        """
        SELECT a.*, c.name AS company_name, c.ruc, c.city, c.activity_hint
        FROM audits a
        JOIN companies c ON c.id = a.company_id
        WHERE a.id = ?
        """,
        (audit_id,),
    ).fetchone()
    if audit is None:
        raise ValueError("Auditoría no encontrada")
    return audit


def _compose_summary(conn: sqlite3.Connection, audit: sqlite3.Row, data: dict[str, str]) -> str:
    """Resumen preliminar del expediente con los datos de investigación 'data'."""
    from services.financial import compute_indicators

    ctx = _load_radar_context(conn, audit["id"])
    profile, location, snapshot = ctx["profile"], ctx["location"], ctx["snapshot"]
    return generate_summary(
        audit, data, len(ctx["sources"]),
        profile=dict(profile) if profile else None,
        location=dict(location) if location else None,
        admins=ctx["admins"],
        shareholders=ctx["shareholders"],
        snapshot=dict(snapshot) if snapshot else None,
        indicators=compute_indicators(dict(snapshot)) if snapshot else {},
        source_checks=ctx["source_checks"],
        sources=ctx["sources"],
        alert_treatments=ctx["alert_treatments"],
        provenance=ctx["provenance"],
    )


def update_research(
    audit_id: int,
    user_id: int,
    data: dict[str, str],
    mark_ready: bool,
    db_path: Path | str = DB_PATH,
) -> str:
    with connect(db_path) as conn:
        audit = _fetch_audit(conn, audit_id)
        summary = _compose_summary(conn, audit, data)
        ts = now_iso()

        conn.execute(
            """
            INSERT INTO research_notes (
                audit_id, commercial_name, economic_activity, legal_status, representative, address,
                tax_obligations, public_contracting, supercias_info, sri_info, sercop_info,
                observations, risk_flags, pasted_text, generated_summary, updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                commercial_name = excluded.commercial_name,
                economic_activity = excluded.economic_activity,
                legal_status = excluded.legal_status,
                representative = excluded.representative,
                address = excluded.address,
                tax_obligations = excluded.tax_obligations,
                public_contracting = excluded.public_contracting,
                supercias_info = excluded.supercias_info,
                sri_info = excluded.sri_info,
                sercop_info = excluded.sercop_info,
                observations = excluded.observations,
                risk_flags = excluded.risk_flags,
                pasted_text = excluded.pasted_text,
                generated_summary = excluded.generated_summary,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (
                audit_id,
                *(data.get(field, "").strip() for field in RESEARCH_FIELDS),
                summary,
                user_id,
                ts,
            ),
        )

        status = "en_investigacion" if audit["status"] == "pendiente" else audit["status"]
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, audit_id),
        )
        return summary


def patch_research(
    audit_id: int,
    user_id: int,
    fields: dict[str, str],
    db_path: Path | str = DB_PATH,
) -> None:
    """Actualiza solo los campos presentes en 'fields' en research_notes (PATCH semántico).

    A diferencia de update_research, NO toca columnas no incluidas en 'fields'.
    Garantiza que la fila exista antes de intentar el UPDATE. Solo acepta
    columnas de RESEARCH_FIELDS, lo que evita inyección SQL en el SET.
    """
    safe = {k: v.strip() for k, v in fields.items() if k in RESEARCH_FIELDS}
    if not safe:
        return
    ts = now_iso()
    with connect(db_path) as conn:
        # Garantizar que la fila exista
        conn.execute(
            "INSERT OR IGNORE INTO research_notes (audit_id, updated_by, updated_at) VALUES (?, ?, ?)",
            (audit_id, user_id, ts),
        )
        # UPDATE solo las columnas enviadas
        set_clause = ", ".join(f"{col} = ?" for col in safe)
        values = list(safe.values()) + [ts, user_id, audit_id]
        conn.execute(
            f"UPDATE research_notes SET {set_clause}, updated_at = ?, updated_by = ? WHERE audit_id = ?",  # noqa: S608
            values,
        )


def refresh_summary(audit_id: int, db_path: Path | str = DB_PATH) -> str:
    with connect(db_path) as conn:
        audit = _fetch_audit(conn, audit_id)
        research = conn.execute(
            "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if research is None:
            conn.execute(
                "INSERT INTO research_notes (audit_id, updated_at) VALUES (?, ?)",
                (audit_id, now_iso()),
            )
            research = conn.execute(
                "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        data = {field: research[field] or "" for field in RESEARCH_FIELDS}
        summary = _compose_summary(conn, audit, data)
        conn.execute(
            "UPDATE research_notes SET generated_summary = ?, updated_at = ? WHERE audit_id = ?",
            (summary, now_iso(), audit_id),
        )
        return summary


def list_sources(audit_id: int, db_path: Path | str = DB_PATH, *, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (audit_id, limit, offset),
        ))


def add_source(
    audit_id: int,
    title: str,
    url: str,
    source_type: str,
    notes: str,
    user_id: int,
    db_path: Path | str = DB_PATH,
) -> int:
    title_clean = title.strip()
    url_clean = url.strip()
    type_clean = source_type.strip()
    notes_clean = notes.strip()
    if not title_clean:
        raise ValueError("El titulo de la fuente es obligatorio")
    if type_clean not in SOURCE_TYPES:
        raise ValueError("Tipo de fuente no permitido")
    if url_clean:
        parsed = urlparse(url_clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("La URL debe iniciar con http:// o https://")

    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, title_clean, url_clean, type_clean, notes_clean, user_id, now_iso()),
        )
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))
        return int(cur.lastrowid)


def _append_evidence(existing: str, *parts: str) -> str:
    lines = [line.strip() for line in (existing or "").splitlines() if line.strip()]
    for part in parts:
        clean = part.strip()
        if clean and clean not in lines:
            lines.append(clean)
    return "\n".join(lines)


def append_research_source_note(
    audit_id: int,
    user_id: int,
    source_type: str,
    finding: str,
    evidence_text: str,
    db_path: Path | str = DB_PATH,
) -> None:
    """Añade hallazgos de una fuente al expediente sin borrar datos previos."""
    finding = finding.strip()
    evidence_text = evidence_text.strip()
    if not finding and not evidence_text:
        return

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO research_notes (audit_id, updated_at) VALUES (?, ?)",
                (audit_id, now_iso()),
            )
            row = conn.execute(
                "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
            ).fetchone()

        data = {field: row[field] or "" for field in RESEARCH_FIELDS}
        label = source_type.strip() or "Otra fuente"
        finding_line = f"{label}: {finding}" if finding else ""
        evidence_line = f"{label} - evidencia: {evidence_text}" if evidence_text else ""
        source_lower = label.lower()

        if "sercop" in source_lower:
            data["sercop_info"] = _append_evidence(data["sercop_info"], finding_line, evidence_line)
            if finding and not data["public_contracting"].strip():
                data["public_contracting"] = finding
        elif "sri" in source_lower:
            data["sri_info"] = _append_evidence(data["sri_info"], finding_line, evidence_line)
        elif "supercias" in source_lower:
            data["supercias_info"] = _append_evidence(data["supercias_info"], finding_line, evidence_line)
        elif "busqueda" in source_lower or "web" in source_lower:
            data["observations"] = _append_evidence(data["observations"], finding_line)
            data["pasted_text"] = _append_evidence(data["pasted_text"], evidence_text)
        else:
            data["observations"] = _append_evidence(data["observations"], finding_line, evidence_line)

    update_research(audit_id, user_id, data, mark_ready=False, db_path=db_path)


def list_source_checks(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id",
            (audit_id,),
        ))


def mark_source_checked(
    check_id: int,
    user_id: int,
    observacion: str,
    db_path: Path | str = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE source_checks
            SET estado='consultada', observacion=?, consultada_por=?, consultada_at=?
            WHERE id=?
            """,
            (observacion.strip(), user_id, now_iso(), check_id),
        )


def mark_matching_source_checked(
    audit_id: int,
    source_type: str,
    user_id: int,
    observacion: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    """Marca como consultada la fuente guiada que coincide con el tipo registrado."""
    source_lower = source_type.strip().lower()
    if not source_lower or source_lower == "otra fuente":
        return False

    if "document" in source_lower:
        tokens = ("document", "econ")
    elif "busqueda" in source_lower or "web" in source_lower:
        tokens = ("web", "busqueda", "búsqueda")
    elif "supercias" in source_lower:
        tokens = ("supercias",)
    elif "sercop" in source_lower:
        tokens = ("sercop",)
    elif "sri" in source_lower:
        tokens = ("sri",)
    else:
        tokens = (source_lower,)

    with connect(db_path) as conn:
        checks = list(conn.execute(
            "SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id", (audit_id,)
        ))
        for check in checks:
            name = (check["fuente"] or "").lower()
            if any(token in name for token in tokens):
                conn.execute(
                    """
                    UPDATE source_checks
                    SET estado='consultada', observacion=?, consultada_por=?, consultada_at=?
                    WHERE id=?
                    """,
                    (observacion.strip(), user_id, now_iso(), check["id"]),
                )
                return True
    return False


def mark_source_pending(check_id: int, db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE source_checks SET estado='pendiente', consultada_por=NULL, consultada_at=NULL WHERE id=?",
            (check_id,),
        )
