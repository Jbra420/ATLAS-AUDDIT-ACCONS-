"""
core/router.py — Tablas declarativas de rutas de Atlas.

- GET_ROUTES: path -> (requires_admin, requires_user, render_fn). render_fn
  (user, query, path, csrf_token) -> str construye la página completa.
- ADMIN_POSTS: path -> (página de vuelta, acción(form, admin) -> mensaje).
- RADAR_POSTS: path -> (pestaña de vuelta, acción(form, audit, user) -> mensaje).
- EXPORTS: path -> (prefijo del archivo, extensión, build(audit) -> contenido en
  texto o, para el Excel, en bytes).

core/server.py despacha estas tablas y ya valida sesión, rol, CSRF y acceso
al expediente antes de llamar a la acción. Una acción que lanza una excepción
vuelve a la página con el mensaje como error. Agregar una ruta es agregar una
función y una línea aquí, sin tocar server.py.
"""
from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from database import (
    LOCATION_FORM_FIELDS,
    PROFILE_FORM_FIELDS,
    RESEARCH_FIELDS,
    add_administrator,
    add_shareholder,
    add_source,
    append_research_source_note,
    archive_audit,
    close_certificate_import,
    connect,
    create_company_audit,
    create_user,
    deactivate_user,
    delete_administrator,
    delete_shareholder,
    get_audit_context,
    get_certificate_import,
    get_research,
    mark_document_pending,
    mark_document_reviewed,
    mark_matching_source_checked,
    mark_source_checked,
    mark_source_pending,
    patch_research,
    reactivate_user,
    reassign_audit,
    refresh_summary,
    restore_audit,
    save_certificate,
    register_alert_treatment,
    register_audit_ruc,
    set_audit_fiscal_year,
    soft_delete_user,
    update_administrator,
    update_company_location_fields,
    update_company_profile_fields,
    update_shareholder,
    upsert_financial_statement,
)
from services.certificados import MAX_CERTIFICADOS, NOMINAS, analizar_nominas, extraer_texto
from services.financial import CAMPOS_FINANCIEROS
from services.resumen_excel import build_resumen_xlsx
from services.company_search import source_map_from_context
from services.company_research import research_company_by_ruc
from services.identificacion import validar_identificacion
from services.trazabilidad import FUENTE_CERTIFICADO_SUPERCIAS, validar_fecha_consulta
from ui.helpers import form_value
from views import auth_views, cuenta
from views.admin import dashboard as admin_dashboard
from views.admin import users as admin_users
from views.admin import companies as admin_companies
from views.auditor import dashboard as auditor_dashboard
from views.auditor.radar import page as radar_page


# ── Rutas GET ────────────────────────────────────────────────────────────────
# Format: path -> (requires_admin, requires_user, render_fn(user, query, path, csrf_token) -> str)
GET_ROUTES: dict[str, tuple[bool, bool, object]] = {
    "/login":           (False, False, lambda u, q, p, csrf: auth_views.render_login_page(u, q, p)),
    "/admin":           (True,  False, lambda u, q, p, csrf: admin_dashboard.render(u, q, p)),
    "/admin/users":     (True,  False, lambda u, q, p, csrf: admin_users.render(u, q, p, csrf_token=csrf)),
    "/admin/companies": (True,  False, lambda u, q, p, csrf: admin_companies.render(u, q, p, csrf_token=csrf)),
    "/admin/audit":     (True,  False, lambda u, q, p, csrf: radar_page.render(u, q, p, csrf_token=csrf)),
    "/auditor":         (False, True,  lambda u, q, p, csrf: auditor_dashboard.render(u, q, p)),
    "/auditor/radar":   (False, True,  lambda u, q, p, csrf: radar_page.render(u, q, p, csrf_token=csrf)),
    "/cuenta":          (False, True,  lambda u, q, p, csrf: cuenta.render(u, q, p, csrf_token=csrf)),
}


# ── Rutas POST y exportaciones ───────────────────────────────────────────────

