"""
tests/test_dossier.py - Pruebas de la ficha final de resultados.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import (
    add_source,
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
    load_demo_if_ruc_matches,
)
from services.company_search import build_source_map
from services.dossier import build_dossier_model, build_dossier_text
from services.financial import compute_indicators


def _make_db() -> Path:
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test_dossier.db"
    init_db(db_path)
    return db_path


class TestDossier(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.admin = authenticate("admin", "admin123", self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.audit_id = get_audit(1, self.admin, self.db)["id"]

    def _build(self, audit_id: int | None = None):
        audit_id = audit_id or self.audit_id
        audit = get_audit(audit_id, self.admin, self.db)
        research = get_research(audit_id, self.db)
        profile = get_company_profile(audit_id, self.db)
        location = get_company_location(audit_id, self.db)
        admins = list_administrators(audit_id, self.db)
        shareholders = list_shareholders(audit_id, self.db)
        docs = list_economic_documents(audit_id, self.db)
        snapshot = get_financial_snapshot(audit_id, self.db)
        source_checks = list_source_checks(audit_id, self.db)
        sources = list_sources(audit_id, self.db)
        indicators = compute_indicators(dict(snapshot) if snapshot else None)
        source_map = build_source_map(
            audit, research, profile, location, admins, shareholders,
            docs, snapshot, source_checks, sources,
        )
        return build_dossier_model(
            audit, research, profile, location, admins, shareholders,
            snapshot, indicators, source_map, sources,
        )

    def test_dossier_model_contains_core_sections(self):
        dossier = self._build()

        self.assertEqual(dossier["title"], "Ficha final de resultados")
        self.assertIn("metrics", dossier)
        self.assertIn("identity", dossier)
        self.assertIn("source_status", dossier)
        self.assertGreaterEqual(dossier["metrics"]["source_percent"], 0)

    def test_dossier_text_exports_evidence_and_sections(self):
        add_source(
            self.audit_id,
            "Consulta SERCOP",
            "https://www.compraspublicas.gob.ec/",
            "SERCOP",
            "Hallazgo: No registra contratos publicos",
            self.auditor["id"],
            self.db,
        )
        dossier = self._build()
        text = build_dossier_text(dossier)

        self.assertIn("ATLAS - FICHA FINAL DE RESULTADOS", text)
        self.assertIn("ESTADO DE FUENTES", text)
        self.assertIn("EVIDENCIA REGISTRADA", text)
        self.assertIn("Consulta SERCOP", text)

    def test_empty_assignment_does_not_crash(self):
        audit_id = create_company_audit(
            "Empresa Nueva", "", "Cuenca", "Servicios", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )
        dossier = self._build(audit_id)
        text = build_dossier_text(dossier)

        self.assertIn("Empresa Nueva", text)
        self.assertIn("En construccion", text)
        self.assertGreater(dossier["metrics"]["pending_count"], 0)

    def test_dossier_has_no_document_checklist(self):
        dossier = self._build()
        self.assertNotIn("documents", dossier)
        self.assertNotIn("DOCUMENTOS ECONOMICOS", build_dossier_text(dossier))

    def test_demo_loaded_dossier_uses_company_profile(self):
        load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        dossier = self._build()
        identity_values = " ".join(item["value"] for item in dossier["identity"])

        self.assertIn("0190377210001", identity_values)
        self.assertGreater(len(dossier["financial"]), 0)


if __name__ == "__main__":
    unittest.main()
