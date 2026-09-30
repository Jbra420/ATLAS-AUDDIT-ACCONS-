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

import json
import sqlite3
from email.message import EmailMessage
from urllib.parse import quote_plus

from database import (
    get_audit,
    get_requerimiento_context,
    get_requerimiento_file,
    get_requerimiento_paquete,
    next_requerimiento_numero,
    register_requerimiento_envio,
    register_requerimiento_paquete,
    review_requerimiento_adjunto,
    ruta_archivo,
    save_requerimiento_adjunto,
    save_requerimiento_carta,
    save_requerimiento_datos,
    save_requerimiento_destinatario,
    save_requerimiento_detalle,
    save_requerimiento_importacion,
    save_requerimiento_items,
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
    lookup_balance_details,
    lookup_catastro,
    lookup_supercias_catalog,
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
from services.requerimiento import (
    ADJUNTOS,
    CAMPOS_CORREO,
    CUADROS,
    DOCUMENTOS,
    ENTREGABLES,
    FORMULARIO,
    FORMULARIO_CARTA,
    ITEMS,
    MAX_FILAS_CUADRO,
    RECEPCIONES,
    VERIFICACIONES_REVISION,
    completar_derivados,
    correo,
    datos_efectivos,
    describir_pdf,
    faltantes,
    faltantes_carta,
    faltantes_paso1,
    formulario_desde,
    instantanea_actual,
    nombre_archivo,
    normalizar_datos,
    paquete_desactualizado,
    precarga,
    referencia_paquete,
    texto_entrega,
    validar_archivo,
    validar_fecha_envio,
    validar_fecha_pasada,
)
from services.requerimiento_docs import (
    build_carta,
    build_certificado,
    build_solicitud_xlsx,
    leer_respuesta_xlsx,
    referencia_del_libro,
    verificar_procedencia,
)
from ui.helpers import form_id, form_value
from views import auth_views, cuenta
from views.admin import dashboard as admin_dashboard
from views.admin import users as admin_users
from views.admin import companies as admin_companies
from views.auditor import dashboard as auditor_dashboard
from views.auditor import requerimiento as requerimiento_page
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
    # Requerimiento inicial: pestaña principal propia, al nivel del levantamiento.
    "/auditor/requerimiento": (False, True, lambda u, q, p, csrf: requerimiento_page.render(u, q, p, csrf_token=csrf)),
    "/admin/requerimiento":   (True,  False, lambda u, q, p, csrf: requerimiento_page.render(u, q, p, csrf_token=csrf)),
}


# ── Rutas POST y exportaciones ───────────────────────────────────────────────

def radar_url(audit_id: int, tab: str, *, msg: str = "", err: str = "") -> str:
    """URL del expediente con su aviso (msg) o error (err) y la pestaña a abrir."""
    key, text = ("err", err) if err else ("msg", msg)
    return f"/auditor/radar?audit_id={audit_id}&{key}={quote_plus(text)}&tab={quote_plus(tab)}"


def _int(form: dict, key: str) -> int:
    # Un id no numérico vale 0: la acción responde "no encontrado" con su propio mensaje.
    return form_id(form, key)


# ── Acciones POST del jefe: (form, admin) -> mensaje ────────────────────────

def _create_user(form: dict, admin: sqlite3.Row) -> str:
    # El jefe auditor es el usuario principal: desde Usuarios solo se crean auditores.
    with connect() as conn:
        # La clave que asigna el jefe es temporal: el auditor la cambia al primer ingreso.
        create_user(conn, form_value(form, "username"), form_value(form, "full_name"), "auditor",
                    form.get("password", [""])[0], must_change_password=True)
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
    # Solo los campos que trae el formulario (vacíos incluidos, para poder
    # borrarlos): patch_research no toca el resto.
    fields = {k: form_value(form, k) for k in RESEARCH_FIELDS if k in form}
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
        mark_source_pending(audit["id"], _int(form, "check_id"))
    else:
        mark_source_checked(audit["id"], _int(form, "check_id"), user["id"], form_value(form, "observacion"))
    return "Fuente actualizada"


