"""Carga e integracion del reporte de estados financieros por ramo."""
from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import (
    apply_balances_catalog_result,
    authenticate,
    connect,
    create_company_audit,
    get_financial_context,
    init_db,
    list_provenance,
    lookup_balances_catalog,
    set_audit_fiscal_year,
    upsert_financial_statement,
)
from scripts.update_balances_catalog import FIELDS, load_catalog
from services.company_research import research_company_by_ruc
from views.auditor.radar import tab_financiero
from services.financial import compute_indicators


RUC = "0190377210001"
HEADER = ["AÑO", "EXPEDIENTE", "RUC", "NOMBRE", "CIIU", *(f"CUENTA_{c}" for c in FIELDS)]
AMOUNTS = ["3.108.776,58", "2107881,79", "1000894,79", "1862784,91",
           "11761,20", "895805,67", "953625,41", "-7279,63"]


class TestBalancesCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = self.root / "balances.db"
        self.app_db = self.root / "atlas.db"
        init_db(self.app_db)
        self.auditor = authenticate("auditor", "auditor123", self.app_db)
        self.admin = authenticate("admin", "admin123", self.app_db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", RUC, "Cuenca", "", "2025",
            self.auditor["id"], self.admin["id"], self.app_db,
        )
        self._write_source()

    def _write_source(self, rows=None):
        with (self.root / "catalogo_2025_1.txt").open("w", encoding="cp1252", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerows((code, f"Descripción de cuenta {code}") for code in FIELDS)
        with (self.root / "balances_2025_1.txt").open("w", encoding="cp1252", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(HEADER)
            writer.writerows(rows or [["2025", "141528", RUC, "GRUCANQUI CIA. LTDA", "G0000", *AMOUNTS]])

    def test_import_lookup_idempotence_and_amounts(self):
        self.assertEqual(load_catalog(self.root, self.catalog), (2025, 1, 0))
        self.assertEqual(load_catalog(self.root, self.catalog), (2025, 1, 0))
        record = lookup_balances_catalog(RUC, self.catalog)[0]
        self.assertEqual(record["nombre"], "GRUCANQUI CIA. LTDA")
        self.assertEqual(record["activo_total"], 3108776.58)
        self.assertEqual(record["utilidad_neta_707"], -7279.63)
        self.assertEqual(record["fuente_sha256"], hashlib.sha256(
            (self.root / "balances_2025_1.txt").read_bytes()).hexdigest())

    def test_invalid_reimport_keeps_previous_year(self):
        load_catalog(self.root, self.catalog)
        bad = ["2025", "141528", RUC, "GRUCANQUI", "G0000", *AMOUNTS]
        bad[HEADER.index("CUENTA_1")] = "no numerico"
        self._write_source([bad])
        with self.assertRaisesRegex(ValueError, "no es numerica"):
            load_catalog(self.root, self.catalog)
        self.assertEqual(lookup_balances_catalog(RUC, self.catalog)[0]["activo_total"], 3108776.58)

    def test_invalid_ruc_is_counted_not_guessed(self):
        valid = ["2025", "141528", RUC, "GRUCANQUI", "G0000", *AMOUNTS]
        invalid = ["2025", "42", "O195123391001", "Otra compañía", "G0000", *AMOUNTS]
        self._write_source([valid, invalid])
        self.assertEqual(load_catalog(self.root, self.catalog), (2025, 1, 1))
        self.assertEqual(lookup_balances_catalog("O195123391001", self.catalog), [])

    def test_search_loads_only_missing_values_and_leaves_year_unselected(self):
        load_catalog(self.root, self.catalog)
        upsert_financial_statement(
            self.audit_id, 2025, {"activo_total": "4000000"},
            user_id=self.auditor["id"], db_path=self.app_db,
        )
        with patch("services.company_research.lookup_catastro", return_value=None), \
             patch("services.company_research.lookup_supercias_catalog", return_value=None), \
             patch("services.company_research.lookup_balances_catalog",
                   side_effect=lambda ruc: lookup_balances_catalog(ruc, self.catalog)):
            result = research_company_by_ruc(self.audit_id, RUC, self.auditor["id"], self.app_db)
            again = research_company_by_ruc(self.audit_id, RUC, self.auditor["id"], self.app_db)
        self.assertEqual((result["financial_years"], again["financial_years"]), (1, 1))
        self.assertEqual(result["pending_source"], "SRI y Supercias")
        context = get_financial_context(self.audit_id, self.app_db)
        self.assertEqual(context["anio_fiscal"], 2025)
        self.assertEqual(context["snapshot"]["origen"], "anual")
        row = context["years"][0]
        self.assertEqual(row["activo_total"], 4000000)
        self.assertEqual(row["otros_ingresos_403"], 11761.20)
        self.assertIsNone(row["utilidad_antes_part_imp"])
        self.assertIn("Origen mixto", row["fuente"])
        self.assertEqual(len([p for p in list_provenance(self.audit_id, self.app_db)
                              if p["campo"] == "2025.otros_ingresos_403"]), 1)
        with connect(self.app_db) as conn:
            status = conn.execute("SELECT status FROM audits WHERE id = ?", (self.audit_id,)).fetchone()[0]
            source_count = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE audit_id = ? AND title LIKE 'Estados financieros%'",
                (self.audit_id,),
            ).fetchone()[0]
        self.assertEqual(source_count, 1)
        self.assertEqual(status, "en_investigacion")

        set_audit_fiscal_year(self.audit_id, 2025, user_id=self.auditor["id"], db_path=self.app_db)
        context = get_financial_context(self.audit_id, self.app_db)
        self.assertEqual(context["snapshot"]["otros_ingresos_403"], 11761.20)
        page = tab_financiero.build(self.audit_id, compute_indicators(context["snapshot"]),
                                   financial=context, ruc=RUC)
        self.assertIn("Fuente de cifras", page)
        self.assertIn("Supercias", page)

    def test_zero_is_a_value_not_a_missing_field(self):
        amounts = list(AMOUNTS)
        amounts[list(FIELDS).index("403")] = "0,00"
        self._write_source([["2025", "141528", RUC, "GRUCANQUI", "G0000", *amounts]])
        load_catalog(self.root, self.catalog)
        rows = lookup_balances_catalog(RUC, self.catalog)
        apply_balances_catalog_result(self.audit_id, self.auditor["id"], rows, self.app_db)
        self.assertEqual(get_financial_context(self.audit_id, self.app_db)["years"][0]["otros_ingresos_403"], 0)


if __name__ == "__main__":
    unittest.main()
