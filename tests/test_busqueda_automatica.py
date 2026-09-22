"""Pruebas de la búsqueda automática SRI + Supercias (Fase 2 del levantamiento).

Los valores de las tablas de mapeo son los que publican realmente el catastro
SRI y el Directorio de Compañías (corte 21/09/2026).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import database
from database import (
    add_administrator,
    apply_sri_research_result,
    apply_supercias_research_result,
    authenticate,
    create_company_audit,
    get_company_profile,
    init_db,
    list_administrators,
    update_company_profile_fields,
)
from services.company_research import build_sri_result, research_company_by_ruc
from services.normalizacion import (
    anio_fiscal_sugerido,
    clasificar_situacion_legal,
    clasificar_tipo_compania,
    con_valor_oficial,
    normalizar_texto,
    regimen_desde_clase,
)
from services.supercias_catalog import build_supercias_result
from views.auditor.radar import tab_financiero, tab_supercias
from services.financial import compute_indicators


REFERENCE_RUC = "0190377210001"

SRI_RECORD = {
    "ruc": REFERENCE_RUC,
    "name": "GRUCANQUI CIA. LTDA",
    "taxpayer_status": "ACTIVO",
    "taxpayer_class": "GEN",
    "taxpayer_type": "SOCIEDAD",
    "start_date": "2011-08-24 00:00:00",
    "province": "AZUAY",
    "canton": "CUENCA",
}

SUPERCIAS_RECORD = {
    "ruc": REFERENCE_RUC,
    "razon_social": "GRUCANQUI CIA. LTDA.",
    "expediente": "141528",
    "situacion_legal": "ACTIVA",
    "tipo_compania": "RESPONSABILIDAD LIMITADA",
    "representante": "CANDO SUAREZ MARIA DANIELA",
    "representante_cargo": "GERENTE GENERAL",
    "ultimo_balance": "2025",
}


class TestRegimen(unittest.TestCase):
    def test_confirmed_codes(self):
        self.assertEqual(regimen_desde_clase("GEN"), "GENERAL")
        self.assertEqual(regimen_desde_clase("RMP"), "RIMPE")
        self.assertEqual(regimen_desde_clase(" rmp "), "RIMPE")

    def test_unconfirmed_code_stays_pending(self):
        self.assertEqual(regimen_desde_clase("SIM"), "")
        self.assertEqual(regimen_desde_clase(""), "")
        self.assertEqual(regimen_desde_clase(None), "")

    def test_sri_result_carries_regimen_and_keeps_raw_class(self):
        profile = build_sri_result(SRI_RECORD)["profile"]
        self.assertEqual(profile["regimen"], "GENERAL")
        self.assertEqual(profile["categoria"], "GEN")

    def test_sri_result_explains_unmapped_class(self):
        result = build_sri_result({**SRI_RECORD, "taxpayer_class": "SIM"})
        self.assertEqual(result["profile"]["regimen"], "")
        self.assertIn("clase SIM sin equivalencia confirmada", result["research"]["sri_info"])


class TestClasificacionSupercias(unittest.TestCase):
    def test_every_company_type_in_directory(self):
        expected = {
            "SOCIEDAD POR ACCIONES SIMPLIFICADA": "S.A.S.",
            "ANÓNIMA": "S.A.",
            "RESPONSABILIDAD LIMITADA": "Cía. Ltda.",
            "SUCURSAL  EXTRANJERA": "Otra",
            "ASOCIACIÓN O CONSORCIO": "Otra",
            "ECONOMÍA MIXTA": "Otra",
            "ANÓNIMA  EN PREDIOS RÚSTICOS": "Otra",
            "ANÓNIMA MULTINACIONAL ANDINA": "Otra",
            "COMANDITA POR ACCIONES": "Otra",
            "": "",
        }
        for raw, categoria in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(clasificar_tipo_compania(raw), categoria)

    def test_every_legal_status_in_directory(self):
        expected = {
            "ACTIVA": "Activa",
            "DISOLUCIÓN Y LIQUIDACIÓN OFICIO INSCRITA EN RM": "Disolución y liquidación",
            "DISOLUCIÓN Y LIQUIDACIÓN DE PLENO DERECHO INSC. RM": "Disolución y liquidación",
            "DISOLUCIÓN Y LIQUIDACIÓN OFICIO NO INSCRITA EN RM": "Disolución y liquidación",
            "INACTIVA": "Inactiva",
            "DISOLUCIÓN Y LIQUIDACIÓN ANTICIPADA INSCRITA RM": "Disolución y liquidación",
            "CANCELACIÓN PERMISO OPERACIÓN - OFICIO INSCRITA RM": "Cancelación de permiso de operación",
            "DISOLUCIÓN Y LIQUIDACIÓ DE PLENO DERECHO NO INS RM": "Disolución y liquidación",
            "DISOLUCIÓN Y LIQUIDACIÓN ANTIC. NO INSCRITA EN RM": "Disolución y liquidación",
            "NO SUJETO CONTROL Y VIGILANCIA SCVS(ART. 432/OTRO)": "Otra",
            "CANCELACIÓN PERMISO OPERACIÓN - OFICIO NO INSCRITA": "Cancelación de permiso de operación",
            "CANCELACIÓN PERMISO OPERACIÓN - VOLUNT INSCRITA RM": "Cancelación de permiso de operación",
            "CANCELACIÓN PERMISO OPERACIÓN - VOLUNT NO INSCRITA": "Cancelación de permiso de operación",
            "MIGRADO A SEGUROS": "Otra",
            "": "",
        }
        for raw, categoria in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(clasificar_situacion_legal(raw), categoria)

    def test_inactiva_is_not_classified_as_activa(self):
        self.assertNotEqual(clasificar_situacion_legal("INACTIVA"), "Activa")

    def test_display_keeps_official_text_only_when_it_adds_information(self):
        self.assertEqual(con_valor_oficial("Activa", "ACTIVA"), "Activa")
        self.assertEqual(
            con_valor_oficial("Cía. Ltda.", "RESPONSABILIDAD LIMITADA"),
            "Cía. Ltda. (RESPONSABILIDAD LIMITADA)",
        )
        self.assertEqual(con_valor_oficial("Otra", "SUCURSAL  EXTRANJERA"), "Otra (SUCURSAL EXTRANJERA)")
        self.assertEqual(con_valor_oficial("", "texto libre"), "texto libre")

    def test_normalizar_texto(self):
        self.assertEqual(normalizar_texto("  Cando  Suárez María "), "CANDO SUAREZ MARIA")


class TestAnioFiscalSugerido(unittest.TestCase):
    HOY = date(2026, 9, 22)

    def test_last_balance_year(self):
        self.assertEqual(
            anio_fiscal_sugerido("2025", self.HOY),
            {"anio": 2025, "fecha_corte": "2025-12-31"},
        )

    def test_invalid_values(self):
        for value in ("", None, "sin dato", "25", "2027", "1985"):
            with self.subTest(value=value):
                self.assertIsNone(anio_fiscal_sugerido(value, self.HOY))

    def test_financial_tab_shows_suggestion_only_when_available(self):
        indicators = compute_indicators(None)
        html = tab_financiero.build(1, indicators, csrf_token="t", ruc=REFERENCE_RUC,
                                    anio_sugerido={"anio": 2025, "fecha_corte": "2025-12-31"})
        self.assertIn("Año fiscal sugerido: 2025", html)
        self.assertIn("2025-12-31", html)
        self.assertNotIn("Año fiscal sugerido",
                         tab_financiero.build(1, indicators, csrf_token="t", ruc=REFERENCE_RUC))


class _SearchCase(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "atlas.db"
        init_db(self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        self.admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "GRUCANQUI CIA. LTDA", REFERENCE_RUC, "Cuenca", "", "2025",
            self.auditor["id"], self.admin["id"], self.db,
        )

    def _apply_sri(self, record=SRI_RECORD):
        apply_sri_research_result(self.audit_id, self.auditor["id"], build_sri_result(record), self.db)

    def _apply_supercias(self, record=SUPERCIAS_RECORD):
        apply_supercias_research_result(
            self.audit_id, self.auditor["id"], build_supercias_result(record), self.db,
        )


class TestRegimenPersistence(_SearchCase):
    def test_search_stores_regimen(self):
        self._apply_sri()
        self.assertEqual(get_company_profile(self.audit_id, self.db)["regimen"], "GENERAL")

    def test_unmapped_class_does_not_erase_confirmed_regimen(self):
        self._apply_sri({**SRI_RECORD, "taxpayer_class": "SIM"})
        update_company_profile_fields(self.audit_id, {"regimen": "RIMPE"}, self.db)
        self._apply_sri({**SRI_RECORD, "taxpayer_class": "SIM"})

        profile = get_company_profile(self.audit_id, self.db)
        self.assertEqual(profile["regimen"], "RIMPE")
        self.assertEqual(profile["categoria"], "SIM")


class TestDirectoryAdministrator(_SearchCase):
    def test_representative_is_registered_as_administrator(self):
        self._apply_supercias()

        admins = list_administrators(self.audit_id, self.db)
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["nombre"], "CANDO SUAREZ MARIA DANIELA")
        self.assertEqual(admins[0]["cargo"], "GERENTE GENERAL")
        self.assertEqual(admins[0]["identificacion"], "")
        self.assertEqual(admins[0]["nacionalidad"], "")

    def test_repeated_search_does_not_duplicate(self):
        self._apply_supercias()
        self._apply_supercias()
        self.assertEqual(len(list_administrators(self.audit_id, self.db)), 1)

    def test_manual_record_with_accents_is_not_duplicated_by_search(self):
        add_administrator(
            self.audit_id, "0102030405", "Cando Suárez María Daniela", "Ecuatoriana",
            "Gerente General", self.db,
        )
        self._apply_supercias()

        admins = list_administrators(self.audit_id, self.db)
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["identificacion"], "0102030405")

    def test_manual_duplicate_after_search_is_rejected(self):
        self._apply_supercias()
        with self.assertRaises(ValueError):
            add_administrator(
                self.audit_id, "0102030405", "Cando Suárez María Daniela", "",
                "Gerente general", self.db,
            )

    def test_same_person_with_other_role_is_allowed(self):
        self._apply_supercias()
        add_administrator(self.audit_id, "", "Cando Suárez María Daniela", "", "Presidente", self.db)
        self.assertEqual(len(list_administrators(self.audit_id, self.db)), 2)

    def test_no_administrator_without_role(self):
        self._apply_supercias({**SUPERCIAS_RECORD, "representante_cargo": ""})
        self.assertEqual(list_administrators(self.audit_id, self.db), [])

    def test_supercias_tab_shows_category_with_official_text(self):
        self._apply_supercias()
        profile = get_company_profile(self.audit_id, self.db)
        html = tab_supercias.build(
            self.audit_id, {"ruc": REFERENCE_RUC, "company_name": "GRUCANQUI"}, profile, None,
            read_only=True,
        )
        self.assertIn("Cía. Ltda. (RESPONSABILIDAD LIMITADA)", html)
        self.assertIn(">Activa<", html)


def _load_catastro_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "update_catastro.py"
    spec = importlib.util.spec_from_file_location("update_catastro_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CSV_HEADER = (
    "NUMERO_RUC|RAZON_SOCIAL|CODIGO_JURISDICCION|ESTADO_CONTRIBUYENTE|CLASE_CONTRIBUYENTE|"
    "FECHA_INICIO_ACTIVIDADES|FECHA_ACTUALIZACION|FECHA_SUSPENSION_DEFINITIVA|"
    "FECHA_REINICIO_ACTIVIDADES|OBLIGADO|TIPO_CONTRIBUYENTE|NUMERO_ESTABLECIMIENTO|"
    "NOMBRE_FANTASIA_COMERCIAL|ESTADO_ESTABLECIMIENTO|DESCRIPCION_PROVINCIA_EST|"
    "DESCRIPCION_CANTON_EST|DESCRIPCION_PARROQUIA_EST|CODIGO_CIIU|ACTIVIDAD_ECONOMICA|"
    "AGENTE_RETENCION|ESPECIAL"
)


def _csv_row(ruc: str, name: str, provincia: str, establecimiento: str = "1") -> str:
    return (
        f"{ruc}|{name}|{provincia}|ACTIVO|GEN|2011-08-24 00:00:00|||||SOCIEDAD|{establecimiento}|"
        f"||{provincia}|CANTON|PARROQUIA|I551001|ACTIVIDAD|S|N"
    )


class TestCatastroLoader(unittest.TestCase):
    def setUp(self):
        self.script = _load_catastro_script()
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "sri_catastro.db"
        self.script.DB_PATH = self.db
        self.azuay = self.dir / "SRI_RUC_Azuay.csv"
        self.azuay.write_text("\n".join([
            _CSV_HEADER,
            _csv_row("0190377210001", "GRUCANQUI CIA. LTDA", "AZUAY"),
            _csv_row("0190377210001", "GRUCANQUI SUCURSAL", "AZUAY", establecimiento="2"),
        ]), encoding="utf-8")
        self.pichincha = self.dir / "SRI_RUC_Pichincha.csv"
        self.pichincha.write_text("\n".join([
            _CSV_HEADER,
            _csv_row("1790013235001", "C.A. ECUATORIANA DE CERAMICA", "PICHINCHA"),
        ]), encoding="utf-8")

    def _main(self, args: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.script.main(args)

    def _rucs(self) -> dict[str, str]:
        with sqlite3.connect(self.db) as conn:
            return dict(conn.execute("SELECT ruc, name FROM sri_catastro"))

    def test_separate_runs_keep_previous_provinces(self):
        self.assertEqual(self._main([str(self.azuay)]), 0)
        self.assertEqual(self._main([str(self.pichincha)]), 0)

        self.assertEqual(self._rucs(), {
            "0190377210001": "GRUCANQUI CIA. LTDA",
            "1790013235001": "C.A. ECUATORIANA DE CERAMICA",
        })

    def test_several_files_in_one_run(self):
        self.assertEqual(self._main([str(self.azuay), str(self.pichincha)]), 0)
        self.assertEqual(len(self._rucs()), 2)

    def test_only_main_establishment_is_loaded(self):
        self._main([str(self.azuay)])
        self.assertEqual(self._rucs()["0190377210001"], "GRUCANQUI CIA. LTDA")

    def test_replace_mode_clears_before_loading(self):
        self._main([str(self.azuay)])
        self._main(["--reemplazar", str(self.pichincha)])
        self.assertEqual(list(self._rucs()), ["1790013235001"])

    def test_missing_file_aborts_before_touching_the_base(self):
        self._main([str(self.azuay)])
        self.assertEqual(self._main(["--reemplazar", str(self.dir / "no_existe.csv")]), 1)
        self.assertIn("0190377210001", self._rucs())

    def test_each_load_is_recorded(self):
        self._main([str(self.azuay), str(self.pichincha)])
        with sqlite3.connect(self.db) as conn:
            meta = list(conn.execute("SELECT archivo, filas, modo FROM sri_catastro_meta ORDER BY id"))
        self.assertEqual(meta, [
            ("SRI_RUC_Azuay.csv", 1, "incremental"),
            ("SRI_RUC_Pichincha.csv", 1, "incremental"),
        ])


_CATALOGS_READY = (
    (Path(database.__file__).parent / "sri_catastro.db").exists()
    and database.SUPERCIAS_CATALOG_PATH.exists()
    and database.lookup_catastro(REFERENCE_RUC) is not None
    and database.lookup_supercias_catalog(REFERENCE_RUC) is not None
)


@unittest.skipUnless(_CATALOGS_READY, "Catálogos locales SRI/Supercias no importados o sin el RUC de referencia")
class TestReferenceCaseAgainstLocalCatalogs(_SearchCase):
    """Criterio de cierre de la Fase 2 con GRUCANQUI y los catálogos reales."""

    def test_reference_case(self):
        research_company_by_ruc(self.audit_id, REFERENCE_RUC, self.auditor["id"], self.db)

        profile = get_company_profile(self.audit_id, self.db)
        admins = list_administrators(self.audit_id, self.db)
        self.assertEqual(profile["regimen"], "GENERAL")
        self.assertEqual(clasificar_tipo_compania(profile["tipo_compania"]), "Cía. Ltda.")
        self.assertEqual(clasificar_situacion_legal(profile["situacion_legal"]), "Activa")
        self.assertEqual(profile["razon_social_sri"], "GRUCANQUI CIA. LTDA")
        self.assertEqual(profile["razon_social_supercias"], "GRUCANQUI CIA. LTDA.")
        self.assertEqual([(a["nombre"], a["cargo"]) for a in admins],
                         [("CANDO SUAREZ MARIA DANIELA", "GERENTE GENERAL")])
        self.assertEqual(anio_fiscal_sugerido(profile["ultimo_anio_balance"], date(2026, 9, 22)),
                         {"anio": 2025, "fecha_corte": "2025-12-31"})


if __name__ == "__main__":
    unittest.main()
