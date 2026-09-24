"""
seed_data.py — Datos de demostración de Atlas (empresa GRUCANQUI CIA. LTDA).

La primera vez que se ejecuta init_db() (tabla users vacía) se crea solo el
jefe auditor, el usuario principal: los auditores los crea él desde Usuarios.
El auditor demo y la empresa demo solo se cargan con init_db(demo=True), que
usan los tests como fixture, y el expediente demo cuando un auditor registra el
RUC de demo. Está separado del paquete database para no mezclar datos de
ejemplo con la lógica de persistencia.

Los imports de database (create_user, now_iso, DEFAULT_ECONOMIC_DOCUMENTS)
se hacen dentro de las funciones, no al nivel del módulo, porque
el paquete database importa este módulo: un import a nivel de módulo aquí crearía
un ciclo de importación.
"""
from __future__ import annotations

import os
import sqlite3

DEMO_RUC = os.environ.get("DEMO_RUC", "0190377210001")
DEMO_COMPANY = "GRUCANQUI CIA. LTDA"


def seed_defaults(conn: sqlite3.Connection, demo: bool = False) -> None:
    """Base nueva: crea el jefe auditor (admin / admin123, a cambiar en "Mi
    cuenta"). Con demo=True agrega además el auditor y la empresa demo."""
    from database import create_user, now_iso

    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count:
        return

    admin_id = create_user(conn, "admin", "Jefe Auditor", "admin", "admin123")
    if not demo:
        return
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
    conn.execute(
        """
        INSERT INTO audit_assignments (audit_id, auditor_id, assigned_by, assigned_at)
        VALUES (?, ?, ?, ?)
        """,
        (audit_id, auditor_id, admin_id, ts),
    )

    # Seed demo completo de GRUCANQUI
    seed_demo_radar(conn, audit_id)


def seed_demo_radar(conn: sqlite3.Connection, audit_id: int) -> None:
    """Carga todos los datos demo de GRUCANQUI CIA. LTDA en las tablas del Radar."""
    from database import DEFAULT_ECONOMIC_DOCUMENTS, now_iso

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
    for ident, nombre, nac, cargo in admins:
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
