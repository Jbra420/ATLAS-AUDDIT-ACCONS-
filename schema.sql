-- schema.sql — Esquema de la base de datos de Atlas (Auddit).
-- Cargado por database.init_db() vía executescript(). Todas las tablas usan
-- CREATE TABLE IF NOT EXISTS, así que aplicarlo sobre una base existente es seguro.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'auditor')),
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    deleted_by INTEGER REFERENCES users(id),
    deletion_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    csrf_token TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS audit_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    auditor_id INTEGER NOT NULL REFERENCES users(id),
    assigned_by INTEGER NOT NULL REFERENCES users(id),
    assigned_at TEXT NOT NULL,
    unassigned_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_assignments_current
ON audit_assignments(audit_id) WHERE unassigned_at IS NULL;

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
