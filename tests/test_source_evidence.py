"""
tests/test_source_evidence.py — Pruebas de evidencia y bitacora de fuentes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import (
    add_source,
    append_research_source_note,
    authenticate,
    get_audit,
    get_research,
    init_db,
    list_sources,
    list_source_checks,
    mark_matching_source_checked,
    refresh_summary,
)


def _make_db() -> Path:
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test_source_evidence.db"
    init_db(db_path, demo=True)
    return db_path


class TestSourceEvidence(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.admin = authenticate("admin", "admin123", self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.audit_id = get_audit(1, self.admin, self.db)["id"]

    def test_source_requires_valid_url_when_present(self):
        with self.assertRaisesRegex(ValueError, "URL"):
            add_source(
                self.audit_id,
                "Consulta sin URL valida",
                "www.sercop.gob.ec",
                "SERCOP",
                "Referencia incompleta",
                self.auditor["id"],
                self.db,
            )

    def test_source_requires_allowed_type(self):
        with self.assertRaisesRegex(ValueError, "Tipo de fuente"):
            add_source(
                self.audit_id,
                "Fuente no clasificada",
                "https://example.com",
                "Red social informal",
                "No debe aceptarse como tipo principal",
                self.auditor["id"],
                self.db,
            )

    def test_sercop_evidence_updates_log_check_and_research(self):
        add_source(
            self.audit_id,
            "Consulta SERCOP",
            "https://www.compraspublicas.gob.ec/",
            "SERCOP",
            "Hallazgo: No registra contratos publicos vigentes",
            self.auditor["id"],
            self.db,
        )
        marked = mark_matching_source_checked(
            self.audit_id,
            "SERCOP",
            self.auditor["id"],
            "No registra contratos publicos vigentes",
            self.db,
        )
        append_research_source_note(
            self.audit_id,
            self.auditor["id"],
            "SERCOP",
            "No registra contratos publicos vigentes",
            "Busqueda realizada por RUC en portal SERCOP.",
            self.db,
        )

        self.assertTrue(marked)
        checks = list_source_checks(self.audit_id, self.db)
        sercop = next(check for check in checks if "SERCOP" in check["fuente"])
        self.assertEqual(sercop["estado"], "consultada")

        research = get_research(self.audit_id, self.db)
        self.assertIn("No registra contratos publicos", research["sercop_info"])
        self.assertIn("No registra contratos publicos", research["public_contracting"])

        summary = refresh_summary(self.audit_id, self.db)
        self.assertIn("Evidencia registrada", summary)
        self.assertIn("Consulta SERCOP", summary)

    def test_web_evidence_updates_observations_and_text(self):
        add_source(
            self.audit_id,
            "Busqueda web general",
            "https://www.google.com/search?q=grucanqui",
            "Busqueda web general",
            "Hallazgo: Sin noticias negativas visibles",
            self.auditor["id"],
            self.db,
        )
        append_research_source_note(
            self.audit_id,
            self.auditor["id"],
            "Busqueda web general",
            "Sin noticias negativas visibles",
            "No se identificaron sanciones en busqueda preliminar.",
            self.db,
        )

        research = get_research(self.audit_id, self.db)
        self.assertIn("Sin noticias negativas", research["observations"])
        self.assertIn("No se identificaron sanciones", research["pasted_text"])
        self.assertEqual(len(list_sources(self.audit_id, self.db)), 1)


if __name__ == "__main__":
    unittest.main()