def _toggle_document(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    if form_value(form, "accion", "revisar") == "revertir":
        mark_document_pending(audit["id"], _int(form, "doc_id"))
    else:
        mark_document_reviewed(audit["id"], _int(form, "doc_id"), user["id"])
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
    if not audit["ruc"]:
        raise ValueError("Registre el RUC del expediente antes de adjuntar el certificado: "
                         "se usa para verificar que el documento sea de esta compañía")
    documentos = []
    for nombre, pdf in archivos:
        try:
            documentos.append((nombre, extraer_texto(pdf)))
        except ValueError as exc:
            raise ValueError(f"{nombre}: {exc}") from exc
    # Verifica que cada documento sea de esta compañía antes de guardar o extraer nada.
    analisis = analizar_nominas(documentos, audit["ruc"])
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


# ── Requerimiento inicial: acciones del auditor (form, audit, user) -> mensaje ─
# El dispatcher es el mismo del radar (CSRF, rol auditor y auditoría asignada);
# solo cambia la página a la que se vuelve.

def requerimiento_url(audit_id: int, seccion: str, *, msg: str = "", err: str = "") -> str:
    key, text = ("err", err) if err else ("msg", msg)
    return f"/auditor/requerimiento?audit_id={audit_id}&{key}={quote_plus(text)}#{quote_plus(seccion)}"


def _un_archivo(form: dict, campo: str, requerido: bool = True) -> tuple[str, bytes] | None:
    archivos = getattr(form, "files", {}).get(campo, [])
    if len(archivos) > 1:
        raise ValueError("Adjunte un solo archivo")
    if not archivos:
        if requerido:
            raise ValueError("Seleccione el archivo")
        return None
    return archivos[0]


def _datos_para_generar(audit: sqlite3.Row) -> tuple[dict, dict]:
    """(contexto del requerimiento, datos confirmados) si se puede generar;
    si falta algún dato, ValueError con lo pendiente."""
    req = get_requerimiento_context(audit["id"])
    if req["guardado"] is None:
        raise ValueError("Confirme primero los datos del requerimiento")
    datos = datos_efectivos(req["guardado"], {})
    if not audit["ruc"] or datos.get("ruc") != audit["ruc"]:
        raise ValueError("El RUC del requerimiento no coincide con el de la auditoría. "
                         "Valide el RUC en Levantamiento de información y confirme los datos de nuevo")
    pendientes = faltantes(datos)
    if pendientes:
        raise ValueError("Faltan datos: " + ", ".join(pendientes))
    return req, datos


def _aviso_desactualizado(audit_id: int) -> str:
    """Tras editar datos, marcas o cuadros: la última generación ya no sirve
    para un envío nuevo (sus documentos y envíos anteriores no cambian)."""
    req = get_requerimiento_context(audit_id)
    if req["paquetes"] and paquete_desactualizado(req["paquetes"][0], instantanea_actual(req)):
        return (f". La generación {req['paquetes'][0]['numero']} quedó desactualizada: genere los documentos "
                "otra vez antes de preparar un nuevo correo")
    return ""


def _req_contrato(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    nombre, contenido = _un_archivo(form, "archivo")
    formato = validar_archivo(nombre, contenido, ADJUNTOS["contrato"][1])
    fecha = form_value(form, "fecha")
    fecha = validar_fecha_pasada(fecha, "Fecha de firma") if fecha else ""
    save_requerimiento_adjunto(audit["id"], "contrato", nombre, contenido, formato, user["id"], fecha=fecha,
                               detalle=describir_pdf(contenido))
    return "Contrato adjuntado, pendiente de revisión: revíselo y deje constancia antes de generar los documentos"


def _req_revisar(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    resultado = form_value(form, "resultado")
    if resultado == "conforme":
        faltan = [texto for clave, texto in VERIFICACIONES_REVISION if form_value(form, f"verif_{clave}") != "1"]
        if faltan:
            raise ValueError("Para dejarlo conforme confirme cada verificación: " + "; ".join(faltan))
    review_requerimiento_adjunto(audit["id"], form_id(form, "adjunto_id"), resultado, form_value(form, "nota"),
                                 user["id"])
    return ("Revisión registrada: documento conforme" if resultado == "conforme" else
            "Revisión registrada con observaciones: el documento no cuenta como válido")


def _req_datos(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    ruc_auditoria = (audit["ruc"] or "").strip()
    ruc_enviado = form_value(form, "ruc").strip()
    if ruc_enviado and ruc_enviado != ruc_auditoria:
        raise ValueError("El RUC del requerimiento debe coincidir con el de la auditoría. "
                         "Corríjalo primero en Levantamiento de información")
    # El paso 1 envía solo sus campos: lo demás (destinatario, equipo,
    # cronograma, datos del representante) se conserva, y los años de los
    # certificados y del ejercicio cerrado y el texto del inventario se
    # derivan otra vez de lo confirmado.
    req = get_requerimiento_context(audit["id"])
    sugeridos = precarga(audit, get_audit_context(audit["id"]))
    completo = formulario_desde(datos_efectivos(req["guardado"], sugeridos))
    completo.update({campo: form_value(form, campo) for campo in FORMULARIO})
    completo.update(anio_certificados="", anio_cerrado="", fechas_inventario="")
    datos = completar_derivados(normalizar_datos(completo), sugeridos)
    datos["ruc"] = ruc_auditoria
    save_requerimiento_datos(audit["id"], datos, user["id"])
    pendientes = faltantes_paso1(datos)
    if pendientes:
        return f"Datos guardados. Para continuar complete: {', '.join(pendientes)}"
    # Con el paso 1 completo se sigue en el paso 2 (documentos).
    return "Datos confirmados: revise los requisitos de cada documento y genérelos" + \
        _aviso_desactualizado(audit["id"]), "documentos"


def _req_carta(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    """Requisitos editables de la carta de encargo (modal del paso 2): cargo
    del representante, firma de Auddit, equipo y cronograma."""
    req = get_requerimiento_context(audit["id"])
    if req["guardado"] is None:
        raise ValueError("Confirme primero los datos del paso 1")
    completo = formulario_desde(datos_efectivos(req["guardado"], {}))
    completo.update({campo: form_value(form, campo) for campo in FORMULARIO_CARTA})
    # La lista del equipo (se agregan y quitan integrantes) y el mes y año de
    # cada entrega del cronograma.
    completo["equipo"] = "\n".join(v for v in form.get("equipo_integrante", []) if v.strip())
    for i, entregable in enumerate(ENTREGABLES):
        completo[f"cronograma_{i}"] = _entrega(form, i, entregable)
    datos = normalizar_datos(completo)
    save_requerimiento_carta(audit["id"], datos)
    pendientes = faltantes_carta(datos)
    return ("Requisitos de la carta de encargo guardados"
            + (f". Pendiente: {', '.join(pendientes)}" if pendientes else "") + _aviso_desactualizado(audit["id"]))


def _entrega(form: dict, i: int, entregable: str) -> str:
    mes, anio = form_value(form, f"entrega_mes_{i}"), form_value(form, f"entrega_anio_{i}")
    if not mes and not anio:
        return ""
    if not (mes.isdigit() and 1 <= int(mes) <= 12 and anio.isdigit() and 2000 <= int(anio) <= 2100):
        raise ValueError(f"Fecha de entrega de «{entregable}»: elija el mes y el año")
    return texto_entrega(int(mes), int(anio))


def _req_destinatario(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    datos = normalizar_datos({campo: form_value(form, campo) for campo in CAMPOS_CORREO})
    save_requerimiento_destinatario(audit["id"], datos)
    return "Destinatario del correo guardado"


def _req_items(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    marcas = {}
    for hoja, filas in ITEMS.items():
        for numero, _texto in filas:
            estado = form_value(form, f"item_{hoja}_{numero}")
            if estado not in ("", "cumplido", "no_aplica"):
                raise ValueError(f"Req. #{hoja} ítem {numero}: estado no válido")
            marcas[(hoja, numero)] = {
                "cumplido": estado == "cumplido", "no_aplica": estado == "no_aplica",
                "observacion": form_value(form, f"obs_{hoja}_{numero}"),
            }
    save_requerimiento_items(audit["id"], marcas)
    return "Marcas de la solicitud guardadas" + _aviso_desactualizado(audit["id"])


def _req_detalle(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    seccion = form_value(form, "seccion")
    if seccion not in CUADROS:
        raise ValueError("Cuadro de detalle no válido")
    columnas = CUADROS[seccion][2]
    filas = [
        [form_value(form, f"{seccion}_{r}_{c}") for c in range(len(columnas))]
        for r in range(min(form_id(form, "filas"), MAX_FILAS_CUADRO + 2))
    ]
    total = save_requerimiento_detalle(audit["id"], seccion, filas)
    return f"Cuadro guardado con {total} fila(s)" + _aviso_desactualizado(audit["id"])


def _req_generar(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    req, datos = _datos_para_generar(audit)
    usados = instantanea_actual(req)
    # Sin cambios de datos ni de plantilla no hay generación nueva: sería el mismo paquete.
    if req["paquetes"] and not paquete_desactualizado(req["paquetes"][0], usados):
        return (f"Sin cambios desde la generación {req['paquetes'][0]['numero']}: los documentos vigentes siguen "
                "siendo los mismos y no se creó otra")
    numero = next_requerimiento_numero(audit["id"])
    referencia = referencia_paquete(audit["id"], numero)
    # Los cuatro se arman en memoria antes de guardar nada.
    contenidos = {
        "carta": build_carta(datos),
        "cert_relacionadas": build_certificado("cert_relacionadas", datos),
        "cert_paraisos": build_certificado("cert_paraisos", datos),
        "solicitud": build_solicitud_xlsx(datos, req["items"], req["detalles"],
                                          {"referencia": referencia, "audit_id": audit["id"]}),
    }
    register_requerimiento_paquete(
        audit["id"], numero, referencia,
        {tipo: (nombre_archivo(tipo, datos, numero), contenido) for tipo, contenido in contenidos.items()},
        usados, user["id"],
    )
    return f"Documentos generados (generación {numero}). Revíselos antes de enviarlos"


def _req_envio(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    req = get_requerimiento_context(audit["id"])
    fecha = validar_fecha_envio(form_value(form, "fecha"))
    destinatario = form_value(form, "destinatario")
    if not destinatario:
        raise ValueError("Indique el destinatario")
    # Se envía una generación completa, nunca documentos sueltos de varias.
    paquete_id = form_id(form, "paquete_id")
    posicion = next((i for i, p in enumerate(req["paquetes"]) if p["id"] == paquete_id), None)
    if posicion is None or len(req["paquetes"][posicion]["archivos"]) != len(DOCUMENTOS):
        raise ValueError("Indique la generación de los cuatro documentos que se envió")
    paquete = req["paquetes"][posicion]
    if json.loads(paquete["datos_json"]).get("ruc") != (audit["ruc"] or ""):
        raise ValueError("El RUC de esta generación no coincide con el de la auditoría")
    if fecha[:16] < paquete["generado_at"][:16]:
        raise ValueError("La fecha del correo no puede ser anterior a la generación de sus documentos")
    modo = form_value(form, "modo", "actual")
    if modo not in ("actual", "historico"):
        raise ValueError("Tipo de registro de envío no válido")
    desactualizado = posicion != 0 or paquete_desactualizado(paquete, instantanea_actual(req))
    justificacion = form_value(form, "justificacion_historica").strip()
    if modo == "actual" and desactualizado:
        raise ValueError("No registre un envío nuevo con documentos desactualizados. Genere el paquete vigente "
                         "o use el registro histórico si el correo ya se había enviado")
    if modo == "historico":
        if not desactualizado:
            raise ValueError("La generación vigente debe registrarse como envío actual")
        if len(" ".join(justificacion.split())) < 15:
            raise ValueError("Explique por qué registra ahora un correo enviado anteriormente (mínimo 15 caracteres)")
        limites = [req["paquetes"][posicion - 1]["generado_at"]] if posicion else []
        cambio = req["guardado"]["updated_at"] if req["guardado"] else None
        anterior = json.loads(paquete["datos_json"])
        actual = instantanea_actual(req) or {}
        contenido_cambio = any(anterior.get(clave) != actual.get(clave)
                              for clave in set(anterior) | set(actual) if clave != "plantilla")
        if cambio and contenido_cambio and cambio >= paquete["generado_at"]:
            limites.append(cambio)
        if limites and fecha[:16] >= min(limites)[:16]:
            raise ValueError("El correo histórico debe haberse enviado antes de que esta generación quedara "
                             "desactualizada")
    nombre, contenido = _un_archivo(form, "evidencia")
    formato = validar_archivo(nombre, contenido, ADJUNTOS["evidencia_correo"][1])
    evidencia = save_requerimiento_adjunto(
        audit["id"], "evidencia_correo", nombre, contenido, formato, user["id"], fecha=fecha[:10],
    )
    register_requerimiento_envio(
        audit["id"], "correo", destinatario, fecha, user["id"], copia=form_value(form, "copia"),
        asunto=form_value(form, "asunto"), paquete_id=paquete_id, evidencia_id=evidencia,
        registro_historico=modo == "historico", justificacion_historica=justificacion,
    )
    return "Envío histórico del correo registrado" if modo == "historico" else "Envío del correo registrado"


def _req_whatsapp(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    req = get_requerimiento_context(audit["id"])
    if not any(e["canal"] == "correo" for e in req["envios"]):
        raise ValueError("Registre primero el envío del correo")
    fecha = validar_fecha_envio(form_value(form, "fecha"))
    destinatario = form_value(form, "destinatario")
    if not destinatario:
        raise ValueError("Indique el grupo o contacto notificado")
    evidencia = None
    archivo = _un_archivo(form, "evidencia", requerido=False)
    if archivo:
        formato = validar_archivo(archivo[0], archivo[1], ADJUNTOS["evidencia_whatsapp"][1])
        evidencia = save_requerimiento_adjunto(
            audit["id"], "evidencia_whatsapp", archivo[0], archivo[1], formato, user["id"], fecha=fecha[:10],
        )
    register_requerimiento_envio(audit["id"], "whatsapp", destinatario, fecha, user["id"], evidencia_id=evidencia)
    return "Aviso por WhatsApp registrado"


def _req_recepcion(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    tipo = form_value(form, "tipo")
    if tipo not in RECEPCIONES:
        raise ValueError("Indique qué documento se recibió")
    fecha = validar_fecha_pasada(form_value(form, "fecha"), "Fecha de recepción")
    nombre, contenido = _un_archivo(form, "archivo")
    formato = validar_archivo(nombre, contenido, RECEPCIONES[tipo][1])
    adjunto = save_requerimiento_adjunto(
        audit["id"], tipo, nombre, contenido, formato, user["id"], fecha=fecha, nota=form_value(form, "nota"),
        detalle=describir_pdf(contenido) if formato == "pdf" else "",
    )
    if tipo != "solicitud_respondida":
        return f"{RECEPCIONES[tipo][0]}: archivo recibido, pendiente de revisión"
    return _importar_respuesta(audit, adjunto, contenido, user)


def _importar_respuesta(audit: sqlite3.Row, adjunto: int, contenido: bytes, user: sqlite3.Row) -> str:
    """Valida estructura y procedencia del Excel ya guardado y solo entonces
    importa sus respuestas. Si no corresponde, queda como evidencia con el
    motivo y el auditor ve el error."""
    paquete = None
    try:
        leida = leer_respuesta_xlsx(contenido)
        refs, _error = referencia_del_libro(leida["identificacion"])
        paquete = get_requerimiento_paquete(next(iter(refs))) if len(refs) == 1 else None
        req = get_requerimiento_context(audit["id"])
        enviados = {e["paquete_id"] for e in req["envios"] if e["canal"] == "correo" and e["paquete_id"]}
        estado, motivo = verificar_procedencia(
            leida["identificacion"], paquete, audit_id=audit["id"], ruc_auditoria=audit["ruc"] or "",
            enviados=enviados,
        )
    except ValueError as exc:
        estado, motivo = "rechazado", str(exc)
    propio = paquete["id"] if paquete is not None and paquete["audit_id"] == audit["id"] else None
    if estado == "importado":
        save_requerimiento_importacion(audit["id"], adjunto, estado, motivo, user["id"], paquete_id=propio,
                                       respuesta={"items": leida["items"], "detalles": leida["detalles"]})
        marcados = sum(1 for i in leida["items"] if i["cumplido"] or i["no_aplica"])
        return (f"Excel respondido importado ({motivo.lower()}): {marcados} de {len(leida['items'])} ítems "
                "con respuesta")
    save_requerimiento_importacion(audit["id"], adjunto, estado, motivo, user["id"], paquete_id=propio)
    if estado == "revision_manual":
        raise ValueError(f"Excel recibido y conservado, NO importado: requiere revisión manual. {motivo}")
    raise ValueError(f"Excel recibido y conservado como evidencia, NO importado. {motivo}. "
                     "Puede cargar una versión corregida: el original se conserva")


# path -> (sección de la página a la que se vuelve, acción)
REQUERIMIENTO_POSTS = {
    "/auditor/requerimiento/contrato": ("contrato", _req_contrato),
    "/auditor/requerimiento/revisar": ("contrato", _req_revisar),
    "/auditor/requerimiento/datos": ("datos", _req_datos),
    "/auditor/requerimiento/items": ("solicitud", _req_items),
    "/auditor/requerimiento/detalle": ("solicitud", _req_detalle),
    "/auditor/requerimiento/generar": ("documentos", _req_generar),
    "/auditor/requerimiento/carta": ("documentos", _req_carta),
    "/auditor/requerimiento/destinatario": ("correo", _req_destinatario),
    "/auditor/requerimiento/envio": ("correo", _req_envio),
    "/auditor/requerimiento/whatsapp": ("whatsapp", _req_whatsapp),
    "/auditor/requerimiento/recepcion": ("recepcion", _req_recepcion),
}


# ── Descargas del requerimiento: (user, query) -> (contenido, nombre, tipo, en línea) ─
# Leer es consultar: el jefe auditor (solo lectura) y el auditor asignado
# pueden descargar; cualquier otro recibe "no disponible".

_TIPOS_ARCHIVO = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "eml": "message/rfc822",
}


def _auditoria_visible(user: sqlite3.Row, query: dict) -> sqlite3.Row:
    audit = get_audit(form_id(query, "audit_id"), user)
    if not audit:
        raise PermissionError("Auditoría no disponible")
    return audit


def _auditoria_del_auditor(user: sqlite3.Row, query: dict) -> sqlite3.Row:
    """Armar documentos nuevos (vista previa, borrador de correo) es trabajo
    del auditor asignado: el jefe solo consulta lo ya registrado."""
    if user["role"] != "auditor":
        raise PermissionError("El jefe auditor está en solo lectura: puede descargar los archivos registrados, "
                              "no preparar documentos ni correos")
    return _auditoria_visible(user, query)


def _descargar_archivo(user: sqlite3.Row, query: dict) -> tuple[bytes, str, str, bool]:
    audit = _auditoria_visible(user, query)
    row = get_requerimiento_file(audit["id"], form_value(query, "origen"), form_id(query, "id"))
    if row is None:
        raise ValueError("Archivo no disponible")
    ruta = ruta_archivo(row["ruta"])
    extension = ruta.suffix.lstrip(".")
    return ruta.read_bytes(), row["nombre"], _TIPOS_ARCHIVO.get(extension, "application/octet-stream"), extension == "pdf"


def _vista_previa(user: sqlite3.Row, query: dict) -> tuple[bytes, str, str, bool]:
    """Documento armado con los datos actuales, sin guardarlo como versión
    (el Excel no lleva referencia de Atlas: no se puede importar)."""
    audit = _auditoria_del_auditor(user, query)
    tipo = form_value(query, "doc")
    if tipo not in DOCUMENTOS:
        raise ValueError("Documento no válido")
    req, datos = _datos_para_generar(audit)
    if tipo == "carta":
        contenido = build_carta(datos)
    elif tipo == "solicitud":
        contenido = build_solicitud_xlsx(datos, req["items"], req["detalles"])
    else:
        contenido = build_certificado(tipo, datos)
    extension = DOCUMENTOS[tipo][1]
    nombre = "VISTA PREVIA " + nombre_archivo(tipo, datos, 0).replace(" v0.", ".")
    return contenido, nombre, _TIPOS_ARCHIVO[extension], extension == "pdf"


def _borrador_correo(user: sqlite3.Row, query: dict) -> tuple[bytes, str, str, bool]:
    """Borrador .eml (sin enviar) de la generación vigente: asunto, cuerpo y
    destinatarios salen de su instantánea y los adjuntos son sus cuatro
    archivos, para que el correo nunca mezcle datos actuales con documentos
    de otra generación. Si los datos cambiaron, hay que regenerar antes."""
    audit = _auditoria_del_auditor(user, query)
    req = get_requerimiento_context(audit["id"])
    if not req["paquetes"]:
        raise ValueError("Genere primero los cuatro documentos")
    paquete = req["paquetes"][0]
    if paquete_desactualizado(paquete, instantanea_actual(req)):
        raise ValueError(f"Los datos cambiaron después de la generación {paquete['numero']}: genere los documentos "
                         "otra vez antes de preparar el nuevo correo")
    if len(paquete["archivos"]) != len(DOCUMENTOS):
        raise ValueError("La generación vigente no tiene los cuatro documentos")
    datos = json.loads(paquete["datos_json"])
    if datos.get("ruc") != (audit["ruc"] or ""):
        raise ValueError("El RUC de la generación no coincide con el de la auditoría. "
                         "Confirme los datos y genere los documentos de nuevo")
    mensaje = correo(datos)
    # El destinatario es el vigente: no forma parte de la generación.
    destino = datos_efectivos(req["guardado"], {})
    email = EmailMessage()
    email["Subject"] = mensaje["asunto"]
    if destino.get("correo_para"):
        email["To"] = destino["correo_para"]
    if destino.get("correo_cc"):
        email["Bcc"] = destino["correo_cc"]
    email["X-Unsent"] = "1"
    email["X-Atlas-Generacion"] = f"{paquete['numero']} ({paquete['referencia']})"
    email.set_content(mensaje["cuerpo"])
    for tipo in DOCUMENTOS:
        version = paquete["archivos"][tipo]
        ruta = ruta_archivo(version["ruta"])
        principal, secundario = _TIPOS_ARCHIVO[ruta.suffix.lstrip(".")].split("/")
        email.add_attachment(ruta.read_bytes(), maintype=principal, subtype=secundario, filename=version["nombre"])
    return (email.as_bytes(), f"borrador_requerimiento_inicial_g{paquete['numero']}.eml", "message/rfc822",
            False)


DOWNLOADS = {
    "/requerimiento/archivo": _descargar_archivo,
    "/requerimiento/vista-previa": _vista_previa,
    "/requerimiento/correo.eml": _borrador_correo,
}


# ── Exportaciones: kind -> (prefijo del archivo, extensión, build(audit) -> contenido) ─

def _summary_txt(audit: sqlite3.Row) -> str:
    # Entrega el resumen tal como lo generó el auditor: descargarlo (también el
    # jefe, en solo lectura) no lo regenera ni escribe en el expediente.
    return get_research(audit["id"])["generated_summary"] or "No existe resumen generado."


def _levantamiento_xlsx(audit: sqlite3.Row) -> bytes:
    ruc = audit["ruc"]
    return build_resumen_xlsx(
        audit, get_audit_context(audit["id"]),
        catalog_sri=lookup_catastro(ruc),
        catalog_supercias=lookup_supercias_catalog(ruc),
        balance_details=lookup_balance_details(ruc),
    )


EXPORTS = {
    "/export/summary": ("atlas_resumen", "txt", _summary_txt),
    "/export/xlsx": ("atlas_levantamiento", "xlsx", _levantamiento_xlsx),
}
