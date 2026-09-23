"""database/personas.py — Bloques 4 y 5 del levantamiento: administradores y accionistas."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from services.identificacion import validar_identificacion
from services.normalizacion import normalizar_texto
from services.trazabilidad import (
    BLOQUE_ACCIONISTAS,
    BLOQUE_ADMINISTRADORES,
    FUENTE_SUPERCIAS_ACCIONISTAS,
    FUENTE_SUPERCIAS_ADMINISTRADORES,
)

from database.base import DB_PATH, _optional_number, _text, _today, _touch_audit, connect
from database.trazabilidad import _record_provenance


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
