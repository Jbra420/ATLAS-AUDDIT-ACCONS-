"""
tests/test_radar.py — Pruebas de la lógica del Radar Empresarial
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import (
    authenticate,
    connect,
    create_company_audit,
    create_user,
    get_audit,
    init_db,
    load_demo_if_ruc_matches,
    get_company_profile,
    get_financial_snapshot,
    mark_document_reviewed,
    list_economic_documents,
)
from services.financial import compute_indicators
from services.summary import generate_summary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> Path:
    """Crea una base de datos temporal para pruebas."""
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test_radar.db"
    init_db(db_path, demo=True)
    return db_path

def _admin_row(db_path: Path) -> sqlite3.Row:
    return authenticate("admin", "admin123", db_path)

def _auditor_row(db_path: Path) -> sqlite3.Row:
    return authenticate("auditor", "auditor123", db_path)


class TestRadarEmpresarial(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)
        self.admin = _admin_row(self.db)
        # Create an audit for testing
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", "0190377210001", "Quito",
            "Servicios de consultoría", "2026", self.auditor["id"], self.admin["id"], self.db
        )

    # Nota: la validación real de RUC (formato, dígito verificador, etc.) se
    # prueba a fondo en tests/test_ruc_validator.py contra services.ruc_validator.
    # No se reimplementa aquí un validador de mentira.

    # 1. test_demo_data_loaded — Ficha demo carga correctamente
    def test_demo_data_loaded(self):
        loaded = load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        self.assertTrue(loaded)
        profile = get_company_profile(self.audit_id, self.db)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["razon_social"], "GRUCANQUI CIA. LTDA")
        
        financial = get_financial_snapshot(self.audit_id, self.db)
        self.assertIsNotNone(financial)
        self.assertEqual(financial["activo_total"], 3108776.58)

    # 2. test_financial_indicators — Cálculos de endeudamiento y margen neto
    def test_financial_indicators(self):
        snapshot = {
            "activo_total": 100000.0,
            "pasivo_total": 65000.0,
            "patrimonio_neto": 40000.0,
            "ingresos_401": 200000.0,
            "otros_ingresos_403": 10000.0,
            "costo_ventas_501": 150000.0,
            "gastos_502": 30000.0,
            "utilidad_neta_707": 30000.0
        }
        inds = compute_indicators(snapshot)
        self.assertEqual(inds["ingresos_totales"], 210000.0)
        self.assertEqual(inds["gastos_totales"], 180000.0)
        self.assertAlmostEqual(inds["razon_endeudamiento"], 0.65) # 65k / 100k
        self.assertAlmostEqual(inds["margen_neto"], 0.142857, places=5) # 30k / 210k
        self.assertAlmostEqual(inds["patrimonio_sobre_activo"], 0.4) # 40k / 100k
        self.assertTrue(any("supera el umbral" in a["mensaje"] for a in inds["alertas"]))

    # 3. test_summary_generation — Resumen incluye indicadores financieros
    def test_summary_generation(self):
        load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        audit = get_audit(self.audit_id, self.admin, self.db)
        research = {"commercial_name": "", "legal_status": "", "economic_activity": "", "representative": "", "address": "", "tax_obligations": "", "public_contracting": "", "supercias_info": "", "sri_info": "", "sercop_info": "", "observations": "", "risk_flags": "", "pasted_text": ""}
        
        # Load db data
        financial = get_financial_snapshot(self.audit_id, self.db)
        inds = compute_indicators(dict(financial)) if financial else {}
        
        summary = generate_summary(audit, research, source_count=0, profile=dict(get_company_profile(self.audit_id, self.db)), location=None, admins=[], shareholders=[], snapshot=dict(financial) if financial else None, indicators=inds, source_checks=[])
        self.assertIn("INFORMACIÓN FINANCIERA", summary)
        self.assertIn("Indicadores calculados", summary)
        self.assertIn("Razón endeudamiento", summary)
        self.assertIn("Margen neto", summary)

    # 4. test_auditor_isolation — Auditor no puede ver auditoría ajena
    def test_auditor_isolation(self):
        with connect(self.db) as conn:
            create_user(conn, "auditor2", "Auditor Dos", "auditor", "clave-dos")
        auditor2 = authenticate("auditor2", "clave-dos", self.db)
        
        # Auditor2 intenta ver audit_id asignado a auditor1
        audit = get_audit(self.audit_id, auditor2, self.db)
        self.assertIsNone(audit)

    # Nota: el comportamiento de mark_ready se prueba una sola vez, en
    # tests/test_database.py::test_mark_ready_no_longer_sends_to_review,
    # para no duplicar la misma aserción en dos archivos.

    # 5. test_document_mark_reviewed — Documento pasa a estado revisado
    def test_document_mark_reviewed(self):
        load_demo_if_ruc_matches(self.audit_id, "0190377210001", self.db)
        docs = list_economic_documents(self.audit_id, self.db)
        self.assertGreater(len(docs), 0)
        
        doc_id = docs[0]["id"]
        mark_document_reviewed(self.audit_id, doc_id, self.auditor["id"], self.db)
        
        docs_updated = list_economic_documents(self.audit_id, self.db)
        doc = next(d for d in docs_updated if d["id"] == doc_id)
        self.assertEqual(doc["estado"], "revisado")
        self.assertEqual(doc["revisado_por"], self.auditor["id"])

if __name__ == "__main__":
    unittest.main()
