"""Pruebas del flujo de consulta de 9 pasos (Fase 6 del levantamiento)."""
from __future__ import annotations

import unittest

from services.flujo import (
    CON_ALERTAS,
    EN_AVANCE,
    PENDIENTE_PASO,
    casilleros_con_valor,
    pasos_levantamiento,
)
from services.validaciones import evaluar_levantamiento


def _pasos(profile=None, snapshot=None, sri=False, supercias=False, admins=None, documentos=False):
    validacion = evaluar_levantamiento({"ruc": "0190377210001"}, profile or {}, {}, admins or [], [], snapshot)
    return {p["numero"]: p for p in pasos_levantamiento(
        validacion, sri, supercias, casilleros_con_valor(snapshot), documentos,
    )}


class TestFlujo(unittest.TestCase):
    def test_nine_steps_in_requirement_order(self):
        pasos = _pasos()
        self.assertEqual(sorted(pasos), list(range(1, 10)))
        self.assertEqual([pasos[n]["tab"] for n in range(1, 10)],
                         ["sri", "supercias", "supercias", "admins", "accionistas",
                          "documentos", "indicadores", "indicadores", "resumen"])
        self.assertEqual(pasos[9]["fuente"], "Sistema")

    def test_empty_expediente(self):
        pasos = _pasos()
        self.assertEqual(pasos[1]["estado"], EN_AVANCE, "El RUC ya cumple su formato")
        self.assertTrue(all(pasos[n]["estado"] == PENDIENTE_PASO for n in range(2, 10)))

    def test_partial_block_is_in_progress(self):
        pasos = _pasos(profile={"expediente_supercias": "141528"}, supercias=True)
        self.assertEqual(pasos[2]["estado"], "completo")
        self.assertEqual(pasos[3]["estado"], EN_AVANCE)

    def test_figures_without_fiscal_year_do_not_complete_steps_7_and_8(self):
        legacy = {"activo_total": 1.0, "pasivo_total": 1.0, "patrimonio_neto": 0.0, "anio_fiscal": None}
        pasos = _pasos(snapshot=legacy)
        self.assertEqual((pasos[6]["estado"], pasos[7]["estado"], pasos[8]["estado"]),
                         (PENDIENTE_PASO, PENDIENTE_PASO, PENDIENTE_PASO))

    def test_step_six_requires_document_consulted_and_fiscal_year(self):
        snapshot = {"anio_fiscal": 2025, "activo_total": 100.0}
        self.assertEqual(_pasos(snapshot=snapshot)[6]["estado"], EN_AVANCE)
        self.assertEqual(_pasos(snapshot=snapshot, documentos=True)[6]["estado"], "completo")
        self.assertEqual(_pasos(documentos=True)[6]["estado"], EN_AVANCE)

    def test_step_nine_asks_for_review_when_there_are_alerts(self):
        pasos = _pasos(profile={"estado_contribuyente": "SUSPENDIDO"})
        self.assertEqual(pasos[9]["estado"], CON_ALERTAS)
        self.assertIn("1 alerta(s)", pasos[9]["detalle"])

    def test_untreated_critical_alert_is_reported(self):
        pasos = _pasos(profile={"contribuyente_fantasma": "SI"})
        self.assertIn("crítica(s) sin tratamiento", pasos[9]["detalle"])


if __name__ == "__main__":
    unittest.main()
