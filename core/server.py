"""
core/server.py — Atlas HTTP Server
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from database import (
    DB_PATH,
    LOCATION_FORM_FIELDS,
    PROFILE_FORM_FIELDS,
    RESEARCH_FIELDS,
    add_administrator,
    add_shareholder,
    add_source,
    append_research_source_note,
    authenticate,
    connect,
    create_company_audit,
    create_session,
    create_user,
    deactivate_user,
    delete_administrator,
    delete_shareholder,
    destroy_session,
    get_audit,
    get_audit_context,
    get_csrf_token,
    get_research,
    init_db,
    mark_document_reviewed,
    mark_document_pending,
    mark_source_checked,
    mark_matching_source_checked,
    mark_source_pending,
    register_audit_ruc,
    refresh_summary,
    patch_research,
    reactivate_user,
    reassign_audit,
    soft_delete_user,
    update_administrator,
    update_company_location_fields,
    update_company_profile_fields,
    update_shareholder,
    register_alert_treatment,
    set_audit_fiscal_year,
    upsert_financial_statement,
    user_from_session,
    validate_csrf_token,
)
from services.financial import CAMPOS_FINANCIEROS, compute_indicators
from services.company_search import source_map_from_context
from services.company_research import research_company_by_ruc
from services.dossier import build_dossier_model, build_dossier_text
from services.identificacion import validar_identificacion
from services.trazabilidad import validar_fecha_consulta
from ui.layout import layout, set_css
from ui.helpers import form_value
from core.router import GET_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
COOKIE_NAME = "atlas_session"
STATIC_DIR = BASE_DIR / "static"
CSS_PATH = STATIC_DIR / "atlas.css"
_CSS_CONTENT: str = ""
_QUIET_MODE: bool = False  # Se activa con --quiet; suprime el log de peticiones HTTP


def load_css() -> None:
    global _CSS_CONTENT
    try:
        _CSS_CONTENT = CSS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _CSS_CONTENT = "/* atlas.css no encontrado */"
    set_css(_CSS_CONTENT)


class _LoginRateLimiter:
    """Rate limiter simple para el endpoint /login.

    Permite hasta MAX_ATTEMPTS intentos fallidos por IP.
    Bloquea la IP durante LOCKOUT_SECONDS segundos al superar el límite.
    Thread-safe mediante threading.Lock.
    """
    MAX_ATTEMPTS = 5
    LOCKOUT_SECONDS = 15 * 60  # 15 minutos

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # ip -> {"count": int, "locked_until": float}
        self._state: dict[str, dict] = {}

    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            state = self._state.get(ip)
            if not state:
                return False
            if state["locked_until"] and time.monotonic() < state["locked_until"]:
                return True
            # Lock expirado: limpiar
            if state.get("locked_until") and time.monotonic() >= state["locked_until"]:
                del self._state[ip]
            return False

    def record_failure(self, ip: str) -> None:
        with self._lock:
            state = self._state.setdefault(ip, {"count": 0, "locked_until": 0.0})
            state["count"] += 1
            if state["count"] >= self.MAX_ATTEMPTS:
                state["locked_until"] = time.monotonic() + self.LOCKOUT_SECONDS

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._state.pop(ip, None)


_LOGIN_LIMITER = _LoginRateLimiter()

class AtlasHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suprime el log de peticiones cuando _QUIET_MODE está activo."""
        if not _QUIET_MODE:
            super().log_message(format, *args)

    def send_html(self, content: str, status_code: int = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_download(self, content: str, filename: str, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data: dict[str, Any], status_code: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, path: str, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def get_cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookies = SimpleCookie(raw)
        morsel = cookies.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def current_user(self) -> sqlite3.Row | None:
        return user_from_session(self.get_cookie_token())

    def parse_post(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return parse_qs(raw, keep_blank_values=True)

    def require_user(self) -> sqlite3.Row | None:
        user = self.current_user()
        if user is None:
            self.redirect("/login")
        return user

    def require_admin(self) -> sqlite3.Row | None:
        user = self.require_user()
        if user is None:
            return None
        if user["role"] != "admin":
            self.deny(user, "No tiene permisos de jefe auditor.")
            return None
        return user

    def require_auditor(self) -> sqlite3.Row | None:
        user = self.require_user()
        if user is None:
            return None
        if user["role"] != "auditor":
            body = layout(
                "Solo lectura", user,
                '<div class="error-msg">El jefe auditor solo puede ver el expediente. Las consultas, verificaciones, ediciones y generación de resumen pertenecen al auditor asignado.</div>',
                active_path="/admin",
            )
            self.send_html(body, 403)
            return None
        return user

    def deny(self, user: sqlite3.Row | None, message: str) -> None:
        self.send_html(layout("Acceso denegado", user, f'<div class="error-msg">{message}</div>'), 403)

    def get_csrf_for_session(self) -> str:
        """Retorna el CSRF token de la sesión activa, o cadena vacía si no hay sesión."""
        return get_csrf_token(self.get_cookie_token())

    def _reject_csrf(self) -> bool:
        """Valida el CSRF token en un POST. Retorna True si debe rechazarse la petición."""
        submitted = form_value(self._cached_form, "_csrf")
        if not validate_csrf_token(self.get_cookie_token(), submitted):
            user = self.current_user()
            self.send_html(
                layout("Solicitud inválida", user,
                       '<div class="error-msg">Solicitud rechazada: token de seguridad inválido o expirado. Recarga la página e inténtalo de nuevo.</div>'),
                403,
            )
            return True
        return False

    # ── GET routing ─────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/logout":
            destroy_session(self.get_cookie_token())
            expired = f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"
            self.redirect("/login", cookie=expired)
            return

        if path.startswith("/static/"):
            if path == "/static/atlas.css":
                encoded = _CSS_CONTENT.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-type", "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            import mimetypes
            # Resuelto contra STATIC_DIR (no Path.cwd(), que depende del
            # directorio desde donde se lanzó el proceso) y comparado ya
            # resuelto para que un "/static/../..." no pueda escapar de la
            # carpeta static/.
            requested = (STATIC_DIR / path.removeprefix("/static/")).resolve()
            if requested.is_file() and requested.is_relative_to(STATIC_DIR.resolve()):
                data = requested.read_bytes()
                mime_type, _ = mimetypes.guess_type(requested)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-type", mime_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        if path == "/api/lookup-ruc":
            # Requiere admin a propósito: esta es la búsqueda de ALTA de empresa
            # (pre-llenar nombre/ciudad/actividad en /admin/companies antes de
            # asignarla a un auditor), tarea exclusiva del jefe. No confundir
            # con la búsqueda de INVESTIGACIÓN del auditor sobre un expediente
            # ya asignado (/auditor/radar/search), que exige require_auditor().
            user = self.require_admin()
            if not user:
                return
            ruc = query.get("ruc", [""])[0].strip()
            
            from services.ruc_validator import validate_ruc
            is_valid, _warn, msg = validate_ruc(ruc)
            if not is_valid:
                self.send_json({"error": msg}, HTTPStatus.BAD_REQUEST)
                return
            from database import lookup_catastro
            data = lookup_catastro(ruc)
            
            if not data:
                # Nivel 2: Fallback al Scraper
                from services.sri_scraper import scrape_ruc_data
                data = scrape_ruc_data(ruc)
                
            if not data:
                self.send_json(
                    {"error": "No se encontraron datos para el RUC ingresado ni en catastro ni vía web."},
                    HTTPStatus.NOT_FOUND,
                )
                return
                
            self.send_json(data)
            return


        if path in EXPORTS:
            current = self.require_user()
            if current:
                self.export_audit(current, query, path)
            return

        if path == "/":
            user = self.current_user()
            if not user:
                self.redirect("/login")
            elif user["role"] == "admin":
                self.redirect("/admin")
            else:
                self.redirect("/auditor")
            return
            
        route = GET_ROUTES.get(path)
        if route:
            req_admin, req_user, handler_fn = route
            user = None
            if req_admin:
                user = self.require_admin()
                if not user: return
            elif req_user:
                user = self.require_user()
                if not user: return
            else:
                user = self.current_user()

            csrf_tok = get_csrf_token(self.get_cookie_token())
            html_content = handler_fn(user, query, path, csrf_tok)

            if html_content:
                self.send_html(html_content)
            return

        self.send_html(layout("No encontrado", self.current_user(), '<div class="error-msg">Ruta no encontrada.</div>'), 404)

    # ── POST routing ────────────────────────────────────────────────────

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        self._cached_form = self.parse_post()  # guardar para _reject_csrf
        form = self._cached_form

        if path == "/login":
            # /login no tiene sesión todavía → excluido de CSRF
            self.login(form)
            return

        # Toda ruta POST que no sea /login requiere CSRF válido
        if self._reject_csrf():
            return

        if path in ADMIN_POSTS:
            admin = self.require_admin()
            if not admin:
                return
            back, action = ADMIN_POSTS[path]
            try:
                self.redirect(f"{back}?msg={quote_plus(action(form, admin))}")
            except Exception as exc:
                self.redirect(f"{back}?err={quote_plus(str(exc))}")
            return

        if path in RADAR_POSTS:
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.deny(current, "Auditoría no disponible.")
                return
            default_tab, action = RADAR_POSTS[path]
            tab = form_value(form, "return_tab") or default_tab
            try:
                self.redirect(radar_url(audit_id, tab, msg=action(form, audit, current)))
            except Exception as exc:
                self.redirect(radar_url(audit_id, tab, err=str(exc)))
            return

        self.send_html(
            layout("No encontrado", self.current_user(),
                   '<div class="error-msg">Ruta POST no encontrada.</div>'),
            404,
        )

    def login(self, form: dict[str, list[str]]) -> None:
        client_ip = self.client_address[0]
        if _LOGIN_LIMITER.is_blocked(client_ip):
            self.redirect("/login?err=Demasiados+intentos.+Espere+15+minutos+e+int%C3%A9ntelo+de+nuevo.")
            return
        user = authenticate(form_value(form, "username"), form_value(form, "password"))
        if not user:
            _LOGIN_LIMITER.record_failure(client_ip)
            self.redirect("/login?err=Usuario+o+clave+incorrecta.")
            return
        _LOGIN_LIMITER.record_success(client_ip)
        token = create_session(user["id"])
        cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={8 * 3600}"
        self.redirect("/admin" if user["role"] == "admin" else "/auditor", cookie=cookie)

    # ── Export endpoints ──────────────────────────────────────────────────

    def export_audit(self, user: sqlite3.Row, query: dict, kind: str) -> None:
        audit = get_audit(int(form_value(query, "audit_id", "0")), user)
        if not audit:
            self.deny(user, "No disponible.")
            return
        prefix, ext, build = EXPORTS[kind]
        safe_name = "".join(
            ch for ch in audit["company_name"].lower().replace(" ", "_") if ch.isalnum() or ch == "_"
        )[:40]
        content_type = "text/csv; charset=utf-8" if ext == "csv" else "text/plain; charset=utf-8"
        self.send_download(build(audit), f"{prefix}_{safe_name}_{audit['period']}.{ext}", content_type)


def radar_url(audit_id: int, tab: str, *, msg: str = "", err: str = "") -> str:
    """URL del expediente con su aviso (msg) o error (err) y la pestaña a abrir."""
    key, text = ("err", err) if err else ("msg", msg)
    return f"/auditor/radar?audit_id={audit_id}&{key}={quote_plus(text)}&tab={quote_plus(tab)}"


def _int(form: dict, key: str) -> int:
    return int(form_value(form, key, "0"))


# ── Acciones POST del jefe: (form, admin) -> mensaje ────────────────────────

def _create_user(form: dict, admin: sqlite3.Row) -> str:
    with connect() as conn:
        create_user(conn, *(form_value(form, k) for k in ("username", "full_name", "role", "password")))
    return "Usuario creado exitosamente"


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


def _generate_summary(form: dict, audit: sqlite3.Row, user: sqlite3.Row) -> str:
    readiness = source_map_from_context(audit, get_audit_context(audit["id"]))["readiness"]
    if not readiness["ready"]:
        labels = [item["label"] for item in readiness["blockers"]]
        preview = ", ".join(labels[:3])
        if len(labels) > 3:
            preview += f" y {len(labels) - 3} requisito(s) más"
        raise ValueError(f"No se puede generar el resumen. Complete: {preview}.")
    refresh_summary(audit["id"])
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
    "/auditor/radar/alert-treatment": ("resumen", _alert_treatment),
    "/auditor/radar/financial-year": ("indicadores", _fiscal_year),
    "/auditor/radar/profile": ("sri", _save_profile),
    "/auditor/radar/administrator": ("admins", _administrator),
    "/auditor/radar/shareholder": ("accionistas", _shareholder),
    "/auditor/radar/summary": ("resumen", _generate_summary),
}


# ── Exportaciones: kind -> (prefijo del archivo, extensión, build(audit) -> contenido) ─

def _summary_txt(audit: sqlite3.Row) -> str:
    has_summary = get_research(audit["id"])["generated_summary"]
    return refresh_summary(audit["id"]) if has_summary else "No existe resumen generado."


def _summary_csv(audit: sqlite3.Row) -> str:
    research = get_research(audit["id"])
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerow(["Campo", "Valor"])
    rows = [
        ("Razón social", audit["company_name"]),
        ("RUC", audit["ruc"]),
        ("Período", audit["period"]),
        ("Ciudad", audit["city"]),
        ("Estado auditoría", audit["status"]),
        *((label, research[key]) for key, label in (
            ("commercial_name", "Nombre comercial"),
            ("economic_activity", "Actividad económica"),
            ("legal_status", "Estado societario"),
            ("representative", "Representante legal"),
            ("address", "Dirección"),
            ("tax_obligations", "Obligaciones tributarias"),
            ("public_contracting", "Contratación pública"),
            ("supercias_info", "Info Supercias"),
            ("sri_info", "Info SRI"),
            ("sercop_info", "Info SERCOP"),
            ("observations", "Observaciones"),
            ("risk_flags", "Riesgos identificados"),
            ("generated_summary", "Resumen generado"),
        )),
    ]
    writer.writerows((label, value or "") for label, value in rows)
    return out.getvalue()


def _dossier_txt(audit: sqlite3.Row) -> str:
    ctx = get_audit_context(audit["id"])
    snapshot = ctx["snapshot"]
    dossier = build_dossier_model(
        audit, ctx["research"], ctx["profile"], ctx["location"], ctx["admins"], ctx["shareholders"],
        snapshot, compute_indicators(dict(snapshot) if snapshot else None),
        source_map_from_context(audit, ctx), ctx["sources"],
    )
    return build_dossier_text(dossier)


EXPORTS = {
    "/export/summary": ("atlas_resumen", "txt", _summary_txt),
    "/export/csv": ("atlas_ficha", "csv", _summary_csv),
    "/export/dossier": ("atlas_ficha_final", "txt", _dossier_txt),
}


def run() -> None:
    global _QUIET_MODE
    parser = argparse.ArgumentParser(description="Atlas — Plataforma de auditoría · Auddit")
    parser.add_argument("--host", default="127.0.0.1", help="Host del servidor local")
    parser.add_argument("--port", type=int, default=8765, help="Puerto del servidor local")
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suprimir el log de peticiones HTTP en la terminal",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="Habilitar HTTPS con certificado TLS autofirmado (recomendado en red local)",
    )
    parser.add_argument("--cert", default="cert.pem", help="Ruta al certificado TLS (PEM)")
    parser.add_argument("--key",  default="key.pem",  help="Ruta a la clave privada TLS (PEM)")
    args = parser.parse_args()
    _QUIET_MODE = args.quiet

    load_css()
    init_db(DB_PATH)

    server = ThreadingHTTPServer((args.host, args.port), AtlasHandler)

    if args.tls:
        import ssl
        cert_path = Path(args.cert)
        key_path  = Path(args.key)
        if not cert_path.exists() or not key_path.exists():
            # Generar certificado autofirmado con subprocess si openssl está disponible
            import subprocess
            print("  ⚠ TLS: generando certificado autofirmado (solo para desarrollo)...")
            try:
                subprocess.run(
                    [
                        "openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-keyout", str(key_path), "-out", str(cert_path),
                        "-days", "365", "-nodes", "-subj",
                        "/C=EC/ST=Pichincha/L=Quito/O=Atlas-Auddit/CN=localhost",
                    ],
                    check=True, capture_output=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"  ✗ No se pudo generar el certificado: {exc}")
                print("  Instale openssl o provea --cert y --key manualmente.")
                raise SystemExit(1)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"
        print("  ⚠  ADVERTENCIA: servidor HTTP sin TLS.")
        print("     En producción use --tls o coloque Atlas detrás de Nginx/Caddy con HTTPS.")

    print("")
    print(f"  ▲ Atlas · Auddit v2.0 — {scheme}://{args.host}:{args.port}")
    print(f"  Base de datos     : {DB_PATH}")
    print("  Credenciales demo : admin/admin123  ·  auditor/auditor123")
    if _QUIET_MODE:
        print("  Modo silencioso   : activo (peticiones HTTP no se imprimen)")
    print("  Ctrl+C para salir.")
    print("")
    server.serve_forever()
