"""database/catalogos.py — Catálogos locales (catastro SRI, Directorio de Compañías y balances de Supercías) y su volcado al expediente."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from services.financial import CASILLEROS
from services.normalizacion import normalizar_texto
from services.trazabilidad import (
    BLOQUE_ADMINISTRADORES,
    BLOQUE_FINANCIERO,
    BLOQUE_UBICACION,
    CAMPOS_PERFIL_SRI,
    CAMPOS_PERFIL_SUPERCIAS,
    CAMPOS_UBICACION,
    FUENTE_CATASTRO_SRI,
    FUENTE_DIRECTORIO_SUPERCIAS,
    bloque_de_campo_perfil,
)

from database.base import BASE_DIR, DB_PATH, _fetch_row, _text, _today, connect, now_iso
from database.trazabilidad import _record_provenance, _record_row_changes
from database.personas import _person_summary
from database.financiero import _audit_ruc, _format_amount, _validar_anio_fiscal


SRI_CATASTRO_PATH = BASE_DIR / "sri_catastro.db"


def lookup_catastro(ruc: str, db_path: Path | str = DB_PATH) -> dict | None:
    """
    Busca el RUC en el catastro local del SRI y devuelve todos los campos
    disponibles para que la investigación automática pueda construir la ficha.

    Nota: db_path se acepta por consistencia con el resto del paquete pero
    no se usa: esta función siempre conecta a SRI_CATASTRO_PATH, no a la
    base de datos de la aplicación.
    """
    catastro_db_path = SRI_CATASTRO_PATH
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
