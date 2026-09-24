"""
services/identificacion.py — Validación de identificaciones de administradores
y accionistas para Atlas.

Reglas del levantamiento de información:
  - Cédula ecuatoriana: 10 dígitos (bloqueante).
  - Además se revisa el código de provincia y el dígito verificador (módulo
    10). Si no coinciden se devuelve una advertencia, no un error: igual que
    en services/ruc_validator.py, la fuente oficial es la referencia final y
    existen cédulas antiguas que no cumplen el algoritmo.
  - RUC (accionistas personas jurídicas): se usa validate_ruc.
  - Pasaporte: alfanumérico de 5 a 20 caracteres.

La identificación vacía es válida al guardar: su obligatoriedad se exige antes
de generar el resumen, no al registrar a la persona.

No consulta bases de datos ni servicios externos.
"""
from __future__ import annotations

import re

from services.ruc_validator import validate_ruc

TIPOS_IDENTIFICACION = {
    "cedula": "Cédula",
    "pasaporte": "Pasaporte",
    "ruc": "RUC",
}

_VALID_PROVINCES = set(range(1, 25)) | {30}
_COEF_MOD10 = [2, 1, 2, 1, 2, 1, 2, 1, 2]
_PASAPORTE_RE = re.compile(r"^[A-Z0-9]{5,20}$")


def _limpiar(valor: str | None) -> str:
    return re.sub(r"[\s.\-]", "", str(valor or "")).upper()


def _cedula_advertencia(cedula: str) -> str:
    digits = [int(d) for d in cedula]
    province = digits[0] * 10 + digits[1]
    if province not in _VALID_PROVINCES:
        return f"La cédula {cedula} tiene un código de provincia no reconocido ({province:02d})."
    if digits[2] >= 6:
        return f"El tercer dígito de la cédula {cedula} no corresponde a una persona natural."
    total = 0
    for digit, coef in zip(digits, _COEF_MOD10):
        value = digit * coef
        total += value - 9 if value >= 10 else value
    if (10 - total % 10) % 10 != digits[9]:
        return f"El dígito verificador de la cédula {cedula} no coincide. Confírmela en la fuente."
    return ""


def cedula_valida(cedula: str) -> bool:
    """Cédula que cumple todo el algoritmo: 10 dígitos, provincia, tercer
    dígito de persona natural y dígito verificador. Más estricta que
    validar_identificacion(), que solo advierte: la usa el lector de
    certificados para no tomar por cédula un teléfono u otro número."""
    return len(cedula) == 10 and cedula.isdigit() and not _cedula_advertencia(cedula)


def ruc_valido(ruc: str) -> bool:
    """RUC de 13 dígitos con dígito verificador correcto (estricto, como
    cedula_valida)."""
    valid, warn, _msg = validate_ruc(ruc)
    return valid and not warn


def inferir_tipo(valor: str | None) -> str:
    """Tipo probable para registros sin tipo declarado: 10 dígitos -> cédula,
    13 dígitos -> RUC, cualquier otro valor -> pasaporte."""
    limpio = _limpiar(valor)
    if not limpio:
        return ""
    if limpio.isdigit() and len(limpio) == 10:
        return "cedula"
    if limpio.isdigit() and len(limpio) == 13:
        return "ruc"
    return "pasaporte"


def validar_identificacion(
    valor: str | None,
    tipo: str | None = "",
    permitidos: tuple[str, ...] = ("cedula", "pasaporte"),
) -> tuple[str, str, str]:
    """Valida y normaliza una identificación.

    Devuelve (tipo, identificacion, advertencia). Lanza ValueError si el
    formato no cumple la regla del tipo. Si no se declara el tipo, se infiere.
    """
    identificacion = _limpiar(valor)
    tipo = (tipo or "").strip().lower()
    if not identificacion:
        return (tipo if tipo in permitidos else ""), "", ""
    if not tipo:
        tipo = inferir_tipo(identificacion)
    if tipo not in permitidos:
        nombres = ", ".join(TIPOS_IDENTIFICACION[t] for t in permitidos)
        raise ValueError(f"Tipo de identificación no permitido. Use: {nombres}")

    if tipo == "cedula":
        if not (identificacion.isdigit() and len(identificacion) == 10):
            raise ValueError("La cédula ecuatoriana debe tener exactamente 10 dígitos")
        return tipo, identificacion, _cedula_advertencia(identificacion)
    if tipo == "ruc":
        valid, _warn, message = validate_ruc(identificacion)
        if not valid:
            raise ValueError(message)
        return tipo, identificacion, ""
    if not _PASAPORTE_RE.match(identificacion):
        raise ValueError("El pasaporte debe tener entre 5 y 20 letras o números")
    return tipo, identificacion, ""
