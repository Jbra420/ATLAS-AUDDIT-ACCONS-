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
    ]:
        if col not in cp_existing:
            conn.execute(f"ALTER TABLE company_profiles ADD COLUMN {col} {col_type}")

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
                audit_id, ruc, razon_social, estado_contribuyente, tipo_contribuyente,
                categoria, obligado_contabilidad, agente_retencion,
                contribuyente_especial, fecha_inicio_actividades,
                fecha_actualizacion, actividad_economica, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                ruc = excluded.ruc,
                razon_social = CASE
                    WHEN TRIM(COALESCE(company_profiles.razon_social, '')) = '' THEN excluded.razon_social
                    ELSE company_profiles.razon_social
                END,
                estado_contribuyente = excluded.estado_contribuyente,
                tipo_contribuyente = excluded.tipo_contribuyente,
                categoria = excluded.categoria,
                obligado_contabilidad = excluded.obligado_contabilidad,
                agente_retencion = excluded.agente_retencion,
                contribuyente_especial = excluded.contribuyente_especial,
                fecha_inicio_actividades = excluded.fecha_inicio_actividades,
                fecha_actualizacion = excluded.fecha_actualizacion,
                actividad_economica = excluded.actividad_economica,
                updated_at = excluded.updated_at
            """,
            (
                audit_id, profile["ruc"], profile["razon_social"],
                profile["estado_contribuyente"], profile["tipo_contribuyente"],
                profile["categoria"], profile["obligado_contabilidad"],
                profile["agente_retencion"], profile["contribuyente_especial"],
                profile["fecha_inicio_actividades"], profile["fecha_actualizacion"],
                profile["actividad_economica"], ts,
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

        profile_cols = [
            "expediente_supercias", "razon_social", "situacion_legal",
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
        # Si el RUC coincide con el demo, cargar datos automáticamente
        if ruc.strip() == DEMO_RUC:
            seed_demo_radar(conn, audit_id)
        else:
            # Crear documentos económicos en blanco
            for nombre in DEFAULT_ECONOMIC_DOCUMENTS:
                conn.execute(
                    "INSERT INTO economic_documents (audit_id, nombre, fecha, estado) VALUES (?, ?, ?, 'pendiente')",
                    (audit_id, nombre, period),
                )
            # Crear source checks en blanco
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
    """
    return {
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
        "snapshot": conn.execute(
            "SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)
        ).fetchone(),
        "source_checks": list(conn.execute(
            "SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id", (audit_id,)
        )),
        "sources": list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC", (audit_id,)
        )),
    }


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


def add_administrator(
    audit_id: int,
    identificacion: str,
    nombre: str,
    nacionalidad: str,
    cargo: str,
    db_path: Path | str = DB_PATH,
) -> int:
    identificacion = identificacion.strip()
    nombre = nombre.strip()
    nacionalidad = nacionalidad.strip()
    cargo = cargo.strip()
    if not nombre or not cargo:
        raise ValueError("Nombre y cargo del administrador son obligatorios")
    if len(nombre) > 160 or len(cargo) > 120:
        raise ValueError("Nombre o cargo excede la longitud permitida")
    if len(identificacion) > 32 or len(nacionalidad) > 80:
        raise ValueError("Identificacion o nacionalidad excede la longitud permitida")

    with connect(db_path) as conn:
        duplicate = conn.execute(
            """
            SELECT id FROM company_administrators
            WHERE audit_id = ? AND nombre = ? COLLATE NOCASE AND cargo = ? COLLATE NOCASE
            """,
            (audit_id, nombre, cargo),
        ).fetchone()
        if duplicate:
            raise ValueError("El administrador con ese cargo ya esta registrado")
        cur = conn.execute(
            """
            INSERT INTO company_administrators
                (audit_id, identificacion, nombre, nacionalidad, cargo)
            VALUES (?, ?, ?, ?, ?)
            """,
            (audit_id, identificacion, nombre, nacionalidad, cargo),
        )
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))
        return int(cur.lastrowid)


def delete_administrator(
    audit_id: int,
    administrator_id: int,
    db_path: Path | str = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM company_administrators WHERE id = ? AND audit_id = ?",
            (administrator_id, audit_id),
        )
        if cur.rowcount != 1:
            raise ValueError("Administrador no encontrado en este expediente")
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))


def add_shareholder(
    audit_id: int,
    numero: str | int | None,
    identificacion: str,
    nombre: str,
    db_path: Path | str = DB_PATH,
) -> int:
    identificacion = identificacion.strip()
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del accionista es obligatorio")
    if len(nombre) > 160 or len(identificacion) > 32:
        raise ValueError("Nombre o identificacion excede la longitud permitida")

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

    with connect(db_path) as conn:
        existing = list(conn.execute(
            "SELECT id, numero, identificacion, nombre FROM company_shareholders WHERE audit_id = ?",
            (audit_id,),
        ))
        normalized_name = nombre.casefold()
        normalized_id = identificacion.casefold()
        for row in existing:
            same_id = bool(normalized_id and normalized_id not in {"-", "--", "—"}) and (
                (row["identificacion"] or "").strip().casefold() == normalized_id
            )
            same_name = (row["nombre"] or "").strip().casefold() == normalized_name
            if same_id or same_name:
                raise ValueError("El accionista ya esta registrado")
            if position and row["numero"] == position:
                raise ValueError("El numero de accionista ya esta en uso")
        if not position:
            position = max((int(row["numero"] or 0) for row in existing), default=0) + 1

        cur = conn.execute(
            """
            INSERT INTO company_shareholders (audit_id, numero, identificacion, nombre)
            VALUES (?, ?, ?, ?)
            """,
            (audit_id, position, identificacion, nombre),
        )
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))
        return int(cur.lastrowid)


def delete_shareholder(
    audit_id: int,
    shareholder_id: int,
    db_path: Path | str = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM company_shareholders WHERE id = ? AND audit_id = ?",
            (shareholder_id, audit_id),
        )
        if cur.rowcount != 1:
            raise ValueError("Accionista no encontrado en este expediente")
        conn.execute("UPDATE audits SET updated_at = ? WHERE id = ?", (now_iso(), audit_id))


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
        snapshot = conn.execute(
            "SELECT * FROM financial_snapshots WHERE audit_id=?", (audit_id,)
        ).fetchone()

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
