"""
tests/test_http.py — Pruebas de integración HTTP para Atlas · Auddit.

Conecta a un servidor Atlas ya en ejecución en el puerto ATLAS_HTTP_TEST_PORT.
Si el servidor no está disponible, los tests se saltean automáticamente.

Escriben en la base (crean empresas y registros de prueba), así que solo se
ejecutan con ATLAS_HTTP_TEST_PORT y con ATLAS_DB_PATH apuntando a una base
desechable, distinta de auddit.db. El servidor y los tests deben usar la misma
base:
    1. export ATLAS_DB_PATH=/tmp/atlas_http.db
    2. Iniciar el servidor: python3 app.py --port 8799
    3. Correr los tests: ATLAS_HTTP_TEST_PORT=8799 python3 -m unittest tests.test_http -v

No dependen de las cuentas de la base: crean (si faltan) un jefe auditor y
un auditor propios de prueba, http_admin y http_auditor.

Los tests verifican:
  - Rutas públicas responden correctamente.
  - El flujo completo login → cookie → dashboard retorna 200 OK.
  - El auditor accede a /auditor y recibe 403 en /admin.
  - HC-1: el admin recibe 403 al intentar rutas POST del auditor.
  - Las rutas inexistentes retornan 404.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import unittest
from datetime import date, timedelta
from urllib.request import urlopen, Request
from urllib.parse import unquote_plus, urlencode
from urllib.error import HTTPError, URLError

from pathlib import Path

from database import BASE_DIR, connect, create_company_audit, create_user

SERVER_HOST = "127.0.0.1"
SERVER_PORT = int(os.environ.get("ATLAS_HTTP_TEST_PORT", "0"))
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


# Cuentas propias de los tests: la base real ya no trae usuarios demo y el
# jefe auditor cambia su contraseña.
ADMIN = ("http_admin", "http-admin-123")
AUDITOR = ("http_auditor", "http-auditor-123")


def _asegurar_usuarios_de_prueba() -> None:
    with connect() as conn:
        for (username, password), role in ((ADMIN, "admin"), (AUDITOR, "auditor")):
            if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                create_user(conn, username, f"Prueba HTTP {role}", role, password)


def _base_desechable() -> bool:
    """True solo si ATLAS_DB_PATH apunta a una base distinta de auddit.db."""
    ruta = os.environ.get("ATLAS_DB_PATH")
    return bool(ruta) and Path(ruta).resolve() != (BASE_DIR / "auddit.db").resolve()


def _server_available() -> bool:
    """Verifica si el servidor Atlas está disponible en el puerto configurado."""
    if not SERVER_PORT or not _base_desechable():
        return False
    try:
        with socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=1):
            pass
    except OSError:
        return False
    _asegurar_usuarios_de_prueba()
    return True


def _get(path: str, cookie: str = "") -> tuple[int, str]:
    """GET al path indicado. Retorna (status_code, body)."""
    headers = {"Cookie": cookie} if cookie else {}
    req = Request(BASE_URL + path, headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.code, ""
    except URLError:
        return 0, ""


def _post_raw(path: str, data: dict, cookie: str = "") -> tuple[int, str, str]:
    """
    POST al path sin seguir redirects. Retorna (status, body, Set-Cookie).
    Usa urllib que sigue redirects por defecto, así que para POST/303 usamos
    http.client directamente.
    """
    import http.client
    body = urlencode(data).encode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
    }
    if cookie:
        headers["Cookie"] = cookie
    conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    status = resp.status
    set_cookie = resp.getheader("Set-Cookie", "")
    location = resp.getheader("Location", "")
    # Para redirects (303), el body es vacío; no leer si es redirect
    if status in (301, 302, 303):
        resp.close()
        conn.close()
        return status, location, set_cookie
    try:
        body_str = resp.read().decode("utf-8", errors="replace")
    except Exception:
        body_str = ""
    conn.close()
    return status, body_str, set_cookie


def _login(username: str, password: str) -> str:
    """
    Hace POST /login y retorna la cookie de sesión.
    Retorna '' si el login falla.
    """
    status, _, set_cookie = _post_raw("/login", {"username": username, "password": password})
    if status == 303 and "atlas_session=" in set_cookie:
        token_part = [p for p in set_cookie.split(";") if "atlas_session=" in p][0]
        return token_part.strip()
    return ""


def _csrf_token(path: str, cookie: str) -> str:
    status, body = _get(path, cookie)
    if status != 200:
        return ""
    match = re.search(r'name="_csrf" value="([^"]+)"', body)
    return match.group(1) if match else ""


_SKIP_REASON = "Defina ATLAS_HTTP_TEST_PORT y ATLAS_DB_PATH (base desechable) y levante el servidor (ver docstring)"


# ── Casos de prueba ───────────────────────────────────────────────────────────

@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPPublicRoutes(unittest.TestCase):
    """Rutas públicas accesibles sin autenticación."""

    def test_login_page_returns_200(self):
        status, body = _get("/login")
        self.assertEqual(status, 200, "GET /login debe retornar 200 OK")
        for credencial in ("admin123", "auditor123", "Demo:"):
            self.assertNotIn(credencial, body, "El login no muestra usuarios ni contraseñas")

    def test_root_without_auth_redirects(self):
        """Sin cookie → redirect (3xx) a /login."""
        import http.client
        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        status = resp.status
        resp.close(); conn.close()
        self.assertIn(status, (301, 302, 303),
                      f"GET / sin autenticación debe redirigir, obtuvo {status}")

    def test_unknown_route_returns_404(self):
        """Ruta inexistente → 404."""
        cookie = _login(*AUDITOR)
        status, _ = _get("/ruta-que-no-existe-xyz", cookie)
        self.assertEqual(status, 404)

    def test_wrong_password_does_not_set_cookie(self):
        """Credenciales incorrectas no deben establecer sesión."""
        cookie = _login(AUDITOR[0], "clave_totalmente_incorrecta")
        self.assertEqual(cookie, "",
                         "Login fallido no debe devolver cookie de sesión")

    def test_static_brand_assets_have_mime_and_content_length(self):
        """Los logos deben servirse completos desde /static con MIME correcto."""
        import http.client

        for path in ("/static/atlas_logo.jpg", "/static/logoauddit.jpeg"):
            with self.subTest(path=path):
                conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
                conn.request("GET", path)
                resp = conn.getresponse()
                body = resp.read()
                content_type = resp.getheader("Content-Type", "")
                content_length = resp.getheader("Content-Length")
                conn.close()

                self.assertEqual(resp.status, 200)
                self.assertIn("image/jpeg", content_type)
                self.assertEqual(int(content_length), len(body))
                self.assertGreater(len(body), 0)

    def test_static_route_rejects_directory_traversal(self):
        """Una ruta /static/../ no puede leer archivos fuera del directorio público."""
        import http.client

        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("GET", "/static/../README.md")
        resp = conn.getresponse()
        resp.read()
        conn.close()

        self.assertEqual(resp.status, 404)


@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPAuditorFlow(unittest.TestCase):
    """Flujo completo del auditor: login → dashboard → aislamiento de roles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cookie = _login(*AUDITOR)

    def test_auditor_login_sets_cookie(self):
        """Login exitoso del auditor debe retornar cookie de sesión."""
        self.assertIn("atlas_session=", self.cookie,
                      "Login de auditor debe establecer cookie atlas_session")

    def test_auditor_dashboard_returns_200(self):
        """Panel del auditor debe responder 200 OK con sesión válida."""
        status, _ = _get("/auditor", self.cookie)
        self.assertEqual(status, 200, "GET /auditor (autenticado) debe retornar 200")

    def test_auditor_cannot_access_admin_dashboard(self):
        """HC-1: el auditor NO debe poder acceder al panel del jefe auditor."""
        status, _ = _get("/admin", self.cookie)
        self.assertEqual(status, 403,
                         "El auditor no debe tener acceso a /admin (debe ser 403)")

    def test_unauthenticated_request_redirects(self):
        """Sin cookie → redirect a /login."""
        import http.client
        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("GET", "/auditor")
        resp = conn.getresponse()
        status = resp.status
        resp.close(); conn.close()
        self.assertIn(status, (301, 302, 303))

    def test_incomplete_audit_cannot_generate_summary_by_direct_post(self):
        """El servidor debe rechazar la generación aunque se omita el botón de la interfaz."""
        with connect() as conn:
            auditor_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_auditor'"
            ).fetchone()["id"]
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_admin'"
            ).fetchone()["id"]

        audit_id = create_company_audit(
            f"Empresa incompleta HTTP {time.time_ns()}", "0190314014001", "Cuenca",
            "Comercio", "2026", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute(
                "SELECT company_id FROM audits WHERE id = ?", (audit_id,)
            ).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)

        csrf_token = _csrf_token(
            f"/auditor/radar?audit_id={audit_id}&tab=resumen", self.cookie,
        )
        self.assertTrue(csrf_token, "La página del expediente debe incluir un token CSRF")

        status, location, _ = _post_raw(
            "/auditor/radar/summary",
            {"audit_id": str(audit_id), "_csrf": csrf_token},
            self.cookie,
        )

        self.assertEqual(status, 303)
        self.assertIn("err=No+se+puede+generar+el+resumen", location)
        self.assertIn("tab=resumen", location)

    @staticmethod
    def _pane_content(html: str, tab_id: str, next_tab_id: str) -> str:
        """Extrae el HTML del pane de un tab, delimitado por el siguiente tab
        en el orden de renderizado (ver lista `tabs` en radar/page.py)."""
        match = re.search(rf'id="tab-{tab_id}"(.*?)id="tab-{next_tab_id}"', html, re.S)
        return match.group(1) if match else ""

    def test_certificate_pdf_upload_review_and_import(self):
        """La nómina, adjuntada en Administradores en dos PDF (administradores y
        accionistas por separado), se propone en una sola revisión; el auditor
        importa ambas nóminas en un paso. Las filas quedan con la fuente del
        certificado y cada PDF como evidencia."""
        import http.client
        from tests.test_certificados import _pdf_con_texto

        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        ruc = "1790000001001"
        audit_id = create_company_audit(
            f"Empresa certificado {time.time_ns()}", ruc, "Cuenca", "", "2025", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute("SELECT company_id FROM audits WHERE id = ?", (audit_id,)).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)

        page = f"/auditor/radar?audit_id={audit_id}&tab=admins"
        pdfs = {
            "administradores.pdf": _pdf_con_texto([
                f"RUC: {ruc}",
                "ADMINISTRADORES",
                "0102030400 TORRES VEGA ANA ECUADOR GERENTE GENERAL",
                "0912345675 PEREZ LUIS ECUADOR PRESIDENTE",
            ]),
            "accionistas.pdf": _pdf_con_texto([f"RUC: {ruc}", "ACCIONISTAS", "0102030400 TORRES VEGA ANA ECUADOR 800,00"]),
        }

        def subir(archivos: dict[str, bytes]) -> str:
            limite = "----atlastest"
            campos = {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), "return_tab": "admins"}
            cuerpo = b"".join(
                f"--{limite}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                for k, v in campos.items()
            ) + b"".join(
                (f"--{limite}\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"{nombre}\"\r\n"
                 "Content-Type: application/pdf\r\n\r\n").encode() + pdf + b"\r\n"
                for nombre, pdf in archivos.items()
            ) + f"--{limite}--\r\n".encode()
            conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
            conn.request("POST", "/auditor/radar/certificado", body=cuerpo, headers={
                "Content-Type": f"multipart/form-data; boundary={limite}", "Cookie": self.cookie,
            })
            resp = conn.getresponse()
            self.assertEqual(resp.status, 303)
            return unquote_plus(resp.getheader("Location", ""))

        # Un PDF de otra compañía se rechaza sin guardar evidencia ni propuesta.
        ajeno = _pdf_con_texto(["RUC: 0190444619001", "ACCIONISTAS", "0102030400 TORRES VEGA ANA ECUADOR 800,00"])
        location = subir({"otra_empresa.pdf": ajeno})
        self.assertIn("err=otra_empresa.pdf: El documento corresponde al RUC 0190444619001", location)
        with connect() as conn:
            guardado = conn.execute(
                "SELECT (SELECT COUNT(*) FROM sources WHERE audit_id = ? AND title LIKE 'Certificado%') "
                "+ (SELECT COUNT(*) FROM nomina_imports WHERE audit_id = ?)", (audit_id, audit_id),
            ).fetchone()[0]
        self.assertEqual(guardado, 0)

        location = subir(pdfs)
        self.assertIn("2 certificados registrados", location)
        self.assertIn("2 administrador", location)
        self.assertIn("1 accionista", location)

        _, body = _get(page, self.cookie)
        admins_pane = self._pane_content(body, "admins", "accionistas")
        accionistas_pane = self._pane_content(body, "accionistas", "indicadores")
        self.assertIn("Certificado registrado como evidencia", admins_pane)
        import_id = re.search(r'name="import_id" value="(\d+)"', admins_pane).group(1)
        self.assertIn(f'name="import_id" value="{import_id}"', accionistas_pane,
                      "La misma revisión aparece en la pestaña Accionistas")

        # Se importa el gerente (sin el presidente) y el accionista, en un solo envío.
        status, location, _ = _post_raw("/auditor/radar/certificado/importar", {
            "audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), "import_id": import_id,
            "return_tab": "admins", "fecha_consulta": "2026-09-01",
            "incluir_administradores": "0", "incluir_accionistas": "0",
            "administradores_identificacion_0": "0102030400", "administradores_nombre_0": "TORRES VEGA ANA",
            "administradores_cargo_0": "GERENTE GENERAL", "administradores_nacionalidad_0": "ECUADOR",
            "accionistas_identificacion_0": "0102030400", "accionistas_nombre_0": "TORRES VEGA ANA",
            "accionistas_capital_0": "800", "accionistas_participacion_porcentaje_0": "100",
        }, self.cookie)
        self.assertEqual(status, 303)
        self.assertIn("Importados+1+administrador", location)
        self.assertIn("1+accionista", location)
        with connect() as conn:
            admins = list(conn.execute(
                "SELECT nombre, cargo, fuente FROM company_administrators WHERE audit_id = ?", (audit_id,)))
            socios = list(conn.execute(
                "SELECT nombre, participacion_porcentaje, fuente FROM company_shareholders WHERE audit_id = ?",
                (audit_id,)))
        fuente = "Supercias — certificado de nómina (PDF adjunto)"
        self.assertEqual([(r["nombre"], r["cargo"], r["fuente"]) for r in admins],
                         [("TORRES VEGA ANA", "GERENTE GENERAL", fuente)])
        self.assertEqual([(r["nombre"], r["participacion_porcentaje"], r["fuente"]) for r in socios],
                         [("TORRES VEGA ANA", 100.0, fuente)])

    def test_complete_audit_via_ui_forms_can_generate_summary(self):
        """Flujo feliz completo del levantamiento de información, exclusivamente
        vía las rutas y controles que la UI expone: marcar fuentes consultadas,
        completar los bloques 1 a 6, revisar validaciones y generar el resumen.
        También comprueba que una alerta crítica bloquea el resumen hasta que
        el auditor registra su tratamiento. Usa un RUC ficticio para no escribir
        ejercicios financieros de un cliente real; el expediente se crea y
        limpia en la propia prueba."""
        ruc = "0999999999001"
        with connect() as conn:
            auditor_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_auditor'"
            ).fetchone()["id"]
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_admin'"
            ).fetchone()["id"]
            if conn.execute("SELECT COUNT(*) FROM financial_statements WHERE ruc = ?", (ruc,)).fetchone()[0]:
                self.skipTest("La base local ya tiene ejercicios del RUC de prueba")

        audit_id = create_company_audit(
            f"Empresa flujo HTTP {time.time_ns()}", ruc, "Cuenca",
            "Servicios de alojamiento", "2026", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute(
                "SELECT company_id FROM audits WHERE id = ?", (audit_id,)
            ).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM financial_statements WHERE ruc = ?", (ruc,))
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)
        page = f"/auditor/radar?audit_id={audit_id}&tab=resumen"

        def post(path: str, fields: dict) -> str:
            status, location, _ = _post_raw(
                path, {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), **fields}, self.cookie,
            )
            self.assertEqual(status, 303, path)
            self.assertNotIn("err=", location, f"{path} rechazó {fields}: {location}")
            return location

        # Fuentes guiadas SRI y Supercias marcadas desde sus pestañas.
        _, body = _get(f"/auditor/radar?audit_id={audit_id}&tab=sri", self.cookie)
        sri_pane = self._pane_content(body, "sri", "supercias")
        sup_pane = self._pane_content(body, "supercias", "ubicacion")
        sri_match = re.search(r'action="/auditor/radar/source-check".*?check_id" value="(\d+)"', sri_pane, re.S)
        sup_match = re.search(r'action="/auditor/radar/source-check".*?check_id" value="(\d+)"', sup_pane, re.S)
        self.assertIsNotNone(sri_match, "El tab SRI debe tener un control para marcar la fuente como consultada")
        self.assertIsNotNone(sup_match, "El tab Supercias debe tener un control para marcar la fuente como consultada")
        for check_id, tab in ((sri_match.group(1), "sri"), (sup_match.group(1), "supercias")):
            post("/auditor/radar/source-check", {
                "check_id": check_id, "accion": "consultar", "return_tab": tab,
                "observacion": "Verificado en prueba",
            })

        _, resumen_body = _get(page, self.cookie)
        self.assertIn("openModal('summaryPendingModal')", resumen_body,
                      "Con datos pendientes, generar el resumen abre el modal de pendientes")
        self.assertNotIn("Validaciones cruzadas y alertas", resumen_body)
        self.assertNotIn("Resumen bloqueado", resumen_body)

        # Sin confirmar, un POST directo sigue rechazado; confirmado, se genera
        # y el resumen deja constancia de lo pendiente.
        status, location, _ = _post_raw(
            "/auditor/radar/summary", {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie)},
            self.cookie,
        )
        self.assertIn("err=", location)
        location = post("/auditor/radar/summary", {"confirmar_pendientes": "1"})
        self.assertIn("pendiente", unquote_plus(location))
        with connect() as conn:
            borrador = conn.execute(
                "SELECT generated_summary FROM research_notes WHERE audit_id = ?", (audit_id,)
            ).fetchone()["generated_summary"]
        self.assertIn("Obligatorio:", borrador)

        # Bloques 1 a 6 del levantamiento.
        post("/auditor/radar/profile", {
            "return_tab": "sri", "razon_social_sri": "EMPRESA PRUEBA CIA. LTDA",
            "estado_contribuyente": "ACTIVO", "tipo_contribuyente": "SOCIEDAD", "regimen": "GENERAL",
            "agente_retencion": "SI", "fecha_inicio_actividades": "2011-08-24",
            "representante_legal_sri": "TORRES ANA", "contribuyente_fantasma": "NO",
            "transacciones_inexistentes": "NO", "fecha_consulta": "2026-09-01",
        })
        post("/auditor/radar/profile", {
            "return_tab": "supercias", "razon_social_supercias": "EMPRESA PRUEBA CIA. LTDA.",
            "expediente_supercias": "141528", "fecha_constitucion": "2011-08-24",
            "tipo_compania": "RESPONSABILIDAD LIMITADA", "situacion_legal": "ACTIVA",
            "objeto_social": "Servicios de alojamiento", "fecha_consulta": "2026-09-01",
        })
        post("/auditor/radar/profile", {
            "return_tab": "ubicacion", "provincia": "AZUAY", "ciudad": "CUENCA",
            "calle": "AV. DEL ESTADIO", "numero": "S/N", "interseccion": "FLORENCIA ASTUDILLO",
            "fecha_consulta": "2026-09-01",
        })
        for nombre, cargo in (("Ana Torres", "Gerente General"), ("Luis Perez", "Presidente")):
            post("/auditor/radar/administrator", {
                "action": "add", "nombre": nombre, "cargo": cargo, "tipo_identificacion": "cedula",
                "identificacion": "0102030400", "fecha_consulta": "2026-09-01",
            })
        post("/auditor/radar/shareholder", {
            "action": "add", "nombre": "Ana Torres", "tipo_identificacion": "cedula",
            "identificacion": "0102030400", "participacion_porcentaje": "100", "fecha_consulta": "2026-09-01",
        })
        post("/auditor/radar/financial-year", {"anio_fiscal": "2025"})
        post("/auditor/radar/financial", {
            "anio_fiscal": "2025", "activo_total": "100", "pasivo_total": "60", "patrimonio_neto": "40",
            "ingresos_401": "50", "otros_ingresos_403": "0", "costo_ventas_501": "20", "gastos_502": "25",
            "utilidad_neta_707": "3", "fecha_consulta": "2026-09-01",
        })

        _, resumen_body = _get(page, self.cookie)
        self.assertIn('action="/auditor/radar/summary"', resumen_body)
        self.assertNotIn("Obligatorios pendientes", resumen_body, "Solo quedan recomendaciones")

        # Una alerta crítica bloquea el resumen hasta registrar su tratamiento.
        post("/auditor/radar/profile", {"return_tab": "sri", "contribuyente_fantasma": "SI"})
        _, body = _get(page, self.cookie)
        self.assertIn('action="/auditor/radar/alert-treatment"', self._pane_content(body, "sri", "supercias"),
                      "La alerta crítica y su tratamiento se registran en la pestaña SRI")
        status, location, _ = _post_raw(
            "/auditor/radar/summary", {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie)},
            self.cookie,
        )
        self.assertEqual(status, 303)
        self.assertIn("err=", location)
        self.assertIn("Tratamiento", unquote_plus(location))
        post("/auditor/radar/alert-treatment", {
            "codigo": "ALERTA_FANTASMA",
            "observacion": "Se solicitará al cliente la resolución del SRI y se evaluará continuidad.",
        })

        location = post("/auditor/radar/summary", {})
        self.assertIn("msg=Resumen+generado", location)
        with connect() as conn:
            summary = conn.execute(
                "SELECT generated_summary FROM research_notes WHERE audit_id = ?", (audit_id,)
            ).fetchone()["generated_summary"]
        self.assertIn("Tratamiento del auditor: Se solicitará al cliente", summary)
        self.assertIn("Razón social SRI = Supercias: coincide", summary)

    def test_administrator_capture_validates_and_records_source(self):
        """Fase 3: la identificación se valida en el servidor, la fecha de
        consulta no puede ser futura y cada cambio queda en la trazabilidad."""
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        audit_id = create_company_audit(
            f"Empresa trazabilidad {time.time_ns()}", "", "Cuenca", "", "2025", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute("SELECT company_id FROM audits WHERE id = ?", (audit_id,)).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)
        page = f"/auditor/radar?audit_id={audit_id}&tab=admins"

        def post(fields: dict) -> str:
            status, location, _ = _post_raw(
                "/auditor/radar/administrator",
                {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), **fields},
                self.cookie,
            )
            self.assertEqual(status, 303)
            return location

        base = {"action": "add", "nombre": "Ana Torres", "cargo": "Presidente", "tipo_identificacion": "cedula"}
        self.assertIn("err=", post({**base, "identificacion": "12345"}))
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertIn("err=", post({**base, "identificacion": "0102030400", "fecha_consulta": tomorrow}))
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM company_administrators WHERE audit_id = ?", (audit_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0, "Una entrada rechazada no debe guardarse")

        self.assertIn("msg=", post({**base, "identificacion": "", "fecha_consulta": "2026-09-01"}))
        with connect() as conn:
            admin = conn.execute(
                "SELECT * FROM company_administrators WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        self.assertIn("msg=", post({
            "action": "update", "administrator_id": str(admin["id"]), "tipo_identificacion": "cedula",
            "identificacion": "0102030400", "nacionalidad": "Ecuatoriana", "fecha_consulta": "2026-09-02",
        }))
        with connect() as conn:
            admin = conn.execute("SELECT * FROM company_administrators WHERE id = ?", (admin["id"],)).fetchone()
            history = conn.execute(
                "SELECT registrado_por, fecha_consulta FROM data_provenance WHERE audit_id = ? ORDER BY id",
                (audit_id,),
            ).fetchall()
        self.assertEqual(admin["identificacion"], "0102030400")
        self.assertEqual([h["fecha_consulta"] for h in history], ["2026-09-01", "2026-09-02"])
        self.assertTrue(all(h["registrado_por"] == auditor_id for h in history))

    def test_financial_statements_require_fiscal_year_and_keep_each_year(self):
        """Fase 4: sin año fiscal no se registran cifras; cada ejercicio se
        guarda aparte y no se sobrescribe."""
        ruc = "0190314014001"
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
            previous = conn.execute(
                "SELECT COUNT(*) FROM financial_statements WHERE ruc = ?", (ruc,)
            ).fetchone()[0]
        if previous:
            self.skipTest("La base local ya tiene ejercicios de este RUC; la prueba no los modifica")
        audit_id = create_company_audit(
            f"Empresa financiero {time.time_ns()}", ruc, "Cuenca", "", "2025", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute("SELECT company_id FROM audits WHERE id = ?", (audit_id,)).fetchone()["company_id"]

        def cleanup() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM financial_statements WHERE ruc = ?", (ruc,))
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup)
        page = f"/auditor/radar?audit_id={audit_id}&tab=indicadores"

        def post(path: str, fields: dict) -> str:
            status, location, _ = _post_raw(
                path, {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), **fields}, self.cookie,
            )
            self.assertEqual(status, 303)
            return location

        self.assertIn("err=", post("/auditor/radar/financial", {"activo_total": "100"}))
        self.assertIn("err=", post("/auditor/radar/financial-year", {"anio_fiscal": str(date.today().year + 1)}))
        self.assertIn("msg=", post("/auditor/radar/financial-year", {"anio_fiscal": "2025"}))
        self.assertIn("msg=", post("/auditor/radar/financial", {"anio_fiscal": "2025", "activo_total": "110"}))
        self.assertIn("msg=", post("/auditor/radar/financial", {"anio_fiscal": "2024", "activo_total": "100"}))

        with connect() as conn:
            rows = dict(conn.execute(
                "SELECT anio_fiscal, activo_total FROM financial_statements WHERE ruc = ?", (ruc,)
            ).fetchall())
        self.assertEqual(rows, {2025: 110.0, 2024: 100.0})
        _, body = _get(page, self.cookie)
        self.assertIn("Variación 2025 vs 2024", body)

    def test_saving_one_tab_does_not_erase_another(self):
        """Regresión: /auditor/radar/profile guardaba perfil y ubicación con lo
        que llegara en el formulario, así que guardar Ubicación vaciaba SRI y
        Supercias, y guardar SRI vaciaba la ubicación."""
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        audit_id = create_company_audit(
            f"Empresa guardado parcial {time.time_ns()}", "", "Cuenca", "", "2025", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute("SELECT company_id FROM audits WHERE id = ?", (audit_id,)).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)

        page = f"/auditor/radar?audit_id={audit_id}&tab=sri"
        forms = [
            {"return_tab": "sri", "estado_contribuyente": "ACTIVO", "regimen": "GENERAL"},
            {"return_tab": "supercias", "situacion_legal": "ACTIVA", "plazo_social": "2061-08-24"},
            {"return_tab": "ubicacion", "calle": "AV. DEL ESTADIO", "numero": "S/N"},
        ]
        for fields in forms:
            status, _, _ = _post_raw(
                "/auditor/radar/profile",
                {"audit_id": str(audit_id), "_csrf": _csrf_token(page, self.cookie), **fields},
                self.cookie,
            )
            self.assertEqual(status, 303)

        with connect() as conn:
            profile = conn.execute("SELECT * FROM company_profiles WHERE audit_id = ?", (audit_id,)).fetchone()
            location = conn.execute("SELECT * FROM company_locations WHERE audit_id = ?", (audit_id,)).fetchone()
        self.assertEqual(profile["estado_contribuyente"], "ACTIVO")
        self.assertEqual(profile["regimen"], "GENERAL")
        self.assertEqual(profile["situacion_legal"], "ACTIVA")
        self.assertEqual(profile["plazo_social"], "2061-08-24")
        self.assertEqual(location["calle"], "AV. DEL ESTADIO")
        self.assertEqual(location["numero"], "S/N")


