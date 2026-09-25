"""database/expedientes.py — Empresas, auditorías, contexto completo del expediente, documentos económicos y progreso."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from seed_data import DEMO_RUC, seed_demo_radar
from services.ruc_validator import format_ruc, validate_ruc

from database.base import DB_PATH, connect, now_iso
from database.certificados import _pending_certificate
from database.financiero import _financial_context


AUDIT_STATUSES = {
    "pendiente": "Pendiente",
    "en_investigacion": "En investigación",
}


# Progreso de etapas para calcular % completitud del expediente.
STAGE_FIELDS = [
    ("ruc_ok", "RUC validado"),
    ("has_sources", "Fuentes consultadas"),
    ("has_sri", "Ficha SRI"),
    ("has_supercias", "Ficha Supercias"),
    ("has_docs", "Documentos revisados"),
    ("has_financials", "Indicadores"),
    ("has_summary", "Resumen generado"),
]


# Documentos económicos estándar para auditoría
DEFAULT_ECONOMIC_DOCUMENTS = [
    "Balance / Estado de Situación Financiera",
    "Estado de Resultados Integral",
    "Nómina de socios / accionistas",
    "Nómina de administradores",
    "Informe de gerente",
    "RUC",
    "Notas a estados financieros",
    "Acta de junta general",
    "Informe de auditoría externa del año anterior",
]


def create_company_audit(
    name: str,
    ruc: str,
    city: str,
    activity_hint: str,
    period: str,
    assigned_auditor_id: int,
    created_by: int,
    db_path: Path | str = DB_PATH,
) -> int:
    ts = now_iso()
    name = name.strip()
    raw_ruc = ruc.strip()
    ruc = format_ruc(raw_ruc)
    city = city.strip()
    activity_hint = activity_hint.strip()
    period = period.strip()
    if not name or not period:
        raise ValueError("Razón social y período auditado son obligatorios")
    if raw_ruc:
        valid, _warn, msg = validate_ruc(ruc)
        if not valid:
            raise ValueError(msg)

    with connect(db_path) as conn:
        auditor = conn.execute(
            "SELECT id, role, active, deleted_at FROM users WHERE id = ?",
            (int(assigned_auditor_id),),
        ).fetchone()
        if (
            auditor is None
            or auditor["role"] != "auditor"
            or auditor["active"] != 1
            or auditor["deleted_at"]
        ):
            raise ValueError("La empresa debe asignarse a un auditor activo")

        cur = conn.execute(
            """
            INSERT INTO companies (name, ruc, city, activity_hint, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, ruc, city, activity_hint, ts, ts),
        )
        company_id = int(cur.lastrowid)
        cur = conn.execute(
            """
            INSERT INTO audits (company_id, period, assigned_auditor_id, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'pendiente', ?, ?, ?)
            """,
            (company_id, period, int(assigned_auditor_id), created_by, ts, ts),
        )
        audit_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO audit_assignments (audit_id, auditor_id, assigned_by, assigned_at)
            VALUES (?, ?, ?, ?)
            """,
            (audit_id, int(assigned_auditor_id), created_by, ts),
        )
        # Un expediente nuevo siempre inicia en blanco, aunque el RUC coincida
        # con el demo: ese RUC corresponde a un cliente real y el expediente no
        # puede contener datos que no provengan de una fuente o del auditor.
        # Los datos demo solo se cargan de forma explícita (seed_defaults y
        # load_demo_if_ruc_matches).
        for nombre in DEFAULT_ECONOMIC_DOCUMENTS:
            conn.execute(
                "INSERT INTO economic_documents (audit_id, nombre, fecha, estado) VALUES (?, ?, ?, 'pendiente')",
                (audit_id, nombre, period),
            )
        fuentes = [
            ("SRI — Consulta de RUC", "Verificar estado tributario, tipo y actividad económica"),
            ("Supercias — Portal societario", "Confirmar estado societario, administradores y capital"),
            ("Supercias — Documentos económicos", "Obtener estados financieros y nómina de accionistas"),
            ("SERCOP", "Verificar historial de contratos públicos e inhabilitaciones"),
            ("Búsqueda web general", "Noticias, referencias, sanciones o información complementaria"),
        ]
        for fuente, uso in fuentes:
            conn.execute(
                "INSERT INTO source_checks (audit_id, fuente, uso, estado) VALUES (?, ?, ?, 'pendiente')",
                (audit_id, fuente, uso),
            )
        return audit_id


def register_audit_ruc(
    audit_id: int,
    ruc: str,
    db_path: Path | str = DB_PATH,
) -> tuple[str, str]:
    """Registra el RUC usado por el auditor si la empresa aun no lo tiene."""
    raw_ruc = ruc.strip()
    clean_ruc = format_ruc(raw_ruc)
    valid, _warn, msg = validate_ruc(clean_ruc)
    if not valid:
        raise ValueError(msg)

    with connect(db_path) as conn:
        audit = conn.execute(
            """
            SELECT a.id, a.company_id, c.ruc
            FROM audits a
            JOIN companies c ON c.id = a.company_id
            WHERE a.id = ?
            """,
            (audit_id,),
        ).fetchone()
        if audit is None:
            raise ValueError("Auditoría no encontrada")

        existing_ruc = audit["ruc"] or ""
        if existing_ruc and existing_ruc != clean_ruc:
            raise ValueError("El RUC ingresado no coincide con el RUC asignado por el jefe auditor")

        ts = now_iso()
        if not existing_ruc:
            conn.execute(
                "UPDATE companies SET ruc = ?, updated_at = ? WHERE id = ?",
                (clean_ruc, ts, audit["company_id"]),
            )
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (ts, audit_id))
    return clean_ruc, msg


def list_admin_audits(db_path: Path | str = DB_PATH, *, archived: bool = False) -> list[sqlite3.Row]:
    """Expedientes del directorio: los vigentes o, con archived=True, los archivados."""
    with connect(db_path) as conn:
        return list(
            conn.execute(
                f"""
                SELECT a.*, c.name AS company_name, c.ruc, c.city, c.activity_hint,
                       u.full_name AS auditor_name, u.username AS auditor_username,
                       u.active AS auditor_active, u.deleted_at AS auditor_deleted_at,
                       ar.full_name AS archived_by_name
                FROM audits a
                JOIN companies c ON c.id = a.company_id
                JOIN users u ON u.id = a.assigned_auditor_id
                LEFT JOIN users ar ON ar.id = a.archived_by
                WHERE a.archived_at IS {"NOT NULL" if archived else "NULL"}
                ORDER BY {"a.archived_at DESC" if archived else "a.updated_at DESC"}, a.id DESC
                """
            )
        )


def _require_active_admin(conn: sqlite3.Connection, user_id: int, accion: str) -> None:
    actor = conn.execute(
        "SELECT id FROM users WHERE id = ? AND role = 'admin' AND active = 1 AND deleted_at IS NULL",
        (user_id,),
    ).fetchone()
    if not actor:
        raise ValueError(f"Solo un administrador activo puede {accion}")


def archive_audit(
    audit_id: int, performed_by: int, reason: str, db_path: Path | str = DB_PATH,
) -> str:
    """Archiva la empresa: sale de los listados y el auditor pierde el acceso,
    pero el expediente, su evidencia y los PDF adjuntos se conservan."""
    reason = reason.strip()
    if len(reason) < 5:
        raise ValueError("Indica un motivo de al menos 5 caracteres para archivar")
    if len(reason) > 250:
        raise ValueError("El motivo no puede superar los 250 caracteres")
    with connect(db_path) as conn:
        _require_active_admin(conn, performed_by, "archivar empresas")
        cur = conn.execute(
            """
            UPDATE audits SET archived_at = ?, archived_by = ?, archive_reason = ?
            WHERE id = ? AND archived_at IS NULL
            """,
            (now_iso(), performed_by, reason, audit_id),
        )
        if cur.rowcount == 0:
            raise ValueError("La empresa no existe o ya está archivada")
    return "Empresa archivada. El expediente y su evidencia se conservaron."


def restore_audit(audit_id: int, performed_by: int, db_path: Path | str = DB_PATH) -> str:
    """Devuelve una empresa archivada al directorio y a su auditor asignado."""
    with connect(db_path) as conn:
        _require_active_admin(conn, performed_by, "restaurar empresas")
        cur = conn.execute(
            """
            UPDATE audits SET archived_at = NULL, archived_by = NULL, archive_reason = NULL, updated_at = ?
            WHERE id = ? AND archived_at IS NOT NULL
            """,
            (now_iso(), audit_id),
        )
        if cur.rowcount == 0:
            raise ValueError("La empresa no existe o no está archivada")
    return "Empresa restaurada"


def reassign_audit(
    audit_id: int,
    new_auditor_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> None:
    ts = now_iso()
    with connect(db_path) as conn:
        _require_active_admin(conn, performed_by, "reasignar empresas")

        auditor = conn.execute(
            """
            SELECT id FROM users
            WHERE id = ? AND role = 'auditor' AND active = 1 AND deleted_at IS NULL
            """,
            (new_auditor_id,),
        ).fetchone()
        if not auditor:
            raise ValueError("El nuevo auditor no es válido o no está activo.")

        audit = conn.execute(
            "SELECT assigned_auditor_id, archived_at FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()
        if not audit:
            raise ValueError("Auditoría no encontrada")
        if audit["archived_at"]:
            raise ValueError("Restaure la empresa antes de reasignarla")
        if audit["assigned_auditor_id"] == new_auditor_id:
            raise ValueError("La empresa ya está asignada a ese auditor")

        conn.execute(
            "UPDATE audits SET assigned_auditor_id = ?, updated_at = ? WHERE id = ?",
            (new_auditor_id, ts, audit_id)
        )
        conn.execute(
            """
            UPDATE audit_assignments
            SET unassigned_at = ?
            WHERE audit_id = ? AND unassigned_at IS NULL
            """,
            (ts, audit_id),
        )
        conn.execute(
            """
            INSERT INTO audit_assignments (audit_id, auditor_id, assigned_by, assigned_at)
            VALUES (?, ?, ?, ?)
            """,
            (audit_id, new_auditor_id, performed_by, ts),
        )


def list_auditor_audits(auditor_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT a.*, c.name AS company_name, c.ruc, c.city, c.activity_hint
                FROM audits a
                JOIN companies c ON c.id = a.company_id
                WHERE a.assigned_auditor_id = ? AND a.archived_at IS NULL
                ORDER BY a.updated_at DESC, a.id DESC
                """,
                (auditor_id,),
            )
        )


