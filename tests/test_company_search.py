"""
tests/test_company_search.py — Pruebas del mapa de busqueda por fuentes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import (
    authenticate,
    create_company_audit,
    get_audit,
    get_company_location,
    get_company_profile,
    get_financial_snapshot,
    get_research,
    init_db,
    list_administrators,
    list_economic_documents,
    list_shareholders,
    list_source_checks,
    list_sources,
    mark_source_checked,
)
from services.company_search import build_source_map


def _make_db() -> Path:
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test_company_search.db"
    init_db(db_path)
    return db_path


class TestCompanySearchMap(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.admin = authenticate("admin", "admin123", self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.audit_id = get_audit(1, self.admin, self.db)["id"]

    def _source_map(self):
        audit = get_audit(self.audit_id, self.admin, self.db)
        return build_source_map(
            audit,
            get_research(self.audit_id, self.db),
            get_company_profile(self.audit_id, self.db),
            get_company_location(self.audit_id, self.db),
            list_administrators(self.audit_id, self.db),
            list_shareholders(self.audit_id, self.db),
            list_economic_documents(self.audit_id, self.db),
            get_financial_snapshot(self.audit_id, self.db),
            list_source_checks(self.audit_id, self.db),
            list_sources(self.audit_id, self.db),
        )

    def test_demo_profile_builds_partial_map_until_sources_are_marked(self):
        source_map = self._source_map()
        sri = next(card for card in source_map["cards"] if card["key"] == "sri")
        supercias = next(card for card in source_map["cards"] if card["key"] == "supercias")

        self.assertEqual(sri["status"], "partial")
        self.assertIn("Fuente SRI consultada", sri["missing"])
        self.assertEqual(supercias["status"], "partial")
        self.assertGreater(source_map["totals"]["percent"], 40)

    def test_marked_source_can_complete_sri_card(self):
        sri_check = next(sc for sc in list_source_checks(self.audit_id, self.db) if "SRI" in sc["fuente"])
        mark_source_checked(sri_check["id"], self.auditor["id"], "Consulta confirmada", self.db)

        source_map = self._source_map()
        sri = next(card for card in source_map["cards"] if card["key"] == "sri")

        self.assertEqual(sri["status"], "complete")
        self.assertEqual(sri["completed"], sri["total"])

    def test_new_assignment_without_ruc_starts_with_pending_cards(self):
        audit_id = create_company_audit(
            "Empresa Pendiente", "", "Cuenca", "Servicios", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )
        audit = get_audit(audit_id, self.admin, self.db)
        source_map = build_source_map(
            audit, None, None, None, [], [], [], None,
            list_source_checks(audit_id, self.db), list_sources(audit_id, self.db),
        )

        sri = next(card for card in source_map["cards"] if card["key"] == "sri")
        self.assertEqual(sri["status"], "pending")
        self.assertFalse(source_map["totals"]["ready_for_summary"])
        blocker_labels = {item["label"] for item in source_map["readiness"]["blockers"]}
        self.assertIn("RUC de 13 dígitos terminado en 001", blocker_labels)
        self.assertIn("Fuente Supercias consultada", blocker_labels)

    CHECKS = [
        {"fuente": "SRI", "estado": "consultada"},
        {"fuente": "Supercias", "estado": "consultada"},
    ]
    PROFILE = {
        "razon_social_sri": "GRUCANQUI CIA. LTDA", "estado_contribuyente": "ACTIVO",
        "tipo_contribuyente": "SOCIEDAD", "regimen": "GENERAL", "agente_retencion": "SI",
        "fecha_inicio_actividades": "2011-08-24", "expediente_supercias": "141528",
        "fecha_constitucion": "2011-08-24", "tipo_compania": "RESPONSABILIDAD LIMITADA",
        "objeto_social": "Servicios de alojamiento", "razon_social_supercias": "GRUCANQUI CIA. LTDA.",
    }
    LOCATION = {"provincia": "AZUAY", "ciudad": "CUENCA", "calle": "AV. DEL ESTADIO",
                "numero": "S/N", "interseccion": "FLORENCIA ASTUDILLO"}
    ADMINS = [
        {"nombre": "Ana Torres", "cargo": "GERENTE GENERAL", "identificacion": "0102030400"},
        {"nombre": "Luis Perez", "cargo": "PRESIDENTE", "identificacion": "0102030400"},
    ]
    SHAREHOLDERS = [{"nombre": "Socio Uno", "identificacion": "0102030400"}]
    SNAPSHOT = {
        "anio_fiscal": 2025, "activo_total": 100.0, "pasivo_total": 60.0, "patrimonio_neto": 40.0,
        "ingresos_401": 50.0, "otros_ingresos_403": 0.0, "costo_ventas_501": 20.0,
        "gastos_502": 25.0, "utilidad_neta_707": 3.0,
    }

    def test_previous_nine_requirements_no_longer_enable_summary(self):
        """Los 9 requisitos del Día 10 ya no bastan: se exigen todos los
        campos obligatorios del levantamiento de información."""
        source_map = build_source_map(
            {"ruc": "0190377210001"},
            {"economic_activity": "Servicios", "legal_status": "ACTIVA", "representative": "Ana Torres"},
            {"estado_contribuyente": "ACTIVO", "situacion_legal": "ACTIVA", "representante_legal": "Ana Torres",
             "actividad_economica": "Servicios"},
            None, [{"nombre": "Ana Torres", "cargo": "GERENTE GENERAL"}], [{"nombre": "Socio Uno"}],
            [], None, self.CHECKS, [],
        )
        blocker_labels = {item["label"] for item in source_map["readiness"]["blockers"]}
        self.assertFalse(source_map["readiness"]["ready"])
        for label in ("Objeto social", "Régimen", "Presidente registrado", "Calle principal",
                      "Año fiscal de los estados financieros"):
            self.assertIn(label, blocker_labels)

    def test_complete_levantamiento_enables_summary_with_optional_warnings(self):
        source_map = build_source_map(
            {"ruc": "0190377210001"}, {"observations": ""}, self.PROFILE, self.LOCATION,
            self.ADMINS, self.SHAREHOLDERS, [], self.SNAPSHOT, self.CHECKS, [],
        )
        readiness = source_map["readiness"]
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(readiness["blocker_count"], 0)
        self.assertEqual(readiness["required_completed"], readiness["required_total"])
        warning_labels = {item["label"] for item in readiness["warnings"]}
        self.assertIn("Plazo social", warning_labels)
        self.assertIn("Beneficiario final", warning_labels)
        self.assertIn("Observaciones del auditor", warning_labels)

    def test_process_controls_still_block(self):
        checks = [{"fuente": "SRI", "estado": "pendiente"}, {"fuente": "Supercias", "estado": "consultada"}]
        source_map = build_source_map(
            {"ruc": "0190377210001"}, {}, self.PROFILE, self.LOCATION,
            self.ADMINS, self.SHAREHOLDERS, [], self.SNAPSHOT, checks, [],
        )
        self.assertEqual([b["label"] for b in source_map["readiness"]["blockers"]], ["Fuente SRI consultada"])


class TestFindCertificateEvidence(unittest.TestCase):
    """find_certificate_evidence: no consulta la BD, solo filtra la lista de
    'sources' ya cargada (usada por tab_admins/tab_accionistas)."""

    def test_matches_supercias_source_with_keyword_and_certificado(self):
        from services.company_search import find_certificate_evidence
        sources = [
            {"source_type": "SERCOP", "title": "Certificado de administradores", "notes": ""},
            {"source_type": "Supercias", "title": "Certificado de administradores", "notes": ""},
        ]
        found = find_certificate_evidence(sources, "administrador")
        self.assertIsNotNone(found)
        self.assertEqual(found["source_type"], "Supercias")

    def test_requires_both_certificado_word_and_keyword(self):
        from services.company_search import find_certificate_evidence
        sources = [{"source_type": "Supercias", "title": "Consulta general de la empresa", "notes": ""}]
        self.assertIsNone(find_certificate_evidence(sources, "administrador"))

    def test_case_insensitive_and_checks_notes_too(self):
        from services.company_search import find_certificate_evidence
        sources = [{"source_type": "Supercias", "title": "Evidencia societaria", "notes": "CERTIFICADO DE ACCIONISTAS adjunto"}]
        self.assertIsNotNone(find_certificate_evidence(sources, "accionista"))

    def test_empty_sources_returns_none(self):
        from services.company_search import find_certificate_evidence
        self.assertIsNone(find_certificate_evidence([], "administrador"))
        self.assertIsNone(find_certificate_evidence(None, "administrador"))


if __name__ == "__main__":
    unittest.main()
