"""
tests/test_tabs_readonly.py — Pruebas de renderizado HTML de tabs con read_only.

Verifica que cuando read_only=True (modo jefe auditor):
  - Los formularios de edición son suprimidos del HTML generado.
  - El HTML de solo lectura sigue conteniendo la información de la empresa.
  - tab_admins y tab_accionistas aceptan read_only sin error.
  - tab_resumen y tab_financiero muestran/ocultan formularios según el rol.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import (
    authenticate,
    create_company_audit,
    get_audit,
    init_db,
    load_demo_if_ruc_matches,
    get_financial_context,
    get_financial_snapshot,
    set_audit_fiscal_year,
)
from services.financial import compute_indicators


def _sample_dossier() -> dict:
    return {
        "title": "Ficha final de resultados",
        "status": "En construccion",
        "closing": "El expediente debe completar los campos pendientes.",
        "metrics": {
            "source_percent": 45,
            "completed_sources": 1,
            "partial_sources": 2,
            "pending_sources": 2,
            "evidence_count": 1,
            "risk_count": 1,
            "pending_count": 2,
        },
        "identity": [
            {"label": "Razon social", "value": "GRUCANQUI CIA. LTDA"},
            {"label": "RUC", "value": "0190377210001"},
        ],
        "source_status": [
            {"title": "SRI", "status": "En avance", "completed": "4/6", "missing": "Obligaciones"},
        ],
        "evidence": [
            {"title": "Consulta SERCOP", "type": "SERCOP", "notes": "Sin contratos registrados"},
        ],
        "financial": [
            {"label": "Activo total", "value": "$100.00"},
        ],
        "risks": ["Revisar soporte financiero."],
        "pending": ["Completar fuente SRI."],
    }


def _blocked_readiness() -> dict:
    return {
        "ready": False,
        "blockers": [
            {"label": "Estado contribuyente", "source": "SRI", "tab": "sri"},
            {"label": "Accionistas registrados", "source": "Supercias", "tab": "accionistas"},
        ],
        "warnings": [
            {"label": "Informacion financiera", "source": "Complementario", "tab": "indicadores"},
        ],
        "required_completed": 7,
        "required_total": 9,
    }


def _make_db() -> Path:
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_tabs.db"
    init_db(db_path)
    return db_path


class TestTabSriSourceCheckReadOnly(unittest.TestCase):
    """tab_sri oculta el control de 'marcar consultada' en modo lectura."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        self.audit = get_audit(self.audit_id, self.admin, self.db)
        self.check = {"id": 1, "fuente": "SRI — Consulta de RUC", "estado": "pendiente"}

    def test_read_only_hides_source_check_control(self):
        from views.auditor.radar.tab_sri import build
        html = build(self.audit_id, self.audit, profile=None, research={},
                     read_only=True, source_check=self.check)
        self.assertNotIn("/auditor/radar/source-check", html)

    def test_auditor_mode_shows_source_check_control(self):
        from views.auditor.radar.tab_sri import build
        html = build(self.audit_id, self.audit, profile=None, research={},
                     read_only=False, csrf_token="token", source_check=self.check)
        self.assertIn("/auditor/radar/source-check", html)
        self.assertIn("Marcar consultada", html)