def get_audit(audit_id: int, user: sqlite3.Row, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        sql = """
            SELECT a.*, c.name AS company_name, c.ruc, c.city, c.activity_hint,
                   u.full_name AS auditor_name, u.username AS auditor_username
            FROM audits a
            JOIN companies c ON c.id = a.company_id
            JOIN users u ON u.id = a.assigned_auditor_id
            WHERE a.id = ?
        """
        params: list[Any] = [audit_id]
        if user["role"] == "auditor":
            # Una empresa archivada solo la ve el administrador (en lectura).
            sql += " AND a.assigned_auditor_id = ? AND a.archived_at IS NULL"
            params.append(user["id"])
        return conn.execute(sql, params).fetchone()


def _load_radar_context(conn: sqlite3.Connection, audit_id: int) -> dict[str, Any]:
    """Lee profile/location/admins/shareholders/docs/snapshot/source_checks/sources
    de un expediente usando una conexión ya abierta. No abre conexión propia:
    la comparten get_audit_context(), update_research() y refresh_summary()
    para no repetir las mismas 8 consultas ni abrir una conexión por cada una.

    "snapshot" es el financiero vigente (ver _financial_context) y "financial"
    trae además los ejercicios del RUC y las cifras sin año fiscal.
    """
    context = {
        "profile": conn.execute(
            "SELECT * FROM company_profiles WHERE audit_id = ?", (audit_id,)
        ).fetchone(),
        "location": conn.execute(
            "SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)
        ).fetchone(),
        "admins": list(conn.execute(
            "SELECT * FROM company_administrators WHERE audit_id = ? ORDER BY id", (audit_id,)
        )),
        "shareholders": list(conn.execute(
            "SELECT * FROM company_shareholders WHERE audit_id = ? ORDER BY numero, id", (audit_id,)
        )),
        "docs": list(conn.execute(
            """SELECT d.*, u.full_name AS revisado_por_nombre
               FROM economic_documents d
               LEFT JOIN users u ON u.id = d.revisado_por
               WHERE d.audit_id = ? ORDER BY d.id""", (audit_id,)
        )),

        "source_checks": list(conn.execute(
            "SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id", (audit_id,)
        )),
        "sources": list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC", (audit_id,)
        )),
        "provenance": list(conn.execute(
            "SELECT * FROM data_provenance WHERE audit_id = ? ORDER BY id", (audit_id,)
        )),
    }
    context["alert_treatments"] = list(conn.execute(
        "SELECT * FROM alert_treatments WHERE audit_id = ?", (audit_id,)
    ))
    context["certificado"] = _pending_certificate(conn, audit_id)
    financial = _financial_context(conn, audit_id)
    context["snapshot"] = financial["snapshot"]
    context["financial"] = financial
    return context


