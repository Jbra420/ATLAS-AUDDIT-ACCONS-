"""
tests/test_http.py — Pruebas de integración HTTP para Atlas · Auddit.

Conecta al servidor ya en ejecución en localhost:8765 (debe estar corriendo).
Si el servidor no está disponible, los tests se saltean automáticamente.

Para correr:
    1. Iniciar el servidor: python3 app.py
    2. Correr los tests:    python3 -m unittest tests.test_http -v

Los tests verifican:
  - Rutas públicas responden correctamente.
  - El flujo completo login → cookie → dashboard retorna 200 OK.
  - El auditor accede a /auditor y recibe 403 en /admin.
  - HC-1: el admin recibe 403 al intentar rutas POST del auditor.
  - Las rutas inexistentes retornan 404.
"""
from __future__ import annotations

import json
import re
import socket
import time
import unittest
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from database import connect, create_company_audit
from seed_data import DEMO_RUC

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def _server_available() -> bool:
    """Verifica si el servidor Atlas está disponible en el puerto configurado."""
    try:
        with socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=1):
            return True
    except OSError:
        return False


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


# ── Casos de prueba ───────────────────────────────────────────────────────────

@unittest.skipUnless(_server_available(), "Servidor Atlas no disponible en localhost:8765 — inicia con: python3 app.py")
class TestHTTPPublicRoutes(unittest.TestCase):
    """Rutas públicas accesibles sin autenticación."""

    def test_login_page_returns_200(self):
        status, _ = _get("/login")
        self.assertEqual(status, 200, "GET /login debe retornar 200 OK")

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
        cookie = _login("auditor", "auditor123")
        status, _ = _get("/ruta-que-no-existe-xyz", cookie)
        self.assertEqual(status, 404)

    def test_wrong_password_does_not_set_cookie(self):
        """Credenciales incorrectas no deben establecer sesión."""
        cookie = _login("auditor", "clave_totalmente_incorrecta")
        self.assertEqual(cookie, "",
                         "Login fallido no debe devolver cookie de sesión")


@unittest.skipUnless(_server_available(), "Servidor Atlas no disponible en localhost:8765 — inicia con: python3 app.py")
class TestHTTPAuditorFlow(unittest.TestCase):
    """Flujo completo del auditor: login → dashboard → aislamiento de roles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cookie = _login("auditor", "auditor123")

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
        csrf_token = _csrf_token("/auditor/radar?audit_id=1&tab=resumen", self.cookie)
        self.assertTrue(csrf_token, "La página del expediente debe incluir un token CSRF")

        status, location, _ = _post_raw(
            "/auditor/radar/summary",
            {"audit_id": "1", "_csrf": csrf_token},
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

    def test_complete_audit_via_ui_forms_can_generate_summary(self):
        """Flujo feliz completo, exclusivamente vía las rutas/controles que la
        UI expone: cubre la regresión donde no existía forma de marcar
        'Fuente SRI/Supercias consultada' desde la interfaz (hallazgo crítico
        del informe de arquitectura — tab_fuentes.py se eliminó sin dejar
        reemplazo en commit 7b80504). El expediente demo se crea y limpia en
        la propia prueba para no depender de IDs o asignaciones persistentes."""
        with connect() as conn:
            auditor_id = conn.execute(
                "SELECT id FROM users WHERE username = 'auditor'"
            ).fetchone()["id"]
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'admin'"
            ).fetchone()["id"]

        audit_id = create_company_audit(
            f"Empresa flujo HTTP {time.time_ns()}", DEMO_RUC, "Cuenca",
            "Servicios de alojamiento", "2026", auditor_id, admin_id,
        )
        with connect() as conn:
            company_id = conn.execute(
                "SELECT company_id FROM audits WHERE id = ?", (audit_id,)
            ).fetchone()["company_id"]

        def cleanup_company() -> None:
            with connect() as conn:
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))

        self.addCleanup(cleanup_company)

        _, body = _get(f"/auditor/radar?audit_id={audit_id}&tab=sri", self.cookie)
        sri_pane = self._pane_content(body, "sri", "supercias")
        sup_pane = self._pane_content(body, "supercias", "ubicacion")

        sri_match = re.search(r'action="/auditor/radar/source-check".*?check_id" value="(\d+)"', sri_pane, re.S)
        sup_match = re.search(r'action="/auditor/radar/source-check".*?check_id" value="(\d+)"', sup_pane, re.S)
        self.assertIsNotNone(sri_match, "El tab SRI debe tener un control para marcar la fuente como consultada")
        self.assertIsNotNone(sup_match, "El tab Supercias debe tener un control para marcar la fuente como consultada")

        csrf_token = _csrf_token(f"/auditor/radar?audit_id={audit_id}&tab=sri", self.cookie)
        for check_id, tab in ((sri_match.group(1), "sri"), (sup_match.group(1), "supercias")):
            status, location, _ = _post_raw(
                "/auditor/radar/source-check",
                {
                    "audit_id": str(audit_id), "check_id": check_id, "accion": "consultar",
                    "return_tab": tab, "observacion": "Verificado en prueba", "_csrf": csrf_token,
                },
                self.cookie,
            )
            self.assertEqual(status, 303)
            self.assertIn(f"tab={tab}", location)

        _, resumen_body = _get(f"/auditor/radar?audit_id={audit_id}&tab=resumen", self.cookie)
        self.assertIn("Resumen habilitado", resumen_body,
                      "Tras marcar SRI y Supercias como consultadas, el resumen debe habilitarse")
        self.assertIn('action="/auditor/radar/summary"', resumen_body)

        summary_csrf = _csrf_token(f"/auditor/radar?audit_id={audit_id}&tab=resumen", self.cookie)
        status, location, _ = _post_raw(
            "/auditor/radar/summary",
            {"audit_id": str(audit_id), "_csrf": summary_csrf},
            self.cookie,
        )
        self.assertEqual(status, 303)
        self.assertIn("msg=Resumen+generado", location)


@unittest.skipUnless(_server_available(), "Servidor Atlas no disponible en localhost:8765 — inicia con: python3 app.py")
class TestHTTPAdminFlow(unittest.TestCase):
    """Flujo completo del jefe auditor: login → dashboard → aislamiento de roles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cookie = _login("admin", "admin123")

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
                "SELECT id FROM users WHERE username = 'admin'"
            ).fetchone()["id"]
            auditor_id = conn.execute(
                "SELECT id FROM users WHERE username = 'auditor'"
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


@unittest.skipUnless(_server_available(), "Servidor Atlas no disponible en localhost:8765 — inicia con: python3 app.py")
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
        cls.cookie = _login("auditor", "auditor123")

    def setUp(self) -> None:
        with connect() as conn:
            auditor_id = conn.execute("SELECT id FROM users WHERE username = 'auditor'").fetchone()["id"]
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
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


if __name__ == "__main__":
    unittest.main()