@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPAdminFlow(unittest.TestCase):
    """Flujo completo del jefe auditor: login → dashboard → aislamiento de roles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cookie = _login(*ADMIN)

    def test_admin_login_sets_cookie(self):
        """Login exitoso del admin debe retornar cookie de sesión."""
        self.assertIn("atlas_session=", self.cookie,
                      "Login de admin debe establecer cookie atlas_session")

    def test_admin_dashboard_returns_200(self):
        """Panel del jefe auditor debe responder 200 OK con sesión válida."""
        status, _ = _get("/admin", self.cookie)
        self.assertEqual(status, 200, "GET /admin (autenticado) debe retornar 200")

    def test_admin_users_page_returns_200(self):
        """Gestión de usuarios del admin debe responder 200."""
        status, _ = _get("/admin/users", self.cookie)
        self.assertEqual(status, 200)

    def test_archivar_y_restaurar_empresa(self):
        """El admin archiva una empresa: sale del directorio y el auditor pierde
        el acceso (ver y editar); el admin la sigue viendo y puede restaurarla."""
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        nombre = f"Empresa archivable {time.time_ns()}"
        audit_id = create_company_audit(nombre, "", "Cuenca", "", "2025", auditor_id, admin_id)

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = "
                             "(SELECT company_id FROM audits WHERE id = ?)", (audit_id,))

        self.addCleanup(cleanup_company)
        auditor_cookie = _login(*AUDITOR)
        radar = f"/auditor/radar?audit_id={audit_id}"

        status, location, _ = _post_raw("/admin/companies/archive", {
            "audit_id": str(audit_id), "archive_reason": "Registrada por error",
            "_csrf": _csrf_token("/admin/companies", self.cookie),
        }, self.cookie)
        self.assertEqual(status, 303)
        self.assertIn("msg=Empresa+archivada", location)

        _, body = _get("/admin/companies", self.cookie)
        activas, archivadas = body.split("Empresas archivadas (")
        self.assertNotIn(nombre, activas)
        self.assertIn(nombre, archivadas)
        self.assertIn("Registrada por error", archivadas)
        _, body = _get(f"/admin/audit?audit_id={audit_id}", self.cookie)
        self.assertIn("Empresa archivada", body)

        _, body = _get("/auditor", auditor_cookie)
        self.assertNotIn(nombre, body)
        _, body = _get(radar, auditor_cookie)
        self.assertIn("no disponible", body)
        status, _, _ = _post_raw("/auditor/radar/profile", {
            "audit_id": str(audit_id), "_csrf": _csrf_token("/auditor", auditor_cookie), "razon_social": "X",
        }, auditor_cookie)
        self.assertEqual(status, 403, "El auditor no puede editar una empresa archivada")

        status, location, _ = _post_raw("/admin/companies/restore", {
            "audit_id": str(audit_id), "_csrf": _csrf_token("/admin/companies", self.cookie),
        }, self.cookie)
        self.assertIn("msg=Empresa+restaurada", location)
        _, body = _get("/auditor", auditor_cookie)
        self.assertIn(nombre, body)

    def test_admin_cannot_post_to_auditor_search(self):
        """
        HC-1: el admin (jefe auditor) NO debe poder ejecutar búsquedas de RUC
        que pertenecen exclusivamente al rol de auditor.
        Debe recibir 403 (forbidden) al intentar POST /auditor/radar/search.
        """
        status, _, _ = _post_raw(
            "/auditor/radar/search",
            {"audit_id": "1", "search_ruc": "0190377210001"},
            self.cookie,
        )
        self.assertEqual(status, 403,
                         f"Admin no debe poder hacer POST /auditor/radar/search — obtuvo {status}")

    def test_admin_cannot_execute_automatic_research(self):
        """El jefe puede ver el resultado, pero no ejecutar la investigación del auditor."""
        status, _, _ = _post_raw(
            "/auditor/radar/investigate",
            {"audit_id": "1", "search_ruc": "0190314014001"},
            self.cookie,
        )
        self.assertEqual(status, 403)

    def test_admin_cannot_post_to_auditor_financial(self):
        """HC-1: el admin no debe poder guardar datos financieros vía POST."""
        status, _, _ = _post_raw(
            "/auditor/radar/financial",
            {"audit_id": "1", "activo_total": "100000"},
            self.cookie,
        )
        self.assertEqual(status, 403,
                         f"Admin no debe poder hacer POST /auditor/radar/financial — obtuvo {status}")

    def test_admin_cannot_post_to_auditor_profile(self):
        """HC-1: el admin no debe poder guardar el perfil de empresa vía POST."""
        status, _, _ = _post_raw(
            "/auditor/radar/profile",
            {"audit_id": "1", "razon_social": "Test"},
            self.cookie,
        )
        self.assertEqual(status, 403,
                         f"Admin no debe poder hacer POST /auditor/radar/profile — obtuvo {status}")

    def test_admin_cannot_post_to_auditor_source(self):
        """HC-1: el admin no debe poder registrar evidencia (incluido el
        certificado de administradores/accionistas de Supercias) vía POST."""
        status, _, _ = _post_raw(
            "/auditor/source",
            {
                "audit_id": "1", "source_type": "Supercias",
                "title": "Certificado de administradores",
            },
            self.cookie,
        )
        self.assertEqual(status, 403,
                         f"Admin no debe poder hacer POST /auditor/source — obtuvo {status}")

    def test_admin_auditor_dashboard_accessible_as_viewer(self):
        """
        GET /auditor es accesible para el admin (requiere sesión, no rol específico).
        El admin ve la lista de expedientes asignados a todos los auditores.
        La separación de roles opera en las rutas POST (HC-1), no en este GET de lectura.
        """
        status, _ = _get("/auditor", self.cookie)
        self.assertEqual(status, 200,
                         "GET /auditor debe ser accesible para admin con sesión activa")

    def test_lookup_ruc_returns_framed_json(self):
        """La búsqueda RUC debe cerrar correctamente el JSON para que fetch().json() no quede pendiente."""
        import http.client
        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("GET", "/api/lookup-ruc?ruc=0190314014001", headers={"Cookie": self.cookie})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        content_length = resp.getheader("Content-Length")
        content_type = resp.getheader("Content-Type", "")
        conn.close()

        self.assertEqual(resp.status, 200)
        self.assertIsNotNone(content_length)
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["name"], "IMPORTADORA AUTOMOTRIZ SALINAS S.A.")

    def test_delete_user_flow_completes_without_csrf_rejection(self):
        """Regresión: modal_html era un string plano (sin prefijo f), así que
        {csrf_input(csrf_token)} nunca se evaluaba y la gestión de usuario
        siempre era rechazada por CSRF inválido. Verifica el endpoint con
        operaciones rechazadas por negocio que no modifican cuentas reales."""
        csrf_token = _csrf_token("/admin/users", self.cookie)
        self.assertTrue(csrf_token)

        with connect() as conn:
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_admin'"
            ).fetchone()["id"]
            auditor_id = conn.execute(
                "SELECT id FROM users WHERE username = 'http_auditor'"
            ).fetchone()["id"]

        # La ruta recibe un CSRF válido y llega a la regla de negocio: un
        # usuario activo no puede darse de baja sin desactivarlo primero.
        status, location, _ = _post_raw(
            "/admin/users/delete",
            {
                "user_id": str(auditor_id),
                "deletion_reason": "Validación no destructiva",
                "_csrf": csrf_token,
            },
            self.cookie,
        )
        self.assertNotEqual(status, 403, "La baja no debe rechazarse por CSRF inválido")
        self.assertEqual(status, 303)
        self.assertIn("Primero+debes+desactivar", location)

        # La protección contra auto-desactivación también se evalúa después
        # del CSRF y deja intacta la sesión administrativa.
        status, location, _ = _post_raw(
            "/admin/users/deactivate",
            {"user_id": str(admin_id), "_csrf": csrf_token},
            self.cookie,
        )
        self.assertNotEqual(status, 403, "La desactivación no debe rechazarse por CSRF inválido")
        self.assertEqual(status, 303)
        self.assertIn("propio+usuario", location)


@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPSuperciasFlow(unittest.TestCase):
    """Fase 5 — pruebas integrales del catálogo local de Supercias y del
    flujo asistido de certificados, end-to-end contra el servidor real.

    Depende de que sri_catastro.db y supercias_catalog.db ya existan y
    contengan el RUC 0190314014001 (mismo RUC que test_lookup_ruc_returns_
    framed_json usa para el catastro SRI): si el catálogo de Supercias no
    fue importado, la aserción sobre "Catálogo local" fallará con un mensaje
    claro en vez de un error de conexión, indicando que hay que correr
    scripts/update_supercias_catalog.py antes de esta prueba.
    """

    RUC = "0190314014001"

    @classmethod
    def setUpClass(cls) -> None:
        cls.cookie = _login(*AUDITOR)

    def setUp(self) -> None:
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        self.audit_id = create_company_audit(
            f"Empresa Supercias HTTP {time.time_ns()}", self.RUC, "Cuenca",
            "Comercio", "2026", auditor_id, admin_id,
        )
        with connect() as conn:
            self.company_id = conn.execute(
                "SELECT company_id FROM audits WHERE id = ?", (self.audit_id,)
            ).fetchone()["company_id"]
        self.addCleanup(self._cleanup_company)

    def _cleanup_company(self) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM companies WHERE id = ?", (self.company_id,))

    def test_investigate_loads_sri_and_supercias_in_one_pass(self):
        csrf_token = _csrf_token(f"/auditor/radar?audit_id={self.audit_id}&tab=sri", self.cookie)
        status, location, _ = _post_raw(
            "/auditor/radar/investigate",
            {"audit_id": str(self.audit_id), "search_ruc": self.RUC, "_csrf": csrf_token},
            self.cookie,
        )
        self.assertEqual(status, 303)
        self.assertIn("datos+SRI+cargados", location)
        self.assertIn("datos+de+Superc", location,
                       "El mensaje debe reportar el resultado real de Supercias, no un 'pendiente' fijo")
        self.assertIn("cargados+desde+el+cat", location,
                       "El catálogo de Supercias no encontró el RUC de prueba: "
                       "¿corriste scripts/update_supercias_catalog.py?")

        _, body = _get(f"/auditor/radar?audit_id={self.audit_id}&tab=supercias", self.cookie)
        self.assertIn("Catálogo local", body)
        self.assertIn("IMPORTADORA AUTOMOTRIZ SALINAS", body)

    def test_repeated_investigate_is_idempotent(self):
        """Repetir la búsqueda del mismo RUC no debe duplicar la fuente
        'Directorio Supercías (catálogo local)' en la bitácora de evidencia."""
        csrf_token = _csrf_token(f"/auditor/radar?audit_id={self.audit_id}&tab=sri", self.cookie)
        for _ in range(2):
            status, _, _ = _post_raw(
                "/auditor/radar/investigate",
                {"audit_id": str(self.audit_id), "search_ruc": self.RUC, "_csrf": csrf_token},
                self.cookie,
            )
            self.assertEqual(status, 303)

        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE audit_id = ? AND title = 'Directorio Supercías (catálogo local)'",
                (self.audit_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_certificate_evidence_flips_admin_badge(self):
        _, body_before = _get(f"/auditor/radar?audit_id={self.audit_id}&tab=admins", self.cookie)
        self.assertIn("Certificado aún no registrado", body_before)

        csrf_token = _csrf_token(f"/auditor/radar?audit_id={self.audit_id}&tab=documentos", self.cookie)
        status, location, _ = _post_raw(
            "/auditor/source",
            {
                "audit_id": str(self.audit_id),
                "source_type": "Supercias",
                "title": "Certificado de administradores",
                "url": "",
                "finding": "Nomina completa obtenida del certificado oficial",
                "notes": "",
                "_csrf": csrf_token,
            },
            self.cookie,
        )
        self.assertEqual(status, 303)

        _, body_after = _get(f"/auditor/radar?audit_id={self.audit_id}&tab=admins", self.cookie)
        self.assertIn("Certificado registrado como evidencia", body_after)
        # El certificado de administradores no debe confundirse con el de accionistas
        # (mismo delimitador de panes que TestHTTPAuditorFlow._pane_content).
        match = re.search(r'id="tab-accionistas"(.*?)id="tab-indicadores"', body_after, re.S)
        accionistas_pane = match.group(1) if match else body_after
        self.assertIn("Certificado aún no registrado", accionistas_pane)


@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPCuenta(unittest.TestCase):
    """Mi cuenta: todos los roles cambian su propia contraseña. El jefe
    auditor solo crea auditores."""

    def test_mi_cuenta_para_ambos_roles(self):
        for usuario in (ADMIN, AUDITOR):
            with self.subTest(usuario=usuario[0]):
                status, body = _get("/cuenta", _login(*usuario))
                self.assertEqual(status, 200)
                self.assertIn('action="/cuenta/password"', body)
                self.assertIn('href="/cuenta"', body, "El nombre del usuario lleva a Mi cuenta")

    def test_cambiar_contrasena(self):
        username = f"http_clave_{time.time_ns()}"
        with connect() as conn:
            create_user(conn, username, "Prueba cambio de clave", "auditor", "clave-inicial")
        cookie = _login(username, "clave-inicial")
        otra_sesion = _login(username, "clave-inicial")

        def cambiar(actual: str, nueva: str, confirmacion: str) -> str:
            _, location, _ = _post_raw("/cuenta/password", {
                "_csrf": _csrf_token("/cuenta", cookie), "current_password": actual,
                "new_password": nueva, "confirm_password": confirmacion,
            }, cookie)
            return unquote_plus(location)

        self.assertIn("err=La contraseña actual no es correcta", cambiar("mala", "clave-nueva-1", "clave-nueva-1"))
        self.assertIn("err=La nueva contraseña y su confirmación no coinciden",
                      cambiar("clave-inicial", "clave-nueva-1", "otra"))
        self.assertIn("msg=Contraseña actualizada", cambiar("clave-inicial", "clave-nueva-1", "clave-nueva-1"))

        self.assertIn('action="/cuenta/password"', _get("/cuenta", cookie)[1],
                      "La sesión de quien cambia sigue abierta")
        self.assertNotIn('action="/cuenta/password"', _get("/cuenta", otra_sesion)[1],
                         "Las demás sesiones se cierran (redirige al login)")
        self.assertEqual(_login(username, "clave-inicial"), "")
        self.assertIn("atlas_session=", _login(username, "clave-nueva-1"))

    def test_cambiar_contrasena_exige_csrf(self):
        status, _, _ = _post_raw("/cuenta/password", {
            "current_password": AUDITOR[1], "new_password": "x" * 10, "confirm_password": "x" * 10,
        }, _login(*AUDITOR))
        self.assertEqual(status, 403)

    def test_usuarios_crea_solo_auditores(self):
        cookie = _login(*ADMIN)
        _, body = _get("/admin/users", cookie)
        self.assertNotIn('name="role"', body, "Sin selector de rol")
        username = f"http_nuevo_{time.time_ns()}"
        _post_raw("/admin/users", {
            "_csrf": _csrf_token("/admin/users", cookie), "username": username,
            "full_name": "Nuevo auditor", "password": "clave-temporal", "role": "admin",
        }, cookie)
        with connect() as conn:
            rol = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()["role"]
        self.assertEqual(rol, "auditor", "Aunque se envíe role=admin, se crea un auditor")


@unittest.skipUnless(_server_available(), _SKIP_REASON)
class TestHTTPExportaciones(unittest.TestCase):
    """Descargas del expediente: el resumen en texto y el levantamiento en Excel."""

    def test_descargas_disponibles_y_eliminadas(self):
        import http.client
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'http_auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'http_admin'").fetchone()["id"]
        audit_id = create_company_audit(
            f"Empresa exportable {time.time_ns()}", "", "Cuenca", "", "2025", auditor_id, admin_id,
        )

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = "
                             "(SELECT company_id FROM audits WHERE id = ?)", (audit_id,))

        self.addCleanup(cleanup_company)

        def descargar(path: str, cookie: str) -> tuple[int, str, bytes]:
            conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=10)
            conn.request("GET", f"{path}?audit_id={audit_id}", headers={"Cookie": cookie})
            resp = conn.getresponse()
            cuerpo = resp.read()
            conn.close()
            return resp.status, resp.getheader("Content-Type", ""), cuerpo

        for usuario in (AUDITOR, ADMIN):
            cookie = _login(*usuario)
            with self.subTest(usuario=usuario[0]):
                status, tipo, cuerpo = descargar("/export/xlsx", cookie)
                self.assertEqual(status, 200)
                self.assertEqual(tipo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.assertTrue(cuerpo.startswith(b"PK"), "Un .xlsx es un archivo zip")
                self.assertEqual(descargar("/export/summary", cookie)[0], 200)
                for eliminada in ("/export/csv", "/export/dossier"):
                    self.assertEqual(descargar(eliminada, cookie)[0], 404, eliminada)

        _, body = _get(f"/auditor/radar?audit_id={audit_id}", _login(*AUDITOR))
        self.assertIn("/export/xlsx", body)
        self.assertNotIn("/export/csv", body)
        self.assertNotIn("/export/dossier", body)


if __name__ == "__main__":
    unittest.main()