def get_audit_context(audit_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """Carga en una sola conexión todo lo que necesitan el Radar Empresarial,
    la generación de resumen y la ficha final: research, profile, location,
    admins, shareholders, docs, snapshot, source_checks y sources.

    Asume que el llamador ya validó que audit_id existe y es accesible
    (p. ej. vía get_audit(audit_id, user)); no repite esa validación.
    Reemplaza llamar por separado a get_research/get_company_profile/
    get_company_location/list_administrators/list_shareholders/
    list_economic_documents/get_financial_snapshot/list_source_checks/
    list_sources, que abrían una conexión SQLite distinta cada una.
    """
    with connect(db_path) as conn:
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
        context = _load_radar_context(conn, audit_id)
    context["research"] = research
    return context


def list_economic_documents(audit_id: int, db_path: Path | str = DB_PATH, *, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM economic_documents WHERE audit_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (audit_id, limit, offset),
        ))


def _set_document_state(audit_id: int, doc_id: int, sql: str, params: tuple, db_path: Path | str) -> None:
    """Cambia el estado de un documento solo si pertenece al expediente."""
    with connect(db_path) as conn:
        cur = conn.execute(f"{sql} WHERE id = ? AND audit_id = ?", (*params, doc_id, audit_id))  # noqa: S608
        if cur.rowcount == 0:
            raise ValueError("Documento no encontrado en este expediente")


