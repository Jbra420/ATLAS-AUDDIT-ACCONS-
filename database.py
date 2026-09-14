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

from services.ruc_validator import format_ruc, validate_ruc
from services.summary import generate_summary, extract_signals


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "auddit.db"

AUDIT_STATUSES = {
    "pendiente": "Pendiente",
    "en_investigacion": "En investigación",
    "listo_revision": "Resumen generado",
    "devuelto": "Devuelto",
    "revisado": "Revisado",
}

REVIEW_STATUSES = {
    "devuelto": "Devuelto con observaciones",
    "revisado": "Revisado por jefe",
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

def init_db(db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'auditor')),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ruc TEXT,
                city TEXT,
                activity_hint TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                period TEXT NOT NULL,
                assigned_auditor_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'pendiente',
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_notes (
                audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE,
                commercial_name TEXT,
                economic_activity TEXT,
                legal_status TEXT,
                representative TEXT,
                address TEXT,
                tax_obligations TEXT,
                public_contracting TEXT,
                supercias_info TEXT,
                sri_info TEXT,
                sercop_info TEXT,
                observations TEXT,
                risk_flags TEXT,
                pasted_text TEXT,
                generated_summary TEXT,
                updated_by INTEGER REFERENCES users(id),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                url TEXT,
                source_type TEXT,
                notes TEXT,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                comments TEXT,
                reviewed_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            -- ── Radar Empresarial v3.0 ────────────────────────────────────

            CREATE TABLE IF NOT EXISTS company_profiles (
                audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE,
                ruc TEXT,
                razon_social TEXT,
                estado_contribuyente TEXT,
                tipo_contribuyente TEXT,
                regimen TEXT,
                categoria TEXT,
                obligado_contabilidad TEXT,
                agente_retencion TEXT,
                contribuyente_especial TEXT,
                fecha_inicio_actividades TEXT,
                fecha_actualizacion TEXT,
                actividad_economica TEXT,
                representante_legal TEXT,
                expediente_supercias TEXT,
                nacionalidad TEXT,
                tipo_compania TEXT,
                situacion_legal TEXT,
                fecha_constitucion TEXT,
                plazo_social TEXT,
                oficina_control TEXT,
                objeto_social TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_locations (
                audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE,
                provincia TEXT,
                canton TEXT,
                ciudad TEXT,
                calle TEXT,
                numero TEXT,
                interseccion TEXT,
                barrio TEXT,
                referencia TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_administrators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                identificacion TEXT,
                nombre TEXT,
                nacionalidad TEXT,
                cargo TEXT
            );

            CREATE TABLE IF NOT EXISTS company_shareholders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                numero INTEGER,
                identificacion TEXT,
                nombre TEXT
            );

            CREATE TABLE IF NOT EXISTS economic_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                nombre TEXT NOT NULL,
                fecha TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                revisado_por INTEGER REFERENCES users(id),
                revisado_at TEXT
            );

            CREATE TABLE IF NOT EXISTS financial_snapshots (
                audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE,
                activo_total REAL,
                pasivo_total REAL,
                patrimonio_neto REAL,
                ingresos_401 REAL,
                otros_ingresos_403 REAL,
                costo_ventas_501 REAL,
                gastos_502 REAL,
                utilidad_neta_707 REAL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                fuente TEXT NOT NULL,
                uso TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                observacion TEXT,
                consultada_por INTEGER REFERENCES users(id),
                consultada_at TEXT
            );
            """
        )
        _migrate(conn)
        seed_defaults(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a tablas existentes (migración idempotente)."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(research_notes)")
    }
    new_cols = [
        ("commercial_name", "TEXT"),
        ("supercias_info", "TEXT"),
        ("sri_info", "TEXT"),
        ("sercop_info", "TEXT"),
    ]
    for col, col_type in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE research_notes ADD COLUMN {col} {col_type}")


# ---------------------------------------------------------------------------
# Seed de datos demo
# ---------------------------------------------------------------------------

DEMO_RUC = "0190377210001"
DEMO_COMPANY = "GRUCANQUI CIA. LTDA"


def seed_defaults(conn: sqlite3.Connection) -> None:
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count:
        return

    admin_id = create_user(conn, "admin", "Jefe Auditor", "admin", "admin123")
    auditor_id = create_user(conn, "auditor", "Auditor Demo", "auditor", "auditor123")

    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO companies (name, ruc, city, activity_hint, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (DEMO_COMPANY, DEMO_RUC, "Cuenca", "Servicios de alojamiento prestados por hoteles", ts, ts),
    )
    company_id = cur.lastrowid
    cur = conn.execute(
        """
        INSERT INTO audits (company_id, period, assigned_auditor_id, status, created_by, created_at, updated_at)
        VALUES (?, ?, ?, 'pendiente', ?, ?, ?)
        """,
        (company_id, "2024", auditor_id, admin_id, ts, ts),
    )
    audit_id = int(cur.lastrowid)

    # Seed demo completo de GRUCANQUI
    seed_demo_radar(conn, audit_id)


def seed_demo_radar(conn: sqlite3.Connection, audit_id: int) -> None:
    """Carga todos los datos demo de GRUCANQUI CIA. LTDA en las tablas del Radar."""
    ts = now_iso()

    # Perfil SRI + Supercias
    conn.execute(
        """
        INSERT OR REPLACE INTO company_profiles (
            audit_id, ruc, razon_social, estado_contribuyente, tipo_contribuyente,
            regimen, categoria, obligado_contabilidad, agente_retencion,
            contribuyente_especial, fecha_inicio_actividades, fecha_actualizacion,
            actividad_economica, representante_legal, expediente_supercias,
            nacionalidad, tipo_compania, situacion_legal, fecha_constitucion,
            plazo_social, oficina_control, objeto_social, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            DEMO_RUC,
            DEMO_COMPANY,
            "ACTIVO",
            "SOCIEDAD",
            "GENERAL",
            "—",
            "SI",
            "SI",
            "NO",
            "2011-08-24",
            "2025-09-08",
            "Servicios de alojamiento prestados por hoteles",
            "Cando Suárez María Daniela",
            "141528",
            "Ecuador",
            "Responsabilidad limitada",
            "Activa",
            "2011-08-24",
            "Pendiente de confirmar",
            "Cuenca",
            "Servicios de alojamiento prestados por hoteles (pendiente de ampliar)",
            ts,
        ),
    )

    # Ubicación
    conn.execute(
        """
        INSERT OR REPLACE INTO company_locations (
            audit_id, provincia, canton, ciudad, calle,
            numero, interseccion, barrio, referencia, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id, "Azuay", "Cuenca", "Cuenca",
            "Av. del Estadio", "S/N", "Florencia Astudillo",
            "Estadio", "Junto a Óptica Sánchez", ts,
        ),
    )

    # Administradores
    conn.execute("DELETE FROM company_administrators WHERE audit_id = ?", (audit_id,))
    admins = [
        ("—", "Cando Suárez María Daniela", "Ecuatoriana", "Gerente General"),
        ("—", "Quito Arias Juan Carlos", "Ecuatoriano", "Presidente"),
    ]
    for i, (ident, nombre, nac, cargo) in enumerate(admins):
        conn.execute(
            "INSERT INTO company_administrators (audit_id, identificacion, nombre, nacionalidad, cargo) VALUES (?, ?, ?, ?, ?)",
            (audit_id, ident, nombre, nac, cargo),
        )

    # Accionistas
    conn.execute("DELETE FROM company_shareholders WHERE audit_id = ?", (audit_id,))
    shareholders = [
        (1, "—", "Cando Suárez María Daniela"),
        (2, "—", "Quito Arias Juan Carlos"),
    ]
    for numero, ident, nombre in shareholders:
        conn.execute(
            "INSERT INTO company_shareholders (audit_id, numero, identificacion, nombre) VALUES (?, ?, ?, ?)",
            (audit_id, numero, ident, nombre),
        )

    # Documentos económicos
    existing_docs = conn.execute(
        "SELECT COUNT(*) FROM economic_documents WHERE audit_id = ?", (audit_id,)
    ).fetchone()[0]
    if not existing_docs:
        for nombre in DEFAULT_ECONOMIC_DOCUMENTS:
            conn.execute(
                "INSERT INTO economic_documents (audit_id, nombre, fecha, estado) VALUES (?, ?, ?, 'pendiente')",
                (audit_id, nombre, "2024"),
            )

    # Snapshot financiero
    conn.execute(
        """
        INSERT OR REPLACE INTO financial_snapshots (
            audit_id, activo_total, pasivo_total, patrimonio_neto,
            ingresos_401, otros_ingresos_403, costo_ventas_501, gastos_502,
            utilidad_neta_707, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            3108776.58, 2107881.79, 1000894.79,
            1862784.91, 11761.20,
            895805.67, 953625.41,
            7279.63,
            ts,
        ),
    )

    # Source checks (fuentes guiadas)
    existing_checks = conn.execute(
        "SELECT COUNT(*) FROM source_checks WHERE audit_id = ?", (audit_id,)
    ).fetchone()[0]
    if not existing_checks:
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
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username.strip().lower(),),
        ).fetchone()
        if user and verify_password(password, user["password_salt"], user["password_hash"]):
            return user
        return None


