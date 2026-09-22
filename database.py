"""
database.py — Capa de persistencia de Atlas (Auddit).

Usa sqlite3 puro. Sin dependencias externas.
La generación de resumen fue movida a services/summary.py.

v3.0 — Radar Empresarial: agrega tablas para company_profiles,
company_locations, company_administrators, company_shareholders,
economic_documents, financial_snapshots y source_checks.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from seed_data import DEMO_RUC, seed_defaults, seed_demo_radar
from services.financial import CAMPOS_FINANCIEROS, CASILLEROS, ETIQUETAS_FINANCIERAS
from services.identificacion import validar_identificacion
from services.normalizacion import normalizar_texto
from services.trazabilidad import (
    BLOQUE_ACCIONISTAS,
    BLOQUE_ADMINISTRADORES,
    BLOQUE_FINANCIERO,
    BLOQUE_UBICACION,
    BLOQUE_VALIDACIONES,
    CAMPOS_PERFIL_SRI,
    CAMPOS_PERFIL_SUPERCIAS,
    CAMPOS_UBICACION,
    FUENTE_CATASTRO_SRI,
    FUENTE_DIRECTORIO_SUPERCIAS,
    FUENTE_MANUAL_POR_BLOQUE,
    FUENTE_SUPERCIAS_ACCIONISTAS,
    FUENTE_SUPERCIAS_ADMINISTRADORES,
    FUENTE_SUPERCIAS_UBICACION,
    bloque_de_campo_perfil,
)
from services.ruc_validator import format_ruc, validate_ruc
from services.summary import generate_summary


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "auddit.db"

AUDIT_STATUSES = {
    "pendiente": "Pendiente",
    "en_investigacion": "En investigación",
}

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")

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


# ---------------------------------------------------------------------------
# Conexión y utilidades
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Seguridad: hashing de contraseñas y sesiones
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected_hash)


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Inicialización de la base de datos
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------

def create_user(
    conn: sqlite3.Connection,
    username: str,
    full_name: str,
    role: str,
    password: str,
) -> int:
    username = username.strip().lower()
    full_name = full_name.strip()
    password = password.strip()
    if role not in {"admin", "auditor"}:
        raise ValueError("Rol no permitido")
    if not username or not full_name or not password:
        raise ValueError("Usuario, nombre y clave son obligatorios")
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("El usuario debe tener entre 3 y 32 caracteres y usar solo letras, números, punto, guion o guion bajo")
    if len(password) < 6:
        raise ValueError("La clave temporal debe tener al menos 6 caracteres")

    existing = conn.execute(
        "SELECT deleted_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing:
        if existing["deleted_at"]:
            raise ValueError("El nombre de usuario pertenece a una cuenta histórica y no puede reutilizarse")
        raise ValueError("El nombre de usuario ya existe")

    salt, pw_hash = hash_password(password)
    try:
        cur = conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_salt, password_hash, active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (username, full_name, role, salt, pw_hash, now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("El nombre de usuario ya existe") from exc
    return int(cur.lastrowid)


def authenticate(username: str, password: str, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1 AND deleted_at IS NULL",
            (username.strip().lower(),),
        ).fetchone()
        if user and verify_password(password, user["password_salt"], user["password_hash"]):
            return user
        return None


def list_users(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            """
            SELECT u.id, u.username, u.full_name, u.role, u.active, u.created_at,
                   u.deleted_at, u.deleted_by, u.deletion_reason,
                   actor.full_name AS deleted_by_name
            FROM users u
            LEFT JOIN users actor ON actor.id = u.deleted_by
            ORDER BY u.role, u.deleted_at IS NOT NULL, u.active DESC, u.full_name
            """
        ))


def list_auditors(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            """
            SELECT id, username, full_name
            FROM users
            WHERE role = 'auditor' AND active = 1 AND deleted_at IS NULL
            ORDER BY full_name
            """
        ))


def _admin_user_action(
    conn: sqlite3.Connection,
    user_id: int,
    performed_by: int,
) -> sqlite3.Row:
    actor = conn.execute(
        "SELECT id, role, active, deleted_at FROM users WHERE id = ?",
        (performed_by,),
    ).fetchone()
    if actor is None or actor["role"] != "admin" or actor["active"] != 1 or actor["deleted_at"]:
        raise ValueError("Solo un administrador activo puede gestionar usuarios")
    if user_id == performed_by:
        raise ValueError("No puedes cambiar el estado de tu propio usuario")

    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        raise ValueError("Usuario no encontrado")
    return target


def deactivate_user(
    user_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> str:
    """Suspende el acceso de una cuenta sin alterar su historial ni asignaciones."""
    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("El usuario ya tiene baja definitiva")
        if target["active"] != 1:
            raise ValueError("El usuario ya está inactivo")
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return "Usuario desactivado. Sus empresas y su historial se conservaron."


def reactivate_user(
    user_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> str:
    """Restablece el acceso de una cuenta suspendida, pero nunca de una cuenta dada de baja."""
    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("Una cuenta con baja definitiva no puede reactivarse")
        if target["active"] == 1:
            raise ValueError("El usuario ya está activo")
        conn.execute("UPDATE users SET active = 1 WHERE id = ?", (user_id,))
    return "Usuario reactivado. Deberá iniciar una sesión nueva."


def soft_delete_user(
    user_id: int,
    performed_by: int,
    deletion_reason: str,
    db_path: Path | str = DB_PATH,
) -> str:
    """Registra una baja definitiva sin borrar la cuenta ni sus relaciones históricas."""
    reason = deletion_reason.strip()
    if len(reason) < 5:
        raise ValueError("Indica un motivo de baja de al menos 5 caracteres")
    if len(reason) > 250:
        raise ValueError("El motivo de baja no puede superar los 250 caracteres")

    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("El usuario ya tiene baja definitiva")
        if target["active"] == 1:
            raise ValueError("Primero debes desactivar al usuario antes de darle de baja")
        conn.execute(
            """
            UPDATE users
            SET active = 0, deleted_at = ?, deleted_by = ?, deletion_reason = ?
            WHERE id = ?
            """,
            (now_iso(), performed_by, reason, user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return "Baja definitiva registrada. Las empresas y el historial del usuario se conservaron."


# ---------------------------------------------------------------------------
# Catastro Local (RUC)
# ---------------------------------------------------------------------------

def lookup_catastro(ruc: str, db_path: Path | str = DB_PATH) -> dict | None:
    """
    Busca el RUC en el catastro local del SRI y devuelve todos los campos
    disponibles para que la investigación automática pueda construir la ficha.

    Nota: db_path se acepta por consistencia con el resto de database.py
    pero no se usa: esta función siempre conecta a sri_catastro.db junto al
    módulo, no a la base de datos de la aplicación. No es intercambiable
    con una base de prueba mediante ese parámetro.
    """
    # Definimos la ruta de la base de catastro asumiendo que está en la raíz junto a la base principal
    catastro_db_path = Path(__file__).parent / "sri_catastro.db"
    if not catastro_db_path.exists():
        return None
        
    try:
        with sqlite3.connect(catastro_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sri_catastro WHERE ruc = ?", (ruc,)).fetchone()
            if row:
                return dict(row)
    except Exception as e:
        import logging
        logging.error(f"Error querying local catastro: {e}")
        
    return None


def apply_sri_research_result(
    audit_id: int,
    user_id: int,
    result: dict[str, dict[str, str]],
    db_path: Path | str = DB_PATH,
) -> None:
    """Guarda una consulta SRI sin modificar campos pertenecientes a Supercias."""
    company = result["company"]
    profile = result["profile"]
    location = result["location"]
    research = result["research"]
    ts = now_iso()

    with connect(db_path) as conn:
        audit = conn.execute(
            "SELECT company_id, status FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()
        if audit is None:
            raise ValueError("Auditoría no encontrada")
        profile_before = _fetch_row(conn, "company_profiles", audit_id)
        location_before = _fetch_row(conn, "company_locations", audit_id)

        conn.execute(
            """
            UPDATE companies
            SET name = ?, ruc = ?, city = ?, activity_hint = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                company["name"], company["ruc"], company["city"],
                company["activity_hint"], ts, audit["company_id"],
            ),
        )
        conn.execute(
            """
            INSERT INTO company_profiles (
                audit_id, ruc, razon_social, razon_social_sri, estado_contribuyente,
                tipo_contribuyente, regimen, categoria, obligado_contabilidad, agente_retencion,
                contribuyente_especial, fecha_inicio_actividades,
                fecha_actualizacion, actividad_economica, ciiu_sri, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                ruc = excluded.ruc,
                razon_social = CASE
                    WHEN TRIM(COALESCE(company_profiles.razon_social, '')) = '' THEN excluded.razon_social
                    ELSE company_profiles.razon_social
                END,
                razon_social_sri = excluded.razon_social_sri,
                estado_contribuyente = excluded.estado_contribuyente,
                tipo_contribuyente = excluded.tipo_contribuyente,
                -- Un código de clase sin equivalencia no borra un régimen ya confirmado.
                regimen = CASE
                    WHEN excluded.regimen <> '' THEN excluded.regimen
                    ELSE company_profiles.regimen
                END,
                categoria = excluded.categoria,
                obligado_contabilidad = excluded.obligado_contabilidad,
                agente_retencion = excluded.agente_retencion,
                contribuyente_especial = excluded.contribuyente_especial,
                fecha_inicio_actividades = excluded.fecha_inicio_actividades,
                fecha_actualizacion = excluded.fecha_actualizacion,
                actividad_economica = excluded.actividad_economica,
                ciiu_sri = excluded.ciiu_sri,
                updated_at = excluded.updated_at
            """,
            (
                audit_id, profile["ruc"], profile["razon_social"], profile["razon_social_sri"],
                profile["estado_contribuyente"], profile["tipo_contribuyente"],
                profile["regimen"], profile["categoria"], profile["obligado_contabilidad"],
                profile["agente_retencion"], profile["contribuyente_especial"],
                profile["fecha_inicio_actividades"], profile["fecha_actualizacion"],
                profile["actividad_economica"], profile["ciiu_sri"], ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO company_locations (audit_id, provincia, canton, ciudad, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                provincia = excluded.provincia,
                canton = excluded.canton,
                ciudad = excluded.ciudad,
                updated_at = excluded.updated_at
            """,
            (
                audit_id, location["provincia"], location["canton"],
                location["ciudad"], ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO research_notes (
                audit_id, commercial_name, economic_activity, sri_info,
                updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                commercial_name = CASE
                    WHEN excluded.commercial_name <> '' THEN excluded.commercial_name
                    ELSE research_notes.commercial_name
                END,
                economic_activity = excluded.economic_activity,
                sri_info = excluded.sri_info,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (
                audit_id, research["commercial_name"], research["economic_activity"],
                research["sri_info"], user_id, ts,
            ),
        )
        _record_catalog_changes(
            conn, audit_id, profile_before, location_before,
            FUENTE_CATASTRO_SRI, user_id,
        )

        conn.execute(
            """
            UPDATE source_checks
            SET estado = 'consultada', observacion = ?, consultada_por = ?, consultada_at = ?
            WHERE audit_id = ? AND lower(fuente) LIKE '%sri%'
            """,
            ("Consulta automática en el catastro local oficial del SRI", user_id, ts, audit_id),
        )

        source = conn.execute(
            """
            SELECT id FROM sources
            WHERE audit_id = ? AND source_type = 'SRI'
              AND title = 'Catastro RUC SRI (base local)'
            """,
            (audit_id,),
        ).fetchone()
        source_notes = f"Consulta automática del RUC {company['ruc']} realizada el {ts}."
        if source:
            conn.execute("UPDATE sources SET notes = ? WHERE id = ?", (source_notes, source["id"]))
        else:
            conn.execute(
                """
                INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at)
                VALUES (?, 'Catastro RUC SRI (base local)', 'https://www.sri.gob.ec/datasets',
                        'SRI', ?, ?, ?)
                """,
                (audit_id, source_notes, user_id, ts),
            )

        status = "en_investigacion" if audit["status"] == "pendiente" else audit["status"]
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, audit_id),
        )


# ---------------------------------------------------------------------------
# Catálogo Local de Supercías (Directorio de Compañías)
# ---------------------------------------------------------------------------

SUPERCIAS_CATALOG_PATH = BASE_DIR / "supercias_catalog.db"
BALANCES_CATALOG_PATH = BASE_DIR / "supercias_balances.db"
BALANCES_SOURCE_URL = (
    "https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/"
    "estadosFinancierosPorRamo.jsf"
)
BALANCES_FIELDS = tuple(field for field, _label, code, _neg in CASILLEROS if code)


def lookup_supercias_catalog(ruc: str) -> dict | None:
    """Busca el RUC en el catálogo local de Supercías (Directorio de Compañías).

    Nota: al igual que lookup_catastro, esta función siempre conecta a
    supercias_catalog.db junto al módulo, nunca a la base de la aplicación.
    No acepta db_path porque no es intercambiable con una base de prueba.
    Retorna None si el catálogo no ha sido importado o el RUC no consta en él;
    nunca lanza una excepción hacia el llamador (mejor esfuerzo, sin 500).
    """
    if not SUPERCIAS_CATALOG_PATH.exists():
        return None
    try:
        with sqlite3.connect(SUPERCIAS_CATALOG_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM supercias_catalog WHERE ruc = ?", (ruc,)
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            meta = conn.execute(
                "SELECT fecha_actualizacion, total_filas FROM supercias_catalog_meta "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            record["_catalogo_fecha_actualizacion"] = meta["fecha_actualizacion"] if meta else ""
            record["_catalogo_total_filas"] = meta["total_filas"] if meta else ""
            return record
    except Exception as e:
        import logging
        logging.error(f"Error querying local Supercias catalog: {e}")
        return None


def apply_supercias_research_result(
    audit_id: int,
    user_id: int,
    result: dict[str, dict[str, str]],
    db_path: Path | str = DB_PATH,
) -> None:
    """Guarda una consulta al catálogo local de Supercías sin pisar campos que
    el auditor ya haya editado a mano ni los que pertenecen a SRI.

    A diferencia de apply_sri_research_result, cada columna solo se llena si
    hoy está vacía (patrón CASE WHEN ... = '' THEN excluded ELSE columna):
    el catálogo de Supercías puede llegar antes o después que la búsqueda SRI
    y nunca debe sobrescribir una corrección manual del auditor.
    """
    profile = result["profile"]
    location = result["location"]
    research = result["research"]
    catalogo = result.get("catalogo", {})
    ts = now_iso()

    def _fill_if_empty(col: str) -> str:
        return (
            f"{col} = CASE WHEN TRIM(COALESCE(company_profiles.{col}, '')) = '' "
            f"THEN excluded.{col} ELSE company_profiles.{col} END"
        )

    with connect(db_path) as conn:
        audit = conn.execute(
            "SELECT company_id, status FROM audits WHERE id = ?", (audit_id,)
        ).fetchone()
        if audit is None:
            raise ValueError("Auditoría no encontrada")
        profile_before = _fetch_row(conn, "company_profiles", audit_id)
        location_before = _fetch_row(conn, "company_locations", audit_id)

        profile_cols = [
            "expediente_supercias", "razon_social", "razon_social_supercias", "situacion_legal",
            "fecha_constitucion", "tipo_compania", "nacionalidad",
            "representante_legal", "representante_cargo", "capital_suscrito",
            "ciiu_nivel1", "ciiu_nivel6", "ultimo_anio_balance", "telefono",
        ]
        set_clause = ", ".join(_fill_if_empty(c) for c in profile_cols)
        conn.execute(
            f"""
            INSERT INTO company_profiles (
                audit_id, {", ".join(profile_cols)},
                supercias_fuente, supercias_catalogo_fecha, updated_at
            ) VALUES (?, {", ".join("?" for _ in profile_cols)}, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                {set_clause},
                supercias_fuente = 'catalogo_local',
                supercias_catalogo_fecha = excluded.supercias_catalogo_fecha,
                updated_at = excluded.updated_at
            """,  # noqa: S608
            (
                audit_id, *(profile[c] for c in profile_cols),
                "catalogo_local", catalogo.get("fecha_actualizacion", ""), ts,
            ),
        )

        loc_cols = ["provincia", "canton", "ciudad", "calle", "numero", "interseccion", "barrio"]
        loc_set_clause = ", ".join(
            f"{c} = CASE WHEN TRIM(COALESCE(company_locations.{c}, '')) = '' "
            f"THEN excluded.{c} ELSE company_locations.{c} END"
            for c in loc_cols
        )
        conn.execute(
            f"""
            INSERT INTO company_locations (audit_id, {", ".join(loc_cols)}, updated_at)
            VALUES (?, {", ".join("?" for _ in loc_cols)}, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                {loc_set_clause},
                updated_at = excluded.updated_at
            """,  # noqa: S608
            (audit_id, *(location[c] for c in loc_cols), ts),
        )

        corte = (catalogo.get("fecha_actualizacion") or "").strip()
        fuente = f"{FUENTE_DIRECTORIO_SUPERCIAS}, corte {corte}" if corte else FUENTE_DIRECTORIO_SUPERCIAS
        _record_catalog_changes(conn, audit_id, profile_before, location_before, fuente, user_id)
        _register_directory_administrators(
            conn, audit_id, result.get("administradores", []), fuente, user_id,
        )

        conn.execute(
            """
            INSERT INTO research_notes (audit_id, legal_status, representative, supercias_info, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                legal_status = CASE WHEN TRIM(COALESCE(research_notes.legal_status, '')) = '' THEN excluded.legal_status ELSE research_notes.legal_status END,
                representative = CASE WHEN TRIM(COALESCE(research_notes.representative, '')) = '' THEN excluded.representative ELSE research_notes.representative END,
                supercias_info = excluded.supercias_info,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (audit_id, research["legal_status"], research["representative"], research["supercias_info"], user_id, ts),
        )

        conn.execute(
            """
            UPDATE source_checks
            SET estado = 'consultada', observacion = ?, consultada_por = ?, consultada_at = ?
            WHERE audit_id = ? AND lower(fuente) LIKE '%supercias%' AND lower(fuente) NOT LIKE '%documento%'
            """,
            ("Consulta automática del Directorio de Compañías (catálogo local)", user_id, ts, audit_id),
        )

        source = conn.execute(
            """
            SELECT id FROM sources
            WHERE audit_id = ? AND source_type = 'Supercias'
              AND title = 'Directorio Supercías (catálogo local)'
            """,
            (audit_id,),
        ).fetchone()
        fecha_cat = catalogo.get("fecha_actualizacion") or "sin fecha declarada"
        expediente = profile.get("expediente_supercias") or "sin expediente"
        source_notes = (
            f"Consulta automática del expediente {expediente} realizada el {ts}. "
            f"Corte del catálogo: {fecha_cat}."
        )
        if source:
            conn.execute("UPDATE sources SET notes = ? WHERE id = ?", (source_notes, source["id"]))
        else:
            conn.execute(
                """
                INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at)
                VALUES (?, 'Directorio Supercías (catálogo local)',
                        'https://mercadodevalores.supercias.gob.ec/reportes/directorioCompanias.jsf',
                        'Supercias', ?, ?, ?)
                """,
                (audit_id, source_notes, user_id, ts),
            )

        status = "en_investigacion" if audit["status"] == "pendiente" else audit["status"]
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, audit_id),
        )


def _record_catalog_changes(
    conn: sqlite3.Connection,
    audit_id: int,
    profile_before: sqlite3.Row | None,
    location_before: sqlite3.Row | None,
    fuente: str,
    user_id: int | None,
) -> None:
    """Registra los campos que cambió una consulta a un catálogo local. La
    fecha de consulta es la de la búsqueda; el corte del catálogo va en la
    fuente."""
    fecha = _today()
    _record_row_changes(
        conn, audit_id, profile_before, _fetch_row(conn, "company_profiles", audit_id),
        CAMPOS_PERFIL_SRI + CAMPOS_PERFIL_SUPERCIAS, bloque_de_campo_perfil,
        lambda _campo: fuente, fecha, user_id,
    )
    _record_row_changes(
        conn, audit_id, location_before, _fetch_row(conn, "company_locations", audit_id),
        CAMPOS_UBICACION, lambda _campo: BLOQUE_UBICACION,
        lambda _campo: fuente, fecha, user_id,
    )


def _register_directory_administrators(
    conn: sqlite3.Connection,
    audit_id: int,
    administradores: list[dict[str, str]],
    fuente: str = FUENTE_DIRECTORIO_SUPERCIAS,
    user_id: int | None = None,
) -> None:
    """Registra en la nómina los administradores que publica el Directorio.

    No duplica a una persona que ya figure con el mismo cargo, aunque el
    auditor la haya escrito con otra capitalización o con tildes. No completa
    identificación ni nacionalidad: el Directorio no las publica.
    """
    if not administradores:
        return
    existing = {
        (normalizar_texto(row["nombre"]), normalizar_texto(row["cargo"]))
        for row in conn.execute(
            "SELECT nombre, cargo FROM company_administrators WHERE audit_id = ?", (audit_id,)
        )
    }
    for admin in administradores:
        nombre = (admin.get("nombre") or "").strip()
        cargo = (admin.get("cargo") or "").strip()
        key = (normalizar_texto(nombre), normalizar_texto(cargo))
        if not nombre or not cargo or key in existing:
            continue
        fecha = _today()
        cur = conn.execute(
            """
            INSERT INTO company_administrators
                (audit_id, identificacion, nombre, nacionalidad, cargo, fuente, fecha_consulta)
            VALUES (?, '', ?, '', ?, ?, ?)
            """,
            (audit_id, nombre, cargo, fuente, fecha),
        )
        _record_provenance(
            conn, audit_id, BLOQUE_ADMINISTRADORES, f"registro:{cur.lastrowid}",
            None, _person_summary(nombre, cargo), fuente, fecha, user_id,
        )
        existing.add(key)


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------

def create_session(user_id: int, db_path: Path | str = DB_PATH) -> str:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_hex(24)  # 48-char hex CSRF token vinculado a la sesión
    created_at = now_iso()
    expires_at = (datetime.now() + timedelta(hours=8)).replace(microsecond=0).isoformat(sep=" ")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, csrf_token) VALUES (?, ?, ?, ?, ?)",
            (session_hash(token), user_id, created_at, expires_at, csrf),
        )
    return token


