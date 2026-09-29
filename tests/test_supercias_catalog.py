"""Pruebas del catálogo local de Supercías (Directorio de Compañías)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import (
    apply_sri_research_result,
    apply_supercias_research_result,
    authenticate,
    connect,
    create_company_audit,
    get_company_profile,
    init_db,
)
from services.company_research import build_sri_result, research_company_by_ruc
from services.supercias_catalog import build_supercias_result


SUPERCIAS_RECORD = {
    "expediente": "141528",
    "ruc": "0190314014001",
    "razon_social": "GRUCANQUI CIA. LTDA.",
    "situacion_legal": "ACTIVA",
    "fecha_constitucion": "24/08/2011",
    "tipo_compania": "RESPONSABILIDAD LIMITADA",
    "pais": "ECUADOR",
    "provincia": "AZUAY",
    "canton": "CUENCA",
    "ciudad": "CUENCA",
    "calle": "AV. DEL ESTADIO",
    "numero": "S/N",
    "interseccion": "FLORENCIA ASTUDILLO",
    "barrio": "ESTADIO",
    "telefono": "074105000",
    "representante": "CANDO SUAREZ MARIA DANIELA",
    "representante_cargo": "GERENTE GENERAL",
    "capital_suscrito": "769.304,00",
    "ciiu_nivel1": "I",
    "ciiu_nivel6": "I5510.01",
    "ultimo_balance": "2025",
    "_catalogo_fecha_actualizacion": "21/09/2026 01:00:17",
    "_catalogo_total_filas": "227665",
}

SRI_RECORD = {
    "ruc": "0190314014001",
    "name": "GRUCANQUI CIA LTDA",
    "city": "CUENCA",
    "activity_hint": "Servicios de alojamiento",
    "taxpayer_status": "ACTIVO",
    "taxpayer_class": "GEN",
    "start_date": "2011-08-24 00:00:00",
    "province": "AZUAY",
    "canton": "CUENCA",
    "parish": "EL SAGRARIO",
}


class TestBuildSuperciasResult(unittest.TestCase):
    def test_maps_official_fields(self):
        result = build_supercias_result(SUPERCIAS_RECORD)
        self.assertEqual(result["profile"]["expediente_supercias"], "141528")
        self.assertEqual(result["profile"]["situacion_legal"], "ACTIVA")
        self.assertEqual(result["profile"]["fecha_constitucion"], "2011-08-24")
        self.assertEqual(result["profile"]["representante_cargo"], "GERENTE GENERAL")
        self.assertEqual(result["profile"]["capital_suscrito"], "769.304,00")
        self.assertEqual(result["location"]["provincia"], "AZUAY")
        self.assertEqual(result["location"]["calle"], "AV. DEL ESTADIO")
        self.assertIn("Expediente: 141528", result["research"]["supercias_info"])
        self.assertEqual(result["catalogo"]["fecha_actualizacion"], "21/09/2026 01:00:17")

    def test_missing_optional_fields_do_not_crash(self):
        result = build_supercias_result({"ruc": "0190377210001", "razon_social": "X"})
        self.assertEqual(result["profile"]["capital_suscrito"], "")
        self.assertEqual(result["profile"]["situacion_legal"], "")


class TestApplySuperciasResearchResult(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.db = directory / "atlas.db"
        init_db(self.db, demo=True)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "Empresa pendiente", "0190314014001", "", "", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )

    def test_preserves_sri_fields_and_marks_only_supercias(self):
        sri_result = build_sri_result(SRI_RECORD)
        apply_sri_research_result(self.audit_id, self.auditor["id"], sri_result, self.db)

        supercias_result = build_supercias_result(SUPERCIAS_RECORD)
        apply_supercias_research_result(self.audit_id, self.auditor["id"], supercias_result, self.db)

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["estado_contribuyente"], "ACTIVO")  # de SRI, intacto
        self.assertEqual(profile["situacion_legal"], "ACTIVA")  # de Supercias
        self.assertEqual(profile["expediente_supercias"], "141528")
        self.assertEqual(profile["capital_suscrito"], "769.304,00")
        self.assertEqual(profile["supercias_fuente"], "catalogo_local")

        with connect(self.db) as conn:
            checks = {
                row["fuente"]: row["estado"]
                for row in conn.execute(
                    "SELECT fuente, estado FROM source_checks WHERE audit_id = ?", (self.audit_id,)
                )
            }
        self.assertEqual(checks["SRI — Consulta de RUC"], "consultada")
        self.assertEqual(checks["Supercias — Portal societario"], "consultada")
        self.assertEqual(checks["Supercias — Documentos económicos"], "pendiente")

    def test_does_not_overwrite_manual_edit(self):
        apply_sri_research_result(
            self.audit_id, self.auditor["id"], build_sri_result(SRI_RECORD), self.db,
        )
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE company_profiles SET situacion_legal = ? WHERE audit_id = ?",
                ("ACTIVA (verificado por el auditor)", self.audit_id),
            )

        apply_supercias_research_result(
            self.audit_id, self.auditor["id"], build_supercias_result(SUPERCIAS_RECORD), self.db,
        )

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["situacion_legal"], "ACTIVA (verificado por el auditor)")

    def test_idempotent_no_duplicate_sources(self):
        supercias_result = build_supercias_result(SUPERCIAS_RECORD)
        apply_supercias_research_result(self.audit_id, self.auditor["id"], supercias_result, self.db)
        apply_supercias_research_result(self.audit_id, self.auditor["id"], supercias_result, self.db)

        with connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE audit_id = ? AND source_type = 'Supercias' "
                "AND title = 'Directorio Supercías (catálogo local)'",
                (self.audit_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)


class TestLookupSuperciasCatalogMissing(unittest.TestCase):
    """Cuando el catálogo no fue importado, todo debe degradar sin excepciones."""

    def test_returns_none_when_catalog_file_absent(self):
        with patch.object(database.catalogos, "SUPERCIAS_CATALOG_PATH", Path(tempfile.mkdtemp()) / "no_existe.db"):
            self.assertIsNone(database.lookup_supercias_catalog("0190377210001"))

    def test_returns_none_when_ruc_not_in_an_existing_catalog(self):
        """Distinto del catálogo ausente: aquí el archivo existe (con su
        esquema real) pero el RUC buscado simplemente no está en él."""
        catalog_path = Path(tempfile.mkdtemp()) / "supercias_catalog.db"
        with connect(catalog_path) as conn:
            conn.execute(
                "CREATE TABLE supercias_catalog (ruc TEXT PRIMARY KEY, razon_social TEXT)"
            )
            conn.execute(
                "CREATE TABLE supercias_catalog_meta (id INTEGER PRIMARY KEY, "
                "total_filas TEXT, fecha_actualizacion TEXT, importado_at TEXT)"
            )
            conn.execute(
                "INSERT INTO supercias_catalog (ruc, razon_social) VALUES ('9999999999001', 'OTRA EMPRESA')"
            )
        with patch.object(database.catalogos, "SUPERCIAS_CATALOG_PATH", catalog_path):
            self.assertIsNone(database.lookup_supercias_catalog("0190314014001"))
            self.assertIsNotNone(database.lookup_supercias_catalog("9999999999001"))

    def test_research_company_by_ruc_reports_pending_when_catalog_absent(self):
        directory = Path(tempfile.mkdtemp())
        db = directory / "atlas.db"
        init_db(db, demo=True)
        auditor = authenticate("auditor", "auditor123", db)
        admin = authenticate("admin", "admin123", db)
        audit_id = create_company_audit(
            "Empresa sin Supercias", "0190314014001", "", "", "2026",
            auditor["id"], admin["id"], db,
        )
        with patch.object(database.catalogos, "SUPERCIAS_CATALOG_PATH", Path(tempfile.mkdtemp()) / "no_existe.db"):
            with patch("services.company_research.lookup_catastro", return_value=dict(SRI_RECORD)):
                outcome = research_company_by_ruc(audit_id, "0190314014001", auditor["id"], db)

        self.assertFalse(outcome["supercias_found"])
        self.assertEqual(outcome["pending_source"], "Supercias")
        self.assertEqual(outcome["supercias_populated_fields"], 0)


class TestResearchCompanyByRucPartialResults(unittest.TestCase):
    """SRI y Supercias se consultan de forma independiente: ninguna es un
    requisito para la otra, solo fallan juntas si ninguna tiene el RUC."""

    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.db = directory / "atlas.db"
        init_db(self.db, demo=True)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "Empresa parcial", "0190314014001", "", "", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )

    def test_supercias_only_when_sri_catastro_missing(self):
        with patch("services.company_research.lookup_catastro", return_value=None), \
             patch("services.company_research.lookup_supercias_catalog", return_value=dict(SUPERCIAS_RECORD)):
            outcome = research_company_by_ruc(self.audit_id, "0190314014001", self.auditor["id"], self.db)

        self.assertFalse(outcome["sri_found"])
        self.assertEqual(outcome["populated_fields"], 0)
        self.assertIsNone(outcome["result"])
        self.assertTrue(outcome["supercias_found"])
        self.assertEqual(outcome["pending_source"], "SRI")

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["expediente_supercias"], "141528")

    def test_sri_only_when_supercias_catalog_missing(self):
        with patch("services.company_research.lookup_catastro", return_value=dict(SRI_RECORD)), \
             patch("services.company_research.lookup_supercias_catalog", return_value=None):
            outcome = research_company_by_ruc(self.audit_id, "0190314014001", self.auditor["id"], self.db)

        self.assertTrue(outcome["sri_found"])
        self.assertFalse(outcome["supercias_found"])
        self.assertEqual(outcome["pending_source"], "Supercias")

    def test_raises_only_when_neither_source_has_the_ruc(self):
        with patch("services.company_research.lookup_catastro", return_value=None), \
             patch("services.company_research.lookup_supercias_catalog", return_value=None), \
             patch("services.company_research.lookup_balances_catalog", return_value=[]):
            with self.assertRaisesRegex(ValueError, "no consta en los catálogos locales"):
                research_company_by_ruc(self.audit_id, "0190314014001", self.auditor["id"], self.db)


if __name__ == "__main__":
    unittest.main()
