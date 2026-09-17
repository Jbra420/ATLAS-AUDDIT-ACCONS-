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


if __name__ == "__main__":
    unittest.main()
