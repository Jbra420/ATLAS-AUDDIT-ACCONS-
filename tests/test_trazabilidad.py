"""Pruebas de captura asistida y trazabilidad por dato (Fase 3 del levantamiento).

Regla del requisito: cada dato registra su fuente y la fecha de consulta.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from database import (
    add_administrator,
    add_shareholder,
    apply_sri_research_result,
    apply_supercias_research_result,
    authenticate,
    connect,
    create_company_audit,
    delete_administrator,
    get_audit_context,
    get_company_profile,
    init_db,
    list_administrators,
    list_provenance,
    list_shareholders,
    update_administrator,
    update_company_location_fields,
    update_company_profile_fields,
    update_shareholder,
)
from services.company_research import build_sri_result
from services.identificacion import inferir_tipo, validar_identificacion
from services.summary import generate_summary
from services.supercias_catalog import build_supercias_result
from services.trazabilidad import (
    FUENTE_CATASTRO_SRI,
    FUENTE_SRI,
    FUENTE_SUPERCIAS_ACCIONISTAS,
    FUENTE_SUPERCIAS_ADMINISTRADORES,
    FUENTE_SUPERCIAS_GENERAL,
    FUENTE_SUPERCIAS_UBICACION,
    etiqueta_traza,
    ultimo_por_campo,
    validar_fecha_consulta,
)
from views.auditor.radar import tab_accionistas, tab_admins, tab_sri, tab_ubicacion

# Cédula con formato y dígito verificador válidos (no corresponde a una persona real conocida).
CEDULA_VALIDA = "0102030400"
REFERENCE_RUC = "0190377210001"
HOY = date(2026, 9, 22)


class TestIdentificacion(unittest.TestCase):
    def test_valid_cedula(self):
        self.assertEqual(validar_identificacion(CEDULA_VALIDA, "cedula"), ("cedula", CEDULA_VALIDA, ""))

    def test_cedula_must_have_ten_digits(self):
        for value in ("010203040", "01020304001", "01020304AB"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "10 dígitos"):
                validar_identificacion(value, "cedula")

    def test_check_digit_mismatch_is_a_warning(self):
        tipo, valor, warning = validar_identificacion("0102030405", "cedula")
        self.assertEqual((tipo, valor), ("cedula", "0102030405"))
        self.assertIn("dígito verificador", warning)

    def test_separators_are_removed(self):
        self.assertEqual(validar_identificacion("010203040-0", "cedula")[1], CEDULA_VALIDA)

    def test_passport(self):
        self.assertEqual(validar_identificacion("a1234567", "pasaporte")[1], "A1234567")
        with self.assertRaises(ValueError):
            validar_identificacion("12", "pasaporte")

    def test_ruc_only_where_allowed(self):
        permitidos = ("cedula", "ruc", "pasaporte")
        self.assertEqual(validar_identificacion(REFERENCE_RUC, "ruc", permitidos)[0], "ruc")
        with self.assertRaisesRegex(ValueError, "no permitido"):
            validar_identificacion(REFERENCE_RUC, "ruc")

    def test_empty_is_valid_when_saving(self):
        self.assertEqual(validar_identificacion("", "cedula"), ("cedula", "", ""))

    def test_type_inference_for_legacy_callers(self):
        self.assertEqual(inferir_tipo(CEDULA_VALIDA), "cedula")
        self.assertEqual(inferir_tipo(REFERENCE_RUC), "ruc")
        self.assertEqual(inferir_tipo("A1234567"), "pasaporte")


class TestFechaConsulta(unittest.TestCase):
    def test_empty_means_today(self):
        self.assertEqual(validar_fecha_consulta("", HOY), "2026-09-22")

    def test_past_date(self):
        self.assertEqual(validar_fecha_consulta("2026-09-01", HOY), "2026-09-01")

    def test_rejects_future_invalid_and_badly_formatted(self):
        for value in ("2026-09-23", "2026-02-30", "22/09/2026", "2026-9-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validar_fecha_consulta(value, HOY)


class _AuditCase(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db, demo=True)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", REFERENCE_RUC, "Cuenca", "", "2025",
            self.auditor["id"], self.admin["id"], self.db,
        )
        self.uid = self.auditor["id"]

    def _history(self, bloque: str | None = None) -> list:
        rows = list_provenance(self.audit_id, self.db)
        return [r for r in rows if bloque is None or r["bloque"] == bloque]


class TestProfileProvenance(_AuditCase):
    def test_each_field_gets_the_source_of_its_block(self):
        update_company_profile_fields(
            self.audit_id,
            {"regimen": "GENERAL", "objeto_social": "Servicios de alojamiento"},
            self.db, user_id=self.uid, fecha_consulta="2026-09-20",
        )
        latest = ultimo_por_campo(self._history())
        regimen = latest[("sri", "regimen")]
        objeto = latest[("supercias", "objeto_social")]
        self.assertEqual(regimen["fuente"], FUENTE_SRI)
        self.assertEqual(objeto["fuente"], FUENTE_SUPERCIAS_GENERAL)
        self.assertEqual(regimen["fecha_consulta"], "2026-09-20")
        self.assertEqual(regimen["registrado_por"], self.uid)
        self.assertIsNone(regimen["valor_anterior"])
        self.assertEqual(regimen["valor_nuevo"], "GENERAL")

    def test_only_changed_fields_are_recorded(self):
        update_company_profile_fields(self.audit_id, {"regimen": "GENERAL"}, self.db, user_id=self.uid)
        update_company_profile_fields(
            self.audit_id, {"regimen": "GENERAL", "plazo_social": "2061-08-24"}, self.db, user_id=self.uid,
        )
        campos = [r["campo"] for r in self._history()]
        self.assertEqual(campos, ["regimen", "plazo_social"])

    def test_history_keeps_previous_value(self):
        update_company_profile_fields(self.audit_id, {"regimen": "GENERAL"}, self.db)
        update_company_profile_fields(self.audit_id, {"regimen": "RIMPE"}, self.db)
        last = self._history()[-1]
        self.assertEqual((last["valor_anterior"], last["valor_nuevo"]), ("GENERAL", "RIMPE"))

    def test_location_uses_location_source(self):
        update_company_location_fields(self.audit_id, {"referencia": "Frente al estadio"}, self.db)
        row = self._history("ubicacion")[-1]
        self.assertEqual((row["campo"], row["fuente"]), ("referencia", FUENTE_SUPERCIAS_UBICACION))

    def test_closed_values_for_ghost_taxpayer(self):
        update_company_profile_fields(self.audit_id, {"contribuyente_fantasma": "no"}, self.db)
        self.assertEqual(get_company_profile(self.audit_id, self.db)["contribuyente_fantasma"], "NO")
        with self.assertRaises(ValueError):
            update_company_profile_fields(self.audit_id, {"transacciones_inexistentes": "tal vez"}, self.db)


class TestCatalogProvenance(_AuditCase):
    def test_search_records_catalog_sources(self):
        apply_sri_research_result(
            self.audit_id, self.uid,
            build_sri_result({"ruc": REFERENCE_RUC, "name": "GRUCANQUI CIA. LTDA", "taxpayer_class": "GEN",
                              "province": "AZUAY", "canton": "CUENCA"}),
            self.db,
        )
        supercias = build_supercias_result({
            "ruc": REFERENCE_RUC, "razon_social": "GRUCANQUI CIA. LTDA.", "situacion_legal": "ACTIVA",
            "representante": "CANDO SUAREZ MARIA DANIELA", "representante_cargo": "GERENTE GENERAL",
            "calle": "AV. DEL ESTADIO", "_catalogo_fecha_actualizacion": "21/09/2026 01:00:17",
        })
        apply_supercias_research_result(self.audit_id, self.uid, supercias, self.db)

        latest = ultimo_por_campo(self._history())
        self.assertEqual(latest[("sri", "regimen")]["fuente"], FUENTE_CATASTRO_SRI)
        self.assertEqual(latest[("ubicacion", "provincia")]["fuente"], FUENTE_CATASTRO_SRI)
        self.assertIn("corte 21/09/2026", latest[("supercias", "situacion_legal")]["fuente"])
        self.assertIn("corte 21/09/2026", latest[("ubicacion", "calle")]["fuente"])
        admin_rows = self._history("administradores")
        self.assertEqual(len(admin_rows), 1)
        self.assertIn("CANDO SUAREZ MARIA DANIELA | GERENTE GENERAL", admin_rows[0]["valor_nuevo"])
        admin = list_administrators(self.audit_id, self.db)[0]
        self.assertIn("corte 21/09/2026", admin["fuente"])
        self.assertEqual(admin["fecha_consulta"], date.today().isoformat())


class TestLegacyDataGetsFirstTrace(_AuditCase):
    """Datos capturados antes de la Fase 3 no tienen trazabilidad. Al volver a
    consultar la fuente reciben su primera traza aunque el valor no cambie."""

    def test_repeated_search_traces_legacy_values_once(self):
        with connect(self.db) as conn:
            conn.execute(
                "INSERT INTO company_profiles (audit_id, regimen, updated_at) VALUES (?, 'GENERAL', '')",
                (self.audit_id,),
            )
        self.assertEqual(self._history(), [])
        result = build_sri_result({"ruc": REFERENCE_RUC, "name": "GRUCANQUI CIA. LTDA", "taxpayer_class": "GEN"})

        apply_sri_research_result(self.audit_id, self.uid, result, self.db)
        apply_sri_research_result(self.audit_id, self.uid, result, self.db)

        regimen = [r for r in self._history("sri") if r["campo"] == "regimen"]
        self.assertEqual(len(regimen), 1)
        self.assertEqual((regimen[0]["valor_anterior"], regimen[0]["valor_nuevo"]), ("GENERAL", "GENERAL"))
        self.assertEqual(regimen[0]["fuente"], FUENTE_CATASTRO_SRI)


class TestAdministrators(_AuditCase):
    def test_invalid_cedula_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "10 dígitos"):
            add_administrator(self.audit_id, "12345", "Ana Torres", "", "Presidente", self.db,
                              tipo_identificacion="cedula")

    def test_add_stores_source_and_consultation_date(self):
        add_administrator(self.audit_id, CEDULA_VALIDA, "Ana Torres", "Ecuatoriana", "Presidente", self.db,
                          tipo_identificacion="cedula", fecha_consulta="2026-09-21", user_id=self.uid)
        admin = list_administrators(self.audit_id, self.db)[0]
        self.assertEqual(admin["tipo_identificacion"], "cedula")
        self.assertEqual(admin["fuente"], FUENTE_SUPERCIAS_ADMINISTRADORES)
        self.assertEqual(admin["fecha_consulta"], "2026-09-21")

    def test_complete_directory_record_keeps_name_and_role(self):
        admin_id = add_administrator(self.audit_id, "", "CANDO SUAREZ MARIA DANIELA", "", "GERENTE GENERAL", self.db)
        update_administrator(
            self.audit_id, admin_id, tipo_identificacion="cedula", identificacion=CEDULA_VALIDA,
            nacionalidad="Ecuatoriana", fecha_consulta="2026-09-22", user_id=self.uid, db_path=self.db,
        )
        admin = list_administrators(self.audit_id, self.db)[0]
        self.assertEqual((admin["nombre"], admin["cargo"]), ("CANDO SUAREZ MARIA DANIELA", "GERENTE GENERAL"))
        self.assertEqual(admin["identificacion"], CEDULA_VALIDA)
        last = self._history("administradores")[-1]
        self.assertNotIn(CEDULA_VALIDA, last["valor_anterior"])
        self.assertIn(CEDULA_VALIDA, last["valor_nuevo"])

    def test_update_is_scoped_to_audit(self):
        other = create_company_audit("Otra", "", "Quito", "", "2025", self.uid, self.admin["id"], self.db)
        admin_id = add_administrator(other, "", "Ana Torres", "", "Presidente", self.db)
        with self.assertRaisesRegex(ValueError, "no encontrado"):
            update_administrator(self.audit_id, admin_id, tipo_identificacion="cedula",
                                 identificacion=CEDULA_VALIDA, nacionalidad="", db_path=self.db)

    def test_delete_leaves_a_trace(self):
        admin_id = add_administrator(self.audit_id, "", "Ana Torres", "", "Presidente", self.db)
        delete_administrator(self.audit_id, admin_id, self.db, user_id=self.uid)
        last = self._history("administradores")[-1]
        self.assertIn("Ana Torres", last["valor_anterior"])
        self.assertIsNone(last["valor_nuevo"])
        self.assertEqual(last["registrado_por"], self.uid)


class TestShareholders(_AuditCase):
    def test_participation_and_final_beneficiary(self):
        add_shareholder(self.audit_id, "", REFERENCE_RUC, "INVERSIONES XYZ S.A.", self.db,
                        tipo_identificacion="ruc", participacion_porcentaje="60,5", capital="465429.92",
                        beneficiario_final="Ana Torres")
        row = list_shareholders(self.audit_id, self.db)[0]
        self.assertEqual(row["tipo_identificacion"], "ruc")
        self.assertAlmostEqual(row["participacion_porcentaje"], 60.5)
        self.assertAlmostEqual(row["capital"], 465429.92)
        self.assertEqual(row["beneficiario_final"], "Ana Torres")
        self.assertEqual(row["fuente"], FUENTE_SUPERCIAS_ACCIONISTAS)

    def test_rejects_out_of_range_numbers(self):
        for kwargs in ({"participacion_porcentaje": "150"}, {"participacion_porcentaje": "-1"},
                       {"capital": "-10"}, {"capital": "mil"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                add_shareholder(self.audit_id, "", "", "Ana Torres", self.db, **kwargs)

    def test_update_completes_existing_record(self):
        shareholder_id = add_shareholder(self.audit_id, "", "", "Ana Torres", self.db)
        update_shareholder(self.audit_id, shareholder_id, tipo_identificacion="cedula",
                           identificacion=CEDULA_VALIDA, participacion_porcentaje="50",
                           db_path=self.db, user_id=self.uid)
        row = list_shareholders(self.audit_id, self.db)[0]
        self.assertEqual((row["identificacion"], row["participacion_porcentaje"]), (CEDULA_VALIDA, 50.0))
        self.assertIn("50 %", self._history("accionistas")[-1]["valor_nuevo"])

    def test_update_rejects_identification_of_another_shareholder(self):
        add_shareholder(self.audit_id, "", CEDULA_VALIDA, "Ana Torres", self.db)
        other_id = add_shareholder(self.audit_id, "", "", "Luis Perez", self.db)
        with self.assertRaisesRegex(ValueError, "Otro accionista"):
            update_shareholder(self.audit_id, other_id, tipo_identificacion="cedula",
                               identificacion=CEDULA_VALIDA, db_path=self.db)


class TestAppendOnly(_AuditCase):
    def setUp(self):
        super().setUp()
        update_company_profile_fields(self.audit_id, {"regimen": "GENERAL"}, self.db)

    def test_history_cannot_be_edited_or_deleted(self):
        with connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE data_provenance SET valor_nuevo = 'RIMPE'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM data_provenance")
        self.assertEqual(self._history()[0]["valor_nuevo"], "GENERAL")

    def test_deleting_the_whole_audit_cascades(self):
        with connect(self.db) as conn:
            conn.execute("DELETE FROM audits WHERE id = ?", (self.audit_id,))
            remaining = conn.execute("SELECT COUNT(*) FROM data_provenance").fetchone()[0]
        self.assertEqual(remaining, 0)


class TestViewsAndSummary(_AuditCase):
    AUDIT = {"ruc": REFERENCE_RUC, "company_name": "GRUCANQUI CIA. LTDA"}

    def test_sri_card_shows_source_and_date(self):
        update_company_profile_fields(self.audit_id, {"contribuyente_fantasma": "NO"}, self.db,
                                      fecha_consulta="2026-09-20")
        ctx = get_audit_context(self.audit_id, self.db)
        html = tab_sri.build(self.audit_id, self.AUDIT, ctx["profile"], None, csrf_token="t",
                             provenance=ctx["provenance"])
        self.assertIn(etiqueta_traza(ctx["provenance"][-1]), html)
        self.assertIn(f"{FUENTE_SRI} · 2026-09-20", html)
        self.assertIn('name="contribuyente_fantasma"', html)
        self.assertIn("Historial de datos SRI", html)

    def test_location_shows_reference(self):
        update_company_location_fields(self.audit_id, {"referencia": "Frente al estadio"}, self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        html = tab_ubicacion.build(self.audit_id, self.AUDIT, ctx["location"], read_only=True,
                                   provenance=ctx["provenance"])
        self.assertIn("Frente al estadio", html)

    def test_people_tabs(self):
        add_administrator(self.audit_id, CEDULA_VALIDA, "Ana Torres", "", "Presidente", self.db)
        add_shareholder(self.audit_id, "", "", "Ana Torres", self.db, participacion_porcentaje="60")
        add_shareholder(self.audit_id, "", "", "Luis Perez", self.db, participacion_porcentaje="30")
        ctx = get_audit_context(self.audit_id, self.db)
        admins_html = tab_admins.build(self.audit_id, self.AUDIT, ctx["admins"], csrf_token="t",
                                       provenance=ctx["provenance"])
        self.assertIn(f"Cédula {CEDULA_VALIDA}", admins_html)
        self.assertIn('value="update"', admins_html)
        self.assertIn(f'value="{CEDULA_VALIDA}"', admins_html)
        shareholders_html = tab_accionistas.build(self.audit_id, self.AUDIT, ctx["shareholders"],
                                                  csrf_token="t", provenance=ctx["provenance"])
        self.assertIn("Suma de participaciones registradas: <strong>90 %</strong>", shareholders_html)

    def test_read_only_tabs_have_no_forms(self):
        add_administrator(self.audit_id, "", "Ana Torres", "", "Presidente", self.db)
        ctx = get_audit_context(self.audit_id, self.db)
        html = tab_admins.build(self.audit_id, self.AUDIT, ctx["admins"], read_only=True)
        self.assertNotIn("<form", html)

    def test_summary_includes_new_fields(self):
        update_company_profile_fields(self.audit_id, {
            "estado_contribuyente": "ACTIVO", "contribuyente_fantasma": "NO",
            "objeto_social": "Servicios de alojamiento",
        }, self.db)
        add_administrator(self.audit_id, CEDULA_VALIDA, "Ana Torres", "", "Presidente", self.db)
        add_shareholder(self.audit_id, "", "", "Luis Perez", self.db, participacion_porcentaje="100")
        ctx = get_audit_context(self.audit_id, self.db)
        audit = {"company_name": "GRUCANQUI", "ruc": REFERENCE_RUC, "period": "2025", "city": "", "activity_hint": ""}
        text = generate_summary(audit, {}, 0, profile=dict(ctx["profile"]), admins=ctx["admins"],
                                shareholders=ctx["shareholders"])
        self.assertRegex(text, r"Objeto social\s+: Servicios de alojamiento")
        self.assertRegex(text, r"Contribuyente fantasma\s+: NO")
        self.assertIn(f"Presidente: Ana Torres — {CEDULA_VALIDA}", text)
        self.assertIn("Luis Perez — identificación pendiente de confirmar; 100 %", text)


if __name__ == "__main__":
    unittest.main()