def mark_document_reviewed(audit_id: int, doc_id: int, user_id: int, db_path: Path | str = DB_PATH) -> None:
    _set_document_state(
        audit_id, doc_id,
        "UPDATE economic_documents SET estado='revisado', revisado_por=?, revisado_at=?",
        (user_id, now_iso()), db_path,
    )


def mark_document_pending(audit_id: int, doc_id: int, db_path: Path | str = DB_PATH) -> None:
    _set_document_state(
        audit_id, doc_id,
        "UPDATE economic_documents SET estado='pendiente', revisado_por=NULL, revisado_at=NULL",
        (), db_path,
    )


def load_demo_if_ruc_matches(
    audit_id: int,
    ruc: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    """Si el RUC coincide con la empresa demo, carga todos los datos en la BD.
    Retorna True si se cargó demo, False si no."""
    if ruc.strip() != DEMO_RUC:
        return False
    with connect(db_path) as conn:
        seed_demo_radar(conn, audit_id)
    return True


def compute_progress(
    audit: sqlite3.Row,
    research: sqlite3.Row,
    source_count: int,
    db_path: Path | str = DB_PATH,
) -> dict:
    """Calcula el progreso de la investigación Radar Empresarial.

    Nota: source_count no se usa en este cuerpo. La etapa "has_sources" se
    calcula con su propia consulta a source_checks (fuentes guiadas), no con
    el conteo de la tabla sources (evidencia libre) que reciben los
    llamadores. Si se decide que ambas deben contar para el progreso, hay
    que revisar esta función junto con la Fase 5 (unificación pendiente).
    """
    audit_id = audit["id"]
    ruc = audit["ruc"] or ""

    # Fuentes guiadas consultadas
    with connect(db_path) as conn:
        checked_sources = conn.execute(
            "SELECT COUNT(*) FROM source_checks WHERE audit_id=? AND estado='consultada'",
            (audit_id,),
        ).fetchone()[0]
        profile = conn.execute(
            "SELECT * FROM company_profiles WHERE audit_id=?", (audit_id,)
        ).fetchone()
        docs_total = conn.execute(
            "SELECT COUNT(*) FROM economic_documents WHERE audit_id=?", (audit_id,)
        ).fetchone()[0]
        docs_reviewed = conn.execute(
            "SELECT COUNT(*) FROM economic_documents WHERE audit_id=? AND estado='revisado'",
            (audit_id,),
        ).fetchone()[0]
        snapshot = _financial_context(conn, audit_id)["snapshot"]

    has_sri = bool(profile and profile["estado_contribuyente"])
    has_supercias = bool(profile and profile["situacion_legal"])
    has_financials = bool(snapshot and snapshot["activo_total"])
    has_docs = docs_total > 0 and docs_reviewed >= max(1, docs_total // 2)

    stages = {
        "ruc_ok": len(ruc) == 13 and ruc.isdigit(),
        "has_sources": checked_sources >= 1,
        "has_sri": has_sri,
        "has_supercias": has_supercias,
        "has_docs": has_docs,
        "has_financials": has_financials,
        "has_summary": bool(research["generated_summary"] if research else False),
    }
    completed = sum(stages.values())
    return {
        "stages": stages,
        "completed": completed,
        "total": len(stages),
        "percent": int(completed / len(stages) * 100),
    }
