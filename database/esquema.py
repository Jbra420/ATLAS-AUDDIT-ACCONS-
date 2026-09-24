"""database/esquema.py — Creación del esquema y migraciones de la base local."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from seed_data import seed_defaults

from database.base import BASE_DIR, DB_PATH, connect
from database.catalogos import lookup_catastro, lookup_supercias_catalog
from database.expedientes import DEFAULT_ECONOMIC_DOCUMENTS


SCHEMA_PATH = BASE_DIR / "schema.sql"


def init_db(db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        seed_defaults(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a tablas existentes (migración idempotente)."""
    # users: una baja definitiva conserva la fila y todas sus referencias históricas
    user_existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for col, col_type in [
        ("deleted_at", "TEXT"),
        ("deleted_by", "INTEGER REFERENCES users(id)"),
        ("deletion_reason", "TEXT"),
    ]:
        if col not in user_existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")

    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_physical_user_delete
        BEFORE DELETE ON users
        BEGIN
            SELECT RAISE(ABORT, 'users are permanent historical records');
        END;

        CREATE TRIGGER IF NOT EXISTS require_inactive_before_user_deletion
        BEFORE UPDATE OF deleted_at ON users
        WHEN OLD.deleted_at IS NULL
             AND NEW.deleted_at IS NOT NULL
             AND OLD.active = 1
        BEGIN
            SELECT RAISE(ABORT, 'user must be inactive before definitive deletion');
        END;

        CREATE TRIGGER IF NOT EXISTS prevent_deleted_user_reactivation
        BEFORE UPDATE OF active ON users
        WHEN OLD.deleted_at IS NOT NULL AND NEW.active = 1
        BEGIN
            SELECT RAISE(ABORT, 'deleted user cannot be reactivated');
        END;
        """
    )

    # research_notes
    rn_existing = {row[1] for row in conn.execute("PRAGMA table_info(research_notes)")}
    for col, col_type in [
        ("commercial_name", "TEXT"),
        ("supercias_info", "TEXT"),
        ("sri_info", "TEXT"),
        ("sercop_info", "TEXT"),
    ]:
        if col not in rn_existing:
            conn.execute(f"ALTER TABLE research_notes ADD COLUMN {col} {col_type}")

    # company_profiles: campos del catálogo local de Supercías (Directorio de
    # Compañías) que no existían en el Radar Empresarial v3.0 original.
    cp_existing = {row[1] for row in conn.execute("PRAGMA table_info(company_profiles)")}
    for col, col_type in [
        ("telefono", "TEXT"),
        ("representante_cargo", "TEXT"),
        ("capital_suscrito", "TEXT"),
        ("ciiu_nivel1", "TEXT"),
        ("ciiu_nivel6", "TEXT"),
        ("ultimo_anio_balance", "TEXT"),
        ("supercias_fuente", "TEXT"),
        ("supercias_catalogo_fecha", "TEXT"),
        # Datos por fuente: las validaciones cruzadas comparan SRI contra
        # Supercias, así que cada fuente conserva su propio valor.
        ("razon_social_sri", "TEXT"),
        ("razon_social_supercias", "TEXT"),
        ("representante_legal_sri", "TEXT"),
        # SRI — alerta crítica del levantamiento (SI / NO).
        ("contribuyente_fantasma", "TEXT"),
        ("transacciones_inexistentes", "TEXT"),
        # Código CIIU del SRI, para el cruce con el CIIU del Directorio.
        ("ciiu_sri", "TEXT"),
    ]:
        if col not in cp_existing:
            conn.execute(f"ALTER TABLE company_profiles ADD COLUMN {col} {col_type}")
    _backfill_source_names(conn)

    # Administradores y accionistas: tipo de identificación, datos del
    # levantamiento y fuente/fecha de consulta de cada registro.
    people_columns = {
        "company_administrators": [
            ("tipo_identificacion", "TEXT"),
            ("fuente", "TEXT"),
            ("fecha_consulta", "TEXT"),
        ],
        "company_shareholders": [
            ("tipo_identificacion", "TEXT"),
            ("participacion_porcentaje", "REAL"),
            ("capital", "REAL"),
            ("beneficiario_final", "TEXT"),
            ("fuente", "TEXT"),
            ("fecha_consulta", "TEXT"),
        ],
    }
    for table, columns in people_columns.items():
        existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, col_type in columns:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    # Año fiscal de los estados financieros de cada auditoría (parámetro
    # previo obligatorio del bloque financiero; distinto del período).
    audit_cols = {row[1] for row in conn.execute("PRAGMA table_info(audits)")}
    if "anio_fiscal_eeff" not in audit_cols:
        conn.execute("ALTER TABLE audits ADD COLUMN anio_fiscal_eeff INTEGER")
    # Empresas archivadas: se ocultan sin borrar el expediente.
    for col, col_type in [
        ("archived_at", "TEXT"),
        ("archived_by", "INTEGER REFERENCES users(id)"),
        ("archive_reason", "TEXT"),
    ]:
        if col not in audit_cols:
            conn.execute(f"ALTER TABLE audits ADD COLUMN {col} {col_type}")

    # Documentos de respaldo agregados al requisito después de crear algunos
    # expedientes: se incorporan como pendientes, sin tocar los existentes.
    for nombre in DEFAULT_ECONOMIC_DOCUMENTS:
        conn.execute(
            """
            INSERT INTO economic_documents (audit_id, nombre, fecha, estado)
            SELECT a.id, ?, a.period, 'pendiente' FROM audits a
            WHERE EXISTS (SELECT 1 FROM economic_documents d WHERE d.audit_id = a.id)
              AND NOT EXISTS (
                  SELECT 1 FROM economic_documents d WHERE d.audit_id = a.id AND d.nombre = ?
              )
            """,
            (nombre, nombre),
        )

    # Trazabilidad de solo inserción. El borrado solo se permite en cascada,
    # cuando se elimina el expediente completo.
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_data_provenance_update
        BEFORE UPDATE ON data_provenance
        BEGIN
            SELECT RAISE(ABORT, 'data_provenance is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS prevent_data_provenance_delete
        BEFORE DELETE ON data_provenance
        WHEN EXISTS (SELECT 1 FROM audits WHERE id = OLD.audit_id)
        BEGIN
            SELECT RAISE(ABORT, 'data_provenance is append-only');
        END;
        """
    )

    # sessions: agregar csrf_token si la BD fue creada antes de esta versión
    sess_existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "csrf_token" not in sess_existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN csrf_token TEXT NOT NULL DEFAULT ''")

    # Historial de asignaciones para conservar qué empresas atendió cada auditor.
    conn.execute(
        """
        INSERT INTO audit_assignments (audit_id, auditor_id, assigned_by, assigned_at, unassigned_at)
        SELECT a.id, a.assigned_auditor_id, a.created_by, a.created_at, NULL
        FROM audits a
        WHERE NOT EXISTS (
            SELECT 1 FROM audit_assignments h WHERE h.audit_id = a.id
        )
        """
    )

    # certificate_imports (una propuesta por tipo) se reemplazó por
    # nomina_imports (un PDF, ambas nóminas). Solo se elimina si está vacía.
    antigua = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'certificate_imports'")
    if antigua.fetchone() and not conn.execute("SELECT 1 FROM certificate_imports LIMIT 1").fetchone():
        conn.execute("DROP TABLE certificate_imports")


