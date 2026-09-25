"""
tests/test_database.py — Pruebas de base de datos y lógica de negocio para Atlas.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import (
    add_administrator,
    add_shareholder,
    archive_audit,
    authenticate,
    change_password,
    compute_progress,
    connect,
    create_company_audit,
    create_session,
    create_user,
    deactivate_user,
    delete_administrator,
    delete_shareholder,
    get_audit,
    get_research,
    init_db,
    list_administrators,
    list_admin_audits,
    list_auditor_audits,
    list_auditors,
    list_shareholders,
    list_users,
    reactivate_user,
    reassign_audit,
    refresh_summary,
    register_audit_ruc,
    restore_audit,
    soft_delete_user,
    update_research,
    user_from_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> Path:
    """Crea una base de datos temporal para pruebas."""
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test.db"
    init_db(db_path, demo=True)
    return db_path


def _auditor_row(db_path: Path) -> sqlite3.Row:
    return authenticate("auditor", "auditor123", db_path)


def _admin_row(db_path: Path) -> sqlite3.Row:
    return authenticate("admin", "admin123", db_path)


def _full_data(**overrides) -> dict:
    base = {
        "commercial_name": "Demo Comercial",
        "economic_activity": "Servicios de auditoría",
        "legal_status": "Activa",
        "representative": "Persona Demo",
        "address": "Quito",
        "tax_obligations": "Sin pendientes registrados",
        "public_contracting": "No registra procesos revisados",
        "supercias_info": "Empresa activa según Supercias",
        "sri_info": "RUC activo, contribuyente regular",
        "sercop_info": "No registra como proveedor",
        "observations": "Revisión inicial completa",
        "risk_flags": "",
        "pasted_text": "RUC 1790000000001 contacto demo@empresa.com telefono 0999999999",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests de autenticación
# ---------------------------------------------------------------------------

class TestAuthentication(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()

    def test_admin_correct_credentials(self):
        user = authenticate("admin", "admin123", self.db)
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")

    def test_auditor_correct_credentials(self):
        user = authenticate("auditor", "auditor123", self.db)
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "auditor")

    def test_wrong_password(self):
        self.assertIsNone(authenticate("admin", "clave_incorrecta", self.db))

    def test_nonexistent_user(self):
        self.assertIsNone(authenticate("usuario_inexistente", "cualquier_clave", self.db))

    def test_case_insensitive_username(self):
        user = authenticate("ADMIN", "admin123", self.db)
        self.assertIsNotNone(user)

    def test_seed_users_exist(self):
        users = list_users(self.db)
        usernames = [u["username"] for u in users]
        self.assertIn("admin", usernames)
        self.assertIn("auditor", usernames)

    def test_base_nueva_solo_crea_al_jefe_auditor(self):
        db = Path(tempfile.mkdtemp()) / "nueva.db"
        init_db(db)
        self.assertEqual([(u["username"], u["role"]) for u in list_users(db)], [("admin", "admin")])
        self.assertIsNone(authenticate("auditor", "auditor123", db), "Sin auditor demo")
        with connect(db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 0,
                             "Sin empresa demo")

    def test_create_user_normalizes_username(self):
        with connect(self.db) as conn:
            create_user(conn, "  AUDITOR.NUEVO  ", "Auditor Nuevo", "auditor", "clave123")
        user = authenticate("auditor.nuevo", "clave123", self.db)
        self.assertIsNotNone(user)

    def test_duplicate_username_raises_clear_error(self):
        with connect(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "ya existe"):
                create_user(conn, "admin", "Otro Admin", "admin", "clave123")

    def test_invalid_username_is_rejected(self):
        with connect(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "usuario debe tener"):
                create_user(conn, "ab", "Usuario Corto", "auditor", "clave123")
            with self.assertRaisesRegex(ValueError, "usuario debe tener"):
                create_user(conn, "auditor nuevo", "Usuario Espacio", "auditor", "clave123")

    def test_short_password_is_rejected(self):
        with connect(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "entre 8 y 128"):
                create_user(conn, "nuevo", "Usuario Nuevo", "auditor", "clave12")

    def test_password_with_edge_spaces_is_rejected(self):
        """Antes se guardaba recortada y luego no servía para entrar."""
        with connect(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "espacios"):
                create_user(conn, "nuevo", "Usuario Nuevo", "auditor", " clave123 ")

    def test_inactive_user_cannot_authenticate(self):
        with connect(self.db) as conn:
            conn.execute("UPDATE users SET active = 0 WHERE username = 'auditor'")
        self.assertIsNone(authenticate("auditor", "auditor123", self.db))


class TestUserLifecycle(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.admin = _admin_row(self.db)
        self.auditor = _auditor_row(self.db)

    def test_deactivate_revokes_access_and_active_sessions(self):
        token = create_session(self.auditor["id"], self.db)
        self.assertIsNotNone(user_from_session(token, self.db))

        message = deactivate_user(self.auditor["id"], self.admin["id"], self.db)

        self.assertIn("empresas", message.lower())
        self.assertIsNone(authenticate("auditor", "auditor123", self.db))
        self.assertIsNone(user_from_session(token, self.db))
        with connect(self.db) as conn:
            session_count = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                (self.auditor["id"],),
            ).fetchone()[0]
        self.assertEqual(session_count, 0)

    def test_reactivate_requires_a_fresh_login(self):
        old_token = create_session(self.auditor["id"], self.db)
        deactivate_user(self.auditor["id"], self.admin["id"], self.db)
        reactivate_user(self.auditor["id"], self.admin["id"], self.db)

        self.assertIsNone(user_from_session(old_token, self.db))
        self.assertIsNotNone(authenticate("auditor", "auditor123", self.db))

    def test_definitive_deletion_requires_prior_deactivation(self):
        with self.assertRaisesRegex(ValueError, "Primero debes desactivar"):
            soft_delete_user(
                self.auditor["id"], self.admin["id"], "Salida de la empresa", self.db,
            )

    def test_definitive_deletion_preserves_user_company_and_audit(self):
        audit_before = list_auditor_audits(self.auditor["id"], self.db)[0]
        deactivate_user(self.auditor["id"], self.admin["id"], self.db)
        soft_delete_user(
            self.auditor["id"], self.admin["id"], "Finalización de relación laboral", self.db,
        )

        users = {row["id"]: row for row in list_users(self.db)}
        self.assertIn(self.auditor["id"], users)
        self.assertIsNotNone(users[self.auditor["id"]]["deleted_at"])
        self.assertEqual(users[self.auditor["id"]]["deleted_by"], self.admin["id"])
        self.assertEqual(
            users[self.auditor["id"]]["deletion_reason"],
            "Finalización de relación laboral",
        )

        audits = {row["id"]: row for row in list_admin_audits(self.db)}
        preserved = audits[audit_before["id"]]
        self.assertEqual(preserved["company_name"], audit_before["company_name"])
        self.assertEqual(preserved["auditor_name"], self.auditor["full_name"])
        self.assertIsNotNone(preserved["auditor_deleted_at"])

    def test_deleted_user_cannot_be_reactivated_or_reassigned(self):
        deactivate_user(self.auditor["id"], self.admin["id"], self.db)
        soft_delete_user(
            self.auditor["id"], self.admin["id"], "Finalización de relación laboral", self.db,
        )

        with self.assertRaisesRegex(ValueError, "no puede reactivarse"):
            reactivate_user(self.auditor["id"], self.admin["id"], self.db)
        self.assertNotIn(self.auditor["id"], [row["id"] for row in list_auditors(self.db)])

        with self.assertRaisesRegex(ValueError, "no está activo"):
            reassign_audit(1, self.auditor["id"], self.admin["id"], self.db)

    def test_deleted_username_is_reserved_for_history(self):
        deactivate_user(self.auditor["id"], self.admin["id"], self.db)
        soft_delete_user(
            self.auditor["id"], self.admin["id"], "Finalización de relación laboral", self.db,
        )

        with connect(self.db) as conn:
            with self.assertRaisesRegex(ValueError, "cuenta histórica"):
                create_user(conn, "auditor", "Nuevo Auditor", "auditor", "clave123")

    def test_admin_cannot_change_own_state(self):
        with self.assertRaisesRegex(ValueError, "propio usuario"):
            deactivate_user(self.admin["id"], self.admin["id"], self.db)

    def test_database_prevents_physical_user_deletion(self):
        with connect(self.db) as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "permanent historical records"):
                conn.execute("DELETE FROM users WHERE id = ?", (self.auditor["id"],))

    def test_reassignment_keeps_previous_auditor_company_history(self):
        with connect(self.db) as conn:
            auditor_two_id = create_user(
                conn, "auditor.dos", "Auditor Dos", "auditor", "clave123",
            )
        audit = list_auditor_audits(self.auditor["id"], self.db)[0]

        reassign_audit(audit["id"], auditor_two_id, self.admin["id"], self.db)

        with connect(self.db) as conn:
            history = list(conn.execute(
                """
                SELECT auditor_id, unassigned_at
                FROM audit_assignments
                WHERE audit_id = ?
                ORDER BY id
                """,
                (audit["id"],),
            ))
        self.assertEqual([row["auditor_id"] for row in history], [self.auditor["id"], auditor_two_id])
        self.assertIsNotNone(history[0]["unassigned_at"])
        self.assertIsNone(history[1]["unassigned_at"])


# ---------------------------------------------------------------------------
# Tests de RBAC
# ---------------------------------------------------------------------------

class TestRBAC(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        # Crear segundo auditor y nueva empresa asignada a él
        with connect(self.db) as conn:
            self.auditor2_id = create_user(conn, "auditor2", "Auditor Dos", "auditor", "clave-dos")
        admin = _admin_row(self.db)
        self.audit_for_auditor2 = create_company_audit(
            "Empresa Solo Auditor2", "0190000000001", "Guayaquil",
            "Comercio", "2026", self.auditor2_id, admin["id"], self.db,
        )

    def test_auditor_cannot_see_other_auditors_company(self):
        """El auditor original no debe poder ver la empresa asignada a auditor2."""
        auditor1 = _auditor_row(self.db)
        audit = get_audit(self.audit_for_auditor2, auditor1, self.db)
        self.assertIsNone(audit, "El auditor no debería ver empresas de otro auditor")

    def test_auditor_sees_only_own_assignments(self):
        """El auditor solo ve las auditorías asignadas a él."""
        auditor1 = _auditor_row(self.db)
        audits = list_auditor_audits(auditor1["id"], self.db)
        company_names = [a["company_name"] for a in audits]
        self.assertNotIn("Empresa Solo Auditor2", company_names)

    def test_admin_can_see_any_audit(self):
        """El admin puede ver cualquier auditoría."""
        admin = _admin_row(self.db)
        audit = get_audit(self.audit_for_auditor2, admin, self.db)
        self.assertIsNotNone(audit)

    def test_auditor2_sees_own_assignment(self):
        auditor2 = authenticate("auditor2", "clave-dos", self.db)
        audits = list_auditor_audits(auditor2["id"], self.db)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["company_name"], "Empresa Solo Auditor2")

    def test_company_cannot_be_assigned_to_admin(self):
        admin = _admin_row(self.db)
        with self.assertRaisesRegex(ValueError, "auditor activo"):
            create_company_audit(
                "Empresa Mal Asignada", "0191111111111", "Quito",
                "Servicios", "2026", admin["id"], admin["id"], self.db,
            )

    def test_company_cannot_be_assigned_to_inactive_auditor(self):
        admin = _admin_row(self.db)
        with connect(self.db) as conn:
            inactive_id = create_user(conn, "inactivo", "Auditor Inactivo", "auditor", "clave123")
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (inactive_id,))
        with self.assertRaisesRegex(ValueError, "auditor activo"):
            create_company_audit(
                "Empresa Inactiva", "0192222222222", "Cuenca",
                "Comercio", "2026", inactive_id, admin["id"], self.db,
            )

    def test_company_requires_13_digit_ruc_when_present(self):
        admin = _admin_row(self.db)
        with self.assertRaisesRegex(ValueError, "RUC"):
            create_company_audit(
                "Empresa RUC Invalido", "ABC", "Cuenca",
                "Comercio", "2026", self.auditor2_id, admin["id"], self.db,
            )

    def test_auditor_search_ruc_registers_ruc_when_assignment_is_empty(self):
        admin = _admin_row(self.db)
        audit_id = create_company_audit(
            "Empresa Sin RUC", "", "Cuenca",
            "Servicios", "2026", self.auditor2_id, admin["id"], self.db,
        )
        clean_ruc, msg = register_audit_ruc(audit_id, "019 000 000 0001", self.db)
        self.assertEqual(clean_ruc, "0190000000001")
        self.assertIn("0190000000001", msg)

        auditor2 = authenticate("auditor2", "clave-dos", self.db)
        audit = get_audit(audit_id, auditor2, self.db)
        self.assertEqual(audit["ruc"], "0190000000001")

    def test_auditor_search_ruc_cannot_replace_assigned_ruc(self):
        with self.assertRaisesRegex(ValueError, "no coincide"):
            register_audit_ruc(self.audit_for_auditor2, "0191111111111", self.db)

    def test_auditor_search_ruc_rejects_invalid_format(self):
        with self.assertRaisesRegex(ValueError, "RUC"):
            register_audit_ruc(self.audit_for_auditor2, "RUC_INVALIDO", self.db)


# ---------------------------------------------------------------------------
# Tests de flujo de investigación y estados
# ---------------------------------------------------------------------------

class TestResearchFlow(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)
        audits = list_auditor_audits(self.auditor["id"], self.db)
        self.audit_id = audits[0]["id"]
        self.admin = _admin_row(self.db)

    def test_initial_status_is_pendiente(self):
        audit = get_audit(self.audit_id, self.admin, self.db)
        self.assertEqual(audit["status"], "pendiente")

    def test_save_research_changes_status_to_en_investigacion(self):
        update_research(self.audit_id, self.auditor["id"], _full_data(), mark_ready=False, db_path=self.db)
        audit = get_audit(self.audit_id, self.admin, self.db)
        self.assertEqual(audit["status"], "en_investigacion")

    def test_mark_ready_no_longer_sends_to_review(self):
        """mark_ready es un parámetro heredado que update_research() ya no lee
        en su cuerpo: se conserva en la firma por compatibilidad con llamadores
        existentes, pero no cambia el resultado. Este test documenta ese hecho
        (True y False deben dar el mismo status) en vez de asumir que hace algo."""
        update_research(self.audit_id, self.auditor["id"], _full_data(), mark_ready=True, db_path=self.db)
        audit = get_audit(self.audit_id, self.admin, self.db)
        self.assertEqual(audit["status"], "en_investigacion")

    def test_research_generates_summary(self):
        summary = update_research(
            self.audit_id, self.auditor["id"], _full_data(), mark_ready=False, db_path=self.db
        )
        self.assertIn("GRUCANQUI", summary)
        research = get_research(self.audit_id, self.db)
        self.assertIsNotNone(research["generated_summary"])

    def test_refresh_summary_creates_notes_for_new_audit(self):
        audit_id = create_company_audit(
            "Empresa Nueva", "", "Cuenca", "Servicios", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )
        with connect(self.db) as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM research_notes WHERE audit_id = ?", (audit_id,)
            ).fetchone())

        summary = refresh_summary(audit_id, self.db)
        self.assertIn("Empresa Nueva", summary)
        self.assertEqual(get_research(audit_id, self.db)["generated_summary"], summary)

    def test_summary_contains_disclaimer(self):
        summary = update_research(
            self.audit_id, self.auditor["id"], _full_data(), mark_ready=False, db_path=self.db
        )
        self.assertIn("PRELIMINAR", summary.upper())

    def test_negated_obligations_not_flagged_as_risk(self):
        data = _full_data(tax_obligations="Sin obligaciones pendientes con el SRI")
        summary = update_research(
            self.audit_id, self.auditor["id"], data, mark_ready=False, db_path=self.db
        )
        self.assertNotIn("Posibles obligaciones tributarias pendientes", summary)

    def test_new_company_assignment(self):
        with connect(self.db) as conn:
            new_auditor_id = create_user(conn, "ana", "Ana Auditora", "auditor", "clave_ana")
        audit_id = create_company_audit(
            "Compania Nueva S.A.", "1799999999001", "Cuenca",
            "Manufacturero", "2026", new_auditor_id, self.admin["id"], self.db,
        )
        new_auditor = authenticate("ana", "clave_ana", self.db)
        audit = get_audit(audit_id, new_auditor, self.db)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["company_name"], "Compania Nueva S.A.")
        audits = list_auditor_audits(new_auditor["id"], self.db)
        self.assertEqual(len(audits), 1)


class TestCompanyPeople(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)
        self.admin = _admin_row(self.db)
        self.audit_id = create_company_audit(
            "Empresa Estructura S.A.", "", "Cuenca", "Servicios", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )

    def test_add_and_delete_administrator(self):
        administrator_id = add_administrator(
            self.audit_id, "0102030405", "Ana Torres", "Ecuatoriana",
            "Gerente General", self.db,
        )
        admins = list_administrators(self.audit_id, self.db)
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["nombre"], "Ana Torres")

        delete_administrator(self.audit_id, administrator_id, self.db)
        self.assertEqual(list_administrators(self.audit_id, self.db), [])

    def test_duplicate_administrator_is_rejected(self):
        add_administrator(
            self.audit_id, "0102030405", "Ana Torres", "Ecuatoriana",
            "Gerente General", self.db,
        )
        with self.assertRaisesRegex(ValueError, "ya esta registrado"):
            add_administrator(
                self.audit_id, "", "Ana Torres", "", "Gerente General", self.db,
            )

    def test_add_shareholders_assigns_next_number(self):
        add_shareholder(self.audit_id, "", "0102030405", "Ana Torres", self.db)
        add_shareholder(self.audit_id, "", "0102030406", "Luis Perez", self.db)
        shareholders = list_shareholders(self.audit_id, self.db)
        self.assertEqual([row["numero"] for row in shareholders], [1, 2])

    def test_duplicate_shareholder_is_rejected(self):
        add_shareholder(self.audit_id, "1", "0102030405", "Ana Torres", self.db)
        with self.assertRaisesRegex(ValueError, "ya esta registrado"):
            add_shareholder(self.audit_id, "2", "0102030405", "Otra Persona", self.db)

    def test_delete_is_scoped_to_audit(self):
        other_audit_id = create_company_audit(
            "Empresa Distinta S.A.", "", "Quito", "Comercio", "2026",
            self.auditor["id"], self.admin["id"], self.db,
        )
        shareholder_id = add_shareholder(
            other_audit_id, "1", "0102030405", "Ana Torres", self.db,
        )
        with self.assertRaisesRegex(ValueError, "no encontrado"):
            delete_shareholder(self.audit_id, shareholder_id, self.db)
        self.assertEqual(len(list_shareholders(other_audit_id, self.db)), 1)


# ---------------------------------------------------------------------------
# Tests de progreso
# ---------------------------------------------------------------------------

class TestProgress(unittest.TestCase):
    """Usa la empresa demo auto-sembrada por seed_defaults/seed_demo_radar
    (RUC, SRI, Supercias y financieros ya completos; solo 'fuentes guiadas
    consultadas' y 'resumen' quedan pendientes), así que las aserciones
    comparan el progreso antes/después de update_research en vez de fijar
    umbrales absolutos: un umbral fijo se vuelve falso en cuanto cambie
    cualquier dato del fixture demo, sin que compute_progress esté mal.
    """

    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)
        audits = list_auditor_audits(self.auditor["id"], self.db)
        self.audit_id = audits[0]["id"]
        self.admin = _admin_row(self.db)

    def _progress(self, source_count: int = 0) -> dict:
        audit = get_audit(self.audit_id, self.admin, self.db)
        research = get_research(self.audit_id, self.db)
        return compute_progress(audit, research, source_count=source_count, db_path=self.db)

    def test_initial_progress_low(self):
        """La demo trae RUC/SRI/Supercias/financieros, pero ninguna fuente
        guiada marcada como consultada ni resumen generado todavía."""
        progress = self._progress()
        self.assertFalse(progress["stages"]["has_sources"])
        self.assertFalse(progress["stages"]["has_summary"])
        self.assertLess(progress["percent"], 100)

    def test_progress_increases_after_research(self):
        before = self._progress(source_count=2)
        update_research(
            self.audit_id, self.auditor["id"], _full_data(), mark_ready=False, db_path=self.db
        )
        after = self._progress(source_count=2)
        self.assertGreater(after["percent"], before["percent"])
        self.assertTrue(after["stages"]["has_summary"])

    def test_progress_includes_summary_without_send_step(self):
        update_research(
            self.audit_id, self.auditor["id"], _full_data(), mark_ready=True, db_path=self.db
        )
        progress = self._progress(source_count=2)
        self.assertTrue(progress["stages"]["has_summary"])
        self.assertNotIn("is_sent", progress["stages"])


class TestCambiarContrasena(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)

    def _cambiar(self, actual="auditor123", nueva="nueva-clave-1", confirmacion=None, token=""):
        return change_password(
            self.auditor["id"], actual, nueva, nueva if confirmacion is None else confirmacion, token, self.db,
        )

    def test_cambia_la_contrasena(self):
        self._cambiar()
        self.assertIsNone(authenticate("auditor", "auditor123", self.db))
        self.assertIsNotNone(authenticate("auditor", "nueva-clave-1", self.db))

    def test_todos_los_roles_pueden_cambiarla(self):
        admin = _admin_row(self.db)
        change_password(admin["id"], "admin123", "jefe-clave-1", "jefe-clave-1", "", self.db)
        self.assertIsNotNone(authenticate("admin", "jefe-clave-1", self.db))

    def test_cierra_las_demas_sesiones_y_conserva_la_actual(self):
        actual, otra = create_session(self.auditor["id"], self.db), create_session(self.auditor["id"], self.db)
        self._cambiar(token=actual)
        self.assertIsNotNone(user_from_session(actual, self.db))
        self.assertIsNone(user_from_session(otra, self.db))

    def test_reglas(self):
        casos = [
            ({"actual": "incorrecta"}, "actual no es correcta"),
            ({"confirmacion": "otra-cosa-1"}, "no coinciden"),
            ({"nueva": "corta"}, "entre 8"),
            ({"nueva": "auditor123"}, "distinta de la actual"),
            ({"actual": ""}, "Ingrese"),
        ]
        for kwargs, error in casos:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self._cambiar(**kwargs)
        self.assertIsNotNone(authenticate("auditor", "auditor123", self.db), "Nada cambió")


class TestClaveInicial(unittest.TestCase):
    """La clave inicial del jefe y la temporal de un auditor deben cambiarse."""

    def setUp(self):
        self.db = _make_db()

    def test_base_nueva_marca_la_clave_del_jefe(self):
        self.assertEqual(_admin_row(self.db)["must_change_password"], 1)

    def test_clave_temporal_de_auditor_y_su_cambio(self):
        with connect(self.db) as conn:
            uid = create_user(conn, "temporal", "Auditor Temporal", "auditor", "temporal-1",
                              must_change_password=True)
        self.assertEqual(authenticate("temporal", "temporal-1", self.db)["must_change_password"], 1)
        change_password(uid, "temporal-1", "propia-clave-1", "propia-clave-1", "", self.db)
        self.assertEqual(authenticate("temporal", "propia-clave-1", self.db)["must_change_password"], 0)

    def test_migracion_marca_solo_la_clave_por_defecto(self):
        with connect(self.db) as conn:
            conn.execute("ALTER TABLE users DROP COLUMN must_change_password")
        init_db(self.db)
        self.assertEqual(_admin_row(self.db)["must_change_password"], 1)

        admin = _admin_row(self.db)
        change_password(admin["id"], "admin123", "jefe-clave-1", "jefe-clave-1", "", self.db)
        with connect(self.db) as conn:
            conn.execute("ALTER TABLE users DROP COLUMN must_change_password")
        init_db(self.db)
        self.assertEqual(authenticate("admin", "jefe-clave-1", self.db)["must_change_password"], 0)


class TestArchivarEmpresa(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.auditor = _auditor_row(self.db)
        self.admin = _admin_row(self.db)
        self.audit_id = create_company_audit(
            "Empresa Archivable", "", "Quito", "", "2026", self.auditor["id"], self.admin["id"], self.db,
        )

    def test_archivar_oculta_la_empresa_sin_borrar_el_expediente(self):
        add_administrator(self.audit_id, "0102030405", "ANA TORRES", "ECUADOR", "GERENTE", db_path=self.db)
        archive_audit(self.audit_id, self.admin["id"], "Registrada por error", self.db)

        self.assertNotIn(self.audit_id, [a["id"] for a in list_admin_audits(self.db)])
        self.assertNotIn(self.audit_id, [a["id"] for a in list_auditor_audits(self.auditor["id"], self.db)])
        self.assertIsNone(get_audit(self.audit_id, self.auditor, self.db), "El auditor pierde el acceso")

        archivada = list_admin_audits(self.db, archived=True)[0]
        self.assertEqual(archivada["id"], self.audit_id)
        self.assertEqual(archivada["archive_reason"], "Registrada por error")
        self.assertEqual(archivada["archived_by_name"], self.admin["full_name"])
        self.assertIsNotNone(get_audit(self.audit_id, self.admin, self.db), "El admin la consulta en lectura")
        self.assertEqual(len(list_administrators(self.audit_id, self.db)), 1, "El expediente se conserva")

    def test_restaurar_devuelve_la_empresa_al_auditor(self):
        archive_audit(self.audit_id, self.admin["id"], "Registrada por error", self.db)
        restore_audit(self.audit_id, self.admin["id"], self.db)
        self.assertIn(self.audit_id, [a["id"] for a in list_auditor_audits(self.auditor["id"], self.db)])
        self.assertEqual(list_admin_audits(self.db, archived=True), [])
        with self.assertRaisesRegex(ValueError, "no está archivada"):
            restore_audit(self.audit_id, self.admin["id"], self.db)

    def test_reglas(self):
        with self.assertRaisesRegex(ValueError, "motivo"):
            archive_audit(self.audit_id, self.admin["id"], "  ", self.db)
        with self.assertRaisesRegex(ValueError, "administrador activo"):
            archive_audit(self.audit_id, self.auditor["id"], "Registrada por error", self.db)
        archive_audit(self.audit_id, self.admin["id"], "Registrada por error", self.db)
        with self.assertRaisesRegex(ValueError, "ya está archivada"):
            archive_audit(self.audit_id, self.admin["id"], "Otra vez", self.db)
        with self.assertRaisesRegex(ValueError, "Restaure"):
            reassign_audit(self.audit_id, self.auditor["id"], self.admin["id"], self.db)


if __name__ == "__main__":
    unittest.main()