class TestTabSuperciasSourceCheckReadOnly(unittest.TestCase):
    """tab_supercias oculta el control de 'marcar consultada' en modo lectura."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        self.audit = get_audit(self.audit_id, self.admin, self.db)
        self.check = {"id": 2, "fuente": "Supercias — Portal societario", "estado": "consultada"}

    def test_read_only_hides_source_check_control(self):
        from views.auditor.radar.tab_supercias import build
        html = build(self.audit_id, self.audit, profile=None, research={},
                     read_only=True, source_check=self.check)
        self.assertNotIn("/auditor/radar/source-check", html)

    def test_auditor_mode_shows_source_check_control_with_current_state(self):
        from views.auditor.radar.tab_supercias import build
        html = build(self.audit_id, self.audit, profile=None, research={},
                     read_only=False, csrf_token="token", source_check=self.check)
        self.assertIn("/auditor/radar/source-check", html)
        self.assertIn("Marcar pendiente", html)  # ya estaba 'consultada'


class TestTabDocumentosReadOnly(unittest.TestCase):
    """tab_documentos: solo la bitácora de evidencia."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        self.sources = [{"title": "Consulta SERCOP", "source_type": "SERCOP", "notes": "Sin contratos"}]

    def test_read_only_has_no_forms(self):
        from views.auditor.radar.tab_documentos import build
        html = build(self.audit_id, self.sources, read_only=True)
        self.assertNotIn("<form", html)
        self.assertIn("Consulta SERCOP", html)

    def test_auditor_mode_has_only_the_evidence_form(self):
        from views.auditor.radar.tab_documentos import build
        html = build(self.audit_id, self.sources, read_only=False, csrf_token="token")
        self.assertIn('action="/auditor/source"', html)
        self.assertIn('name="_csrf" value="token"', html)
        self.assertNotIn("/auditor/radar/document", html)
        self.assertNotIn("/auditor/radar/source-check", html)
        self.assertNotIn("Documentos económicos", html)
        self.assertNotIn("Otras fuentes guiadas", html)

    def test_empty_state_placeholder(self):
        from views.auditor.radar.tab_documentos import build
        self.assertIn("Sin evidencias registradas", build(self.audit_id, [], read_only=True))


class TestTabAdminsReadOnly(unittest.TestCase):
    """tab_admins acepta read_only sin error y no rompe el HTML."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        self.audit = get_audit(self.audit_id, self.admin, self.db)

    def test_build_with_read_only_true_no_error(self):
        from views.auditor.radar.tab_admins import build
        html = build(self.audit_id, self.audit, admins=[], read_only=True)
        self.assertIsInstance(html, str)
        self.assertIn("Administradores registrados", html)
        self.assertNotIn("/auditor/radar/administrator", html)
        self.assertNotIn("<form", html)

    def test_build_with_read_only_false_no_error(self):
        from views.auditor.radar.tab_admins import build
        html = build(self.audit_id, self.audit, admins=[], read_only=False, csrf_token="token")
        self.assertIsInstance(html, str)
        self.assertIn("Administradores registrados", html)
        self.assertIn("/auditor/radar/administrator", html)
        self.assertIn('name="_csrf" value="token"', html)

    def test_empty_admins_shows_placeholder(self):
        from views.auditor.radar.tab_admins import build
        html = build(self.audit_id, self.audit, admins=[], read_only=True)
        self.assertIn("Sin administradores registrados", html)

    def test_read_only_hides_assisted_certificate_panel(self):
        from views.auditor.radar.tab_admins import build
        html = build(self.audit_id, self.audit, admins=[], read_only=True)
        self.assertNotIn("Flujo asistido", html)

    def test_assisted_panel_shows_pending_without_certificate(self):
        from views.auditor.radar.tab_admins import build
        html = build(
            self.audit_id, self.audit, admins=[], read_only=False,
            csrf_token="token", sources=[],
        )
        self.assertIn("Flujo asistido", html)
        self.assertIn("Certificado aún no registrado", html)
        self.assertNotIn("Certificado registrado como evidencia", html)

    def test_assisted_panel_shows_registered_with_matching_certificate(self):
        from views.auditor.radar.tab_admins import build
        sources = [{
            "source_type": "Supercias",
            "title": "Certificado de administradores",
            "notes": "",
        }]
        html = build(
            self.audit_id, self.audit, admins=[], read_only=False,
            csrf_token="token", sources=sources,
        )
        self.assertIn("Certificado registrado como evidencia", html)

    def test_assisted_panel_ignores_unrelated_evidence(self):
        from views.auditor.radar.tab_admins import build
        sources = [{"source_type": "SERCOP", "title": "Certificado de administradores", "notes": ""}]
        html = build(
            self.audit_id, self.audit, admins=[], read_only=False,
            csrf_token="token", sources=sources,
        )
        self.assertIn("Certificado aún no registrado", html)


class TestTabAccionistasReadOnly(unittest.TestCase):
    """tab_accionistas acepta read_only sin error."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        self.audit = get_audit(self.audit_id, self.admin, self.db)

    def test_build_read_only_true_no_error(self):
        from views.auditor.radar.tab_accionistas import build
        html = build(self.audit_id, self.audit, shareholders=[], read_only=True)
        self.assertIsInstance(html, str)
        self.assertIn("Nómina de socios", html)
        self.assertNotIn("/auditor/radar/shareholder", html)
        self.assertNotIn("<form", html)

    def test_build_read_only_false_no_error(self):
        from views.auditor.radar.tab_accionistas import build
        html = build(self.audit_id, self.audit, shareholders=[], read_only=False, csrf_token="token")
        self.assertIsInstance(html, str)
        self.assertIn("/auditor/radar/shareholder", html)
        self.assertIn('name="_csrf" value="token"', html)

    def test_empty_shareholders_shows_placeholder(self):
        from views.auditor.radar.tab_accionistas import build
        html = build(self.audit_id, self.audit, shareholders=[], read_only=True)
        self.assertIn("Sin accionistas registrados", html)

    def test_read_only_hides_assisted_certificate_panel(self):
        from views.auditor.radar.tab_accionistas import build
        html = build(self.audit_id, self.audit, shareholders=[], read_only=True)
        self.assertNotIn("Flujo asistido", html)

    def test_assisted_panel_shows_pending_without_certificate(self):
        from views.auditor.radar.tab_accionistas import build
        html = build(
            self.audit_id, self.audit, shareholders=[], read_only=False,
            csrf_token="token", sources=[],
        )
        self.assertIn("Certificado aún no registrado", html)

    def test_assisted_panel_shows_registered_with_matching_certificate(self):
        from views.auditor.radar.tab_accionistas import build
        sources = [{
            "source_type": "Supercias",
            "title": "Certificado de nómina de accionistas",
            "notes": "",
        }]
        html = build(
            self.audit_id, self.audit, shareholders=[], read_only=False,
            csrf_token="token", sources=sources,
        )
        self.assertIn("Certificado registrado como evidencia", html)


