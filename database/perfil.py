"""database/perfil.py — Bloques 1 a 3 del levantamiento: perfil SRI/Supercias y ubicación de la empresa."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from services.trazabilidad import (
    BLOQUE_UBICACION,
    FUENTE_MANUAL_POR_BLOQUE,
    FUENTE_SUPERCIAS_UBICACION,
    bloque_de_campo_perfil,
)

from database.base import DB_PATH, _fetch_row, _text, _today, connect, now_iso
from database.trazabilidad import _record_row_changes, _update_fields


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


def get_company_location(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)
        ).fetchone()