def get_csrf_token(session_token: str | None, db_path: Path | str = DB_PATH) -> str:
    """Retorna el CSRF token asociado a la sesión activa. Cadena vacía si no hay sesión."""
    if not session_token:
        return ""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT csrf_token FROM sessions WHERE token_hash = ? AND expires_at >= ?",
            (session_hash(session_token), now_iso()),
        ).fetchone()
    return row["csrf_token"] if row else ""


def validate_csrf_token(
    session_token: str | None,
    submitted_csrf: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    """Valida el CSRF token enviado en un formulario contra el almacenado en la sesión.

    Usa hmac.compare_digest para evitar timing attacks.
    """
    if not session_token or not submitted_csrf:
        return False
    expected = get_csrf_token(session_token, db_path)
    if not expected:
        return False
    return hmac.compare_digest(expected, submitted_csrf)


def user_from_session(token: str | None, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    if not token:
        return None
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        return conn.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND u.active = 1 AND u.deleted_at IS NULL
              AND s.expires_at >= ?
            """,
            (session_hash(token), now_iso()),
        ).fetchone()


def destroy_session(token: str | None, db_path: Path | str = DB_PATH) -> None:
    if not token:
        return
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session_hash(token),))


# ---------------------------------------------------------------------------
# Empresas y auditorías
# ---------------------------------------------------------------------------

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


def list_admin_audits(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT a.*, c.name AS company_name, c.ruc, c.city, c.activity_hint,
                       u.full_name AS auditor_name, u.username AS auditor_username,
                       u.active AS auditor_active, u.deleted_at AS auditor_deleted_at
                FROM audits a
                JOIN companies c ON c.id = a.company_id
                JOIN users u ON u.id = a.assigned_auditor_id
                ORDER BY a.updated_at DESC, a.id DESC
                """
            )
        )


