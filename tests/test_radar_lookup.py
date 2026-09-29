"""Estados del encabezado compacto de búsqueda del expediente."""

import unittest

from views.auditor.radar.page import _render_search_bar


class TestRadarLookup(unittest.TestCase):
    def render(self, *, ruc="0190444619001", read_only=False, sri=None, supercias=None, years=0):
        return _render_search_bar(
            3, "COBBLERCOMPANY CIA. LTDA.", ruc, read_only, "csrf-token",
            sri, supercias, years,
        )

    def test_assigned_ruc_has_one_search_action_and_no_editable_ruc(self):
        html = self.render()
        self.assertIn("COBBLERCOMPANY CIA. LTDA.", html)
        self.assertIn("RUC válido", html)
        self.assertIn('name="search_ruc" value="0190444619001"', html)
        self.assertIn('action="/auditor/radar/investigate"', html)
        self.assertEqual(html.count('type="submit"'), 1)
        self.assertNotIn('id="rs_ruc"', html)
        self.assertNotIn("Validar RUC", html)

    def test_missing_ruc_is_entered_inline_before_search(self):
        html = self.render(ruc=None)
        self.assertIn('id="rs_ruc"', html)
        self.assertIn('pattern="[0-9]{13}"', html)
        self.assertIn("RUC pendiente", html)
        self.assertIn("Iniciar búsqueda", html)
        self.assertNotIn("SRI pendiente", html)

    def test_source_status_and_refresh_action_use_available_data(self):
        html = self.render(
            sri={"estado": "consultada"},
            supercias={"estado": "pendiente"},
            years=2,
        )
        self.assertIn("SRI consultado", html)
        self.assertIn("Supercias pendiente", html)
        self.assertIn("Financiero 2 año(s) disponible(s)", html)
        self.assertIn("Actualizar búsqueda", html)
        self.assertIn('data-confirm-refresh="true"', html)
        self.assertIn('<dialog id="radar-refresh-dialog"', html)
        self.assertIn("correcciones que usted registró a mano", html)
        self.assertNotIn("window.confirm", html)

    def test_first_search_has_no_confirmation_dialog(self):
        html = self.render()
        self.assertNotIn('data-confirm-refresh="true"', html)
        self.assertNotIn('<dialog id="radar-refresh-dialog"', html)

    def test_admin_sees_status_without_search_form(self):
        html = self.render(read_only=True, sri={"estado": "consultada"})
        self.assertIn("Solo lectura", html)
        self.assertIn("SRI consultado", html)
        self.assertNotIn("<form", html)
        self.assertNotIn('action="/auditor/radar/investigate"', html)

    def test_company_name_is_escaped(self):
        html = _render_search_bar(3, "ACME <script>", None, True, "", None, None, 0)
        self.assertIn("ACME &lt;script&gt;", html)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
