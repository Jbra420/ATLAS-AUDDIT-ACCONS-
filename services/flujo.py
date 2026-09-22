"""
services/flujo.py — Flujo de consulta del levantamiento de información.

Traduce el resultado de services/validaciones.evaluar_levantamiento() a los 9
pasos del requisito ("8. Flujo de consulta"), para que la interfaz guíe al
auditor en ese orden. No decide requisitos por su cuenta: cada paso agrupa los
requisitos que ya evalúa el servicio de validaciones. No consulta bases de datos.
"""
from __future__ import annotations

from typing import Any

from services.rowutil import row_get
from services.validaciones import CASILLEROS_OBLIGATORIOS, NIVEL_CRITICA, NO_COINCIDE, PENDIENTE

COMPLETO = "completo"
EN_AVANCE = "en_avance"
PENDIENTE_PASO = "pendiente"
CON_ALERTAS = "con_alertas"

ESTADOS = {
    COMPLETO: "Completo",
    EN_AVANCE: "En avance",
    PENDIENTE_PASO: "Pendiente",
    CON_ALERTAS: "Revisar alertas",
}

_CASILLEROS_ESF = ("1", "2", "3")
_CASILLEROS_ERI = ("401", "403", "501", "502", "707")


def _estado(requisitos: list[dict]) -> tuple[str, str]:
    total = len(requisitos)
    cumplidos = sum(1 for r in requisitos if r["ok"])
    if total and cumplidos == total:
        return COMPLETO, f"{cumplidos} de {total} requisitos"
    if cumplidos:
        return EN_AVANCE, f"{cumplidos} de {total} requisitos"
    return PENDIENTE_PASO, f"{cumplidos} de {total} requisitos"


def _por_fuente(validacion: dict, *fuentes: str) -> list[dict]:
    return [r for r in validacion.get("requisitos", []) if r["source"] in fuentes]


def _casilleros(registrados: dict[str, bool], casilleros: tuple[str, ...]) -> list[dict]:
    return [{"ok": registrados.get(c, False)} for c in casilleros]


def pasos_levantamiento(
    validacion: dict,
    fuente_sri_consultada: bool,
    fuente_supercias_consultada: bool,
    casilleros_registrados: dict[str, bool],
    documentos_economicos_consultados: bool = False,
) -> list[dict[str, Any]]:
    """Los 9 pasos del flujo de consulta con su estado.

    casilleros_registrados: {"1": True, "401": False, ...} del ejercicio de la
    auditoría (ver casilleros_con_valor).
    """
    anio = [r for r in _por_fuente(validacion, "Financiero") if r["label"].startswith("Año fiscal")]
    pasos = [
        (1, "SRI", "Consulta de RUC: registrar el bloque 1", "sri",
         [{"ok": fuente_sri_consultada}] + _por_fuente(validacion, "SRI")),
        (2, "Supercias", "Búsqueda de compañías por RUC", "supercias",
         [{"ok": fuente_supercias_consultada}]),
        (3, "Supercias", "Información general: registrar los bloques 2 y 3", "supercias",
         _por_fuente(validacion, "Supercias", "Ubicación")),
        (4, "Supercias", "Administradores actuales: registrar el bloque 4", "admins",
         _por_fuente(validacion, "Administradores")),
        (5, "Supercias", "Accionistas y Kárdex: registrar el bloque 5", "accionistas",
         _por_fuente(validacion, "Accionistas")),
        (6, "Supercias", "Documentos económicos: consultar y confirmar año fiscal", "documentos",
         anio + [{"ok": documentos_economicos_consultados}]),
        (7, "Supercias", "Estado de Situación Financiera: casilleros 1, 2 y 3", "indicadores",
         _casilleros(casilleros_registrados, _CASILLEROS_ESF)),
        (8, "Supercias", "Estado de Resultados Integral: casilleros 401, 403, 501, 502 y 707", "indicadores",
         _casilleros(casilleros_registrados, _CASILLEROS_ERI)),
    ]
    resultado = []
    for numero, fuente, accion, tab, requisitos in pasos:
        estado, detalle = _estado(requisitos)
        resultado.append({"numero": numero, "fuente": fuente, "accion": accion, "tab": tab,
                          "estado": estado, "etiqueta": ESTADOS[estado], "detalle": detalle})

    # Paso 9: el sistema ejecuta las validaciones. Queda pendiente mientras
    # falten datos para algún cruce; pide revisión si hay alertas, cruces que
    # no coinciden o una alerta crítica sin tratamiento.
    cruces = validacion.get("cruces", [])
    alertas = validacion.get("alertas", [])
    sin_datos = sum(1 for c in cruces if c["estado"] == PENDIENTE)
    no_coinciden = sum(1 for c in cruces if c["estado"] == NO_COINCIDE)
    criticas_sin_tratar = sum(1 for a in alertas if a["nivel"] == NIVEL_CRITICA and not a.get("tratamiento"))
    if alertas or no_coinciden:
        estado = CON_ALERTAS
    elif sin_datos:
        estado = PENDIENTE_PASO if sin_datos == len(cruces) else EN_AVANCE
    else:
        estado = COMPLETO
    partes = [f"{len(cruces) - sin_datos} de {len(cruces)} validaciones con datos"]
    if alertas:
        partes.append(f"{len(alertas)} alerta(s)")
    if no_coinciden:
        partes.append(f"{no_coinciden} no coincide(n)")
    if criticas_sin_tratar:
        partes.append(f"{criticas_sin_tratar} crítica(s) sin tratamiento")
    resultado.append({
        "numero": 9, "fuente": "Sistema", "accion": "Ejecutar validaciones cruzadas y revisar alertas",
        "tab": "resumen", "estado": estado, "etiqueta": ESTADOS[estado], "detalle": " · ".join(partes),
    })
    return resultado


def casilleros_con_valor(snapshot: Any) -> dict[str, bool]:
    """Qué casilleros obligatorios tienen valor en el ejercicio de la auditoría.
    Las cifras sin año fiscal no cuentan: el requisito exige el año."""
    if not row_get(snapshot, "anio_fiscal", None):
        return {c: False for _campo, c in CASILLEROS_OBLIGATORIOS}
    return {c: row_get(snapshot, campo, None) is not None for campo, c in CASILLEROS_OBLIGATORIOS}