def reassign_audit(
    audit_id: int,
    new_auditor_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> None:
    ts = now_iso()
    with connect(db_path) as conn:
        actor = conn.execute(
            """
            SELECT id FROM users
            WHERE id = ? AND role = 'admin' AND active = 1 AND deleted_at IS NULL
            """,
            (performed_by,),
        ).fetchone()
        if not actor:
            raise ValueError("Solo un administrador activo puede reasignar empresas")

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
            "SELECT assigned_auditor_id FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()
        if not audit:
            raise ValueError("Auditoría no encontrada")
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
                WHERE a.assigned_auditor_id = ?
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
            sql += " AND a.assigned_auditor_id = ?"
            params.append(user["id"])
        return conn.execute(sql, params).fetchone()


# ---------------------------------------------------------------------------
# Contexto completo del expediente (una sola conexión)
# ---------------------------------------------------------------------------

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
            "SELECT * FROM economic_documents WHERE audit_id = ? ORDER BY id", (audit_id,)
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


# ---------------------------------------------------------------------------
# Notas de investigación
# ---------------------------------------------------------------------------

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


def update_research(
    audit_id: int,
    user_id: int,
    data: dict[str, str],
    mark_ready: bool,
    db_path: Path | str = DB_PATH,
) -> str:
    with connect(db_path) as conn:
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

        ctx = _load_radar_context(conn, audit_id)
        profile, location, snapshot = ctx["profile"], ctx["location"], ctx["snapshot"]
        admins, shareholders = ctx["admins"], ctx["shareholders"]
        source_checks, sources = ctx["source_checks"], ctx["sources"]
        source_count = len(sources)

        from services.financial import compute_indicators
        indicators = compute_indicators(dict(snapshot)) if snapshot else {}

        summary = generate_summary(
            audit, data, source_count,
            profile=dict(profile) if profile else None,
            location=dict(location) if location else None,
            admins=admins,
            shareholders=shareholders,
            snapshot=dict(snapshot) if snapshot else None,
            indicators=indicators,
            source_checks=source_checks,
            sources=sources,
            alert_treatments=ctx["alert_treatments"],
            provenance=ctx["provenance"],
        )
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
                data.get("commercial_name", "").strip(),
                data.get("economic_activity", "").strip(),
                data.get("legal_status", "").strip(),
                data.get("representative", "").strip(),
                data.get("address", "").strip(),
                data.get("tax_obligations", "").strip(),
                data.get("public_contracting", "").strip(),
                data.get("supercias_info", "").strip(),
                data.get("sri_info", "").strip(),
                data.get("sercop_info", "").strip(),
                data.get("observations", "").strip(),
                data.get("risk_flags", "").strip(),
                data.get("pasted_text", "").strip(),
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
    Garantiza que la fila exista antes de intentar el UPDATE.
    Columnas permitidas para evitar inyección SQL:
    """
    ALLOWED = {
        "commercial_name", "economic_activity", "legal_status", "representative",
        "address", "tax_obligations", "public_contracting", "supercias_info",
        "sri_info", "sercop_info", "observations", "risk_flags", "pasted_text",
    }
    safe = {k: v.strip() for k, v in fields.items() if k in ALLOWED}
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
        research = conn.execute(
            "SELECT * FROM research_notes WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if research is None:
            return "No existe resumen generado."
        data = {
            "commercial_name": research["commercial_name"] or "",
            "economic_activity": research["economic_activity"] or "",
            "legal_status": research["legal_status"] or "",
            "representative": research["representative"] or "",
            "address": research["address"] or "",
            "tax_obligations": research["tax_obligations"] or "",
            "public_contracting": research["public_contracting"] or "",
            "supercias_info": research["supercias_info"] or "",
            "sri_info": research["sri_info"] or "",
            "sercop_info": research["sercop_info"] or "",
            "observations": research["observations"] or "",
            "risk_flags": research["risk_flags"] or "",
            "pasted_text": research["pasted_text"] or "",
        }

        ctx = _load_radar_context(conn, audit_id)
        profile, location, snapshot = ctx["profile"], ctx["location"], ctx["snapshot"]
        admins, shareholders = ctx["admins"], ctx["shareholders"]
        source_checks, sources = ctx["source_checks"], ctx["sources"]
        source_count = len(sources)

        from services.financial import compute_indicators
        indicators = compute_indicators(dict(snapshot)) if snapshot else {}

        summary = generate_summary(
            audit, data, source_count,
            profile=dict(profile) if profile else None,
            location=dict(location) if location else None,
            admins=admins,
            shareholders=shareholders,
            snapshot=dict(snapshot) if snapshot else None,
            indicators=indicators,
            source_checks=source_checks,
            sources=sources,
            alert_treatments=ctx["alert_treatments"],
            provenance=ctx["provenance"],
        )
        conn.execute(
            "UPDATE research_notes SET generated_summary = ?, updated_at = ? WHERE audit_id = ?",
            (summary, now_iso(), audit_id),
        )
        return summary


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Radar Empresarial — Perfil de empresa
# ---------------------------------------------------------------------------

def get_company_profile(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM company_profiles WHERE audit_id = ?", (audit_id,)
        ).fetchone()


def upsert_company_profile(audit_id: int, data: dict, db_path: Path | str = DB_PATH) -> None:
    ts = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO company_profiles (
                audit_id, ruc, razon_social, estado_contribuyente, tipo_contribuyente,
                regimen, categoria, obligado_contabilidad, agente_retencion,
                contribuyente_especial, fecha_inicio_actividades, fecha_actualizacion,
                actividad_economica, representante_legal, expediente_supercias,
                nacionalidad, tipo_compania, situacion_legal, fecha_constitucion,
                plazo_social, oficina_control, objeto_social,
                telefono, representante_cargo, capital_suscrito,
                ciiu_nivel1, ciiu_nivel6, ultimo_anio_balance, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(audit_id) DO UPDATE SET
                ruc=excluded.ruc, razon_social=excluded.razon_social,
                estado_contribuyente=excluded.estado_contribuyente,
                tipo_contribuyente=excluded.tipo_contribuyente,
                regimen=excluded.regimen, categoria=excluded.categoria,
                obligado_contabilidad=excluded.obligado_contabilidad,
                agente_retencion=excluded.agente_retencion,
                contribuyente_especial=excluded.contribuyente_especial,
                fecha_inicio_actividades=excluded.fecha_inicio_actividades,
                fecha_actualizacion=excluded.fecha_actualizacion,
                actividad_economica=excluded.actividad_economica,
                representante_legal=excluded.representante_legal,
                expediente_supercias=excluded.expediente_supercias,
                nacionalidad=excluded.nacionalidad, tipo_compania=excluded.tipo_compania,
                situacion_legal=excluded.situacion_legal,
                fecha_constitucion=excluded.fecha_constitucion,
                plazo_social=excluded.plazo_social,
                oficina_control=excluded.oficina_control,
                objeto_social=excluded.objeto_social,
                telefono=excluded.telefono,
                representante_cargo=excluded.representante_cargo,
                capital_suscrito=excluded.capital_suscrito,
                ciiu_nivel1=excluded.ciiu_nivel1,
                ciiu_nivel6=excluded.ciiu_nivel6,
                ultimo_anio_balance=excluded.ultimo_anio_balance,
                updated_at=excluded.updated_at
            """,
            (
                audit_id,
                data.get("ruc", ""), data.get("razon_social", ""),
                data.get("estado_contribuyente", ""), data.get("tipo_contribuyente", ""),
                data.get("regimen", ""), data.get("categoria", ""),
                data.get("obligado_contabilidad", ""), data.get("agente_retencion", ""),
                data.get("contribuyente_especial", ""),
                data.get("fecha_inicio_actividades", ""), data.get("fecha_actualizacion", ""),
                data.get("actividad_economica", ""), data.get("representante_legal", ""),
                data.get("expediente_supercias", ""), data.get("nacionalidad", ""),
                data.get("tipo_compania", ""), data.get("situacion_legal", ""),
                data.get("fecha_constitucion", ""), data.get("plazo_social", ""),
                data.get("oficina_control", ""), data.get("objeto_social", ""),
                data.get("telefono", ""), data.get("representante_cargo", ""),
                data.get("capital_suscrito", ""), data.get("ciiu_nivel1", ""),
                data.get("ciiu_nivel6", ""), data.get("ultimo_anio_balance", ""),
                ts,
            ),
        )


