"""Pruebas de la búsqueda automática y persistencia selectiva de datos SRI."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import (
    apply_sri_research_result,
    authenticate,
    connect,
    create_company_audit,
    get_company_location,
    get_company_profile,
    init_db,
    upsert_company_profile,
)
from services.company_research import build_sri_result


SRI_RECORD = {
    "ruc": "0190314014001",
    "name": "IMPORTADORA AUTOMOTRIZ SALINAS S.A.",
    "city": "CUENCA",
    "activity_hint": "VENTA DE PARTES PARA VEHICULOS",
    "taxpayer_status": "ACTIVO",
    "taxpayer_class": "GEN",
    "start_date": "2002-05-30 00:00:00",
    "update_date": "2026-01-30 16:51:40",
    "accounting_required": "S",
    "taxpayer_type": "SOCIEDAD",
    "trade_name": "RECTIFICADORA SALINAS",
    "province": "AZUAY",
    "canton": "CUENCA",
    "parish": "SAN BLAS",
    "ciiu_code": "G453000",
    "withholding_agent": "S",
    "special_taxpayer": "N",
}


class TestCompanyResearch(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.db = directory / "atlas.db"
        init_db(self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "Empresa pendiente", "0190314014001", "", "", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )

    def test_maps_official_sri_fields(self):
        result = build_sri_result(SRI_RECORD)

        self.assertEqual(result["profile"]["estado_contribuyente"], "ACTIVO")
        self.assertEqual(result["profile"]["obligado_contabilidad"], "SI")
        self.assertEqual(result["profile"]["contribuyente_especial"], "NO")
        self.assertEqual(result["profile"]["fecha_inicio_actividades"], "2002-05-30")
        self.assertEqual(result["location"]["provincia"], "AZUAY")
        self.assertIn("SAN BLAS, CUENCA, AZUAY", result["research"]["sri_info"])

    def test_preserves_supercias_fields_and_marks_only_sri(self):
        upsert_company_profile(
            self.audit_id,
            {
                "ruc": SRI_RECORD["ruc"],
                "representante_legal": "REPRESENTANTE EXISTENTE",
                "expediente_supercias": "EXP-123",
                "situacion_legal": "ACTIVA",
            },
            self.db,
        )
        result = build_sri_result(SRI_RECORD)

        apply_sri_research_result(self.audit_id, self.auditor["id"], result, self.db)
        apply_sri_research_result(self.audit_id, self.auditor["id"], result, self.db)

        profile = get_company_profile(self.audit_id, self.db)
        location = get_company_location(self.audit_id, self.db)
        self.assertEqual(profile["razon_social"], SRI_RECORD["name"])
        self.assertEqual(profile["representante_legal"], "REPRESENTANTE EXISTENTE")
        self.assertEqual(profile["expediente_supercias"], "EXP-123")
        self.assertEqual(profile["situacion_legal"], "ACTIVA")
        self.assertEqual(location["canton"], "CUENCA")

        with connect(self.db) as conn:
            checks = {
                row["fuente"]: row["estado"]
                for row in conn.execute(
                    "SELECT fuente, estado FROM source_checks WHERE audit_id = ?",
                    (self.audit_id,),
                )
            }
            source_count = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE audit_id = ? AND source_type = 'SRI'",
                (self.audit_id,),
            ).fetchone()[0]

        self.assertEqual(checks["SRI — Consulta de RUC"], "consultada")
        self.assertEqual(checks["Supercias — Portal societario"], "pendiente")
        self.assertEqual(checks["Supercias — Documentos económicos"], "pendiente")
        self.assertEqual(source_count, 1)


if __name__ == "__main__":
    unittest.main()
