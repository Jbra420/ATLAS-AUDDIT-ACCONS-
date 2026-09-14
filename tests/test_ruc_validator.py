"""
tests/test_ruc_validator.py — Pruebas del validador de RUC ecuatoriano para Atlas.
"""
from __future__ import annotations

import unittest

from services.ruc_validator import validate_ruc, format_ruc


class TestRucValidator(unittest.TestCase):

    # ── Casos inválidos básicos ──────────────────────────────────────────

    def test_empty_ruc(self):
        valid, msg = validate_ruc("")
        self.assertFalse(valid)
        self.assertIn("vacío", msg.lower())

    def test_too_short(self):
        valid, msg = validate_ruc("179000000000")  # 12 dígitos
        self.assertFalse(valid)
        self.assertIn("13", msg)

    def test_too_long(self):
        valid, msg = validate_ruc("17900000000011")  # 14 dígitos
        self.assertFalse(valid)

    def test_contains_letters(self):
        valid, msg = validate_ruc("179A000000001")
        self.assertFalse(valid)
        self.assertIn("13", msg)

    def test_invalid_province_code(self):
        # Provincia 25 no existe
        valid, msg = validate_ruc("2590000000001")
        self.assertFalse(valid)
        self.assertIn("provincia", msg.lower())

    def test_invalid_third_digit(self):
        # Tercer dígito 7 no está permitido
        valid, msg = validate_ruc("1770000000001")
        self.assertFalse(valid)

    # ── Provincias válidas ───────────────────────────────────────────────

    def test_province_01_valid(self):
        # RUC demo de la provincia 01 (Azuay) — puede no pasar dígito verificador
        # pero debe pasar la validación de provincia
        ruc = "0190000000001"
        valid, msg = validate_ruc(ruc)
        # Puede ser valid=True (con advertencia) o False (verificador)
        # Lo importante es que NO falle por provincia
        self.assertNotIn("provincia", msg.lower() if not valid else "")

    def test_province_30_extranjero(self):
        ruc = "3000000000001"
        valid, msg = validate_ruc(ruc)
        # No debe fallar por provincia
        self.assertNotIn("Código de provincia inválido", msg)

    # ── RUC demo de prueba (seed) ────────────────────────────────────────

    def test_demo_ruc_seed(self):
        """El RUC demo de la seed tiene formato válido aunque sea ficticio."""
        ruc = "1790000000001"
        valid, msg = validate_ruc(ruc)
        # Debe pasar formato y provincia; puede advertir sobre verificador
        self.assertIn("1790000000001", msg)
        # No debe decir "provincia inválido"
        self.assertNotIn("Código de provincia inválido", msg)

    # ── Formato de tercer dígito por tipo ────────────────────────────────

    def test_persona_natural_third_digit(self):
        """Tercer dígito 0-5 → persona natural."""
        ruc = "1710000000001"
        valid, msg = validate_ruc(ruc)
        # Solo verificamos que no falle por tercer dígito
        self.assertNotIn("Tercer dígito inválido", msg)

    def test_entidad_publica_third_digit(self):
        """Tercer dígito 6 → entidad pública."""
        ruc = "1760000000001"
        valid, msg = validate_ruc(ruc)
        self.assertNotIn("Tercer dígito inválido", msg)

    def test_sociedad_privada_third_digit(self):
        """Tercer dígito 9 → sociedad privada."""
        ruc = "1790000000001"
        valid, msg = validate_ruc(ruc)
        self.assertNotIn("Tercer dígito inválido", msg)

    # ── format_ruc ───────────────────────────────────────────────────────

    def test_format_ruc_removes_spaces(self):
        self.assertEqual(format_ruc("179 0000 000 001"), "1790000000001")

    def test_format_ruc_removes_dashes(self):
        self.assertEqual(format_ruc("179-000-000-0001"), "1790000000001")

    def test_format_ruc_none(self):
        self.assertEqual(format_ruc(None), "")

    def test_format_ruc_empty(self):
        self.assertEqual(format_ruc(""), "")


if __name__ == "__main__":
    unittest.main()
