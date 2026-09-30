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

-- ── Requerimiento inicial (segundo paso del proceso) ─────────────────
-- Datos propios del requerimiento, separados del levantamiento pero de la
-- misma auditoría. Los años se guardan por separado porque no son iguales
-- entre sí: año auditado, año al que se refieren los certificados y último
-- ejercicio cerrado. Todo lo confirma el auditor; nada se deduce.
CREATE TABLE IF NOT EXISTS requerimientos (
    audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE,
    empresa TEXT,
    ruc TEXT,
    representante_titulo TEXT,
    representante_nombre TEXT,
    representante_cargo TEXT,
    representante_identificacion TEXT,
    representante_nacionalidad TEXT,
    representante_ciudad TEXT,
    anio_auditado INTEGER,
    anio_certificados INTEGER,
    anio_cerrado INTEGER,
    fecha_documentos TEXT,
    fecha_corte TEXT,
    fechas_inventario TEXT,
    inventario_desde TEXT,
    inventario_hasta TEXT,
    cronograma_json TEXT NOT NULL DEFAULT '[]',
    equipo_json TEXT NOT NULL DEFAULT '[]',
    auddit_representante TEXT,
    auddit_cargo TEXT,
    correo_para_nombre TEXT,
    correo_para TEXT,
    correo_cc TEXT,
    confirmado_por INTEGER REFERENCES users(id),
    confirmado_at TEXT,
    updated_at TEXT NOT NULL
);

-- Marcas que el auditor prepara en las hojas 1 y 2 del Excel antes de enviarlo.
CREATE TABLE IF NOT EXISTS requerimiento_items (
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    hoja INTEGER NOT NULL CHECK (hoja IN (1, 2)),
    numero INTEGER NOT NULL,
    cumplido INTEGER NOT NULL DEFAULT 0,
    no_aplica INTEGER NOT NULL DEFAULT 0,
    observacion TEXT NOT NULL DEFAULT '',
    CHECK (NOT (cumplido = 1 AND no_aplica = 1)),
    PRIMARY KEY (audit_id, hoja, numero)
);

-- Filas precargadas de los cuadros de detalle (hojas 3 y 4), una por fila.
CREATE TABLE IF NOT EXISTS requerimiento_detalles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    seccion TEXT NOT NULL,
    orden INTEGER NOT NULL,
    valores_json TEXT NOT NULL
);

-- Generación de los cuatro documentos: se registra junto con sus cuatro
-- archivos en una sola transacción (nunca queda a medias). La referencia va
-- dentro del Excel para reconocer la respuesta del cliente; datos_json es la
-- instantánea con la que se armaron los documentos y el borrador del correo.
CREATE TABLE IF NOT EXISTS requerimiento_paquetes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    numero INTEGER NOT NULL,
    referencia TEXT NOT NULL UNIQUE,
    datos_json TEXT NOT NULL,
    generado_por INTEGER REFERENCES users(id),
    generado_at TEXT NOT NULL,
    UNIQUE (audit_id, numero)
);

-- Documentos generados: cada generación es una versión nueva e inmutable
-- (archivo propio, SHA-256, autor, fecha y datos usados).
CREATE TABLE IF NOT EXISTS requerimiento_archivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    paquete_id INTEGER REFERENCES requerimiento_paquetes(id),
    tipo TEXT NOT NULL,
    version INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    ruta TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    datos_json TEXT NOT NULL,
    generado_por INTEGER REFERENCES users(id),
    generado_at TEXT NOT NULL,
    UNIQUE (audit_id, tipo, version)
);

-- Archivos que sube el auditor: contrato firmado, evidencias de envío y
-- documentos que devuelve el cliente. El original se conserva siempre.
-- Recibir no es revisar: un PDF firmado queda pendiente hasta que el auditor
-- deja constancia de su revisión (revisado_*); Atlas no valida firmas
-- electrónicas. El Excel respondido guarda el resultado de su validación
-- (importado, rechazado o revision_manual) y la generación que reconoció.
CREATE TABLE IF NOT EXISTS requerimiento_adjuntos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    ruta TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    fecha TEXT,
    nota TEXT NOT NULL DEFAULT '',
    subido_por INTEGER REFERENCES users(id),
    subido_at TEXT NOT NULL,
    detalle_archivo TEXT NOT NULL DEFAULT '',
    revision_resultado TEXT CHECK (revision_resultado IN ('conforme', 'observado')),
    revision_nota TEXT NOT NULL DEFAULT '',
    revisado_por INTEGER REFERENCES users(id),
    revisado_at TEXT,
    importacion_estado TEXT CHECK (importacion_estado IN ('importado', 'rechazado', 'revision_manual')),
    importacion_detalle TEXT NOT NULL DEFAULT '',
    paquete_id INTEGER REFERENCES requerimiento_paquetes(id)
);

-- Envíos manuales registrados por el auditor (correo corporativo y aviso
-- por WhatsApp). Generar o descargar documentos nunca crea un envío.
CREATE TABLE IF NOT EXISTS requerimiento_envios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    canal TEXT NOT NULL CHECK (canal IN ('correo', 'whatsapp')),
    destinatario TEXT NOT NULL,
    copia TEXT NOT NULL DEFAULT '',
    asunto TEXT NOT NULL DEFAULT '',
    fecha TEXT NOT NULL,
    archivos_json TEXT NOT NULL DEFAULT '[]',
    paquete_id INTEGER REFERENCES requerimiento_paquetes(id),
    evidencia_id INTEGER REFERENCES requerimiento_adjuntos(id),
    registro_historico INTEGER NOT NULL DEFAULT 0 CHECK (registro_historico IN (0, 1)),
    justificacion_historica TEXT NOT NULL DEFAULT '',
    registrado_por INTEGER REFERENCES users(id),
    registrado_at TEXT NOT NULL
);

-- Respuestas importadas del Excel devuelto por el cliente, ligadas al
-- archivo recibido. Las observaciones se guardan tal como llegaron.
CREATE TABLE IF NOT EXISTS requerimiento_respuestas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    adjunto_id INTEGER NOT NULL REFERENCES requerimiento_adjuntos(id),
    items_json TEXT NOT NULL,
    detalles_json TEXT NOT NULL,
    importado_por INTEGER REFERENCES users(id),
    importado_at TEXT NOT NULL
);