# Campos que los formularios de las pestañas SRI y Supercias pueden editar.
# ruc no está: el RUC del expediente solo cambia por register_audit_ruc().
PROFILE_FORM_FIELDS = (
    "razon_social_sri", "estado_contribuyente", "tipo_contribuyente", "regimen",
    "obligado_contabilidad", "agente_retencion", "contribuyente_especial",
    "fecha_inicio_actividades", "actividad_economica", "representante_legal_sri",
    "contribuyente_fantasma", "transacciones_inexistentes", "ciiu_sri",
    "razon_social_supercias", "expediente_supercias", "nacionalidad", "tipo_compania",
    "situacion_legal", "fecha_constitucion", "plazo_social", "oficina_control",
    "objeto_social", "representante_legal", "representante_cargo", "telefono",
    "capital_suscrito", "ciiu_nivel1", "ciiu_nivel6", "ultimo_anio_balance",
)

LOCATION_FORM_FIELDS = (
    "provincia", "canton", "ciudad", "calle", "numero", "interseccion", "barrio", "referencia",
)

# Campos de lista cerrada: el servidor rechaza cualquier otro valor.
PROFILE_CLOSED_VALUES = {
    "contribuyente_fantasma": {"", "SI", "NO"},
    "transacciones_inexistentes": {"", "SI", "NO"},
}


# ---------------------------------------------------------------------------
# Trazabilidad por dato
# ---------------------------------------------------------------------------

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


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


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


def _fetch_row(conn: sqlite3.Connection, table: str, audit_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM {table} WHERE audit_id = ?", (audit_id,)  # noqa: S608
    ).fetchone()


