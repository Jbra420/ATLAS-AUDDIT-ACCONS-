"""
services/trazabilidad.py — Reglas de trazabilidad por dato para Atlas.

Regla del levantamiento: cada dato registra su fuente y la fecha de consulta.

La fuente no la escribe el auditor: queda fijada por el bloque del dato (y, en
la búsqueda automática, por el catálogo local consultado). La fecha de consulta
es la fecha en que se revisó la fuente oficial: la indica el auditor en cada
formulario (por defecto, hoy) y no puede ser futura.

No consulta bases de datos: database.py registra y lee el historial; este
módulo solo define las fuentes, valida fechas y resume el historial.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from services.rowutil import row_get

# Fuente oficial de cada bloque del levantamiento (sección "Fuente" del requisito).
FUENTE_SRI = "SRI — Consulta de RUC"
FUENTE_SUPERCIAS_GENERAL = "Supercias — Información general"
FUENTE_SUPERCIAS_UBICACION = "Supercias — Información general / Ubicación"
FUENTE_SUPERCIAS_ADMINISTRADORES = "Supercias — Administradores actuales"
FUENTE_SUPERCIAS_ACCIONISTAS = "Supercias — Accionistas / Kárdex"

# Catálogos locales usados por "Iniciar búsqueda".
FUENTE_CATASTRO_SRI = "Catastro RUC SRI (base local)"
FUENTE_DIRECTORIO_SUPERCIAS = "Directorio de Compañías Supercias (catálogo local)"

BLOQUE_SRI = "sri"
BLOQUE_SUPERCIAS = "supercias"
BLOQUE_UBICACION = "ubicacion"
BLOQUE_ADMINISTRADORES = "administradores"
BLOQUE_ACCIONISTAS = "accionistas"
BLOQUE_FINANCIERO = "financiero"
BLOQUE_VALIDACIONES = "validaciones"

FUENTE_MANUAL_POR_BLOQUE = {
    BLOQUE_SRI: FUENTE_SRI,
    BLOQUE_SUPERCIAS: FUENTE_SUPERCIAS_GENERAL,
    BLOQUE_UBICACION: FUENTE_SUPERCIAS_UBICACION,
    BLOQUE_ADMINISTRADORES: FUENTE_SUPERCIAS_ADMINISTRADORES,
    BLOQUE_ACCIONISTAS: FUENTE_SUPERCIAS_ACCIONISTAS,
}

# Campos del perfil agrupados por bloque del levantamiento. Los campos que no
# figuran aquí (ruc, razon_social heredada, metadatos del catálogo) no se
# historizan.
CAMPOS_PERFIL_SRI = (
    "razon_social_sri", "estado_contribuyente", "tipo_contribuyente", "regimen",
    "categoria", "obligado_contabilidad", "agente_retencion", "contribuyente_especial",
    "fecha_inicio_actividades", "fecha_actualizacion", "actividad_economica",
    "representante_legal_sri", "contribuyente_fantasma", "transacciones_inexistentes", "ciiu_sri",
)
CAMPOS_PERFIL_SUPERCIAS = (
    "razon_social_supercias", "expediente_supercias", "nacionalidad", "tipo_compania",
    "situacion_legal", "fecha_constitucion", "plazo_social", "oficina_control",
    "objeto_social", "representante_legal", "representante_cargo", "telefono",
    "capital_suscrito", "ciiu_nivel1", "ciiu_nivel6", "ultimo_anio_balance",
)
CAMPOS_UBICACION = (
    "provincia", "canton", "ciudad", "calle", "numero", "interseccion", "barrio", "referencia",
)


def bloque_de_campo_perfil(campo: str) -> str:
    if campo in CAMPOS_PERFIL_SRI:
        return BLOQUE_SRI
    if campo in CAMPOS_PERFIL_SUPERCIAS:
        return BLOQUE_SUPERCIAS
    return ""


def validar_fecha_consulta(valor: str | None, hoy: date | None = None) -> str:
    """Devuelve la fecha AAAA-MM-DD validada; vacía equivale a hoy.

    Lanza ValueError si el formato no es AAAA-MM-DD, si la fecha no existe o
    si es posterior a hoy.
    """
    hoy = hoy or date.today()
    texto = str(valor or "").strip()
    if not texto:
        return hoy.isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        raise ValueError("La fecha de consulta debe tener el formato AAAA-MM-DD")
    try:
        fecha = date.fromisoformat(texto)
    except ValueError as exc:
        raise ValueError("La fecha de consulta no es una fecha válida") from exc
    if fecha > hoy:
        raise ValueError("La fecha de consulta no puede ser posterior a hoy")
    return fecha.isoformat()


def ultimo_por_campo(historial: Iterable[Any]) -> dict[tuple[str, str], Any]:
    """Último registro de trazabilidad por (bloque, campo).

    historial debe venir en orden cronológico (id ascendente); el último
    registro de cada campo es el que describe su valor vigente.
    """
    ultimo: dict[tuple[str, str], Any] = {}
    for row in historial or []:
        ultimo[(row_get(row, "bloque"), row_get(row, "campo"))] = row
    return ultimo


def etiqueta_traza(row: Any) -> str:
    """Texto corto "fuente · fecha" para mostrar junto a un dato."""
    if not row:
        return ""
    fuente = str(row_get(row, "fuente", "")).strip()
    fecha = str(row_get(row, "fecha_consulta", "")).strip()
    return " · ".join(p for p in (fuente, fecha) if p)
