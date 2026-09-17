"""
tests/test_ruc_validator.py — Pruebas del validador de RUC ecuatoriano para Atlas.
"""
from __future__ import annotations

import unittest

from services.ruc_validator import validate_ruc, format_ruc


class TestRucValidator(unittest.TestCase):

    # ── Casos inválidos básicos ──────────────────────────────────────────

    def test_empty_ruc(self):
        valid, warn, msg = validate_ruc("")
        self.assertFalse(valid)
        self.assertFalse(warn)
        self.assertIn("vacío", msg.lower())

    def test_too_short(self):
        valid, warn, msg = validate_ruc("179000000000")  # 12 dígitos
        self.assertFalse(valid)
        self.assertFalse(warn)
        self.assertIn("13", msg)

    def test_too_long(self):
        valid, warn, msg = validate_ruc("17900000000011")  # 14 dígitos
        self.assertFalse(valid)
        self.assertFalse(warn)

    def test_contains_letters(self):
        valid, warn, msg = validate_ruc("179A000000001")
        self.assertFalse(valid)
        self.assertFalse(warn)
        self.assertIn("13", msg)

    def test_invalid_province_code(self):
        # Provincia 25 no existe
        valid, warn, msg = validate_ruc("2590000000001")
        self.assertFalse(valid)
        self.assertFalse(warn)
        self.assertIn("provincia", msg.lower())

    def test_invalid_third_digit(self):
        # Tercer dígito 7 no está permitido
        valid, warn, msg = validate_ruc("1770000000001")
        self.assertFalse(valid)
        self.assertFalse(warn)

    # ── Señal de advertencia (warn) ──────────────────────────────────────

    def test_checksum_mismatch_sets_warn_without_blocking(self):
        """Formato/provincia válidos pero dígito verificador que no cuadra:
        valid debe seguir en True (no bloqueamos) y warn debe ser True."""
        ruc = "0190000000001"  # provincia y tercer dígito válidos, checksum no
        valid, warn, msg = validate_ruc(ruc)
        self.assertTrue(valid)
        self.assertTrue(warn)
        self.assertIn("dígito verificador", msg.lower())

    def test_valid_checksum_does_not_set_warn(self):
        """Un RUC con dígito verificador correcto no debe marcar warn."""
        valid, warn, msg = validate_ruc("1790377210001")
        self.assertTrue(valid)
        self.assertFalse(warn)

    def test_no_message_contains_emoji_markers(self):
        """Los mensajes ya no deben depender de emojis para señalar estado;
        eso lo hacen los booleanos valid/warn."""
        for ruc in ("", "123", "2590000000001", "0190000000001", "1790377210001"):
            _, _, msg = validate_ruc(ruc)
            for marker in ("⚠", "✓", "✗"):
                self.assertNotIn(marker, msg)

    # ── Provincias válidas ───────────────────────────────────────────────

    def test_province_01_valid(self):
        # RUC demo de la provincia 01 (Azuay) — puede no pasar dígito verificador
        # pero debe pasar la validación de provincia
        ruc = "0190000000001"
        valid, warn, msg = validate_ruc(ruc)
        # Puede ser valid=True (con advertencia) o False (verificador)
        # Lo importante es que NO falle por provincia
        self.assertNotIn("provincia", msg.lower() if not valid else "")

    def test_province_30_extranjero(self):
        ruc = "3000000000001"
        valid, warn, msg = validate_ruc(ruc)
        # No debe fallar por provincia
        self.assertNotIn("Código de provincia inválido", msg)

    # ── RUC demo de prueba (seed) ────────────────────────────────────────

    def test_demo_ruc_seed(self):
        """El RUC demo de la seed tiene formato válido aunque sea ficticio."""
        ruc = "1790000000001"
        valid, warn, msg = validate_ruc(ruc)
        # Debe pasar formato y provincia; puede advertir sobre verificador
        self.assertIn("1790000000001", msg)
        # No debe decir "provincia inválido"
        self.assertNotIn("Código de provincia inválido", msg)

    # ── Formato de tercer dígito por tipo ────────────────────────────────

    def test_persona_natural_third_digit(self):
        """Tercer dígito 0-5 → persona natural."""
        ruc = "1710000000001"
        valid, warn, msg = validate_ruc(ruc)
        # Solo verificamos que no falle por tercer dígito
        self.assertNotIn("Tercer dígito inválido", msg)

    def test_entidad_publica_third_digit(self):
        """Tercer dígito 6 → entidad pública."""
        ruc = "1760000000001"
        valid, warn, msg = validate_ruc(ruc)
        self.assertNotIn("Tercer dígito inválido", msg)

    def test_sociedad_privada_third_digit(self):
        """Tercer dígito 9 → sociedad privada."""
        ruc = "1790000000001"
        valid, warn, msg = validate_ruc(ruc)
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
