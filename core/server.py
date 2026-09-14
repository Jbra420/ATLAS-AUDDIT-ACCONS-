"""
core/server.py — Atlas HTTP Server
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import sqlite3
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from database import (
    AUDIT_STATUSES,
    DB_PATH,
    DEMO_RUC,
    add_source,
    append_research_source_note,
    authenticate,
    compute_progress,
    connect,
    create_company_audit,
    create_session,
    create_user,
    destroy_session,
    get_audit,
    get_company_location,
    get_company_profile,
    get_financial_snapshot,
    get_research,
    init_db,
    list_admin_audits,
    list_administrators,
    list_auditor_audits,
    list_auditors,
    delete_user,
    list_economic_documents,
    list_shareholders,
    list_source_checks,
    list_sources,
    load_demo_if_ruc_matches,
    mark_document_reviewed,
    mark_document_pending,
    mark_source_checked,
    mark_matching_source_checked,
    mark_source_pending,
    register_audit_ruc,
    refresh_summary,
    seed_demo_radar,
    update_research,
    upsert_company_profile,
    upsert_company_location,
    upsert_financial_snapshot,
    user_from_session,
)
from services.ruc_validator import validate_ruc
from services.summary import extract_signals, generate_summary
from services.financial import compute_indicators
from ui.layout import layout, set_css
from ui.helpers import esc, form_value, _now
from core.router import GET_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
COOKIE_NAME = "atlas_session"
CSS_PATH = BASE_DIR / "static" / "atlas.css"
_CSS_CONTENT: str = ""
_QUIET_MODE: bool = False  # Se activa con --quiet; suprime el log de peticiones HTTP


def load_css() -> None:
    global _CSS_CONTENT
    try:
        _CSS_CONTENT = CSS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _CSS_CONTENT = "/* atlas.css no encontrado */"
    set_css(_CSS_CONTENT)

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

        if path == "/static/atlas.css":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-type", "text/css")
            self.end_headers()
            self.wfile.write(_CSS_CONTENT.encode("utf-8"))
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
                
            html_content = handler_fn(user, query, path)
            if html_content:
                self.send_html(html_content)
            return

        self.send_html(layout("No encontrado", self.current_user(), '<div class="error-msg">Ruta no encontrada.</div>'), 404)

    # ── POST routing ────────────────────────────────────────────────────

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        form = self.parse_post()

        if path == "/login":
            username = form_value(form, "username")
            password = form_value(form, "password")
            user = authenticate(username, password)
            if not user:
                # Login err is handled by redirect now or sending html
                self.redirect("/login?err=Usuario+o+clave+incorrecta.")
                return
            token = create_session(user["id"])
            cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={8 * 3600}"
            self.redirect("/admin" if user["role"] == "admin" else "/auditor", cookie=cookie)
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

        if path == "/admin/users/delete":
            admin = self.require_admin()
            if not admin:
                return
            try:
                target_user_id = int(form_value(form, "user_id", "0"))
                if target_user_id == admin["id"]:
                    raise ValueError("No puedes eliminar tu propio usuario activo.")
                msg = delete_user(target_user_id)
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
            data = {
                "commercial_name": form_value(form, "commercial_name"),
                "economic_activity": form_value(form, "economic_activity"),
                "legal_status": form_value(form, "legal_status"),
                "representative": form_value(form, "representative"),
                "address": form_value(form, "address"),
                "tax_obligations": form_value(form, "tax_obligations"),
                "public_contracting": form_value(form, "public_contracting"),
                "supercias_info": form_value(form, "supercias_info"),
                "sri_info": form_value(form, "sri_info"),
                "sercop_info": form_value(form, "sercop_info"),
                "observations": form_value(form, "observations"),
                "risk_flags": form_value(form, "risk_flags"),
                "pasted_text": form_value(form, "pasted_text"),
            }
            update_research(audit_id, current["id"], data, mark_ready=False)
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
                self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Evidencia+registrada&tab=fuentes")
            except Exception as exc:
                self.redirect(f"/auditor/radar?audit_id={audit_id}&err={quote_plus(str(exc))}&tab=fuentes")
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
                clean_ruc, validation_msg = register_audit_ruc(audit_id, ruc_input)
                loaded = load_demo_if_ruc_matches(audit_id, clean_ruc)
                if loaded:
                    msg = "RUC validado. Expediente de investigación inicializado con éxito."
                else:
                    msg = f"{validation_msg} Expediente listo para completar con fuentes oficiales."
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
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Fuente+actualizada&tab=fuentes")
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
                "activo_total": form_value(form, "activo_total"),
                "pasivo_total": form_value(form, "pasivo_total"),
                "patrimonio_neto": form_value(form, "patrimonio_neto"),
                "ingresos_401": form_value(form, "ingresos_401"),
                "otros_ingresos_403": form_value(form, "otros_ingresos_403"),
                "costo_ventas_501": form_value(form, "costo_ventas_501"),
                "gastos_502": form_value(form, "gastos_502"),
                "utilidad_neta_707": form_value(form, "utilidad_neta_707"),
            }
            upsert_financial_snapshot(audit_id, fin_data)
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Indicadores+guardados&tab=indicadores")
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
            profile_data = {
                k: form_value(form, k)
                for k in [
                    "ruc", "razon_social", "estado_contribuyente", "tipo_contribuyente",
                    "regimen", "categoria", "obligado_contabilidad", "agente_retencion",
                    "contribuyente_especial", "fecha_inicio_actividades", "fecha_actualizacion",
                    "actividad_economica", "representante_legal", "expediente_supercias",
                    "nacionalidad", "tipo_compania", "situacion_legal", "fecha_constitucion",
                    "plazo_social", "oficina_control", "objeto_social",
                ]
            }
            loc_data = {
                k: form_value(form, k)
                for k in ["provincia", "canton", "ciudad", "calle", "numero",
                          "interseccion", "barrio", "referencia"]
            }
            upsert_company_profile(audit_id, profile_data)
            upsert_company_location(audit_id, loc_data)
            tab = form_value(form, "return_tab", "sri")
            self.redirect(f"/auditor/radar?audit_id={audit_id}&msg=Datos+guardados&tab={tab}")
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
            research = get_research(audit_id)
            profile = get_company_profile(audit_id)
            location = get_company_location(audit_id)
            admins = list_administrators(audit_id)
            shareholders = list_shareholders(audit_id)
            snapshot = get_financial_snapshot(audit_id)
            source_checks = list_source_checks(audit_id)
            sources = list_sources(audit_id)
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
            )
            with connect() as conn:
                conn.execute(
                    "INSERT INTO research_notes (audit_id, updated_at) VALUES (?, ?) ON CONFLICT(audit_id) "
                    "DO UPDATE SET generated_summary=?, updated_at=?",
                    (audit_id, _now(), summary, _now()),
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
    args = parser.parse_args()
    _QUIET_MODE = args.quiet

    load_css()
    init_db(DB_PATH)

    server = ThreadingHTTPServer((args.host, args.port), AtlasHandler)
    print("")
    print(f"  ▲ Atlas · Auddit v2.0 (Redesign) — http://{args.host}:{args.port}")
    print(f"  Base de datos     : {DB_PATH}")
    print("  Credenciales demo : admin/admin123  ·  auditor/auditor123")
    if _QUIET_MODE:
        print("  Modo silencioso   : activo (peticiones HTTP no se imprimen)")
    print("  Ctrl+C para salir.")
    print("")
    server.serve_forever()