def list_provenance(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    """Historial de trazabilidad del expediente en orden cronológico."""
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM data_provenance WHERE audit_id = ? ORDER BY id", (audit_id,)
        ))


def _today() -> str:
    return datetime.now().date().isoformat()


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


def update_company_profile_fields(
    audit_id: int,
    data: dict,
    db_path: Path | str = DB_PATH,
    *,
    user_id: int | None = None,
    fecha_consulta: str | None = None,
) -> None:
    """Guarda en el perfil solo los campos enviados por el formulario y
    registra cada cambio con la fuente de su bloque (SRI o Supercias)."""
    for campo, permitidos in PROFILE_CLOSED_VALUES.items():
        if campo in data and _text(data[campo]).upper() not in permitidos:
            raise ValueError(f"Valor no permitido para {campo}: use SI o NO")
        if campo in data:
            data = {**data, campo: _text(data[campo]).upper()}
    fecha = fecha_consulta or _today()
    with connect(db_path) as conn:
        before = _fetch_row(conn, "company_profiles", audit_id)
        _update_fields(conn, "company_profiles", audit_id, data, PROFILE_FORM_FIELDS)
        after = _fetch_row(conn, "company_profiles", audit_id)
        _record_row_changes(
            conn, audit_id, before, after, PROFILE_FORM_FIELDS,
            bloque_de_campo_perfil,
            lambda campo: FUENTE_MANUAL_POR_BLOQUE[bloque_de_campo_perfil(campo)],
            fecha, user_id,
        )


def update_company_location_fields(
    audit_id: int,
    data: dict,
    db_path: Path | str = DB_PATH,
    *,
    user_id: int | None = None,
    fecha_consulta: str | None = None,
) -> None:
    """Guarda en la ubicación solo los campos enviados por el formulario y
    registra cada cambio con la fuente del bloque Ubicación."""
    fecha = fecha_consulta or _today()
    with connect(db_path) as conn:
        before = _fetch_row(conn, "company_locations", audit_id)
        _update_fields(conn, "company_locations", audit_id, data, LOCATION_FORM_FIELDS)
        after = _fetch_row(conn, "company_locations", audit_id)
        _record_row_changes(
            conn, audit_id, before, after, LOCATION_FORM_FIELDS,
            lambda _campo: BLOQUE_UBICACION,
            lambda _campo: FUENTE_SUPERCIAS_UBICACION,
            fecha, user_id,
        )


# ---------------------------------------------------------------------------
# Radar Empresarial — Ubicación
# ---------------------------------------------------------------------------

def get_company_location(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)
        ).fetchone()


def upsert_company_location(audit_id: int, data: dict, db_path: Path | str = DB_PATH) -> None:
    ts = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO company_locations (
                audit_id, provincia, canton, ciudad, calle,
                numero, interseccion, barrio, referencia, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(audit_id) DO UPDATE SET
                provincia=excluded.provincia, canton=excluded.canton,
                ciudad=excluded.ciudad, calle=excluded.calle,
                numero=excluded.numero, interseccion=excluded.interseccion,
                barrio=excluded.barrio, referencia=excluded.referencia,
                updated_at=excluded.updated_at
            """,
            (
                audit_id,
                data.get("provincia", ""), data.get("canton", ""),
                data.get("ciudad", ""), data.get("calle", ""),
                data.get("numero", ""), data.get("interseccion", ""),
                data.get("barrio", ""), data.get("referencia", ""),
                ts,
            ),
        )


# ---------------------------------------------------------------------------
# Radar Empresarial — Administradores y Accionistas
# ---------------------------------------------------------------------------

def list_administrators(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM company_administrators WHERE audit_id = ? ORDER BY id",
            (audit_id,),
        ))


def list_shareholders(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM company_shareholders WHERE audit_id = ? ORDER BY numero, id",
            (audit_id,),
        ))


def _person_summary(*parts: Any) -> str:
    """Texto de un registro de persona para el historial: "NOMBRE | CARGO | ID"."""
    return " | ".join(_text(p) for p in parts if _text(p))


def _touch_audit(conn: sqlite3.Connection, audit_id: int) -> None:
    conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))


def _administrator_summary(row: sqlite3.Row) -> str:
    return _person_summary(
        row["nombre"], row["cargo"], row["tipo_identificacion"], row["identificacion"], row["nacionalidad"],
    )


def add_administrator(
    audit_id: int,
    identificacion: str,
    nombre: str,
    nacionalidad: str,
    cargo: str,
    db_path: Path | str = DB_PATH,
    *,
    tipo_identificacion: str = "",
    fecha_consulta: str | None = None,
    user_id: int | None = None,
) -> int:
    """Registra un administrador transcrito desde "Administradores actuales".

    La identificación es opcional al guardar (su falta se exige antes del
    resumen), pero si se ingresa debe cumplir el formato de su tipo.
    """
    nombre = nombre.strip()
    nacionalidad = nacionalidad.strip()
    cargo = cargo.strip()
    if not nombre or not cargo:
        raise ValueError("Nombre y cargo del administrador son obligatorios")
    if len(nombre) > 160 or len(cargo) > 120:
        raise ValueError("Nombre o cargo excede la longitud permitida")
    if len(identificacion.strip()) > 32 or len(nacionalidad) > 80:
        raise ValueError("Identificacion o nacionalidad excede la longitud permitida")
    tipo, identificacion, _warning = validar_identificacion(
        identificacion, tipo_identificacion, ("cedula", "pasaporte"),
    )
    fecha = fecha_consulta or _today()

    with connect(db_path) as conn:
        # Misma regla que _register_directory_administrators: sin distinguir
        # mayúsculas, tildes ni espacios, para no duplicar lo que trajo el Directorio.
        key = (normalizar_texto(nombre), normalizar_texto(cargo))
        duplicate = any(
            (normalizar_texto(row["nombre"]), normalizar_texto(row["cargo"])) == key
            for row in conn.execute(
                "SELECT nombre, cargo FROM company_administrators WHERE audit_id = ?", (audit_id,)
            )
        )
        if duplicate:
            raise ValueError("El administrador con ese cargo ya esta registrado")
        cur = conn.execute(
            """
            INSERT INTO company_administrators
                (audit_id, tipo_identificacion, identificacion, nombre, nacionalidad, cargo,
                 fuente, fecha_consulta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, tipo, identificacion, nombre, nacionalidad, cargo,
             FUENTE_SUPERCIAS_ADMINISTRADORES, fecha),
        )
        row = conn.execute("SELECT * FROM company_administrators WHERE id = ?", (cur.lastrowid,)).fetchone()
        _record_provenance(
            conn, audit_id, BLOQUE_ADMINISTRADORES, f"registro:{cur.lastrowid}",
            None, _administrator_summary(row), FUENTE_SUPERCIAS_ADMINISTRADORES, fecha, user_id,
        )
        _touch_audit(conn, audit_id)
        return int(cur.lastrowid)


def update_administrator(
    audit_id: int,
    administrator_id: int,
    *,
    tipo_identificacion: str,
    identificacion: str,
    nacionalidad: str,
    fecha_consulta: str | None = None,
    user_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """Completa la identificación y la nacionalidad de un administrador ya
    registrado (p. ej. el que trajo el Directorio sin cédula). Nombre y cargo
    no se editan: si están mal, el registro se elimina y se vuelve a crear."""
    nacionalidad = nacionalidad.strip()
    if len(nacionalidad) > 80 or len(identificacion.strip()) > 32:
        raise ValueError("Identificacion o nacionalidad excede la longitud permitida")
    tipo, identificacion, _warning = validar_identificacion(
        identificacion, tipo_identificacion, ("cedula", "pasaporte"),
    )
    fecha = fecha_consulta or _today()
    with connect(db_path) as conn:
        before = conn.execute(
            "SELECT * FROM company_administrators WHERE id = ? AND audit_id = ?",
            (administrator_id, audit_id),
        ).fetchone()
        if before is None:
            raise ValueError("Administrador no encontrado en este expediente")
        conn.execute(
            """
            UPDATE company_administrators
            SET tipo_identificacion = ?, identificacion = ?, nacionalidad = ?,
                fuente = ?, fecha_consulta = ?
            WHERE id = ? AND audit_id = ?
            """,
            (tipo, identificacion, nacionalidad, FUENTE_SUPERCIAS_ADMINISTRADORES, fecha,
             administrator_id, audit_id),
        )
        after = conn.execute(
            "SELECT * FROM company_administrators WHERE id = ?", (administrator_id,)
        ).fetchone()
        if _administrator_summary(before) != _administrator_summary(after):
            _record_provenance(
                conn, audit_id, BLOQUE_ADMINISTRADORES, f"registro:{administrator_id}",
                _administrator_summary(before), _administrator_summary(after),
                FUENTE_SUPERCIAS_ADMINISTRADORES, fecha, user_id,
            )
        _touch_audit(conn, audit_id)


def delete_administrator(
    audit_id: int,
    administrator_id: int,
    db_path: Path | str = DB_PATH,
    *,
    user_id: int | None = None,
) -> None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM company_administrators WHERE id = ? AND audit_id = ?",
            (administrator_id, audit_id),
        ).fetchone()
        if row is None:
            raise ValueError("Administrador no encontrado en este expediente")
        conn.execute(
            "DELETE FROM company_administrators WHERE id = ? AND audit_id = ?",
            (administrator_id, audit_id),
        )
        # La eliminación queda en el historial: el registro deja de existir,
        # pero no el rastro de que existió y de quién lo quitó.
        _record_provenance(
            conn, audit_id, BLOQUE_ADMINISTRADORES, f"registro:{administrator_id}",
            _administrator_summary(row), None,
            row["fuente"] or FUENTE_SUPERCIAS_ADMINISTRADORES, _today(), user_id,
        )
        _touch_audit(conn, audit_id)


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


