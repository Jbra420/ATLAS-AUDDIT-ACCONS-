"""Pruebas de integridad del expediente (Fase 1 del levantamiento de información).

Cubre estas garantías:
  - guardar una pestaña no vacía los datos de otra (actualización parcial);
  - crear un expediente con el RUC demo no carga datos que no provengan de
    una fuente o del auditor;
  - la razón social de SRI y la de Supercias se conservan por separado;
  - un auditor no puede cambiar documentos ni fuentes de otro expediente;
  - registrar evidencia no genera el resumen.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database
from database import (
    add_source,
    append_research_source_note,
    apply_sri_research_result,
    apply_supercias_research_result,
    authenticate,
    connect,
    create_company_audit,
    get_audit_context,
    get_company_location,
    get_company_profile,
    get_research,
    init_db,
    mark_document_pending,
    mark_document_reviewed,
    mark_source_checked,
    mark_source_pending,
    refresh_summary,
    update_company_location_fields,
    update_company_profile_fields,
)
from seed_data import DEMO_RUC
from services.company_research import build_sri_result
from services.supercias_catalog import build_supercias_result
from views.auditor.radar import tab_sri, tab_supercias


# Caso de referencia del requisito, con los valores reales de los catálogos
# locales (catastro SRI y Directorio de Compañías, corte 21/09/2026).
SRI_RECORD = {
    "ruc": "0190377210001",
    "name": "GRUCANQUI CIA. LTDA",
    "city": "CUENCA",
    "activity_hint": "SERVICIOS DE ALOJAMIENTO PRESTADOS POR HOTELES",
    "taxpayer_status": "ACTIVO",
    "taxpayer_class": "GEN",
    "start_date": "2011-08-24 00:00:00",
    "update_date": "2025-09-08 15:20:40",
    "accounting_required": "S",
    "taxpayer_type": "SOCIEDAD",
    "province": "AZUAY",
    "canton": "CUENCA",
    "parish": "HUAYNACAPAC",
    "withholding_agent": "S",
    "special_taxpayer": "N",
}

SUPERCIAS_RECORD = {
    "ruc": "0190377210001",
    "razon_social": "GRUCANQUI CIA. LTDA.",
    "expediente": "141528",
    "situacion_legal": "ACTIVA",
    "fecha_constitucion": "2011-08-24",
    "tipo_compania": "RESPONSABILIDAD LIMITADA",
    "representante": "CANDO SUAREZ MARIA DANIELA",
    "representante_cargo": "GERENTE GENERAL",
    "provincia": "AZUAY",
    "ciudad": "CUENCA",
    "calle": "AV. DEL ESTADIO",
    "numero": "S/N",
    "interseccion": "FLORENCIA ASTUDILLO",
    "barrio": "ESTADIO",
}


class _TempDbCase(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db, demo=True)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)

    def _create_audit(self, ruc: str = "0190377210001") -> int:
        return create_company_audit(
            "GRUCANQUI CIA. LTDA", ruc, "Cuenca", "", "2025",
            self.auditor["id"], self.admin["id"], self.db,
        )


class TestPartialSave(_TempDbCase):
    def setUp(self):
        super().setUp()
        self.audit_id = self._create_audit()
        update_company_profile_fields(self.audit_id, {
            "estado_contribuyente": "ACTIVO",
            "regimen": "GENERAL",
            "situacion_legal": "ACTIVA",
            "objeto_social": "Servicios de alojamiento",
        }, self.db)
        update_company_location_fields(self.audit_id, {
            "provincia": "AZUAY", "ciudad": "CUENCA", "calle": "AV. DEL ESTADIO",
        }, self.db)

    def test_saving_location_keeps_sri_and_supercias(self):
        update_company_location_fields(self.audit_id, {"referencia": "Frente al estadio"}, self.db)

        profile = get_company_profile(self.audit_id, self.db)
        location = get_company_location(self.audit_id, self.db)
        self.assertEqual(profile["estado_contribuyente"], "ACTIVO")
        self.assertEqual(profile["objeto_social"], "Servicios de alojamiento")
        self.assertEqual(location["calle"], "AV. DEL ESTADIO")
        self.assertEqual(location["referencia"], "Frente al estadio")

    def test_saving_supercias_keeps_sri_fields(self):
        update_company_profile_fields(self.audit_id, {"plazo_social": "2061-08-24"}, self.db)

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["regimen"], "GENERAL")
        self.assertEqual(profile["situacion_legal"], "ACTIVA")
        self.assertEqual(profile["plazo_social"], "2061-08-24")

    def test_empty_value_clears_only_that_field(self):
        update_company_profile_fields(self.audit_id, {"objeto_social": ""}, self.db)

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["objeto_social"], "")
        self.assertEqual(profile["estado_contribuyente"], "ACTIVO")

    def test_fields_outside_whitelist_are_ignored(self):
        update_company_profile_fields(
            self.audit_id,
            {"ruc": "9999999999001", "supercias_fuente": "manipulado", "audit_id": "999"},
            self.db,
        )

        profile = get_company_profile(self.audit_id, self.db)
        self.assertNotEqual(profile["ruc"], "9999999999001")
        self.assertNotEqual(profile["supercias_fuente"], "manipulado")
        self.assertEqual(profile["audit_id"], self.audit_id)

    def test_first_save_creates_row(self):
        audit_id = self._create_audit("0190314014001")
        self.assertIsNone(get_company_location(audit_id, self.db))

        update_company_location_fields(audit_id, {"numero": "S/N"}, self.db)

        self.assertEqual(get_company_location(audit_id, self.db)["numero"], "S/N")


class TestNoDemoOnCreate(_TempDbCase):
    def test_demo_ruc_creates_blank_expediente(self):
        audit_id = self._create_audit(DEMO_RUC)
        ctx = get_audit_context(audit_id, self.db)

        self.assertIsNone(ctx["profile"])
        self.assertIsNone(ctx["location"])
        self.assertIsNone(ctx["snapshot"])
        self.assertEqual(ctx["admins"], [])
        self.assertEqual(ctx["shareholders"], [])
        self.assertEqual(len(ctx["docs"]), len(database.DEFAULT_ECONOMIC_DOCUMENTS))
        self.assertTrue(ctx["source_checks"])
        self.assertTrue(all(c["estado"] == "pendiente" for c in ctx["source_checks"]))


class TestNamesBySource(_TempDbCase):
    def setUp(self):
        super().setUp()
        self.audit_id = self._create_audit()

    def _run_search(self):
        apply_sri_research_result(
            self.audit_id, self.auditor["id"], build_sri_result(SRI_RECORD), self.db,
        )
        apply_supercias_research_result(
            self.audit_id, self.auditor["id"], build_supercias_result(SUPERCIAS_RECORD), self.db,
        )

    def test_search_keeps_both_company_names(self):
        self._run_search()

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["razon_social_sri"], "GRUCANQUI CIA. LTDA")
        self.assertEqual(profile["razon_social_supercias"], "GRUCANQUI CIA. LTDA.")

    def test_representante_sri_is_separate_from_supercias(self):
        self._run_search()
        update_company_profile_fields(
            self.audit_id, {"representante_legal_sri": "CANDO SUAREZ MARIA DANIELA"}, self.db,
        )

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["representante_legal_sri"], "CANDO SUAREZ MARIA DANIELA")
        self.assertEqual(profile["representante_legal"], "CANDO SUAREZ MARIA DANIELA")
        self.assertEqual(profile["representante_cargo"], "GERENTE GENERAL")

    def _clear_names(self):
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE company_profiles SET razon_social_sri = NULL, razon_social_supercias = NULL "
                "WHERE audit_id = ?",
                (self.audit_id,),
            )

    def test_migration_backfills_from_consulted_catalogs(self):
        self._run_search()
        self._clear_names()

        with mock.patch.object(database.esquema, "lookup_catastro", return_value={"name": "GRUCANQUI CIA. LTDA"}), \
             mock.patch.object(database.esquema, "lookup_supercias_catalog",
                               return_value={"razon_social": "GRUCANQUI CIA. LTDA."}):
            init_db(self.db, demo=True)

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["razon_social_sri"], "GRUCANQUI CIA. LTDA")
        self.assertEqual(profile["razon_social_supercias"], "GRUCANQUI CIA. LTDA.")

    def test_migration_skips_sources_not_consulted(self):
        update_company_profile_fields(self.audit_id, {"estado_contribuyente": "ACTIVO"}, self.db)
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE company_profiles SET ruc = ? WHERE audit_id = ?",
                ("0190377210001", self.audit_id),
            )

        with mock.patch.object(database.esquema, "lookup_catastro", return_value={"name": "X"}) as sri, \
             mock.patch.object(database.esquema, "lookup_supercias_catalog", return_value={"razon_social": "Y"}) as sup:
            init_db(self.db, demo=True)

        profile = get_company_profile(self.audit_id, self.db)
        self.assertIsNone(profile["razon_social_sri"])
        self.assertIsNone(profile["razon_social_supercias"])
        sri.assert_not_called()
        sup.assert_not_called()


class TestFormsSendOnlyTheirFields(unittest.TestCase):
    AUDIT = {"ruc": "0190377210001", "company_name": "GRUCANQUI CIA. LTDA"}
    PROFILE = {"situacion_legal": "ACTIVA", "estado_contribuyente": "ACTIVO", "plazo_social": "2061-08-24"}

    def _field_names(self, html: str) -> set[str]:
        import re
        # Campos de control del formulario, no datos del perfil. fecha_consulta
        # es la fecha en que se revisó la fuente (trazabilidad, Fase 3).
        control = {"_csrf", "audit_id", "return_tab", "fecha_consulta"}
        return set(re.findall(r'name="([a-z_0-9]+)"', html)) - control

    def test_sri_form_has_no_supercias_fields(self):
        html = tab_sri.build(1, self.AUDIT, self.PROFILE, None, csrf_token="t")
        names = self._field_names(html)
        self.assertIn("razon_social_sri", names)
        self.assertIn("representante_legal_sri", names)
        self.assertFalse(names & {"situacion_legal", "plazo_social", "objeto_social", "ruc", "razon_social"})

    def test_supercias_form_edits_plazo_and_has_no_sri_fields(self):
        html = tab_supercias.build(1, self.AUDIT, self.PROFILE, None, csrf_token="t")
        names = self._field_names(html)
        self.assertIn("plazo_social", names)
        self.assertIn("razon_social_supercias", names)
        self.assertFalse(names & {"estado_contribuyente", "regimen", "ruc", "razon_social"})

    def test_every_form_field_is_whitelisted(self):
        sri = self._field_names(tab_sri.build(1, self.AUDIT, self.PROFILE, None, csrf_token="t"))
        sup = self._field_names(tab_supercias.build(1, self.AUDIT, self.PROFILE, None, csrf_token="t"))
        self.assertLessEqual(sri | sup, set(database.PROFILE_FORM_FIELDS))


class TestAccesoEntreExpedientes(_TempDbCase):
    """Un id de documento o de fuente de otro expediente no se acepta."""

    def setUp(self):
        super().setUp()
        self.propio = self._create_audit()
        self.ajeno = self._create_audit("0190314014001")
        ctx = get_audit_context(self.ajeno, self.db)
        self.doc_ajeno = ctx["docs"][0]["id"]
        self.check_ajeno = ctx["source_checks"][0]["id"]

    def _estado(self, tabla: str, row_id: int) -> str:
        with connect(self.db) as conn:
            return conn.execute(f"SELECT estado FROM {tabla} WHERE id = ?", (row_id,)).fetchone()[0]

    def test_documento_de_otro_expediente(self):
        with self.assertRaisesRegex(ValueError, "no encontrado"):
            mark_document_reviewed(self.propio, self.doc_ajeno, self.auditor["id"], self.db)
        with self.assertRaisesRegex(ValueError, "no encontrado"):
            mark_document_pending(self.propio, self.doc_ajeno, self.db)
        self.assertEqual(self._estado("economic_documents", self.doc_ajeno), "pendiente")

    def test_fuente_de_otro_expediente(self):
        with self.assertRaisesRegex(ValueError, "no encontrada"):
            mark_source_checked(self.propio, self.check_ajeno, self.auditor["id"], "x", self.db)
        with self.assertRaisesRegex(ValueError, "no encontrada"):
            mark_source_pending(self.propio, self.check_ajeno, self.db)
        self.assertEqual(self._estado("source_checks", self.check_ajeno), "pendiente")

    def test_documento_propio_se_marca(self):
        doc = get_audit_context(self.propio, self.db)["docs"][0]["id"]
        mark_document_reviewed(self.propio, doc, self.auditor["id"], self.db)
        self.assertEqual(self._estado("economic_documents", doc), "revisado")


class TestResumenSoloBajoPedido(_TempDbCase):
    def setUp(self):
        super().setUp()
        self.audit_id = self._create_audit()

    def test_registrar_evidencia_no_genera_resumen(self):
        add_source(self.audit_id, "Consulta", "", "SRI", "", self.auditor["id"], self.db)
        append_research_source_note(self.audit_id, self.auditor["id"], "SRI", "RUC activo", "", self.db)
        research = get_research(self.audit_id, self.db)
        self.assertIsNone(research["generated_summary"])
        self.assertIn("SRI: RUC activo", research["sri_info"])
        with connect(self.db) as conn:
            status = conn.execute("SELECT status FROM audits WHERE id = ?", (self.audit_id,)).fetchone()[0]
        self.assertEqual(status, "en_investigacion")

    def test_registrar_evidencia_conserva_resumen_generado(self):
        refresh_summary(self.audit_id, self.db)
        antes = get_research(self.audit_id, self.db)["generated_summary"]
        append_research_source_note(self.audit_id, self.auditor["id"], "SRI", "RUC activo", "", self.db)
        self.assertEqual(get_research(self.audit_id, self.db)["generated_summary"], antes)


if __name__ == "__main__":
    unittest.main()
