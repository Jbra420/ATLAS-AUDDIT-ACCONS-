"""
core/server.py — Servidor HTTP de Atlas: sesión, CSRF, roles, archivos
estáticos y despacho a las tablas de rutas de core/router.py.
"""
from __future__ import annotations

import argparse
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
    authenticate,
    create_session,
    destroy_session,
    get_audit,
    get_csrf_token,
    init_db,
    user_from_session,
    validate_csrf_token,
)
from ui.layout import layout, set_css
from ui.helpers import form_value
from core.router import ADMIN_POSTS, EXPORTS, GET_ROUTES, RADAR_POSTS, radar_url

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