def _shareholder_summary(row: sqlite3.Row) -> str:
    participacion = row["participacion_porcentaje"]
    capital = row["capital"]
    return _person_summary(
        row["nombre"], row["tipo_identificacion"], row["identificacion"],
        f"{participacion:g} %" if participacion is not None else "",
        f"capital {capital:,.2f}" if capital is not None else "",
        f"beneficiario final: {row['beneficiario_final']}" if _text(row["beneficiario_final"]) else "",
    )


def _validate_shareholder_details(
    identificacion: str,
    tipo_identificacion: str,
    participacion_porcentaje: Any,
    capital: Any,
    beneficiario_final: str,
) -> tuple[str, str, float | None, float | None, str]:
    beneficiario_final = _text(beneficiario_final)
    if len(identificacion.strip()) > 32 or len(beneficiario_final) > 160:
        raise ValueError("Identificacion o beneficiario final excede la longitud permitida")
    tipo, identificacion, _warning = validar_identificacion(
        identificacion, tipo_identificacion, ("cedula", "ruc", "pasaporte"),
    )
    participacion = _optional_number(participacion_porcentaje, "El porcentaje de participacion", 0, 100)
    capital_value = _optional_number(capital, "El capital de participacion", 0)
    return tipo, identificacion, participacion, capital_value, beneficiario_final


def _shareholder_duplicate(
    existing: list[sqlite3.Row], nombre: str, identificacion: str, exclude_id: int | None = None,
) -> bool:
    normalized_name = normalizar_texto(nombre)
    normalized_id = identificacion.casefold()
    for row in existing:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        same_id = bool(normalized_id and normalized_id not in {"-", "--", "—"}) and (
            (row["identificacion"] or "").strip().casefold() == normalized_id
        )
        if same_id or normalizar_texto(row["nombre"]) == normalized_name:
            return True
    return False


def add_shareholder(
    audit_id: int,
    numero: str | int | None,
    identificacion: str,
    nombre: str,
    db_path: Path | str = DB_PATH,
    *,
    tipo_identificacion: str = "",
    participacion_porcentaje: Any = "",
    capital: Any = "",
    beneficiario_final: str = "",
    fecha_consulta: str | None = None,
    user_id: int | None = None,
) -> int:
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del accionista es obligatorio")
    if len(nombre) > 160:
        raise ValueError("Nombre o identificacion excede la longitud permitida")
    tipo, identificacion, participacion, capital_value, beneficiario_final = _validate_shareholder_details(
        identificacion, tipo_identificacion, participacion_porcentaje, capital, beneficiario_final,
    )

    raw_number = str(numero or "").strip()
    if raw_number:
        try:
            position = int(raw_number)
        except ValueError as exc:
            raise ValueError("El numero del accionista debe ser un entero positivo") from exc
        if position < 1:
            raise ValueError("El numero del accionista debe ser un entero positivo")
    else:
        position = 0
    fecha = fecha_consulta or _today()

    with connect(db_path) as conn:
        existing = list(conn.execute(
            "SELECT id, numero, identificacion, nombre FROM company_shareholders WHERE audit_id = ?",
            (audit_id,),
        ))
        if _shareholder_duplicate(existing, nombre, identificacion):
            raise ValueError("El accionista ya esta registrado")
        if position and any(row["numero"] == position for row in existing):
            raise ValueError("El numero de accionista ya esta en uso")
        if not position:
            position = max((int(row["numero"] or 0) for row in existing), default=0) + 1

        cur = conn.execute(
            """
            INSERT INTO company_shareholders (
                audit_id, numero, tipo_identificacion, identificacion, nombre,
                participacion_porcentaje, capital, beneficiario_final, fuente, fecha_consulta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, position, tipo, identificacion, nombre, participacion, capital_value,
             beneficiario_final, FUENTE_SUPERCIAS_ACCIONISTAS, fecha),
        )
        row = conn.execute("SELECT * FROM company_shareholders WHERE id = ?", (cur.lastrowid,)).fetchone()
        _record_provenance(
            conn, audit_id, BLOQUE_ACCIONISTAS, f"registro:{cur.lastrowid}",
            None, _shareholder_summary(row), FUENTE_SUPERCIAS_ACCIONISTAS, fecha, user_id,
        )
        _touch_audit(conn, audit_id)
        return int(cur.lastrowid)


def update_shareholder(
    audit_id: int,
    shareholder_id: int,
    *,
    tipo_identificacion: str,
    identificacion: str,
    participacion_porcentaje: Any = "",
    capital: Any = "",
    beneficiario_final: str = "",
    fecha_consulta: str | None = None,
    user_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """Completa identificación, participación y beneficiario final de un
    accionista ya registrado. El nombre no se edita."""
    tipo, identificacion, participacion, capital_value, beneficiario_final = _validate_shareholder_details(
        identificacion, tipo_identificacion, participacion_porcentaje, capital, beneficiario_final,
    )
    fecha = fecha_consulta or _today()
    with connect(db_path) as conn:
        before = conn.execute(
            "SELECT * FROM company_shareholders WHERE id = ? AND audit_id = ?",
            (shareholder_id, audit_id),
        ).fetchone()
        if before is None:
            raise ValueError("Accionista no encontrado en este expediente")
        existing = list(conn.execute(
            "SELECT id, numero, identificacion, nombre FROM company_shareholders WHERE audit_id = ?",
            (audit_id,),
        ))
        if identificacion and any(
            row["id"] != shareholder_id
            and (row["identificacion"] or "").strip().casefold() == identificacion.casefold()
            for row in existing
        ):
            raise ValueError("Otro accionista ya tiene esa identificacion")
        conn.execute(
            """
            UPDATE company_shareholders
            SET tipo_identificacion = ?, identificacion = ?, participacion_porcentaje = ?,
                capital = ?, beneficiario_final = ?, fuente = ?, fecha_consulta = ?
            WHERE id = ? AND audit_id = ?
            """,
            (tipo, identificacion, participacion, capital_value, beneficiario_final,
             FUENTE_SUPERCIAS_ACCIONISTAS, fecha, shareholder_id, audit_id),
        )
        after = conn.execute("SELECT * FROM company_shareholders WHERE id = ?", (shareholder_id,)).fetchone()
        if _shareholder_summary(before) != _shareholder_summary(after):
            _record_provenance(
                conn, audit_id, BLOQUE_ACCIONISTAS, f"registro:{shareholder_id}",
                _shareholder_summary(before), _shareholder_summary(after),
                FUENTE_SUPERCIAS_ACCIONISTAS, fecha, user_id,
            )
        _touch_audit(conn, audit_id)


def delete_shareholder(
    audit_id: int,
    shareholder_id: int,
    db_path: Path | str = DB_PATH,
    *,
    user_id: int | None = None,
) -> None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM company_shareholders WHERE id = ? AND audit_id = ?",
            (shareholder_id, audit_id),
        ).fetchone()
        if row is None:
            raise ValueError("Accionista no encontrado en este expediente")
        conn.execute(
            "DELETE FROM company_shareholders WHERE id = ? AND audit_id = ?",
            (shareholder_id, audit_id),
        )
        _record_provenance(
            conn, audit_id, BLOQUE_ACCIONISTAS, f"registro:{shareholder_id}",
            _shareholder_summary(row), None,
            row["fuente"] or FUENTE_SUPERCIAS_ACCIONISTAS, _today(), user_id,
        )
        _touch_audit(conn, audit_id)


# ---------------------------------------------------------------------------
# Radar Empresarial — Documentos económicos
# ---------------------------------------------------------------------------

def list_economic_documents(audit_id: int, db_path: Path | str = DB_PATH, *, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM economic_documents WHERE audit_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (audit_id, limit, offset),
        ))


def mark_document_reviewed(doc_id: int, user_id: int, db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE economic_documents SET estado='revisado', revisado_por=?, revisado_at=? WHERE id=?",
            (user_id, now_iso(), doc_id),
        )


def mark_document_pending(doc_id: int, db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE economic_documents SET estado='pendiente', revisado_por=NULL, revisado_at=NULL WHERE id=?",
            (doc_id,),
        )


# ---------------------------------------------------------------------------
# Radar Empresarial — Snapshot financiero
# ---------------------------------------------------------------------------

def get_financial_snapshot(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)
        ).fetchone()


def upsert_financial_snapshot(audit_id: int, data: dict, db_path: Path | str = DB_PATH) -> None:
    ts = now_iso()

    def _f(key: str) -> float | None:
        v = data.get(key, "")
        try:
            return float(str(v).replace(",", ".").strip())
        except (ValueError, TypeError):
            return None

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO financial_snapshots (
                audit_id, activo_total, pasivo_total, patrimonio_neto,
                ingresos_401, otros_ingresos_403, costo_ventas_501, gastos_502,
                utilidad_neta_707, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(audit_id) DO UPDATE SET
                activo_total=excluded.activo_total,
                pasivo_total=excluded.pasivo_total,
                patrimonio_neto=excluded.patrimonio_neto,
                ingresos_401=excluded.ingresos_401,
                otros_ingresos_403=excluded.otros_ingresos_403,
                costo_ventas_501=excluded.costo_ventas_501,
                gastos_502=excluded.gastos_502,
                utilidad_neta_707=excluded.utilidad_neta_707,
                updated_at=excluded.updated_at
            """,
            (
                audit_id,
                _f("activo_total"), _f("pasivo_total"), _f("patrimonio_neto"),
                _f("ingresos_401"), _f("otros_ingresos_403"),
                _f("costo_ventas_501"), _f("gastos_502"),
                _f("utilidad_neta_707"),
                ts,
            ),
        )


