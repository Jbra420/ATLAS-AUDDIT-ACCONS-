"""Pruebas de validaciones cruzadas, alertas y requisitos (Fase 5 del levantamiento).

Los casos de razón social y CIIU usan los valores reales de los catálogos
locales (GRUCANQUI y COBBLERCOMPANY).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import database
from database import (
    authenticate,
    create_company_audit,
    get_audit_context,
    init_db,
    list_provenance,
    register_alert_treatment,
)
from services.company_research import research_company_by_ruc
from services.summary import generate_summary
from services.validaciones import (
    COINCIDE,
    NO_COINCIDE,
    PENDIENTE,
    REVISAR,
    evaluar_levantamiento,
    normalizar_razon_social,
)
from views.auditor.radar import tab_sri

REFERENCE_RUC = "0190377210001"
AUDIT = {"ruc": REFERENCE_RUC, "company_name": "GRUCANQUI CIA. LTDA", "period": "2025", "city": "", "activity_hint": ""}
PROFILE = {
    "razon_social_sri": "GRUCANQUI CIA. LTDA", "razon_social_supercias": "GRUCANQUI CIA. LTDA.",
    "estado_contribuyente": "ACTIVO", "tipo_contribuyente": "SOCIEDAD", "regimen": "GENERAL",
    "agente_retencion": "SI", "fecha_inicio_actividades": "2011-08-24",
    "expediente_supercias": "141528", "fecha_constitucion": "2011-08-24",
    "tipo_compania": "RESPONSABILIDAD LIMITADA", "situacion_legal": "ACTIVA",
    "objeto_social": "Servicios de alojamiento", "actividad_economica": "SERVICIOS DE ALOJAMIENTO",
    "representante_legal_sri": "MARIA DANIELA CANDO SUAREZ",
    "ciiu_sri": "I551001", "ciiu_nivel6": "I5510.01",
}
LOCATION = {"provincia": "AZUAY", "ciudad": "CUENCA", "calle": "AV. DEL ESTADIO",
            "numero": "S/N", "interseccion": "FLORENCIA ASTUDILLO"}
ADMINS = [
    {"nombre": "CANDO SUAREZ MARIA DANIELA", "cargo": "GERENTE GENERAL", "identificacion": "0102030400"},
    {"nombre": "Luis Perez", "cargo": "Presidente", "identificacion": "0102030400"},
]
SHAREHOLDERS = [{"nombre": "Ana Torres", "identificacion": "0102030400"}]
SNAPSHOT = {
    "anio_fiscal": 2025, "activo_total": 3108776.58, "pasivo_total": 2107881.79, "patrimonio_neto": 1000894.79,
    "ingresos_401": 1862784.91, "otros_ingresos_403": 11761.20, "costo_ventas_501": 895805.67,
    "gastos_502": 953625.41, "utilidad_neta_707": 7279.63,
}


def evaluar(profile=None, location=None, admins=None, shareholders=None, snapshot=None, tratamientos=None,
            audit=AUDIT):
    return evaluar_levantamiento(
        audit,
        PROFILE if profile is None else profile,
        LOCATION if location is None else location,
        ADMINS if admins is None else admins,
        SHAREHOLDERS if shareholders is None else shareholders,
        SNAPSHOT if snapshot is None else snapshot,
        tratamientos,
    )


def cruce(resultado, codigo):
    return next(c for c in resultado["cruces"] if c["codigo"] == codigo)


def codigos(resultado):
    return {a["codigo"] for a in resultado["alertas"]}


class TestNormalizacionRazonSocial(unittest.TestCase):
    def test_real_cases_are_equivalent(self):
        casos = [
            ("GRUCANQUI CIA. LTDA", "GRUCANQUI CIA. LTDA."),
            ("COBBLERCOMPANY CIA. LTDA.", "COBBLERCOMPANY CIA.LTDA."),
            ("C.A. ECUATORIANA DE CERAMICA", "COMPAÑÍA ANÓNIMA ECUATORIANA DE CERÁMICA"),
            ("IMPORTADORA S.A.", "IMPORTADORA SOCIEDAD ANONIMA"),
            ("ABC S.A.S.", "ABC SOCIEDAD POR ACCIONES SIMPLIFICADA"),
        ]
        for a, b in casos:
            with self.subTest(a=a, b=b):
                self.assertEqual(normalizar_razon_social(a), normalizar_razon_social(b))

    def test_different_companies_differ(self):
        self.assertNotEqual(normalizar_razon_social("GRUCANQUI CIA. LTDA"),
                            normalizar_razon_social("GRUCANQUI HOTELES CIA. LTDA"))


class TestRequisitos(unittest.TestCase):
    def test_complete_levantamiento_has_no_pending(self):
        resultado = evaluar()
        self.assertEqual(resultado["pendientes"], [])
        self.assertEqual(resultado["requisitos_cumplidos"], resultado["requisitos_total"])

    def test_empty_expediente_lists_every_block(self):
        resultado = evaluar({}, {}, [], [], {}, audit={"ruc": ""})
        fuentes = {p["source"] for p in resultado["pendientes"]}
        self.assertEqual(fuentes, {"SRI", "Supercias", "Ubicación", "Administradores", "Accionistas", "Financiero"})

    def test_format_rules(self):
        labels = lambda r: {p["label"] for p in r["pendientes"]}  # noqa: E731
        self.assertIn("RUC de 13 dígitos terminado en 001", labels(evaluar(audit={"ruc": "0190377210002"})))
        self.assertIn("Número de expediente (numérico)",
                      labels(evaluar({**PROFILE, "expediente_supercias": "EXP-141528"})))
        self.assertIn("Fecha de constitución (AAAA-MM-DD)",
                      labels(evaluar({**PROFILE, "fecha_constitucion": "24/08/2011"})))

    def test_general_manager_and_president_are_required(self):
        solo_gerente = [ADMINS[0]]
        labels = {p["label"] for p in evaluar(admins=solo_gerente)["pendientes"]}
        self.assertIn("Presidente registrado", labels)
        vice = [ADMINS[0], {**ADMINS[1], "cargo": "VICEPRESIDENTE"}]
        self.assertIn("Presidente registrado", {p["label"] for p in evaluar(admins=vice)["pendientes"]})
        ejecutivo = [ADMINS[0], {**ADMINS[1], "cargo": "PRESIDENTE EJECUTIVO"}]
        self.assertNotIn("Presidente registrado", {p["label"] for p in evaluar(admins=ejecutivo)["pendientes"]})

    def test_missing_identifications_are_named(self):
        admins = [ADMINS[0], {**ADMINS[1], "identificacion": ""}]
        labels = {p["label"] for p in evaluar(admins=admins)["pendientes"]}
        self.assertIn("Identificación de cada administrador (falta: Luis Perez)", labels)
        labels = {p["label"] for p in evaluar(shareholders=[{"nombre": "Socio", "identificacion": "—"}])["pendientes"]}
        self.assertIn("Identificación de cada accionista (falta: Socio)", labels)

    def test_financial_year_and_boxes(self):
        labels = {p["label"] for p in evaluar(snapshot={**SNAPSHOT, "anio_fiscal": None})["pendientes"]}
        self.assertIn("Año fiscal de los estados financieros", labels)
        labels = {p["label"] for p in evaluar(snapshot={**SNAPSHOT, "otros_ingresos_403": None})["pendientes"]}
        self.assertIn("Casilleros del ejercicio 2025 (falta: 403)", labels)

    def test_optional_fields_are_only_recommendations(self):
        resultado = evaluar({**PROFILE, "situacion_legal": "", "actividad_economica": ""})
        self.assertEqual(resultado["pendientes"], [])
        recomendaciones = {r["label"] for r in resultado["recomendaciones"]}
        self.assertIn("Situación legal", recomendaciones)
        self.assertIn("Actividad económica principal", recomendaciones)


class TestCruces(unittest.TestCase):
    def test_reference_case_matches(self):
        resultado = evaluar()
        self.assertEqual(cruce(resultado, "CRUCE_RAZON_SOCIAL")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_FECHAS")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_REPRESENTANTE")["estado"], COINCIDE,
                         "El orden de nombres y apellidos no debe importar")
        self.assertEqual(cruce(resultado, "CRUCE_BALANCE")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_CIIU")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_ACTIVIDAD_OBJETO")["estado"], REVISAR)

    def test_mismatches(self):
        resultado = evaluar({**PROFILE, "razon_social_supercias": "OTRA CIA. LTDA.",
                             "fecha_constitucion": "2011-08-20", "representante_legal_sri": "PEREZ LUIS"})
        self.assertEqual(cruce(resultado, "CRUCE_RAZON_SOCIAL")["estado"], NO_COINCIDE)
        fechas = cruce(resultado, "CRUCE_FECHAS")
        self.assertEqual(fechas["estado"], NO_COINCIDE)
        self.assertIn("Diferencia: 4 día(s)", fechas["detalle"])
        self.assertEqual(cruce(resultado, "CRUCE_REPRESENTANTE")["estado"], NO_COINCIDE)

    def test_balance_tolerance(self):
        dentro = evaluar(snapshot={**SNAPSHOT, "activo_total": SNAPSHOT["activo_total"] + 1.00})
        fuera = evaluar(snapshot={**SNAPSHOT, "activo_total": SNAPSHOT["activo_total"] + 1.01})
        self.assertEqual(cruce(dentro, "CRUCE_BALANCE")["estado"], COINCIDE)
        self.assertEqual(cruce(fuera, "CRUCE_BALANCE")["estado"], NO_COINCIDE)
        self.assertIn("ALERTA_BALANCE", codigos(fuera))

    def test_missing_data_is_pending_not_mismatch(self):
        resultado = evaluar({}, {}, [], [], {})
        self.assertTrue(all(c["estado"] == PENDIENTE for c in resultado["cruces"]))
        self.assertEqual(resultado["alertas"], [])


class TestAlertas(unittest.TestCase):
    def test_ruc_not_active(self):
        self.assertIn("ALERTA_RUC_NO_ACTIVO", codigos(evaluar({**PROFILE, "estado_contribuyente": "SUSPENDIDO"})))
        self.assertNotIn("ALERTA_RUC_NO_ACTIVO", codigos(evaluar()))

    def test_legal_status_uses_every_non_active_category(self):
        for situacion in ("DISOLUCIÓN Y LIQUIDACIÓN OFICIO INSCRITA EN RM",
                          "CANCELACIÓN PERMISO OPERACIÓN - OFICIO INSCRITA RM", "INACTIVA",
                          "NO SUJETO CONTROL Y VIGILANCIA SCVS(ART. 432/OTRO)"):
            with self.subTest(situacion=situacion):
                self.assertIn("ALERTA_SITUACION_LEGAL", codigos(evaluar({**PROFILE, "situacion_legal": situacion})))

    def test_ghost_taxpayer_is_critical_and_blocks_until_treated(self):
        for campo in ("contribuyente_fantasma", "transacciones_inexistentes"):
            with self.subTest(campo=campo):
                resultado = evaluar({**PROFILE, campo: "SI"})
                alerta = next(a for a in resultado["alertas"] if a["codigo"] == "ALERTA_FANTASMA")
                self.assertEqual(alerta["nivel"], "critica")
                self.assertTrue(any("Tratamiento de alerta crítica" in p["label"] for p in resultado["pendientes"]))

        tratado = evaluar({**PROFILE, "contribuyente_fantasma": "SI"},
                          tratamientos=[{"codigo": "ALERTA_FANTASMA", "observacion": "Se revisó con el cliente."}])
        self.assertEqual(tratado["pendientes"], [])
        self.assertEqual(tratado["alertas"][0]["tratamiento"], "Se revisó con el cliente.")


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db, demo=True)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", REFERENCE_RUC, "Cuenca", "", "2025",
            self.auditor["id"], self.admin["id"], self.db,
        )


class TestAlertTreatment(_DbCase):
    def test_register_and_trace(self):
        register_alert_treatment(self.audit_id, "ALERTA_FANTASMA", "Se solicitará la resolución del SRI.",
                                 user_id=self.auditor["id"], db_path=self.db)
        register_alert_treatment(self.audit_id, "ALERTA_FANTASMA", "Resolución revisada: el caso fue archivado.",
                                 user_id=self.auditor["id"], db_path=self.db)
        tratamientos = get_audit_context(self.audit_id, self.db)["alert_treatments"]
        self.assertEqual(len(tratamientos), 1)
        self.assertEqual(tratamientos[0]["observacion"], "Resolución revisada: el caso fue archivado.")
        historial = [r for r in list_provenance(self.audit_id, self.db) if r["bloque"] == "validaciones"]
        self.assertEqual([r["valor_nuevo"] for r in historial],
                         ["Se solicitará la resolución del SRI.", "Resolución revisada: el caso fue archivado."])

    def test_rejects_unknown_code_and_short_text(self):
        with self.assertRaises(ValueError):
            register_alert_treatment(self.audit_id, "ALERTA_BALANCE", "Texto suficientemente largo", db_path=self.db)
        with self.assertRaises(ValueError):
            register_alert_treatment(self.audit_id, "ALERTA_FANTASMA", "corto", db_path=self.db)


class TestSummaryAndView(unittest.TestCase):
    def test_summary_lists_cross_checks_and_treated_alert(self):
        text = generate_summary(
            AUDIT, {}, 0, profile={**PROFILE, "contribuyente_fantasma": "SI"}, location=LOCATION,
            admins=ADMINS, shareholders=SHAREHOLDERS, snapshot=SNAPSHOT,
            alert_treatments=[{"codigo": "ALERTA_FANTASMA", "observacion": "Se revisó la resolución del SRI."}],
        )
        self.assertIn("Razón social SRI = Supercias: coincide", text)
        self.assertIn("Activo = Pasivo + Patrimonio: coincide", text)
        self.assertIn("[Alerta crítica] El SRI registra al contribuyente como contribuyente fantasma.", text)
        self.assertIn("Tratamiento del auditor: Se revisó la resolución del SRI.", text)

    def test_legal_status_risk_is_reported_once(self):
        text = generate_summary(AUDIT, {}, 0, profile={**PROFILE, "situacion_legal": "INACTIVA"})
        self.assertEqual(text.count("La situación legal en Supercias no es activa"), 1)

    def test_sri_tab_offers_treatment_form_only_to_auditor(self):
        """La alerta crítica y su tratamiento se registran en la pestaña SRI,
        junto al dato que la origina."""
        alertas = evaluar({**PROFILE, "contribuyente_fantasma": "SI"})["alertas"]
        audit = {"ruc": "0190377210001", "company_name": "GRUCANQUI CIA. LTDA"}
        auditor = tab_sri.build(1, audit, PROFILE, {}, csrf_token="t", alertas=alertas)
        jefe = tab_sri.build(1, audit, PROFILE, {}, read_only=True, alertas=alertas)
        self.assertIn('action="/auditor/radar/alert-treatment"', auditor)
        self.assertIn("Alerta crítica", jefe)
        self.assertIn("Tratamiento pendiente de registro", jefe)
        self.assertNotIn("alert-treatment", jefe)

    def test_untreated_critical_alert_links_to_sri_tab(self):
        pendientes = evaluar({**PROFILE, "contribuyente_fantasma": "SI"})["pendientes"]
        tratamiento = [p for p in pendientes if p["label"].startswith("Tratamiento de alerta crítica")]
        self.assertEqual([p["tab"] for p in tratamiento], ["sri"])


_CATALOGS_READY = (
    database.SRI_CATASTRO_PATH.exists()
    and database.SUPERCIAS_CATALOG_PATH.exists()
    and database.lookup_catastro(REFERENCE_RUC) is not None
    and database.lookup_supercias_catalog(REFERENCE_RUC) is not None
)


@unittest.skipUnless(_CATALOGS_READY, "Catálogos locales SRI/Supercias no importados o sin el RUC de referencia")
class TestReferenceCaseAgainstLocalCatalogs(_DbCase):
    """Criterio de cierre de la Fase 5 con GRUCANQUI y los catálogos reales."""

    def test_reference_case_cross_checks(self):
        research_company_by_ruc(self.audit_id, REFERENCE_RUC, self.auditor["id"], self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        resultado = evaluar_levantamiento(
            AUDIT, ctx["profile"], ctx["location"], ctx["admins"], ctx["shareholders"], ctx["snapshot"],
        )
        self.assertEqual(cruce(resultado, "CRUCE_RAZON_SOCIAL")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_FECHAS")["estado"], COINCIDE)
        self.assertEqual(cruce(resultado, "CRUCE_CIIU")["estado"], COINCIDE)
        self.assertEqual(codigos(resultado), set())
        self.assertIn("Objeto social", {p["label"] for p in resultado["pendientes"]})
        self.assertEqual(ctx["profile"]["ciiu_sri"], "I551001")


if __name__ == "__main__":
    unittest.main()