def radar_url(audit_id: int, tab: str, *, msg: str = "", err: str = "") -> str:
    """URL del expediente con su aviso (msg) o error (err) y la pestaña a abrir."""
    key, text = ("err", err) if err else ("msg", msg)
    return f"/auditor/radar?audit_id={audit_id}&{key}={quote_plus(text)}&tab={quote_plus(tab)}"


def _int(form: dict, key: str) -> int:
    return int(form_value(form, key, "0"))


# ── Acciones POST del jefe: (form, admin) -> mensaje ────────────────────────

def _create_user(form: dict, admin: sqlite3.Row) -> str:
    # El jefe auditor es el usuario principal: desde Usuarios solo se crean auditores.
    with connect() as conn:
        create_user(conn, form_value(form, "username"), form_value(form, "full_name"), "auditor",
                    form_value(form, "password"))
    return "Auditor creado exitosamente"


def _create_company(form: dict, admin: sqlite3.Row) -> str:
    create_company_audit(
        *(form_value(form, k) for k in ("name", "ruc", "city", "activity_hint", "period")),
        _int(form, "assigned_auditor_id"),
        admin["id"],
    )
    return "Empresa asignada correctamente"


def _reassign(form: dict, admin: sqlite3.Row) -> str:
    reassign_audit(_int(form, "audit_id"), _int(form, "new_auditor_id"), admin["id"])
    return "Auditor reasignado correctamente"


# path -> (página a la que se vuelve, acción)
ADMIN_POSTS = {
    "/admin/users": ("/admin/users", _create_user),
    "/admin/users/deactivate": ("/admin/users", lambda f, a: deactivate_user(_int(f, "user_id"), a["id"])),
    "/admin/users/reactivate": ("/admin/users", lambda f, a: reactivate_user(_int(f, "user_id"), a["id"])),
    "/admin/users/delete": (
        "/admin/users",
        lambda f, a: soft_delete_user(_int(f, "user_id"), a["id"], form_value(f, "deletion_reason")),
    ),
    "/admin/companies": ("/admin/companies", _create_company),
    "/admin/companies/reassign": ("/admin/companies", _reassign),
    "/admin/companies/archive": (
        "/admin/companies",
        lambda f, a: archive_audit(_int(f, "audit_id"), a["id"], form_value(f, "archive_reason")),
    ),
    "/admin/companies/restore": ("/admin/companies", lambda f, a: restore_audit(_int(f, "audit_id"), a["id"])),
}


# ── Acciones POST del auditor sobre un expediente: (form, audit, user) -> mensaje ─
# El dispatcher ya validó CSRF, rol auditor y acceso al expediente.