def list_users(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT id, username, full_name, role, active, created_at FROM users ORDER BY role, full_name"
        ))


def list_auditors(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT id, username, full_name FROM users WHERE role = 'auditor' AND active = 1 ORDER BY full_name"
        ))


def delete_user(user_id: int, db_path: Path | str = DB_PATH) -> str:
    """Elimina un usuario por su ID. Si tiene relaciones asociadas, lo desactiva en su lugar."""
    try:
        with connect(db_path) as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return "Usuario eliminado exitosamente"
    except sqlite3.IntegrityError:
        with connect(db_path) as conn:
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        return "El usuario tiene expedientes o revisiones asociadas, por lo que fue desactivado permanentemente en lugar de borrado."


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------

def create_session(user_id: int, db_path: Path | str = DB_PATH) -> str:
    token = secrets.token_urlsafe(32)
    created_at = now_iso()
    expires_at = (datetime.now() + timedelta(hours=8)).replace(microsecond=0).isoformat(sep=" ")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (session_hash(token), user_id, created_at, expires_at),
        )
    return token


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
            WHERE s.token_hash = ? AND u.active = 1 AND s.expires_at >= ?
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
        valid, msg = validate_ruc(ruc)
        if not valid:
            raise ValueError(msg)

    with connect(db_path) as conn:
        auditor = conn.execute(
            "SELECT id, role, active FROM users WHERE id = ?",
            (int(assigned_auditor_id),),
        ).fetchone()
        if auditor is None or auditor["role"] != "auditor" or auditor["active"] != 1:
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
    valid, msg = validate_ruc(clean_ruc)
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
                       u.full_name AS auditor_name, u.username AS auditor_username
                FROM audits a
                JOIN companies c ON c.id = a.company_id
                JOIN users u ON u.id = a.assigned_auditor_id
                ORDER BY a.updated_at DESC, a.id DESC
                """
            )
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

        source_count = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE audit_id = ?", (audit_id,)
        ).fetchone()[0]

        profile = conn.execute("SELECT * FROM company_profiles WHERE audit_id = ?", (audit_id,)).fetchone()
        location = conn.execute("SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)).fetchone()
        snapshot = conn.execute("SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)).fetchone()
        admins = list(conn.execute("SELECT * FROM company_administrators WHERE audit_id = ? ORDER BY id", (audit_id,)))
        shareholders = list(conn.execute("SELECT * FROM company_shareholders WHERE audit_id = ? ORDER BY numero, id", (audit_id,)))
        source_checks = list(conn.execute("SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id", (audit_id,)))
        sources = list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC",
            (audit_id,),
        ))
        
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

        if audit["status"] in {"pendiente", "devuelto", "listo_revision"}:
            status = "en_investigacion"
        else:
            status = audit["status"]
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, audit_id),
        )
        return summary


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
        source_count = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE audit_id = ?", (audit_id,)
        ).fetchone()[0]
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
        
        profile = conn.execute("SELECT * FROM company_profiles WHERE audit_id = ?", (audit_id,)).fetchone()
        location = conn.execute("SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)).fetchone()
        snapshot = conn.execute("SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)).fetchone()
        admins = list(conn.execute("SELECT * FROM company_administrators WHERE audit_id = ? ORDER BY id", (audit_id,)))
        shareholders = list(conn.execute("SELECT * FROM company_shareholders WHERE audit_id = ? ORDER BY numero, id", (audit_id,)))
        source_checks = list(conn.execute("SELECT * FROM source_checks WHERE audit_id = ? ORDER BY id", (audit_id,)))
        sources = list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC",
            (audit_id,),
        ))
        
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

def list_sources(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM sources WHERE audit_id = ? ORDER BY created_at DESC, id DESC",
            (audit_id,),
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
# Revisiones del jefe
# ---------------------------------------------------------------------------

def review_audit(
    audit_id: int,
    status: str,
    comments: str,
    reviewed_by: int,
    db_path: Path | str = DB_PATH,
) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError("Estado de revisión no permitido")
    with connect(db_path) as conn:
        ts = now_iso()
        conn.execute(
            "INSERT INTO reviews (audit_id, status, comments, reviewed_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (audit_id, status, comments.strip(), reviewed_by, ts),
        )
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, audit_id),
        )


def list_reviews(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT r.*, u.full_name AS reviewer_name
                FROM reviews r
                JOIN users u ON u.id = r.reviewed_by
                WHERE r.audit_id = ?
                ORDER BY r.created_at DESC, r.id DESC
                """,
                (audit_id,),
            )
        )


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
                plazo_social, oficina_control, objeto_social, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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


# ---------------------------------------------------------------------------
# Radar Empresarial — Documentos económicos
# ---------------------------------------------------------------------------

def list_economic_documents(audit_id: int, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM economic_documents WHERE audit_id = ? ORDER BY id",
            (audit_id,),
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
    """Calcula el progreso de la investigación Radar Empresarial."""
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
