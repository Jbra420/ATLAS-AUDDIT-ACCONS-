"""Prueba de aceptación del levantamiento de información (Fase 6).

Recorre el levantamiento completo con el caso de referencia GRUCANQUI CIA.
LTDA. (RUC 0190377210001) y comprueba los requisitos que evalúa
services/validaciones (los mismos que muestra la pestaña Resumen):

  - "Iniciar búsqueda" sobre los catálogos locales reales (SRI y Supercias).
  - Los datos que ningún catálogo publica (objeto social, identificaciones,
    presidente y accionistas) son DATOS DE PRUEBA. Las cifras financieras
    de 2025 coinciden con el reporte local descargado para este RUC.
  - Validaciones cruzadas, alertas y resumen.

Se omite si los catálogos locales no están importados o no contienen el RUC.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import database
from database import (
    add_administrator,
    add_shareholder,
    authenticate,
    create_company_audit,
    get_audit,
    get_audit_context,
    init_db,
    list_administrators,
    refresh_summary,
    register_alert_treatment,
    set_audit_fiscal_year,
    update_administrator,
    update_company_location_fields,
    update_company_profile_fields,
    upsert_financial_statement,
)
from services.company_research import research_company_by_ruc
from services.company_search import build_source_map

REFERENCE_RUC = "0190377210001"
CEDULA_PRUEBA = "0102030400"
CIFRAS_PRUEBA_2025 = {
    "activo_total": "3108776.58", "pasivo_total": "2107881.79", "patrimonio_neto": "1000894.79",
    "ingresos_401": "1862784.91", "otros_ingresos_403": "11761.20",
    "costo_ventas_501": "895805.67", "gastos_502": "953625.41", "utilidad_neta_707": "7279.63",
}

_CATALOGS_READY = (
    (Path(database.__file__).parent / "sri_catastro.db").exists()
    and database.SUPERCIAS_CATALOG_PATH.exists()
    and database.lookup_catastro(REFERENCE_RUC) is not None
    and database.lookup_supercias_catalog(REFERENCE_RUC) is not None
)


@unittest.skipUnless(_CATALOGS_READY, "Catálogos locales SRI/Supercias no importados o sin el RUC de referencia")
class TestAceptacionGrucanqui(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.uid = self.auditor["id"]
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", REFERENCE_RUC, "Cuenca", "", "2026",
            self.uid, self.admin["id"], self.db,
        )

    def _estado(self):
        audit = get_audit(self.audit_id, self.admin, self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        source_map = build_source_map(
            audit, ctx["research"], ctx["profile"], ctx["location"], ctx["admins"], ctx["shareholders"],
            ctx["docs"], ctx["snapshot"], ctx["source_checks"], ctx["sources"],
            alert_treatments=ctx["alert_treatments"],
        )
        return source_map

    @staticmethod
    def _requisitos(source_map, *fuentes):
        return [r for r in source_map["validacion"]["requisitos"] if r["source"] in fuentes]

    @staticmethod
    def _falta(source_map, card):
        cards = {c["key"]: c for c in source_map["cards"]}
        return cards[card]["missing"]

    def _completar_levantamiento(self):
        # SRI (manual): datos del SRI que el catastro no publica.
        update_company_profile_fields(self.audit_id, {
            "representante_legal_sri": "CANDO SUAREZ MARIA DANIELA",
            "contribuyente_fantasma": "NO", "transacciones_inexistentes": "NO",
        }, self.db, user_id=self.uid, fecha_consulta="2026-09-22")
        # Supercias (manual): información general que el Directorio no publica.
        update_company_profile_fields(self.audit_id, {
            "objeto_social": "Prestación de servicios de alojamiento (dato de prueba)",
            "plazo_social": "2061-08-24", "oficina_control": "Cuenca",
        }, self.db, user_id=self.uid, fecha_consulta="2026-09-22")
        update_company_location_fields(self.audit_id, {"referencia": "Frente al estadio"}, self.db,
                                       user_id=self.uid, fecha_consulta="2026-09-22")
        # Administradores: completar la gerente general del Directorio y registrar al presidente.
        gerente = list_administrators(self.audit_id, self.db)[0]
        update_administrator(self.audit_id, gerente["id"], tipo_identificacion="cedula",
                             identificacion=CEDULA_PRUEBA, nacionalidad="Ecuatoriana",
                             fecha_consulta="2026-09-22", user_id=self.uid, db_path=self.db)
        add_administrator(self.audit_id, CEDULA_PRUEBA, "Presidente de prueba", "Ecuatoriana", "Presidente",
                          self.db, tipo_identificacion="cedula", fecha_consulta="2026-09-22", user_id=self.uid)
        # Accionistas.
        add_shareholder(self.audit_id, "", CEDULA_PRUEBA, "Accionista de prueba", self.db,
                        tipo_identificacion="cedula", participacion_porcentaje="100",
                        fecha_consulta="2026-09-22", user_id=self.uid)
        # Financiero: año fiscal y casilleros.
        set_audit_fiscal_year(self.audit_id, 2025, user_id=self.uid, db_path=self.db)
        upsert_financial_statement(self.audit_id, 2025, CIFRAS_PRUEBA_2025,
                                   fecha_consulta="2026-09-22", user_id=self.uid, db_path=self.db)

    def test_flujo_completo_hasta_el_resumen(self):
        source_map = self._estado()
        self.assertFalse(source_map["readiness"]["ready"])
        self.assertIn("Fuente Supercias consultada", self._falta(source_map, "supercias"))
        self.assertFalse(any(r["ok"] for r in self._requisitos(
            source_map, "Supercias", "Administradores", "Accionistas", "Financiero")),
            "Un expediente nuevo no tiene requisitos cumplidos ni datos demo")

        # Búsqueda automática en los catálogos locales.
        research_company_by_ruc(self.audit_id, REFERENCE_RUC, self.uid, self.db)
        source_map = self._estado()
        self.assertNotIn("Fuente Supercias consultada", self._falta(source_map, "supercias"))
        self.assertFalse(source_map["readiness"]["ready"])
        pendientes = {b["label"] for b in source_map["readiness"]["blockers"]}
        self.assertIn("Objeto social", pendientes)
        self.assertIn("Presidente registrado", pendientes)

        self._completar_levantamiento()
        source_map = self._estado()
        self.assertTrue(source_map["readiness"]["ready"], source_map["readiness"]["blockers"])
        incumplidos = [r["label"] for r in source_map["validacion"]["requisitos"] if not r["ok"]]
        self.assertEqual(incumplidos, [])
        self.assertEqual(source_map["validacion"]["alertas"], [])
        cruces = {c["codigo"]: c["estado"] for c in source_map["validacion"]["cruces"]}
        self.assertEqual(cruces, {
            "CRUCE_RAZON_SOCIAL": "coincide", "CRUCE_FECHAS": "coincide", "CRUCE_REPRESENTANTE": "coincide",
            "CRUCE_BALANCE": "coincide", "CRUCE_CIIU": "coincide", "CRUCE_ACTIVIDAD_OBJETO": "revisar",
        })

        resumen = refresh_summary(self.audit_id, self.db)
        for titulo in ("1. IDENTIFICACIÓN TRIBUTARIA (SRI)", "2. INFORMACIÓN SOCIETARIA (SUPERCIAS)",
                       "3. UBICACIÓN", "4. ADMINISTRADORES", "5. ACCIONISTAS", "6. INFORMACIÓN FINANCIERA",
                       "7. VALIDACIONES CRUZADAS Y ALERTAS", "10. PENDIENTES DE VALIDACIÓN"):
            self.assertIn(titulo, resumen)
        self.assertIn("Fuente: Catastro RUC SRI (base local)", resumen)
        self.assertIn("Directorio de Compañías Supercias (catálogo local), corte", resumen)
        self.assertNotIn("..", resumen.replace("...", ""), "Sin puntos duplicados en el texto")
        self.assertIn("Supercias — Documentos económicos (EEFF al 2025-12-31) (consulta 2026-09-22)", resumen)
        self.assertNotIn("sin trazabilidad registrada", resumen)
        self.assertNotIn("□ Obligatorio", resumen)
        self.assertIn("Año fiscal EEFF: 2025", resumen)

    def test_alertas_no_bloquean_salvo_la_critica(self):
        research_company_by_ruc(self.audit_id, REFERENCE_RUC, self.uid, self.db)
        self._completar_levantamiento()

        upsert_financial_statement(self.audit_id, 2025, {**CIFRAS_PRUEBA_2025, "activo_total": "3200000"},
                                   user_id=self.uid, db_path=self.db)
        source_map = self._estado()
        self.assertIn("ALERTA_BALANCE", {a["codigo"] for a in source_map["validacion"]["alertas"]})
        self.assertTrue(source_map["readiness"]["ready"], "Una alerta alta no bloquea el resumen")

        update_company_profile_fields(self.audit_id, {"contribuyente_fantasma": "SI"}, self.db, user_id=self.uid)
        source_map = self._estado()
        self.assertFalse(source_map["readiness"]["ready"])
        register_alert_treatment(self.audit_id, "ALERTA_FANTASMA",
                                 "Se solicitó al cliente la resolución del SRI (prueba).",
                                 user_id=self.uid, db_path=self.db)
        source_map = self._estado()
        self.assertTrue(source_map["readiness"]["ready"])
        self.assertIn("Tratamiento del auditor: Se solicitó al cliente", refresh_summary(self.audit_id, self.db))


if __name__ == "__main__":
    unittest.main()