def _backfill_source_names(conn: sqlite3.Connection) -> None:
    """Completa razon_social_sri / razon_social_supercias en expedientes que ya
    fueron consultados antes de existir esas columnas.

    Solo usa el catálogo que efectivamente se consultó en ese expediente
    (evidencia 'Catastro RUC SRI (base local)' o supercias_fuente =
    'catalogo_local'), para no atribuir a un expediente datos de una fuente
    que el auditor nunca consultó. Si el RUC ya no consta en el catálogo, el
    campo queda vacío (pendiente de confirmar).
    """
    rows = list(conn.execute(
        """
        SELECT p.audit_id, p.ruc, p.razon_social_sri, p.razon_social_supercias,
               p.supercias_fuente,
               EXISTS (
                   SELECT 1 FROM sources s
                   WHERE s.audit_id = p.audit_id AND s.source_type = 'SRI'
                     AND s.title = 'Catastro RUC SRI (base local)'
               ) AS sri_consultado
        FROM company_profiles p
        WHERE TRIM(COALESCE(p.ruc, '')) <> ''
          AND (TRIM(COALESCE(p.razon_social_sri, '')) = ''
               OR TRIM(COALESCE(p.razon_social_supercias, '')) = '')
        """
    ))
    for row in rows:
        ruc = row["ruc"].strip()
        if row["sri_consultado"] and not (row["razon_social_sri"] or "").strip():
            record = lookup_catastro(ruc)
            name = ((record or {}).get("name") or "").strip()
            if name:
                conn.execute(
                    "UPDATE company_profiles SET razon_social_sri = ? WHERE audit_id = ?",
                    (name, row["audit_id"]),
                )
        if row["supercias_fuente"] == "catalogo_local" and not (row["razon_social_supercias"] or "").strip():
            record = lookup_supercias_catalog(ruc)
            name = ((record or {}).get("razon_social") or "").strip()
            if name:
                conn.execute(
                    "UPDATE company_profiles SET razon_social_supercias = ? WHERE audit_id = ?",
                    (name, row["audit_id"]),
                )
