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
    -- 1 mientras la cuenta use una clave inicial o temporal que debe cambiar.
    must_change_password INTEGER NOT NULL DEFAULT 0,
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
    updated_at TEXT NOT NULL,
    -- Archivar oculta la empresa de los listados y del auditor sin borrar el
    -- expediente ni su evidencia; el administrador puede restaurarla.
    archived_at TEXT,
    archived_by INTEGER REFERENCES users(id),
    archive_reason TEXT
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

-- ── Levantamiento de información: trazabilidad por dato ────────────────
-- Historial de solo inserción (los triggers de _migrate() impiden editar o
-- borrar filas). Cada fila registra un cambio de un dato del expediente con
-- su fuente, la fecha en que se consultó esa fuente y quién lo registró.
CREATE TABLE IF NOT EXISTS data_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    bloque TEXT NOT NULL,
    campo TEXT NOT NULL,
    valor_anterior TEXT,
    valor_nuevo TEXT,
    fuente TEXT NOT NULL,
    fecha_consulta TEXT NOT NULL,
    registrado_por INTEGER REFERENCES users(id),
    registrado_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_provenance_audit
ON data_provenance(audit_id, bloque, campo, id);

-- ── Levantamiento de información: estados financieros por año fiscal ──
-- Una fila por cliente (RUC) y año fiscal. Un año nuevo nunca sobrescribe
-- otro, y las auditorías del mismo RUC comparten sus ejercicios. Los totales
-- de ingresos (401 + 403) y gastos (501 + 502) se calculan, no se guardan.
CREATE TABLE IF NOT EXISTS financial_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ruc TEXT NOT NULL CHECK (length(ruc) = 13),
    anio_fiscal INTEGER NOT NULL CHECK (anio_fiscal BETWEEN 1990 AND 2100),
    fecha_corte TEXT NOT NULL,
    activo_total REAL,
    pasivo_total REAL,
    patrimonio_neto REAL,
    ingresos_401 REAL,
    otros_ingresos_403 REAL,
    costo_ventas_501 REAL,
    gastos_502 REAL,
    utilidad_antes_part_imp REAL,
    utilidad_neta_707 REAL,
    fecha_junta_aprobacion TEXT,
    fuente TEXT,
    fecha_consulta TEXT,
    registrado_por INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    UNIQUE (ruc, anio_fiscal)
);

-- ── Certificado de nómina adjunto (administradores y accionistas) ────
-- Un PDF trae las dos nóminas, o vienen en varios PDF que se adjuntan
-- juntos (archivo, sha256 y ruta guardan uno por línea). Cada PDF queda
-- como evidencia con su SHA-256, y
-- administradores_json / accionistas_json son la propuesta del analizador:
-- nada entra a la nómina hasta que el auditor la confirma.
CREATE TABLE IF NOT EXISTS nomina_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    archivo TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    ruta TEXT NOT NULL,
    administradores_json TEXT NOT NULL DEFAULT '[]',
    accionistas_json TEXT NOT NULL DEFAULT '[]',
    advertencias_json TEXT NOT NULL DEFAULT '[]',
    fecha_certificado TEXT,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'importado', 'descartado')),
    subido_por INTEGER REFERENCES users(id),
    subido_at TEXT NOT NULL
);

-- ── Levantamiento de información: tratamiento de alertas críticas ────
-- Una alerta crítica (p. ej. contribuyente fantasma) no bloquea el trabajo,
-- pero el resumen exige que el auditor registre cómo la trató.
CREATE TABLE IF NOT EXISTS alert_treatments (
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    codigo TEXT NOT NULL,
    observacion TEXT NOT NULL,
    registrado_por INTEGER REFERENCES users(id),
    registrado_at TEXT NOT NULL,
    PRIMARY KEY (audit_id, codigo)
);