def _save_research(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    # Solo los campos enviados con contenido: patch_research no toca el resto.
    fields = {k: v for k in RESEARCH_FIELDS if (v := form_value(form, k))}
    patch_research(audit["id"], user["id"], fields)
    return "Avance guardado correctamente"


def _add_source(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    source_type, finding = form_value(form, "source_type"), form_value(form, "finding")
    evidence_text, notes = form_value(form, "evidence_text"), form_value(form, "notes")
    composed_notes = "\n".join(
        part for part in [
            f"Hallazgo: {finding}" if finding else "",
            f"Evidencia: {evidence_text}" if evidence_text else "",
            f"Notas: {notes}" if notes else "",
        ] if part
    )
    add_source(audit["id"], form_value(form, "title"), form_value(form, "url"), source_type, composed_notes, user["id"])
    mark_matching_source_checked(audit["id"], source_type, user["id"], finding or notes or "Evidencia registrada")
    append_research_source_note(audit["id"], user["id"], source_type, finding, evidence_text)
    return "Evidencia registrada"


def _register_ruc(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    _clean_ruc, validation_msg = register_audit_ruc(audit["id"], form_value(form, "search_ruc"))
    return f"{validation_msg} Ahora puede iniciar la búsqueda automática."


def _investigate(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    clean_ruc, _validation_msg = register_audit_ruc(audit["id"], form_value(form, "search_ruc"))
    outcome = research_company_by_ruc(audit["id"], clean_ruc, user["id"])
    if outcome["sri_found"]:
        sri_msg = f"{outcome['populated_fields']} datos SRI cargados desde el catastro local."
    else:
        sri_msg = "SRI: RUC no encontrado en el catastro local."
    if outcome["supercias_found"]:
        supercias_msg = f"{outcome['supercias_populated_fields']} datos de Supercías cargados desde el catálogo local."
    else:
        supercias_msg = (
            "Supercías: RUC no encontrado en el catálogo local "
            "(¿está actualizado? use scripts/update_supercias_catalog.py) o el catálogo aún no fue importado."
        )
    years = outcome["financial_years"]
    financial_msg = (
        f"{years} ejercicio(s) financiero(s) disponible(s) desde el reporte local de Supercías; "
        "confirme el año fiscal en Información financiera."
        if years else "Balances: sin cifras para este RUC en los archivos importados."
    )
    return (
        f"Búsqueda completada: {sri_msg} {supercias_msg} {financial_msg} "
        "Revise los resultados y edítelos si es necesario."
    )


def _toggle_source_check(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    if form_value(form, "accion", "consultar") == "revertir":
        mark_source_pending(_int(form, "check_id"))
    else:
        mark_source_checked(_int(form, "check_id"), user["id"], form_value(form, "observacion"))
    return "Fuente actualizada"


def _toggle_document(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    if form_value(form, "accion", "revisar") == "revertir":
        mark_document_pending(_int(form, "doc_id"))
    else:
        mark_document_reviewed(_int(form, "doc_id"), user["id"])
    return "Documento actualizado"


def _save_financial(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    # Sin año fiscal explícito no se registran cifras (parámetro previo
    # obligatorio del requisito).
    anio = form_value(form, "anio_fiscal") or str(audit["anio_fiscal_eeff"] or "")
    if not anio:
        raise ValueError("Registre primero el año fiscal de los estados financieros")
    upsert_financial_statement(
        audit["id"], anio,
        {k: form_value(form, k) for k in (*CAMPOS_FINANCIEROS, "fecha_junta_aprobacion")},
        fecha_consulta=validar_fecha_consulta(form_value(form, "fecha_consulta")),
        user_id=user["id"],
    )
    return f"Estados financieros {anio} guardados"


def _alert_treatment(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    register_alert_treatment(
        audit["id"], form_value(form, "codigo"), form_value(form, "observacion"), user_id=user["id"],
    )
    return "Tratamiento registrado"


def _fiscal_year(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    anio = set_audit_fiscal_year(audit["id"], form_value(form, "anio_fiscal"), user_id=user["id"])
    return f"Año fiscal {anio} registrado"


def _save_profile(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    # Cada pestaña envía solo sus campos; los ausentes no se tocan.
    fecha_consulta = validar_fecha_consulta(form_value(form, "fecha_consulta"))
    for fields, update in (
        (PROFILE_FORM_FIELDS, update_company_profile_fields),
        (LOCATION_FORM_FIELDS, update_company_location_fields),
    ):
        data = {k: form_value(form, k) for k in fields if k in form}
        if data:
            update(audit["id"], data, user_id=user["id"], fecha_consulta=fecha_consulta)
    return "Datos guardados"


def _person_action(form: dict, noun: str, id_field: str, delete, save) -> str:
    """Alta, edición o baja de un administrador o accionista.

    save(fecha_consulta, tipo, identificacion, row_id) registra la persona;
    row_id es None en un alta. Una identificación con formato dudoso no
    bloquea el registro: se devuelve como advertencia en el mensaje.
    """
    action = form_value(form, "action", "add")
    if action == "delete":
        delete(_int(form, id_field))
        return f"{noun} eliminado"
    tipo, identificacion = form_value(form, "tipo_identificacion"), form_value(form, "identificacion")
    fecha_consulta = validar_fecha_consulta(form_value(form, "fecha_consulta"))
    row_id = _int(form, id_field) if action == "update" else None
    warning = save(fecha_consulta, tipo, identificacion, row_id)
    msg = f"{noun} {'registrado' if row_id is None else 'actualizado'}"
    return f"{msg}. Advertencia: {warning}" if warning else msg


def _administrator(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    audit_id, user_id = audit["id"], user["id"]

    def save(fecha_consulta, tipo, identificacion, row_id):
        warning = validar_identificacion(identificacion, tipo, ("cedula", "pasaporte"))[2]
        common = dict(tipo_identificacion=tipo, fecha_consulta=fecha_consulta, user_id=user_id)
        if row_id is not None:
            update_administrator(
                audit_id, row_id, identificacion=identificacion,
                nacionalidad=form_value(form, "nacionalidad"), **common,
            )
        else:
            add_administrator(
                audit_id, identificacion, form_value(form, "nombre"),
                form_value(form, "nacionalidad"), form_value(form, "cargo"), **common,
            )
        return warning

    return _person_action(
        form, "Administrador", "administrator_id",
        lambda row_id: delete_administrator(audit_id, row_id, user_id=user_id), save,
    )


def _shareholder(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    audit_id, user_id = audit["id"], user["id"]

    def save(fecha_consulta, tipo, identificacion, row_id):
        warning = validar_identificacion(identificacion, tipo, ("cedula", "ruc", "pasaporte"))[2]
        details = {
            k: form_value(form, k) for k in ("participacion_porcentaje", "capital", "beneficiario_final")
        } | {"tipo_identificacion": tipo, "fecha_consulta": fecha_consulta, "user_id": user_id}
        if row_id is not None:
            update_shareholder(audit_id, row_id, identificacion=identificacion, **details)
        else:
            add_shareholder(
                audit_id, form_value(form, "numero"), identificacion, form_value(form, "nombre"), **details,
            )
        return warning

    return _person_action(
        form, "Accionista", "shareholder_id",
        lambda row_id: delete_shareholder(audit_id, row_id, user_id=user_id), save,
    )


def _upload_certificate(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    """Adjunta los certificados PDF de nómina (uno o varios: Supercias puede
    entregar administradores y accionistas por separado). Quedan como
    evidencia y una sola propuesta de ambas nóminas espera la revisión del auditor."""
    archivos = getattr(form, "files", {}).get("archivo", [])
    if not archivos:
        raise ValueError("Seleccione el certificado en PDF")
    if len(archivos) > MAX_CERTIFICADOS:
        raise ValueError(f"Adjunte como máximo {MAX_CERTIFICADOS} PDF a la vez")
    documentos = []
    for nombre, pdf in archivos:
        try:
            documentos.append((nombre, extraer_texto(pdf)))
        except ValueError as exc:
            raise ValueError(f"{nombre}: {exc}") from exc
    analisis = analizar_nominas(documentos, audit["ruc"] or "")
    save_certificate(audit["id"], archivos, analisis, user["id"])
    registrado = "Certificado registrado" if len(archivos) == 1 else f"{len(archivos)} certificados registrados"
    adm, acc = (len(analisis[n]) for n in NOMINAS)
    if not adm and not acc:
        return f"{registrado} como evidencia. No se detectaron filas: registre la nómina manualmente."
    return (f"{registrado} como evidencia. Detectados {adm} administrador(es) y "
            f"{acc} accionista(s): revíselos y confirme la importación.")


def _import_rows(form: dict, audit: sqlite3.Row, user: sqlite3.Row, nomina: str, total: int) -> tuple[int, list[str]]:
    """Importa las filas marcadas de una nómina: (importadas, omitidas con motivo)."""
    indices = sorted({int(i) for i in form.get(f"incluir_{nomina}", []) if i.isdigit() and int(i) < total})
    comunes = dict(
        fecha_consulta=validar_fecha_consulta(form_value(form, "fecha_consulta")),
        user_id=user["id"], fuente=FUENTE_CERTIFICADO_SUPERCIAS,
    )
    importadas, omitidas = 0, []
    for i in indices:
        valor = lambda campo: form_value(form, f"{nomina}_{campo}_{i}")  # noqa: E731
        try:
            if nomina == "administradores":
                add_administrator(
                    audit["id"], valor("identificacion"), valor("nombre"), valor("nacionalidad"), valor("cargo"),
                    **comunes,
                )
            else:
                add_shareholder(
                    audit["id"], "", valor("identificacion"), valor("nombre"),
                    participacion_porcentaje=valor("participacion_porcentaje"), capital=valor("capital"),
                    **comunes,
                )
            importadas += 1
        except ValueError as exc:
            omitidas.append(f"{valor('nombre') or f'fila {i + 1}'} ({exc})")
    return importadas, omitidas


def _import_certificate(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    """Importa a ambas nóminas las filas que el auditor marcó (y corrigió)."""
    propuesta = get_certificate_import(audit["id"], _int(form, "import_id"))
    if not propuesta or propuesta["estado"] != "pendiente":
        raise ValueError("El certificado ya fue revisado o no existe")
    if not any(form.get(f"incluir_{n}") for n in NOMINAS):
        raise ValueError("Marque al menos una fila para importar")
    resultado = {n: _import_rows(form, audit, user, n, len(propuesta[n])) for n in NOMINAS}
    close_certificate_import(audit["id"], propuesta["id"], "importado")
    msg = (f"Importados {resultado['administradores'][0]} administrador(es) y "
           f"{resultado['accionistas'][0]} accionista(s) del certificado")
    omitidas = [o for _, lista in resultado.values() for o in lista]
    return f"{msg}. Omitidos: {'; '.join(omitidas)}" if omitidas else msg


def _discard_certificate(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    close_certificate_import(audit["id"], _int(form, "import_id"), "descartado")
    return "Propuesta descartada. El certificado sigue registrado como evidencia"


def _generate_summary(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    # Con obligatorios pendientes solo se genera si el auditor lo confirmó en
    # el modal de la pestaña Resumen (confirmar_pendientes=1). El resumen deja
    # constancia de lo pendiente en su sección "Pendientes de validación".
    blockers = source_map_from_context(audit, get_audit_context(audit["id"]))["readiness"]["blockers"]
    if blockers and form_value(form, "confirmar_pendientes") != "1":
        labels = [item["label"] for item in blockers]
        preview = ", ".join(labels[:3])
        if len(labels) > 3:
            preview += f" y {len(labels) - 3} requisito(s) más"
        raise ValueError(f"No se puede generar el resumen. Complete: {preview}.")
    refresh_summary(audit["id"])
    if blockers:
        return f"Resumen generado con {len(blockers)} requisito(s) obligatorio(s) pendiente(s)"
    return "Resumen generado"


# path -> (pestaña por defecto al volver, acción). Un formulario puede pedir
# otra pestaña con el campo return_tab.
RADAR_POSTS = {
    "/auditor/radar": ("resumen", _save_research),
    "/auditor/source": ("documentos", _add_source),
    "/auditor/radar/search": ("sri", _register_ruc),
    "/auditor/radar/investigate": ("sri", _investigate),
    "/auditor/radar/source-check": ("sri", _toggle_source_check),
    "/auditor/radar/document": ("documentos", _toggle_document),
    "/auditor/radar/financial": ("indicadores", _save_financial),
    "/auditor/radar/alert-treatment": ("sri", _alert_treatment),
    "/auditor/radar/financial-year": ("indicadores", _fiscal_year),
    "/auditor/radar/profile": ("sri", _save_profile),
    "/auditor/radar/administrator": ("admins", _administrator),
    "/auditor/radar/shareholder": ("accionistas", _shareholder),
    "/auditor/radar/certificado": ("admins", _upload_certificate),
    "/auditor/radar/certificado/importar": ("admins", _import_certificate),
    "/auditor/radar/certificado/descartar": ("admins", _discard_certificate),
    "/auditor/radar/summary": ("resumen", _generate_summary),
}


# ── Exportaciones: kind -> (prefijo del archivo, extensión, build(audit) -> contenido) ─

def _summary_txt(audit: sqlite3.Row) -> str:
    has_summary = get_research(audit["id"])["generated_summary"]
    return refresh_summary(audit["id"]) if has_summary else "No existe resumen generado."


def _levantamiento_xlsx(audit: sqlite3.Row) -> bytes:
    return build_resumen_xlsx(audit, get_audit_context(audit["id"]))


EXPORTS = {
    "/export/summary": ("atlas_resumen", "txt", _summary_txt),
    "/export/xlsx": ("atlas_levantamiento", "xlsx", _levantamiento_xlsx),
}
