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
from services.summary import generate_summary
from services.financial import CAMPOS_FINANCIEROS, compute_indicators
from services.company_search import build_source_map
from services.company_research import research_company_by_ruc
from services.dossier import build_dossier_model, build_dossier_text
from services.identificacion import validar_identificacion
from services.trazabilidad import validar_fecha_consulta
from ui.layout import layout, set_css
from ui.helpers import form_value, _now
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
            body = layout(
                "Acceso denegado", user,
                '<div class="error-msg">No tiene permisos de jefe auditor.</div>',
            )
            self.send_html(body, 403)
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


        if path == "/export/summary":
            current = self.require_user()
            if current:
                self.export_summary_txt(current, query)
            return

        if path == "/export/csv":
            current = self.require_user()
            if current:
                self.export_summary_csv(current, query)
            return

        if path == "/export/dossier":
            current = self.require_user()
            if current:
                self.export_dossier_txt(current, query)
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
        parsed = urlparse(self.path)
        path = parsed.path
        self._cached_form = self.parse_post()  # guardar para _reject_csrf
        form = self._cached_form

        if path == "/login":
            # /login no tiene sesión todavía → excluido de CSRF
            client_ip = self.client_address[0]
            if _LOGIN_LIMITER.is_blocked(client_ip):
                self.redirect("/login?err=Demasiados+intentos.+Espere+15+minutos+e+int%C3%A9ntelo+de+nuevo.")
                return
            username = form_value(form, "username")
            password = form_value(form, "password")
            user = authenticate(username, password)
            if not user:
                _LOGIN_LIMITER.record_failure(client_ip)
                self.redirect("/login?err=Usuario+o+clave+incorrecta.")
                return
            _LOGIN_LIMITER.record_success(client_ip)
            token = create_session(user["id"])

            cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={8 * 3600}"
            self.redirect("/admin" if user["role"] == "admin" else "/auditor", cookie=cookie)
            return

        # Toda ruta POST que no sea /login requiere CSRF válido
        if self._reject_csrf():
            return

        if path == "/admin/users":
            admin = self.require_admin()
            if not admin:
                return
            try:
                with connect() as conn:
                    create_user(
                        conn,
                        form_value(form, "username"),
                        form_value(form, "full_name"),
                        form_value(form, "role"),
                        form_value(form, "password"),
                    )
                self.redirect("/admin/users?msg=Usuario+creado+exitosamente")
            except Exception as exc:
                self.redirect(f"/admin/users?err={quote_plus(str(exc))}")
            return

        if path == "/admin/users/deactivate":
            admin = self.require_admin()
            if not admin:
                return
            try:
                target_user_id = int(form_value(form, "user_id", "0"))
                msg = deactivate_user(target_user_id, admin["id"])
                self.redirect(f"/admin/users?msg={quote_plus(msg)}")
            except Exception as exc:
                self.redirect(f"/admin/users?err={quote_plus(str(exc))}")
            return

        if path == "/admin/users/reactivate":
            admin = self.require_admin()
            if not admin:
                return
            try:
                target_user_id = int(form_value(form, "user_id", "0"))
                msg = reactivate_user(target_user_id, admin["id"])
                self.redirect(f"/admin/users?msg={quote_plus(msg)}")
            except Exception as exc:
                self.redirect(f"/admin/users?err={quote_plus(str(exc))}")
            return

        if path == "/admin/users/delete":
            admin = self.require_admin()
            if not admin:
                return
            try:
                target_user_id = int(form_value(form, "user_id", "0"))
                msg = soft_delete_user(
                    target_user_id,
                    admin["id"],
                    form_value(form, "deletion_reason"),
                )
                self.redirect(f"/admin/users?msg={quote_plus(msg)}")
            except Exception as exc:
                self.redirect(f"/admin/users?err={quote_plus(str(exc))}")
            return

        if path == "/admin/companies":
            admin = self.require_admin()
            if not admin:
                return
            try:
                create_company_audit(
                    form_value(form, "name"),
                    form_value(form, "ruc"),
                    form_value(form, "city"),
                    form_value(form, "activity_hint"),
                    form_value(form, "period"),
                    int(form_value(form, "assigned_auditor_id", "0")),
                    admin["id"],
                )
                self.redirect("/admin/companies?msg=Empresa+asignada+correctamente")
            except Exception as exc:
                self.redirect(f"/admin/companies?err={quote_plus(str(exc))}")
            return

        if path == "/admin/companies/reassign":
            admin = self.require_admin()
            if not admin:
                return
            try:
                audit_id = int(form_value(form, "audit_id", "0"))
                new_auditor_id = int(form_value(form, "new_auditor_id", "0"))
                reassign_audit(audit_id, new_auditor_id, admin["id"])
                self.redirect("/admin/companies?msg=Auditor+reasignado+correctamente")
            except Exception as exc:
                self.redirect(f"/admin/companies?err={quote_plus(str(exc))}")
            return

        if path == "/auditor/radar":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'),
                    403,
                )
                return
            # Solo actualizar los campos presentes (observaciones y banderas de riesgo)
            # patch_research hace UPDATE selectivo sin tocar el resto de la investigación
            fields = {}
            for key in ("observations", "risk_flags", "pasted_text"):
                val = form_value(form, key)
                if val:  # solo incluir si el campo fue enviado y tiene contenido
                    fields[key] = val
            # Si vienen campos de investigación completa (multi-campo), usamlos todos
            research_keys = (
                "commercial_name", "economic_activity", "legal_status",
                "representative", "address", "tax_obligations",
                "public_contracting", "supercias_info", "sri_info",
                "sercop_info", "pasted_text",
            )
            for key in research_keys:
                val = form_value(form, key)
                if val:
                    fields[key] = val
            patch_research(audit_id, current["id"], fields)
            msg = "Avance guardado correctamente"
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={quote_plus(msg)}&tab=resumen")
            return

        if path == "/auditor/source":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'),
                    403,
                )
                return
            try:
                source_type = form_value(form, "source_type")
                finding = form_value(form, "finding")
                evidence_text = form_value(form, "evidence_text")
                notes = form_value(form, "notes")
                composed_notes = "\n".join(
                    part for part in [
                        f"Hallazgo: {finding}" if finding else "",
                        f"Evidencia: {evidence_text}" if evidence_text else "",
                        f"Notas: {notes}" if notes else "",
                    ] if part
                )
                add_source(
                    audit_id,
                    form_value(form, "title"),
                    form_value(form, "url"),
                    source_type,
                    composed_notes,
                    current["id"],
                )
                mark_matching_source_checked(
                    audit_id,
                    source_type,
                    current["id"],
                    finding or notes or "Evidencia registrada",
                )
                append_research_source_note(audit_id, current["id"], source_type, finding, evidence_text)
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Evidencia+registrada&tab=documentos")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=documentos")
            return

        if path == "/auditor/radar/search":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            ruc_input = form_value(form, "search_ruc").strip()
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                _clean_ruc, validation_msg = register_audit_ruc(audit_id, ruc_input)
                msg = f"{validation_msg} Ahora puede iniciar la búsqueda automática."
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={quote_plus(msg)}&tab=sri")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=sri")
            return

        if path == "/auditor/radar/investigate":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            ruc_input = form_value(form, "search_ruc").strip()
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                clean_ruc, _validation_msg = register_audit_ruc(audit_id, ruc_input)
                outcome = research_company_by_ruc(audit_id, clean_ruc, current["id"])
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
                msg = (
                    f"Búsqueda completada: {sri_msg} {supercias_msg} {financial_msg} "
                    "Revise los resultados y edítelos si es necesario."
                )
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={quote_plus(msg)}&tab=sri")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=sri")
            return

        if path == "/auditor/radar/source-check":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            check_id = int(form_value(form, "check_id", "0"))
            accion = form_value(form, "accion", "consultar")
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            if accion == "revertir":
                mark_source_pending(check_id)
            else:
                obs = form_value(form, "observacion", "")
                mark_source_checked(check_id, current["id"], obs)
            tab = form_value(form, "return_tab", "sri")
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Fuente+actualizada&tab={tab}")
            return

        if path == "/auditor/radar/document":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            doc_id = int(form_value(form, "doc_id", "0"))
            accion = form_value(form, "accion", "revisar")
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            if accion == "revertir":
                mark_document_pending(doc_id)
            else:
                mark_document_reviewed(doc_id, current["id"])
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Documento+actualizado&tab=documentos")
            return

        if path == "/auditor/radar/financial":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            fin_data = {
                k: form_value(form, k) for k in (*CAMPOS_FINANCIEROS, "fecha_junta_aprobacion")
            }
            # Sin año fiscal explícito no se registran cifras (parámetro previo
            # obligatorio del requisito).
            anio = form_value(form, "anio_fiscal") or str(audit["anio_fiscal_eeff"] or "")
            try:
                if not anio:
                    raise ValueError("Registre primero el año fiscal de los estados financieros")
                upsert_financial_statement(
                    audit_id, anio, fin_data,
                    fecha_consulta=validar_fecha_consulta(form_value(form, "fecha_consulta")),
                    user_id=current["id"],
                )
            except ValueError as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=indicadores")
                return
            msg = quote_plus(f"Estados financieros {anio} guardados")
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={msg}&tab=indicadores")
            return

        if path == "/auditor/radar/alert-treatment":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            if not get_audit(audit_id, current):
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                register_alert_treatment(
                    audit_id, form_value(form, "codigo"), form_value(form, "observacion"),
                    user_id=current["id"],
                )
            except ValueError as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=resumen")
                return
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Tratamiento+registrado&tab=resumen")
            return

        if path == "/auditor/radar/financial-year":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            if not get_audit(audit_id, current):
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                anio = set_audit_fiscal_year(audit_id, form_value(form, "anio_fiscal"), user_id=current["id"])
            except ValueError as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=indicadores")
                return
            msg = quote_plus(f"Año fiscal {anio} registrado")
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={msg}&tab=indicadores")
            return

        if path == "/auditor/radar/profile":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            # Cada pestaña envía solo sus campos; los ausentes no se tocan.
            profile_data = {k: form_value(form, k) for k in PROFILE_FORM_FIELDS if k in form}
            loc_data = {k: form_value(form, k) for k in LOCATION_FORM_FIELDS if k in form}
            tab = quote_plus(form_value(form, "return_tab", "sri"))
            try:
                fecha_consulta = validar_fecha_consulta(form_value(form, "fecha_consulta"))
                if profile_data:
                    update_company_profile_fields(
                        audit_id, profile_data, user_id=current["id"], fecha_consulta=fecha_consulta,
                    )
                if loc_data:
                    update_company_location_fields(
                        audit_id, loc_data, user_id=current["id"], fecha_consulta=fecha_consulta,
                    )
            except ValueError as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab={tab}")
                return
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Datos+guardados&tab={tab}")
            return

        if path == "/auditor/radar/administrator":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            if not get_audit(audit_id, current):
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                action = form_value(form, "action", "add")
                if action == "delete":
                    delete_administrator(
                        audit_id,
                        int(form_value(form, "administrator_id", "0")),
                        user_id=current["id"],
                    )
                    msg = "Administrador eliminado"
                else:
                    fecha_consulta = validar_fecha_consulta(form_value(form, "fecha_consulta"))
                    tipo = form_value(form, "tipo_identificacion")
                    identificacion = form_value(form, "identificacion")
                    warning = validar_identificacion(identificacion, tipo, ("cedula", "pasaporte"))[2]
                    if action == "update":
                        update_administrator(
                            audit_id,
                            int(form_value(form, "administrator_id", "0")),
                            tipo_identificacion=tipo,
                            identificacion=identificacion,
                            nacionalidad=form_value(form, "nacionalidad"),
                            fecha_consulta=fecha_consulta,
                            user_id=current["id"],
                        )
                        msg = "Administrador actualizado"
                    else:
                        add_administrator(
                            audit_id,
                            identificacion,
                            form_value(form, "nombre"),
                            form_value(form, "nacionalidad"),
                            form_value(form, "cargo"),
                            tipo_identificacion=tipo,
                            fecha_consulta=fecha_consulta,
                            user_id=current["id"],
                        )
                        msg = "Administrador registrado"
                    if warning:
                        msg = f"{msg}. Advertencia: {warning}"
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={quote_plus(msg)}&tab=admins")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=admins")
            return

        if path == "/auditor/radar/shareholder":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            if not get_audit(audit_id, current):
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            try:
                action = form_value(form, "action", "add")
                if action == "delete":
                    delete_shareholder(
                        audit_id,
                        int(form_value(form, "shareholder_id", "0")),
                        user_id=current["id"],
                    )
                    msg = "Accionista eliminado"
                else:
                    fecha_consulta = validar_fecha_consulta(form_value(form, "fecha_consulta"))
                    tipo = form_value(form, "tipo_identificacion")
                    identificacion = form_value(form, "identificacion")
                    warning = validar_identificacion(identificacion, tipo, ("cedula", "ruc", "pasaporte"))[2]
                    details = {
                        "tipo_identificacion": tipo,
                        "participacion_porcentaje": form_value(form, "participacion_porcentaje"),
                        "capital": form_value(form, "capital"),
                        "beneficiario_final": form_value(form, "beneficiario_final"),
                        "fecha_consulta": fecha_consulta,
                        "user_id": current["id"],
                    }
                    if action == "update":
                        update_shareholder(
                            audit_id,
                            int(form_value(form, "shareholder_id", "0")),
                            identificacion=identificacion,
                            **details,
                        )
                        msg = "Accionista actualizado"
                    else:
                        add_shareholder(
                            audit_id,
                            form_value(form, "numero"),
                            identificacion,
                            form_value(form, "nombre"),
                            **details,
                        )
                        msg = "Accionista registrado"
                    if warning:
                        msg = f"{msg}. Advertencia: {warning}"
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg={quote_plus(msg)}&tab=accionistas")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=accionistas")
            return

        if path == "/auditor/radar/summary":
            current = self.require_auditor()
            if not current:
                return
            audit_id = int(form_value(form, "audit_id", "0"))
            audit = get_audit(audit_id, current)
            if not audit:
                self.send_html(
                    layout("Acceso denegado", current,
                           '<div class="error-msg">Auditoría no disponible.</div>'), 403,
                )
                return
            ctx = get_audit_context(audit_id)
            research = ctx["research"]
            profile, location = ctx["profile"], ctx["location"]
            admins, shareholders = ctx["admins"], ctx["shareholders"]
            docs = ctx["docs"]
            snapshot = ctx["snapshot"]
            source_checks, sources = ctx["source_checks"], ctx["sources"]
            source_map = build_source_map(
                audit,
                research,
                profile,
                location,
                admins,
                shareholders,
                docs,
                snapshot,
                source_checks,
                sources,
                alert_treatments=ctx["alert_treatments"],
            )
            readiness = source_map["readiness"]
            if not readiness["ready"]:
                labels = [item["label"] for item in readiness["blockers"]]
                preview = ", ".join(labels[:3])
                remaining = len(labels) - 3
                if remaining > 0:
                    preview += f" y {remaining} requisito(s) más"
                message = f"No se puede generar el resumen. Complete: {preview}."
                self.redirect(
                    f"/auditor/radar?audit_id={audit_id}&err={quote_plus(message)}&tab=resumen"
                )
                return
            source_count = len(sources)
            indicators = compute_indicators(snapshot)
            data = {
                "commercial_name": research["commercial_name"] or "",
                "economic_activity": research["economic_activity"] or "",
                "legal_status": research["legal_status"] or "",
                "representative": research["representative"] or "",
                "address": research["address"] or "",
                "tax_obligations": research["tax_obligations"] or "",
                "public_contracting": research["public_contracting"] or "",
                "supercias_info": research["supercias_info"] or "",
                "sri_info": research["sri_info"] or "",
                "sercop_info": research["sercop_info"] or "",
                "observations": research["observations"] or "",
                "risk_flags": research["risk_flags"] or "",
                "pasted_text": research["pasted_text"] or "",
            }
            summary = generate_summary(
                audit, data, source_count,
                profile=profile, location=location,
                admins=admins, shareholders=shareholders,
                snapshot=snapshot, indicators=indicators,
                source_checks=source_checks,
                sources=sources,
                alert_treatments=ctx["alert_treatments"],
                provenance=ctx["provenance"],
            )
            with connect() as conn:
                conn.execute(
                    "INSERT INTO research_notes (audit_id, generated_summary, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(audit_id) "
                    "DO UPDATE SET generated_summary=?, updated_at=?",
                    (audit_id, summary, _now(), summary, _now()),
                )
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Resumen+generado&tab=resumen")
            return

        self.send_html(
            layout("No encontrado", self.current_user(),
                   '<div class="error-msg">Ruta POST no encontrada.</div>'),
            404,
        )

    # ── Export endpoints ──────────────────────────────────────────────────

    def export_summary_txt(self, user: sqlite3.Row, query: dict) -> None:
        audit_id = int(form_value(query, "audit_id", "0"))
        audit = get_audit(audit_id, user)
        if not audit:
            self.send_html(layout("Acceso denegado", user, '<div class="error-msg">No disponible.</div>'), 403)
            return
        research = get_research(audit_id)
        summary = refresh_summary(audit_id) if research["generated_summary"] else "No existe resumen generado."
        safe_name = "".join(
            ch for ch in audit["company_name"].lower().replace(" ", "_") if ch.isalnum() or ch == "_"
        )[:40]
        filename = f"atlas_resumen_{safe_name}_{audit['period']}.txt"
        self.send_download(summary, filename)

    def export_summary_csv(self, user: sqlite3.Row, query: dict) -> None:
        audit_id = int(form_value(query, "audit_id", "0"))
        audit = get_audit(audit_id, user)
        if not audit:
            self.send_html(layout("Acceso denegado", user, '<div class="error-msg">No disponible.</div>'), 403)
            return
        research = get_research(audit_id)
        out = io.StringIO()
        writer = csv.writer(out, quoting=csv.QUOTE_ALL)
        writer.writerow(["Campo", "Valor"])
        fields = [
            ("Razón social", audit["company_name"]),
            ("RUC", audit["ruc"] or ""),
            ("Período", audit["period"]),
            ("Ciudad", audit["city"] or ""),
            ("Estado auditoría", audit["status"]),
            ("Nombre comercial", research["commercial_name"] or ""),
            ("Actividad económica", research["economic_activity"] or ""),
            ("Estado societario", research["legal_status"] or ""),
            ("Representante legal", research["representative"] or ""),
            ("Dirección", research["address"] or ""),
            ("Obligaciones tributarias", research["tax_obligations"] or ""),
            ("Contratación pública", research["public_contracting"] or ""),
            ("Info Supercias", research["supercias_info"] or ""),
            ("Info SRI", research["sri_info"] or ""),
            ("Info SERCOP", research["sercop_info"] or ""),
            ("Observaciones", research["observations"] or ""),
            ("Riesgos identificados", research["risk_flags"] or ""),
            ("Resumen generado", research["generated_summary"] or ""),
        ]
        for label, value in fields:
            writer.writerow([label, value])
        safe_name = "".join(
            ch for ch in audit["company_name"].lower().replace(" ", "_") if ch.isalnum() or ch == "_"
        )[:40]
        filename = f"atlas_ficha_{safe_name}_{audit['period']}.csv"
        self.send_download(out.getvalue(), filename, content_type="text/csv; charset=utf-8")

    def export_dossier_txt(self, user: sqlite3.Row, query: dict) -> None:
        audit_id = int(form_value(query, "audit_id", "0"))
        audit = get_audit(audit_id, user)
        if not audit:
            self.send_html(layout("Acceso denegado", user, '<div class="error-msg">No disponible.</div>'), 403)
            return
        ctx = get_audit_context(audit_id)
        research = ctx["research"]
        profile, location = ctx["profile"], ctx["location"]
        admins, shareholders = ctx["admins"], ctx["shareholders"]
        docs, snapshot = ctx["docs"], ctx["snapshot"]
        source_checks, sources = ctx["source_checks"], ctx["sources"]
        indicators = compute_indicators(dict(snapshot) if snapshot else None)
        source_map = build_source_map(
            audit, research, profile, location, admins, shareholders,
            docs, snapshot, source_checks, sources,
            alert_treatments=ctx["alert_treatments"],
        )
        dossier = build_dossier_model(
            audit, research, profile, location, admins, shareholders,
            docs, snapshot, indicators, source_map, sources,
        )
        safe_name = "".join(
            ch for ch in audit["company_name"].lower().replace(" ", "_") if ch.isalnum() or ch == "_"
        )[:40]
        filename = f"atlas_ficha_final_{safe_name}_{audit['period']}.txt"
        self.send_download(build_dossier_text(dossier), filename)


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
