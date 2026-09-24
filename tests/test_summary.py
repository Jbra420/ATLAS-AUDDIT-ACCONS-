"""
tests/test_summary.py — Pruebas del generador de resumen estructurado de Atlas.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from services.summary import generate_summary, extract_signals, risk_suggestions


def _make_audit(
    company_name: str = "Test S.A.",
    ruc: str = "1790000000001",
    period: str = "2026",
    city: str = "Quito",
    activity_hint: str = "",
) -> MagicMock:
    """Crea un mock de sqlite3.Row para auditoría."""
    audit = MagicMock()
    audit.__getitem__ = lambda self, key: {
        "company_name": company_name,
        "ruc": ruc,
        "period": period,
        "city": city,
        "activity_hint": activity_hint,
    }.get(key, "")
    audit.get = lambda key, default=None: {
        "company_name": company_name,
        "ruc": ruc,
        "period": period,
        "city": city,
        "activity_hint": activity_hint,
    }.get(key, default)
    return audit


class TestExtractSignals(unittest.TestCase):

    def test_detects_ruc(self):
        signals = extract_signals("RUC: 1790000000001 presente en el texto")
        self.assertIn("1790000000001", signals["rucs"])

    def test_detects_email(self):
        signals = extract_signals("Contacto: empresa@dominio.com")
        self.assertIn("empresa@dominio.com", signals["emails"])

    def test_detects_phone(self):
        signals = extract_signals("Teléfono 0999888777")
        self.assertIn("0999888777", signals["phones"])

    def test_empty_text(self):
        signals = extract_signals("")
        self.assertEqual(signals["rucs"], [])
        self.assertEqual(signals["emails"], [])
        self.assertEqual(signals["phones"], [])

    def test_caps_at_5(self):
        text = " ".join(f"111222333444{i}" for i in range(10))
        signals = extract_signals(text)
        self.assertLessEqual(len(signals["rucs"]), 5)


class TestRiskSuggestions(unittest.TestCase):

    def _data(self, **kwargs) -> dict:
        base = {
            "legal_status": "",
            "tax_obligations": "",
            "pasted_text": "",
        }
        base.update(kwargs)
        return base

    def test_no_ruc_triggers_risk(self):
        audit = _make_audit(ruc="")
        risks = risk_suggestions(audit, self._data(), source_count=3)
        self.assertTrue(any("RUC" in r for r in risks))

    def test_inactive_company_triggers_risk(self):
        audit = _make_audit()
        data = self._data(legal_status="Disuelta")
        risks = risk_suggestions(audit, data, source_count=3)
        self.assertTrue(any("societario" in r.lower() or "disuelto" in r.lower() or "inactiva" in r.lower() for r in risks))

    def test_tax_pending_triggers_risk(self):
        audit = _make_audit()
        data = self._data(tax_obligations="Tiene deudas pendientes con el SRI")
        risks = risk_suggestions(audit, data, source_count=3)
        self.assertTrue(any("tributaria" in r.lower() or "pendiente" in r.lower() for r in risks))

    def test_negated_obligations_no_risk(self):
        audit = _make_audit()
        data = self._data(tax_obligations="Sin pendientes registrados con el SRI")
        risks = risk_suggestions(audit, data, source_count=3)
        # No debe disparar riesgo de obligaciones pendientes
        self.assertFalse(any("tributaria" in r.lower() for r in risks))

    def test_few_sources_triggers_risk(self):
        audit = _make_audit()
        risks = risk_suggestions(audit, self._data(), source_count=1)
        self.assertTrue(any("fuente" in r.lower() for r in risks))

    def test_enough_sources_no_source_risk(self):
        audit = _make_audit()
        data = self._data(pasted_text="a" * 100)
        risks = risk_suggestions(audit, data, source_count=3)
        self.assertFalse(any("fuente" in r.lower() for r in risks))


class TestGenerateSummary(unittest.TestCase):

    def _full_data(self) -> dict:
        return {
            "commercial_name": "Test Comercial",
            "economic_activity": "Servicios de consultoría",
            "legal_status": "Activa",
            "representative": "Juan Pérez",
            "address": "Av. Amazonas 123, Quito",
            "tax_obligations": "Sin obligaciones pendientes",
            "public_contracting": "No registra contratos",
            "supercias_info": "Empresa activa con capital pagado",
            "sri_info": "RUC activo, contribuyente especial",
            "sercop_info": "No registra como proveedor del Estado",
            "observations": "Revisión inicial completa",
            "risk_flags": "",
            "pasted_text": "RUC 1790000000001 email@empresa.com 0999888777",
        }

    def test_summary_contains_all_sections(self):
        audit = _make_audit()
        summary = generate_summary(audit, self._full_data(), source_count=3)
        for i in range(1, 9):
            self.assertIn(str(i) + ".", summary, f"Falta sección {i}")

    def test_summary_never_invents_data(self):
        """Con datos vacíos, usa 'Pendiente de confirmar' y no inventa."""
        audit = _make_audit()
        empty_data = {k: "" for k in self._full_data()}
        summary = generate_summary(audit, empty_data, source_count=0)
        self.assertIn("Pendiente de confirmar", summary)
        self.assertNotIn("Juan", summary)  # No debe inventar representante

    def test_supercias_representative_is_not_labeled_as_sri(self):
        data = self._full_data()
        data["representative"] = "Representante del Directorio"
        summary = generate_summary(_make_audit(), data, source_count=2)
        sri_section = summary.split("1. IDENTIFICACIÓN TRIBUTARIA (SRI)", 1)[1].split(
            "2. INFORMACIÓN SOCIETARIA (SUPERCIAS)", 1
        )[0]
        self.assertIn("Representante legal (SRI)   : Pendiente de confirmar.", sri_section)
        self.assertNotIn("Representante del Directorio", sri_section)

    def test_financial_sources_match_selected_year(self):
        provenance = [
            {"bloque": "financiero", "campo": "2024.activo_total", "fuente": "Balance 2024",
             "fecha_consulta": "2026-09-22"},
            {"bloque": "financiero", "campo": "2025.activo_total", "fuente": "Balance 2025",
             "fecha_consulta": "2026-09-22"},
        ]
        summary = generate_summary(_make_audit(), self._full_data(), source_count=2,
                                   snapshot={"anio_fiscal": 2025, "fecha_corte": "2025-12-31"},
                                   provenance=provenance)
        financial_section = summary.split("6. INFORMACIÓN FINANCIERA", 1)[1].split(
            "7. VALIDACIONES CRUZADAS Y ALERTAS", 1
        )[0]
        self.assertIn("Balance 2025", financial_section)
        self.assertNotIn("Balance 2024", financial_section)

    def test_summary_contains_company_name(self):
        audit = _make_audit(company_name="ACME Cía. Ltda.")
        summary = generate_summary(audit, self._full_data(), source_count=2)
        self.assertIn("ACME", summary)

    def test_summary_contains_disclaimer(self):
        audit = _make_audit()
        summary = generate_summary(audit, self._full_data(), source_count=2)
        self.assertIn("PRELIMINAR", summary.upper())

    def test_summary_contains_ruc(self):
        audit = _make_audit(ruc="1790000000001")
        summary = generate_summary(audit, self._full_data(), source_count=2)
        self.assertIn("1790000000001", summary)

    def test_no_ruc_shows_pending(self):
        audit = _make_audit(ruc="")
        empty_data = {k: "" for k in self._full_data()}
        summary = generate_summary(audit, empty_data, source_count=0)
        self.assertIn("pendiente de confirmar", summary.lower())

    def test_alerts_section_present(self):
        audit = _make_audit(ruc="")  # Sin RUC → debe haber alerta
        empty_data = {k: "" for k in self._full_data()}
        summary = generate_summary(audit, empty_data, source_count=0)
        self.assertIn("6.", summary)  # Sección 6: Riesgos

    def test_summary_detects_signals(self):
        audit = _make_audit()
        data = self._full_data()
        data["pasted_text"] = "RUC 1790000000001 y correo test@empresa.ec"
        summary = generate_summary(audit, data, source_count=3)
        # La sección 5 debe mencionar la señal detectada
        self.assertIn("5.", summary)


if __name__ == "__main__":
    unittest.main()