# ---------------------------------------------------------------------------
# Levantamiento — Estados financieros por año fiscal
# ---------------------------------------------------------------------------

FUENTE_AUDITOR_PARAMETRO = "Auditor — parámetro del levantamiento"
FUENTE_AUDITOR_TRATAMIENTO = "Auditor — tratamiento de alerta crítica"


def fuente_documentos_economicos(anio: int) -> str:
    return f"Supercias — Documentos económicos (EEFF al {anio}-12-31)"


def _validar_anio_fiscal(anio: Any) -> int:
    text = _text(anio)
    if not re.fullmatch(r"\d{4}", text):
        raise ValueError("El año fiscal debe tener 4 dígitos, por ejemplo 2025")
    value = int(text)
    if value < 1990 or value > datetime.now().year:
        raise ValueError("El año fiscal debe estar entre 1990 y el año en curso")
    return value


def _validar_fecha_opcional(valor: Any, etiqueta: str) -> str:
    text = _text(valor)
    if not text:
        return ""
    try:
        fecha = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{etiqueta} debe tener el formato AAAA-MM-DD") from exc
    if fecha > datetime.now().date():
        raise ValueError(f"{etiqueta} no puede ser posterior a hoy")
    return fecha.isoformat()


def _audit_ruc(conn: sqlite3.Connection, audit_id: int) -> str:
    row = conn.execute(
        "SELECT c.ruc FROM audits a JOIN companies c ON c.id = a.company_id WHERE a.id = ?",
        (audit_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Auditoría no encontrada")
    ruc = _text(row["ruc"])
    if len(ruc) != 13:
        raise ValueError("Registre el RUC del expediente antes de cargar información financiera")
    return ruc


def set_audit_fiscal_year(
    audit_id: int,
    anio_fiscal: Any,
    *,
    user_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    """Registra el año fiscal de los estados financieros de la auditoría
    (parámetro previo obligatorio del bloque 6)."""
    anio = _validar_anio_fiscal(anio_fiscal)
    with connect(db_path) as conn:
        _audit_ruc(conn, audit_id)
        before = conn.execute("SELECT anio_fiscal_eeff FROM audits WHERE id = ?", (audit_id,)).fetchone()
        conn.execute(
            "UPDATE audits SET anio_fiscal_eeff = ?, updated_at = ? WHERE id = ?",
            (anio, now_iso(), audit_id),
        )
        if before["anio_fiscal_eeff"] != anio:
            _record_provenance(
                conn, audit_id, BLOQUE_FINANCIERO, "anio_fiscal",
                _text(before["anio_fiscal_eeff"]) or None, str(anio),
                FUENTE_AUDITOR_PARAMETRO, _today(), user_id,
            )
    return anio


def _statement_value(value: Any, campo: str, admite_negativos: bool) -> float | None:
    etiqueta = ETIQUETAS_FINANCIERAS[campo]
    return _optional_number(value, etiqueta, float("-inf") if admite_negativos else 0)


def upsert_financial_statement(
    audit_id: int,
    anio_fiscal: Any,
    data: dict,
    *,
    fecha_consulta: str | None = None,
    user_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    """Guarda los casilleros de un año fiscal del RUC del expediente.

    Solo modifica la fila de ese año: los demás ejercicios no se tocan. Un
    casillero vacío se guarda como pendiente (NULL). Cada cambio queda en la
    trazabilidad con la fuente "Documentos económicos" de ese año.
    """
    anio = _validar_anio_fiscal(anio_fiscal)
    values = {
        campo: _statement_value(data.get(campo), campo, negativos)
        for campo, _l, _c, negativos in CASILLEROS
    }
    fecha_junta = _validar_fecha_opcional(data.get("fecha_junta_aprobacion"), "La fecha de la junta")
    fecha = fecha_consulta or _today()
    fuente = fuente_documentos_economicos(anio)
    columns = (*CAMPOS_FINANCIEROS, "fecha_junta_aprobacion")
    with connect(db_path) as conn:
        ruc = _audit_ruc(conn, audit_id)
        before = conn.execute(
            "SELECT * FROM financial_statements WHERE ruc = ? AND anio_fiscal = ?", (ruc, anio),
        ).fetchone()
        conn.execute(
            f"""
            INSERT INTO financial_statements (
                ruc, anio_fiscal, fecha_corte, {", ".join(columns)},
                fuente, fecha_consulta, registrado_por, updated_at
            ) VALUES (?, ?, ?, {", ".join("?" for _ in columns)}, ?, ?, ?, ?)
            ON CONFLICT (ruc, anio_fiscal) DO UPDATE SET
                {", ".join(f"{c} = excluded.{c}" for c in columns)},
                fuente = excluded.fuente,
                fecha_consulta = excluded.fecha_consulta,
                registrado_por = excluded.registrado_por,
                updated_at = excluded.updated_at
            """,  # noqa: S608 — columnas constantes del módulo
            (ruc, anio, f"{anio}-12-31", *(values[c] for c in CAMPOS_FINANCIEROS), fecha_junta or None,
             fuente, fecha, user_id, now_iso()),
        )
        after = conn.execute(
            "SELECT * FROM financial_statements WHERE ruc = ? AND anio_fiscal = ?", (ruc, anio),
        ).fetchone()
        for campo in columns:
            anterior = _format_amount(before[campo]) if before is not None else ""
            nuevo = _format_amount(after[campo])
            # Guardar una cifra importada sin cambiarla confirma su revisión
            # contra el documento económico; el historial debe reflejarla.
            confirmed_catalog = before is not None and before["fuente"] != fuente and bool(nuevo)
            if anterior != nuevo or confirmed_catalog:
                _record_provenance(
                    conn, audit_id, BLOQUE_FINANCIERO, f"{anio}.{campo}",
                    anterior or None, nuevo or None, fuente, fecha, user_id,
                )
        _touch_audit(conn, audit_id)
        return int(after["id"])


def _format_amount(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return _text(value)


def list_financial_statements(ruc: str, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM financial_statements WHERE ruc = ? ORDER BY anio_fiscal DESC", (_text(ruc),)
        ))


def lookup_balances_catalog(
    ruc: str, catalog_path: Path | str = BALANCES_CATALOG_PATH,
) -> list[dict[str, Any]]:
    """Lee ejercicios disponibles del reporte local de Supercias por RUC."""
    path = Path(catalog_path)
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT b.*, i.archivo AS archivo_fuente, i.sha256 AS fuente_sha256,
                          i.importado_at AS fuente_importada_at, i.url AS fuente_url
                   FROM balances b JOIN importaciones i ON i.anio_fiscal = b.anio_fiscal
                   WHERE b.ruc = ? ORDER BY b.anio_fiscal DESC""",
                (_text(ruc),),
            ).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        import logging
        logging.exception("No se pudo consultar el catalogo local de balances Supercias")
        return []


def apply_balances_catalog_result(
    audit_id: int,
    user_id: int,
    rows: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> int:
    """Completa casilleros NULL sin pisar cifras revisadas por el auditor."""
    if not rows:
        return 0
    ts = now_iso()
    imported = 0
    with connect(db_path) as conn:
        ruc = _audit_ruc(conn, audit_id)
        for record in rows:
            year = _validar_anio_fiscal(record["anio_fiscal"])
            if record["ruc"] != ruc:
                raise ValueError("El RUC del balance no corresponde al expediente")
            source = f"Supercias - Estados financieros por ramo (catalogo local, {year})"
            consulted = str(record["fuente_importada_at"])[:10]
            values = {field: record[field] for field in BALANCES_FIELDS}
            before = conn.execute(
                "SELECT * FROM financial_statements WHERE ruc = ? AND anio_fiscal = ?",
                (ruc, year),
            ).fetchone()
            if before is None:
                fields = ", ".join(BALANCES_FIELDS)
                marks = ", ".join("?" for _ in BALANCES_FIELDS)
                conn.execute(
                    f"""INSERT INTO financial_statements (
                            ruc, anio_fiscal, fecha_corte, {fields}, fuente,
                            fecha_consulta, registrado_por, updated_at
                        ) VALUES (?, ?, ?, {marks}, ?, ?, ?, ?)""",  # noqa: S608
                    (ruc, year, f"{year}-12-31", *(values[f] for f in BALANCES_FIELDS),
                     source, consulted, user_id, ts),
                )
            else:
                missing = [field for field in BALANCES_FIELDS if before[field] is None]
                if missing:
                    updates = ", ".join(f"{field} = ?" for field in missing)
                    conn.execute(
                        f"""UPDATE financial_statements SET {updates},
                                fuente = ?, fecha_consulta = ?, registrado_por = ?, updated_at = ?
                            WHERE ruc = ? AND anio_fiscal = ?""",  # noqa: S608
                        (*(values[field] for field in missing),
                         "Origen mixto; ver trazabilidad por dato", consulted, user_id, ts, ruc, year),
                    )
            after = conn.execute(
                "SELECT * FROM financial_statements WHERE ruc = ? AND anio_fiscal = ?",
                (ruc, year),
            ).fetchone()
            traced = {
                row["campo"] for row in conn.execute(
                    "SELECT campo FROM data_provenance WHERE audit_id = ? AND bloque = ?",
                    (audit_id, BLOQUE_FINANCIERO),
                )
            }
            for field in BALANCES_FIELDS:
                before_value = before[field] if before is not None else None
                after_value = after[field]
                key = f"{year}.{field}"
                if after_value is None or (before_value == after_value and key in traced):
                    continue
                # Un valor anterior de otro expediente conserva su origen original.
                field_source = source if before_value is None else (before["fuente"] or source)
                field_date = consulted if before_value is None else (before["fecha_consulta"] or consulted)
                _record_provenance(
                    conn, audit_id, BLOQUE_FINANCIERO, key,
                    _format_amount(before_value) or None, _format_amount(after_value),
                    field_source, field_date, user_id,
                )
            title = f"Estados financieros por ramo Supercias ({year}, catalogo local)"
            notes = (
                f"Consulta por RUC {ruc}; ejercicio {year}; archivo {record['archivo_fuente']}; "
                f"SHA-256 {record['fuente_sha256']}; importado {record['fuente_importada_at']}. "
                "No sustituye el documento economico original ni su revision."
            )
            existing_source = conn.execute(
                "SELECT id FROM sources WHERE audit_id = ? AND title = ?", (audit_id, title),
            ).fetchone()
            if existing_source is None:
                conn.execute(
                    """INSERT INTO sources (audit_id, title, url, source_type, notes, created_by, created_at)
                       VALUES (?, ?, ?, 'Supercias', ?, ?, ?)""",
                    (audit_id, title, record.get("fuente_url") or BALANCES_SOURCE_URL, notes, user_id, ts),
                )
            imported += 1
        conn.execute(
            """UPDATE audits
               SET status = CASE WHEN status = 'pendiente' THEN 'en_investigacion' ELSE status END,
                   updated_at = ? WHERE id = ?""",
            (ts, audit_id),
        )
    return imported


def _financial_context(conn: sqlite3.Connection, audit_id: int) -> dict[str, Any]:
    """Financiero vigente de la auditoría.

    - snapshot: cifras del año fiscal de la auditoría (origen "anual"). Si
      aún no hay año o no hay cifras de ese año, las cifras registradas antes
      de la Fase 4 sin año fiscal (origen "sin_anio"), para no perderlas de
      vista; si tampoco existen, None.
    - legacy: esas cifras sin año, que el formulario ofrece para confirmar.
    - years: todos los ejercicios del RUC, del más reciente al más antiguo.
    """
    row = conn.execute(
        """
        SELECT a.anio_fiscal_eeff, c.ruc
        FROM audits a JOIN companies c ON c.id = a.company_id WHERE a.id = ?
        """,
        (audit_id,),
    ).fetchone()
    anio = row["anio_fiscal_eeff"] if row else None
    ruc = _text(row["ruc"]) if row else ""
    years = list(conn.execute(
        "SELECT * FROM financial_statements WHERE ruc = ? ORDER BY anio_fiscal DESC", (ruc,)
    )) if ruc else []
    legacy_row = conn.execute(
        "SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)
    ).fetchone()
    legacy = dict(legacy_row) if legacy_row else None

    current = next((dict(y) for y in years if anio is not None and y["anio_fiscal"] == anio), None)
    if current is not None:
        snapshot = {**current, "origen": "anual"}
    elif legacy is not None:
        snapshot = {**legacy, "anio_fiscal": None, "fecha_corte": None, "origen": "sin_anio"}
    else:
        snapshot = None
    return {"snapshot": snapshot, "legacy": legacy, "years": years, "anio_fiscal": anio}


def get_financial_context(audit_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    with connect(db_path) as conn:
        return _financial_context(conn, audit_id)


# ---------------------------------------------------------------------------
# Levantamiento — Tratamiento de alertas críticas
# ---------------------------------------------------------------------------

# Alertas cuyo tratamiento debe registrar el auditor antes del resumen.
ALERTAS_CRITICAS = {"ALERTA_FANTASMA"}


def register_alert_treatment(
    audit_id: int,
    codigo: str,
    observacion: str,
    *,
    user_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """Registra (o reemplaza) la observación del auditor sobre una alerta
    crítica. Cada versión queda en la trazabilidad."""
    codigo = _text(codigo)
    observacion = _text(observacion)
    if codigo not in ALERTAS_CRITICAS:
        raise ValueError("La alerta indicada no requiere tratamiento")
    if len(observacion) < 15:
        raise ValueError("Describa el tratamiento de la alerta (al menos 15 caracteres)")
    if len(observacion) > 2000:
        raise ValueError("El tratamiento excede la longitud permitida (2000 caracteres)")
    with connect(db_path) as conn:
        before = conn.execute(
            "SELECT observacion FROM alert_treatments WHERE audit_id = ? AND codigo = ?", (audit_id, codigo),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO alert_treatments (audit_id, codigo, observacion, registrado_por, registrado_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (audit_id, codigo) DO UPDATE SET
                observacion = excluded.observacion,
                registrado_por = excluded.registrado_por,
                registrado_at = excluded.registrado_at
            """,
            (audit_id, codigo, observacion, user_id, now_iso()),
        )
        if not before or before["observacion"] != observacion:
            _record_provenance(
                conn, audit_id, BLOQUE_VALIDACIONES, codigo,
                before["observacion"] if before else None, observacion,
                FUENTE_AUDITOR_TRATAMIENTO, _today(), user_id,
            )
        _touch_audit(conn, audit_id)


# ---------------------------------------------------------------------------
# Radar Empresarial — Source checks (fuentes guiadas)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Radar Empresarial — Carga completa de datos demo por RUC
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Progreso de investigación (8 etapas)
# ---------------------------------------------------------------------------

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
