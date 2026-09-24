"""Pruebas de información financiera por año fiscal (Fase 4 del levantamiento).

Reglas del requisito:
  - Parámetro previo obligatorio: registrar explícitamente el año fiscal.
  - La información financiera se guarda por año y no se sobrescribe.
  - Totales de ingresos (401 + 403) y gastos (501 + 502) calculados.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from database import (
    DEFAULT_ECONOMIC_DOCUMENTS,
    authenticate,
    connect,
    create_company_audit,
    get_audit_context,
    get_financial_context,
    init_db,
    list_financial_statements,
    list_provenance,
    load_demo_if_ruc_matches,
    set_audit_fiscal_year,
    upsert_financial_statement,
)
from services.financial import comparativo, compute_indicators
from services.summary import generate_summary
from views.auditor.radar import tab_financiero

REFERENCE_RUC = "0190377210001"
CIFRAS_2025 = {
    "activo_total": "3108776.58", "pasivo_total": "2107881.79", "patrimonio_neto": "1000894.79",
    "ingresos_401": "1862784.91", "otros_ingresos_403": "11761.20",
    "costo_ventas_501": "895805.67", "gastos_502": "953625.41", "utilidad_neta_707": "7279.63",
}


class _FinancialCase(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = self._audit(REFERENCE_RUC)

    def _audit(self, ruc: str, period: str = "2025") -> int:
        return create_company_audit(
            "GRUCANQUI CIA. LTDA", ruc, "Cuenca", "", period,
            self.auditor["id"], self.admin["id"], self.db,
        )

    def _save(self, anio, data, audit_id=None, **kwargs):
        return upsert_financial_statement(audit_id or self.audit_id, anio, data, db_path=self.db, **kwargs)


class TestFiscalYear(_FinancialCase):
    def test_register_fiscal_year(self):
        set_audit_fiscal_year(self.audit_id, "2025", user_id=self.auditor["id"], db_path=self.db)
        self.assertEqual(get_financial_context(self.audit_id, self.db)["anio_fiscal"], 2025)
        traza = [r for r in list_provenance(self.audit_id, self.db) if r["campo"] == "anio_fiscal"]
        self.assertEqual((traza[0]["valor_nuevo"], traza[0]["registrado_por"]), ("2025", self.auditor["id"]))

    def test_invalid_years(self):
        for value in ("25", "abc", "1985", str(date.today().year + 1), ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                set_audit_fiscal_year(self.audit_id, value, db_path=self.db)

    def test_requires_ruc(self):
        audit_id = self._audit("")
        with self.assertRaisesRegex(ValueError, "RUC"):
            set_audit_fiscal_year(audit_id, 2025, db_path=self.db)


class TestStatementsByYear(_FinancialCase):
    def test_new_year_does_not_overwrite_previous(self):
        self._save(2025, CIFRAS_2025)
        self._save(2024, {"activo_total": "2900000", "pasivo_total": "2000000"})

        rows = {r["anio_fiscal"]: r for r in list_financial_statements(REFERENCE_RUC, self.db)}
        self.assertEqual(sorted(rows), [2024, 2025])
        self.assertAlmostEqual(rows[2025]["activo_total"], 3108776.58)
        self.assertAlmostEqual(rows[2024]["activo_total"], 2900000)
        self.assertEqual(rows[2025]["fecha_corte"], "2025-12-31")

    def test_same_ruc_shares_years_across_audits(self):
        self._save(2024, {"activo_total": "2900000"})
        otra = self._audit(REFERENCE_RUC, period="2026")
        self._save(2025, CIFRAS_2025, audit_id=otra)

        years = get_financial_context(otra, self.db)["years"]
        self.assertEqual([y["anio_fiscal"] for y in years], [2025, 2024])

    def test_number_rules(self):
        self._save(2025, {"patrimonio_neto": "-1500.50", "utilidad_neta_707": "-20", "activo_total": "1500,25"})
        row = list_financial_statements(REFERENCE_RUC, self.db)[0]
        self.assertEqual((row["patrimonio_neto"], row["utilidad_neta_707"], row["activo_total"]),
                         (-1500.50, -20.0, 1500.25))
        self.assertIsNone(row["pasivo_total"])
        for bad in ({"activo_total": "-1"}, {"ingresos_401": "mil"}, {"gastos_502": "-5"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._save(2025, bad)

    def test_invalid_junta_date(self):
        for value in ("31/12/2025", (date.today().replace(year=date.today().year + 1)).isoformat()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self._save(2025, {"fecha_junta_aprobacion": value})

    def test_each_changed_box_is_traced(self):
        self._save(2025, CIFRAS_2025, fecha_consulta="2026-09-20", user_id=self.auditor["id"])
        self._save(2025, CIFRAS_2025)  # sin cambios: no agrega trazas
        self._save(2025, {**CIFRAS_2025, "activo_total": "3108777.58"})

        rows = [r for r in list_provenance(self.audit_id, self.db) if r["bloque"] == "financiero"]
        self.assertEqual(len(rows), len(CIFRAS_2025) + 1)
        self.assertEqual(rows[0]["fuente"], "Supercias — Documentos económicos (EEFF al 2025-12-31)")
        self.assertEqual(rows[0]["fecha_consulta"], "2026-09-20")
        last = rows[-1]
        self.assertEqual((last["campo"], last["valor_anterior"], last["valor_nuevo"]),
                         ("2025.activo_total", "3108776.58", "3108777.58"))


class TestFinancialContext(_FinancialCase):
    def test_snapshot_is_the_fiscal_year_of_the_audit(self):
        self._save(2024, {"activo_total": "1"})
        self._save(2025, CIFRAS_2025)
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)

        snapshot = get_audit_context(self.audit_id, self.db)["snapshot"]
        self.assertEqual((snapshot["origen"], snapshot["anio_fiscal"]), ("anual", 2025))
        self.assertTrue(compute_indicators(snapshot)["tiene_datos"])

    def test_figures_without_year_remain_visible_but_marked(self):
        load_demo_if_ruc_matches(self.audit_id, REFERENCE_RUC, self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        self.assertEqual(ctx["snapshot"]["origen"], "sin_anio")
        self.assertIsNone(ctx["snapshot"]["anio_fiscal"])

    def test_demo_figures_never_reach_the_ruc_table(self):
        """El RUC demo es un cliente real: sus cifras demo no pueden
        mezclarse con los ejercicios de ese RUC."""
        load_demo_if_ruc_matches(self.audit_id, REFERENCE_RUC, self.db)
        self.assertEqual(list_financial_statements(REFERENCE_RUC, self.db), [])


class TestFormatoMoneda(unittest.TestCase):
    def test_negative_sign_goes_before_the_symbol(self):
        from services.financial import formato_moneda
        self.assertEqual(formato_moneda(-1500.5), "-$1,500.50")
        self.assertEqual(formato_moneda(1500.5), "$1,500.50")
        self.assertEqual(formato_moneda(None), "—")

    def test_comparative_shows_negative_variation(self):
        estados = [{"anio_fiscal": 2025, "otros_ingresos_403": 0.0}, {"anio_fiscal": 2024, "otros_ingresos_403": 1.0}]
        html = tab_financiero._comparative_table(estados, 2025)
        self.assertIn("-$1.00", html)
        self.assertNotIn("$-", html)


class TestComparativo(unittest.TestCase):
    def test_variation_against_closest_previous_year(self):
        estados = [
            {"anio_fiscal": 2025, "activo_total": 110.0, "ingresos_401": 50.0, "otros_ingresos_403": 5.0},
            {"anio_fiscal": 2023, "activo_total": 100.0, "ingresos_401": 40.0, "otros_ingresos_403": None},
        ]
        comp = comparativo(estados, 2025)
        self.assertEqual((comp["anios"], comp["base"], comp["previo"]), ([2025, 2023], 2025, 2023))
        activo = comp["filas"][0]
        self.assertEqual((activo["variacion"], activo["variacion_pct"]), (10.0, 0.1))
        total_ingresos = next(f for f in comp["filas"] if f["etiqueta"].startswith("Total ingresos"))
        self.assertTrue(total_ingresos["calculado"])
        self.assertEqual(total_ingresos["valores"], [55.0, 40.0])

    def test_single_year_has_no_variation(self):
        comp = comparativo([{"anio_fiscal": 2025, "activo_total": 1.0}], 2025)
        self.assertIsNone(comp["previo"])
        self.assertIsNone(comp["filas"][0]["variacion"])


class TestFinancialTab(_FinancialCase):
    def _html(self, **kwargs):
        ctx = get_audit_context(self.audit_id, self.db)
        return tab_financiero.build(
            self.audit_id, compute_indicators(ctx["snapshot"]), csrf_token="t",
            financial=ctx["financial"], ruc=REFERENCE_RUC, provenance=ctx["provenance"], **kwargs,
        )

    def test_year_is_not_asked_before_showing_figures(self):
        html = self._html()
        self.assertNotIn('action="/auditor/radar/financial-year"', html)
        self.assertIn("No hay cifras financieras disponibles", html)
        self.assertIn('action="/auditor/radar/financial"', html)

    def test_latest_year_with_figures_is_shown_without_choosing(self):
        self._save(2024, {"activo_total": "2900000"})
        self._save(2025, CIFRAS_2025)
        set_audit_fiscal_year(self.audit_id, 2006, db_path=self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        self.assertEqual((ctx["snapshot"]["origen"], ctx["snapshot"]["anio_fiscal"]), ("anual", 2025))
        html = self._html()
        self.assertIn('<span class="badge badge-green">Ejercicio 2025</span>', html)
        self.assertIn("Ejercicios con cifras: 2025, 2024", html)
        self.assertIn("$3,108,776.58", html)

    def test_opened_year_drives_the_whole_tab(self):
        self._save(2024, {"activo_total": "2900000"})
        self._save(2025, CIFRAS_2025)
        html = self._html(anio_edicion=2024)
        self.assertIn('<span class="badge badge-green">Ejercicio 2024</span>', html)
        self.assertIn('<div class="kpi-value">$2,900,000.00</div>', html)
        self.assertNotIn('<div class="kpi-value">$3,108,776.58</div>', html)
        self.assertIn('name="anio_fiscal" value="2024"', html)

        html_2012 = self._html(anio_edicion=2012)
        self.assertIn("Ejercicio 2012 sin cifras", html_2012)
        self.assertIn("2012</strong> (EEFF al 2012-12-31) — sin cifras registradas", html_2012)
        self.assertNotIn('<div class="kpi-value">$3,108,776.58</div>', html_2012)

    def test_registered_year_with_figures_is_kept(self):
        self._save(2024, {"activo_total": "2900000"})
        self._save(2025, CIFRAS_2025)
        set_audit_fiscal_year(self.audit_id, 2024, db_path=self.db)
        self.assertEqual(get_audit_context(self.audit_id, self.db)["snapshot"]["anio_fiscal"], 2024)

    def test_other_year_opens_its_own_figures(self):
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self._save(2025, CIFRAS_2025)

        html_2024 = self._html(anio_edicion=2024)
        self.assertIn('name="anio_fiscal" value="2024"', html_2024)
        self.assertNotIn('value="3108776.58"', html_2024,
                         "El formulario de 2024 no puede venir con las cifras de 2025")

        html_2025 = self._html()
        self.assertIn('name="anio_fiscal" value="2025"', html_2025)
        self.assertIn('name="activo_total" type="number" step="0.01" min="0" value="3108776.58"', html_2025)

    def test_figures_without_year_are_offered_for_confirmation(self):
        load_demo_if_ruc_matches(self.audit_id, REFERENCE_RUC, self.db)
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        html = self._html()
        self.assertIn("antes de exigir el año fiscal", html)
        self.assertIn('value="3108776.58"', html)

    def test_comparative_table_and_history(self):
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self._save(2025, CIFRAS_2025)
        self._save(2024, {"activo_total": "2900000"})
        html = self._html()
        self.assertIn("Variación 2025 vs 2024", html)
        self.assertIn("$208,776.58", html)
        self.assertIn("2025 · Total activo (casillero 1)", html)

    def test_read_only_has_no_forms(self):
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self.assertNotIn("<form", self._html(read_only=True))

    def test_without_ruc(self):
        html = tab_financiero.build(1, compute_indicators(None), csrf_token="t", ruc="")
        self.assertIn("Registre el RUC", html)
        self.assertNotIn("<form", html)


class TestSummaryAndDocuments(_FinancialCase):
    AUDIT = {"company_name": "GRUCANQUI", "ruc": REFERENCE_RUC, "period": "2025", "city": "", "activity_hint": ""}

    def test_summary_shows_year_and_every_box(self):
        set_audit_fiscal_year(self.audit_id, 2025, db_path=self.db)
        self._save(2025, CIFRAS_2025)
        snapshot = get_audit_context(self.audit_id, self.db)["snapshot"]
        text = generate_summary(self.AUDIT, {}, 0, snapshot=snapshot, indicators=compute_indicators(snapshot))
        self.assertRegex(text, r"Año fiscal\s+: 2025 \(EEFF al 2025-12-31\)")
        self.assertIn("6. INFORMACIÓN FINANCIERA", text)
        self.assertIn("Ingresos de actividades ordinarias (casillero 401): $1,862,784.91", text)
        self.assertIn("Total ingresos (401 + 403): $1,874,546.11", text)
        self.assertIn("Utilidad antes de participación e impuestos (casillero por confirmar): Pendiente", text)

    def test_summary_flags_figures_without_year(self):
        load_demo_if_ruc_matches(self.audit_id, REFERENCE_RUC, self.db)
        snapshot = get_audit_context(self.audit_id, self.db)["snapshot"]
        text = generate_summary(self.AUDIT, {}, 0, snapshot=snapshot, indicators=compute_indicators(snapshot))
        self.assertIn("cifras registradas sin año fiscal", text)
        self.assertIn("Confirmar el año fiscal de las cifras financieras registradas.", text)

    def test_previous_year_audit_report_added_once_to_existing_audits(self):
        nombre = "Informe de auditoría externa del año anterior"
        self.assertIn(nombre, DEFAULT_ECONOMIC_DOCUMENTS)
        with connect(self.db) as conn:
            conn.execute("DELETE FROM economic_documents WHERE audit_id = ? AND nombre = ?", (self.audit_id, nombre))
        init_db(self.db)
        init_db(self.db)
        with connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM economic_documents WHERE audit_id = ? AND nombre = ?",
                (self.audit_id, nombre),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
