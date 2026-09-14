"""
services/ruc_validator.py — Validador de RUC ecuatoriano para Atlas.

Reglas implementadas (fase 1):
  - Exactamente 13 dígitos numéricos.
  - Los 2 primeros dígitos = código de provincia (01-24 o 30).
  - Dígito 3 indica tipo de contribuyente:
      0-5  → persona natural
      6    → entidad pública
      9    → sociedad privada/extranjera
  - Los 3 últimos dígitos = código de establecimiento (001-999).
  - Dígito verificador calculado según módulo 10 (persona natural / sociedad)
    o módulo 11 (entidad pública). Si el cálculo no concuerda se marca como
    "formato posiblemente inválido" pero no se bloquea (las bases de datos
    oficiales son la fuente de verdad).

No se hace consulta a servicios externos. Esta validación es local y preliminar.
"""
from __future__ import annotations

import re


# Provincias válidas en Ecuador (códigos 01-24 + 30 para extranjeros)
_VALID_PROVINCES = set(range(1, 25)) | {30}

# Coeficientes para módulo 10 (persona natural y sociedad privada)
_COEF_MOD10 = [2, 1, 2, 1, 2, 1, 2, 1, 2]

# Coeficientes para módulo 11 (entidad pública)
_COEF_MOD11 = [3, 2, 7, 6, 5, 4, 3, 2]


def _mod10_check(digits: list[int]) -> bool:
    """Verifica dígito de control para persona natural (posición 0-8, verificador en 9)."""
    total = 0
    for i, coef in enumerate(_COEF_MOD10):
        val = digits[i] * coef
        if val >= 10:
            val -= 9
        total += val
    expected = (10 - (total % 10)) % 10
    return expected == digits[9]


def _mod11_check_public(digits: list[int]) -> bool:
    """Verifica dígito de control para entidad pública (posición 0-7, verificador en 8)."""
    total = sum(d * c for d, c in zip(digits[:8], _COEF_MOD11))
    remainder = total % 11
    if remainder == 0:
        expected = 0
    elif remainder == 1:
        # Inválido según la norma, pero no bloqueamos
        return False
    else:
        expected = 11 - remainder
    return expected == digits[8]


def _mod11_check_private(digits: list[int]) -> bool:
    """Verifica dígito de control para sociedad privada/extranjera (posición 0-8, verificador en 9)."""
    coefs = [4, 3, 2, 7, 6, 5, 4, 3, 2]
    total = sum(d * c for d, c in zip(digits[:9], coefs))
    remainder = total % 11
    if remainder == 0:
        expected = 0
    elif remainder == 1:
        return False
    else:
        expected = 11 - remainder
    return expected == digits[9]


def validate_ruc(ruc: str) -> tuple[bool, str]:
    """
    Valida un RUC ecuatoriano.

    Returns:
        (True, mensaje_ok) si el RUC pasa las validaciones.
        (False, mensaje_error) si hay algún problema.
    """
    if not ruc:
        return False, "El RUC no puede estar vacío."

    ruc = ruc.strip()

    if not re.fullmatch(r"\d{13}", ruc):
        return False, f"El RUC debe tener exactamente 13 dígitos numéricos. Se recibió: '{ruc}'."

    digits = [int(c) for c in ruc]
    province = digits[0] * 10 + digits[1]

    if province not in _VALID_PROVINCES:
        return (
            False,
            f"Código de provincia inválido ({province:02d}). "
            "Los códigos válidos son 01-24 y 30.",
        )

    third = digits[2]

    if third in range(0, 6):
        # Persona natural
        tipo = "Persona natural"
        check_ok = _mod10_check(digits)
    elif third == 6:
        # Entidad pública
        tipo = "Entidad pública"
        check_ok = _mod11_check_public(digits)
    elif third == 9:
        # Sociedad privada / extranjera
        tipo = "Sociedad privada o extranjera"
        check_ok = _mod11_check_private(digits)
    else:
        return (
            False,
            f"Tercer dígito inválido ({third}). Valores permitidos: 0-6 y 9.",
        )

    establishment = int(ruc[10:13])
    if establishment < 1:
        return False, "Los últimos 3 dígitos (código de establecimiento) deben ser 001 o mayor."

    if not check_ok:
        # No bloqueamos: el dígito verificador puede fallar en RUC ficticios de prueba.
        # Advertimos pero dejamos continuar para no obstaculizar el trabajo del auditor.
        return (
            True,
            f"RUC {ruc} — {tipo}. ⚠ El dígito verificador no coincide; "
            "confirmar con fuente oficial (SRI).",
        )

    return True, f"RUC {ruc} válido — {tipo}."


def format_ruc(ruc: str) -> str:
    """Devuelve el RUC limpio (solo dígitos, sin espacios)."""
    return re.sub(r"\D", "", ruc or "")
