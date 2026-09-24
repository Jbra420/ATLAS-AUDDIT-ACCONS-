"""
services/normalizacion.py — Normalización de valores de los catálogos oficiales.

Traduce los códigos y textos del catastro SRI y del Directorio de Compañías a
las categorías del levantamiento de información, sin perder el valor original:
quien llama conserva el dato crudo y usa la categoría para mostrar y validar.

Regla: solo se traducen valores cuyo significado está confirmado. Lo que no
tiene equivalencia devuelve "" (régimen) u "Otra" (clasificaciones), para que
el auditor lo confirme en la fuente en lugar de que Atlas lo suponga.

No consulta bases de datos ni servicios externos.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date


def normalizar_texto(value: str | None) -> str:
    """Mayúsculas, sin tildes y con espacios simples. Base para comparar
    nombres escritos con distinta capitalización o acentuación."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


# ---------------------------------------------------------------------------
# SRI — Régimen
# ---------------------------------------------------------------------------

# CLASE_CONTRIBUYENTE del catastro. El catastro también publica "SIM" (solo en
# sociedades); su equivalencia no está confirmada, así que no se traduce.
_REGIMEN_POR_CLASE = {
    "GEN": "GENERAL",
    "RMP": "RIMPE",
}


def regimen_desde_clase(clase: str | None) -> str:
    """GEN -> GENERAL, RMP -> RIMPE; cualquier otro código -> "" (pendiente)."""
    return _REGIMEN_POR_CLASE.get(normalizar_texto(clase), "")


# ---------------------------------------------------------------------------
# Supercias — Tipo de compañía y situación legal
# ---------------------------------------------------------------------------

_TIPO_COMPANIA = {
    "RESPONSABILIDAD LIMITADA": "Cía. Ltda.",
    "ANONIMA": "S.A.",
    "SOCIEDAD POR ACCIONES SIMPLIFICADA": "S.A.S.",
}


def clasificar_tipo_compania(raw: str | None) -> str:
    """Categoría del levantamiento: Cía. Ltda. / S.A. / S.A.S. / Otra.

    Solo las tres formas del requisito tienen categoría propia. Las variantes
    (p. ej. "ANÓNIMA EN PREDIOS RÚSTICOS", "SUCURSAL EXTRANJERA") quedan como
    "Otra" y se muestran junto a su texto oficial.
    """
    key = normalizar_texto(raw)
    if not key:
        return ""
    return _TIPO_COMPANIA.get(key, "Otra")


def clasificar_situacion_legal(raw: str | None) -> str:
    """Categoría del levantamiento a partir del texto del Directorio.

    El Directorio publica textos como "DISOLUCIÓN Y LIQUIDACIÓN OFICIO
    INSCRITA EN RM" (también con la errata "LIQUIDACIÓ"). Se agrupan en:
    Activa, Disolución y liquidación, Disolución, Liquidación, Inactiva,
    Cancelación de permiso de operación u Otra.
    """
    key = normalizar_texto(raw)
    if not key:
        return ""
    if key == "ACTIVA":
        return "Activa"
    if key == "INACTIVA":
        return "Inactiva"
    disolucion = "DISOLUCION" in key
    liquidacion = "LIQUIDACI" in key
    if disolucion and liquidacion:
        return "Disolución y liquidación"
    if disolucion:
        return "Disolución"
    if liquidacion:
        return "Liquidación"
    if key.startswith("CANCELACION PERMISO"):
        return "Cancelación de permiso de operación"
    return "Otra"


def con_valor_oficial(categoria: str, raw: str | None) -> str:
    """Muestra la categoría junto al texto oficial cuando aportan algo distinto:
    "Cía. Ltda. (RESPONSABILIDAD LIMITADA)", pero solo "Activa" para "ACTIVA"."""
    raw_text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not categoria:
        return raw_text
    if not raw_text or normalizar_texto(categoria) == normalizar_texto(raw_text):
        return categoria
    return f"{categoria} ({raw_text})"


# ---------------------------------------------------------------------------
# Información financiera — año fiscal sugerido
# ---------------------------------------------------------------------------

def anio_fiscal_sugerido(ultimo_balance: str | int | None, hoy: date | None = None) -> dict | None:
    """Año fiscal sugerido a partir del último balance presentado a Supercias.

    Devuelve {"anio": 2025, "fecha_corte": "2025-12-31"} o None si el valor no
    es un año válido (vacío, texto o posterior al año en curso). Es solo una
    sugerencia: el auditor debe confirmar el año antes de registrar cifras.
    """
    text = str(ultimo_balance or "").strip()
    if not re.fullmatch(r"\d{4}", text):
        return None
    anio = int(text)
    actual = (hoy or date.today()).year
    if anio < 1990 or anio > actual:
        return None
    return {"anio": anio, "fecha_corte": f"{anio}-12-31"}
