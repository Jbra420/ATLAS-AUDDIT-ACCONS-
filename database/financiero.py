"""database/financiero.py — Bloque 6 del levantamiento: año fiscal, estados financieros por RUC y año, y tratamiento de alertas."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from services.financial import CAMPOS_FINANCIEROS, CASILLEROS, ETIQUETAS_FINANCIERAS
from services.trazabilidad import BLOQUE_FINANCIERO, BLOQUE_VALIDACIONES

from database.base import DB_PATH, _optional_number, _text, _today, _touch_audit, connect, now_iso
from database.trazabilidad import _record_provenance


def get_financial_snapshot(audit_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM financial_snapshots WHERE audit_id = ?", (audit_id,)
        ).fetchone()


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


def _financial_context(conn: sqlite3.Connection, audit_id: int) -> dict[str, Any]:
    """Financiero vigente de la auditoría.

    - anio_confirmado: año fiscal que el auditor confirmó como auditado
      (audits.anio_fiscal_eeff) o None.
    - anio_fiscal: ejercicio que se muestra. Es el confirmado, tenga o no
      cifras (nunca se sustituye por otro); sin confirmar, el más reciente con
      cifras del RUC.
    - snapshot: cifras de ese ejercicio (origen "anual"). Si no hay cifras
      anuales, las registradas antes de la Fase 4 sin año fiscal (origen
      "sin_anio"), para no perderlas de vista; si tampoco existen, None.
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

    confirmado = anio
    if anio is None and years:
        anio = years[0]["anio_fiscal"]
    current = next((dict(y) for y in years if anio is not None and y["anio_fiscal"] == anio), None)
    if current is not None:
        snapshot = {**current, "origen": "anual"}
    elif legacy is not None:
        snapshot = {**legacy, "anio_fiscal": None, "fecha_corte": None, "origen": "sin_anio"}
    else:
        snapshot = None
    return {
        "snapshot": snapshot, "legacy": legacy, "years": years,
        "anio_fiscal": anio, "anio_confirmado": confirmado,
    }


def get_financial_context(audit_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    with connect(db_path) as conn:
        return _financial_context(conn, audit_id)


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