class TestTabResumenReadOnly(unittest.TestCase):
    """tab_resumen oculta el formulario de generación en modo lectura."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        from database import get_research
        self.research = get_research(self.audit_id, self.db)

    def test_read_only_suppresses_generate_button(self):
        """El botón 'Generar resumen' no debe aparecer para el jefe auditor."""
        from views.auditor.radar.tab_resumen import build
        html = build(self.audit_id, self.research, read_only=True)
        self.assertNotIn('/auditor/radar/summary', html,
                         "read_only=True no debe mostrar el formulario de generación de resumen")

    def test_read_only_has_no_pending_modal(self):
        """El jefe no genera el resumen: no recibe el modal de pendientes."""
        from views.auditor.radar.tab_resumen import build
        html = build(
            self.audit_id,
            self.research,
            readiness=_blocked_readiness(),
            read_only=True,
        )
        self.assertNotIn("summaryPendingModal", html)
        self.assertNotIn("Completar", html)

    def test_auditor_mode_has_generate_button(self):
        """El auditor sí debe ver el botón para generar el resumen."""
        from views.auditor.radar.tab_resumen import build
        html = build(self.audit_id, self.research, read_only=False)
        self.assertIn('/auditor/radar/summary', html,
                      "read_only=False debe mostrar el formulario de generación")
        self.assertNotIn("summaryPendingModal", html, "Sin pendientes se genera directo")

    def test_pending_items_open_modal_with_option_to_continue(self):
        """Con puntos pendientes, "Generar resumen" abre un modal que los lista
        y permite generar igual (confirmar_pendientes=1)."""
        from views.auditor.radar.tab_resumen import build
        html = build(
            self.audit_id,
            self.research,
            readiness=_blocked_readiness(),
            read_only=False,
        )
        self.assertIn("openModal('summaryPendingModal')", html)
        self.assertIn("Estado contribuyente", html)
        self.assertIn('name="confirmar_pendientes" value="1"', html)
        self.assertIn("Generar de todas formas", html)
        self.assertIn("Volver y completar", html)

    def test_pending_modal_separates_warnings(self):
        """Las recomendaciones se muestran separadas de los requisitos obligatorios."""
        from views.auditor.radar.tab_resumen import build
        html = build(
            self.audit_id,
            self.research,
            readiness=_blocked_readiness(),
            read_only=False,
        )
        self.assertIn("Obligatorios pendientes", html)
        self.assertIn("Recomendaciones", html)
        self.assertIn("Informacion financiera", html)

    def test_pending_items_have_real_navigation_targets(self):
        from views.auditor.radar.tab_resumen import build
        html = build(
            self.audit_id,
            self.research,
            readiness=_blocked_readiness(),
            read_only=False,
        )
        self.assertIn(
            f'/auditor/radar?audit_id={self.audit_id}&tab=sri#radar-tabs-main',
            html,
        )
        self.assertIn("closeModal('summaryPendingModal'); return switchTab('sri')", html)

    def test_summary_content_visible_in_both_modes(self):
        """El contenido del resumen (si existe) debe verse en ambos modos."""
        from views.auditor.radar.tab_resumen import build
        html_ro = build(self.audit_id, self.research, read_only=True)
        html_aw = build(self.audit_id, self.research, read_only=False)
        self.assertIsInstance(html_ro, str)
        self.assertIsInstance(html_aw, str)
        self.assertGreater(len(html_ro), 0)

    def test_dossier_visible_without_admin_generate_action(self):
        """El jefe puede ver y exportar la ficha final sin generar resumen."""
        from views.auditor.radar.tab_resumen import build
        html = build(self.audit_id, self.research, _sample_dossier(), read_only=True)

        self.assertIn("Ficha final de resultados", html)
        self.assertIn("/export/dossier", html)
        self.assertIn("Consulta SERCOP", html)
        self.assertNotIn("/auditor/radar/summary", html)


class TestTabFinancieroReadOnly(unittest.TestCase):
    """tab_financiero oculta el formulario de edición en modo lectura."""

    def setUp(self):
        self.db = _make_db()
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Consultoría", "2026", self.auditor["id"], self.admin["id"], self.db,
        )
        load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        snapshot = get_financial_snapshot(self.audit_id, self.db)
        self.indicators = compute_indicators(dict(snapshot) if snapshot else None)
        # Mismos argumentos que usa page.py (Fase 4: año fiscal y RUC).
        self.kwargs = {
            "financial": get_financial_context(self.audit_id, self.db),
            "ruc": "0190377210001",
        }

    def test_read_only_suppresses_financial_form(self):
        """El formulario de datos financieros no debe aparecer para el jefe auditor."""
        from views.auditor.radar.tab_financiero import build
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self.kwargs["financial"] = get_financial_context(self.audit_id, self.db)
        html = build(self.audit_id, self.indicators, read_only=True, **self.kwargs)
        self.assertNotIn("<form", html)
        self.assertNotIn('/auditor/radar/financial', html,
                         "read_only=True no debe mostrar el formulario de edición financiera")

    def test_auditor_mode_has_financial_form(self):
        """El auditor sí debe ver el formulario para ingresar datos financieros."""
        from views.auditor.radar.tab_financiero import build
        html = build(self.audit_id, self.indicators, read_only=False, **self.kwargs)
        self.assertNotIn('/auditor/radar/financial-year', html,
                         "No se pide el año fiscal antes de mostrar la información")
        self.assertIn('action="/auditor/radar/financial"', html)
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self.kwargs["financial"] = get_financial_context(self.audit_id, self.db)
        html = build(self.audit_id, self.indicators, read_only=False, **self.kwargs)
        self.assertIn('action="/auditor/radar/financial"', html,
                      "Con año fiscal, read_only=False debe mostrar el formulario de casilleros")

    def test_indicators_visible_in_both_modes(self):
        """Los indicadores calculados deben aparecer para ambos roles."""
        from views.auditor.radar.tab_financiero import build
        html_ro = build(self.audit_id, self.indicators, read_only=True, **self.kwargs)
        html_aw = build(self.audit_id, self.indicators, read_only=False, **self.kwargs)
        # Ambos deben mostrar la sección de indicadores
        for html in (html_ro, html_aw):
            self.assertIn("Indicadores calculados", html)
            self.assertIn("67.8%", html)


if __name__ == "__main__":
    unittest.main()
