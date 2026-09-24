"""
services/validaciones.py — Requisitos, validaciones cruzadas y alertas del
levantamiento de información.

Única fuente de decisión: la interfaz, el servidor (antes de generar el
resumen), el resumen y la ficha final consumen el resultado de
evaluar_levantamiento(). No consulta bases de datos.

Convenciones del requisito:
  - Campo "Sí"       -> requisito: si falta, bloquea el resumen.
  - Campo "Opcional" -> recomendación: se muestra, no bloquea.
  - "Alerta"         -> se señala al equipo; no bloquea. Una alerta crítica
                        exige que el auditor registre su tratamiento.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from services.financial import formato_moneda
from services.normalizacion import clasificar_situacion_legal, normalizar_texto
from services.rowutil import row_get

# Diferencia admitida entre Activo y Pasivo + Patrimonio por redondeo.
TOLERANCIA_BALANCE = 1.00

NIVEL_CRITICA = "critica"
NIVEL_ALTA = "alta"
NIVEL_MEDIA = "media"
NIVEL_INFO = "informativa"

COINCIDE = "coincide"
NO_COINCIDE = "no_coincide"
PENDIENTE = "pendiente"
REVISAR = "revisar"

_FUENTE_SRI = "SRI"
_FUENTE_SUPERCIAS = "Supercias"
_FUENTE_UBICACION = "Ubicación"
_FUENTE_ADMINS = "Administradores"
_FUENTE_ACCIONISTAS = "Accionistas"
_FUENTE_FINANCIERO = "Financiero"
_FUENTE_SISTEMA = "Validaciones"

CASILLEROS_OBLIGATORIOS = (
    ("activo_total", "1"), ("pasivo_total", "2"), ("patrimonio_neto", "3"),
    ("ingresos_401", "401"), ("otros_ingresos_403", "403"),
    ("costo_ventas_501", "501"), ("gastos_502", "502"), ("utilidad_neta_707", "707"),
)


# ---------------------------------------------------------------------------
# Utilidades de comparación
# ---------------------------------------------------------------------------

def _v(row: Any, key: str) -> str:
    value = row_get(row, key, "")
    text = "" if value is None else str(value).strip()
    return "" if text in {"-", "—"} else text


def _num(row: Any, key: str) -> float | None:
    value = row_get(row, key, None)
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


_EQUIVALENCIAS_SOCIETARIAS = (
    ("COMPANIA LIMITADA", "CIA LTDA"),
    ("SOCIEDAD POR ACCIONES SIMPLIFICADA", "SAS"),
    ("SOCIEDAD ANONIMA", "SA"),
    ("COMPANIA ANONIMA", "CA"),
)


def normalizar_razon_social(nombre: str | None) -> str:
    """Forma comparable de una razón social: sin tildes, mayúsculas, sin
    puntuación, con siglas unidas ("S.A." y "SA") y formas societarias
    equivalentes ("COMPAÑÍA LIMITADA" y "CIA. LTDA.")."""
    tokens = re.sub(r"[^\w\s]", " ", normalizar_texto(nombre)).split()
    unidos: list[str] = []
    sigla = ""
    for token in tokens:
        # Letras sueltas consecutivas forman una sigla: "S A S" -> "SAS".
        if len(token) == 1:
            sigla += token
            continue
        if sigla:
            unidos.append(sigla)
            sigla = ""
        unidos.append(token)
    if sigla:
        unidos.append(sigla)
    text = " ".join(unidos)
    for largo, corto in _EQUIVALENCIAS_SOCIETARIAS:
        text = re.sub(rf"\b{largo}\b", corto, text)
    return text


def _nombre_persona(nombre: str | None) -> tuple[str, ...]:
    """Tokens ordenados de un nombre: el SRI y Supercias pueden escribir
    apellidos y nombres en distinto orden."""
    return tuple(sorted(re.sub(r"[^\w\s]", " ", normalizar_texto(nombre)).split()))


def _fecha(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]) else None
    except ValueError:
        return None


def _ciiu(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalizar_texto(value))


def _cargo(row: Any, cargo: str) -> bool:
    """El cargo contiene el nombre buscado como palabras completas:
    "PRESIDENTE EJECUTIVO" es presidente; "VICEPRESIDENTE" no."""
    return re.search(rf"\b{cargo}\b", normalizar_texto(row_get(row, "cargo", ""))) is not None


# ---------------------------------------------------------------------------
# Requisitos y recomendaciones
# ---------------------------------------------------------------------------

def _item(label: str, fuente: str, tab: str) -> dict[str, str]:
    return {"label": label, "source": fuente, "tab": tab}


def _requisitos(audit, profile, location, admins, shareholders, snapshot) -> tuple[list, list, list]:
    requisitos: list[tuple[dict, bool]] = []
    recomendaciones: list[dict] = []
    recomendaciones_todas: list[tuple[dict, bool]] = []

    def req(label: str, fuente: str, tab: str, ok: bool) -> None:
        requisitos.append((_item(label, fuente, tab), ok))

    def rec(label: str, fuente: str, tab: str, ok: bool) -> None:
        recomendaciones_todas.append((_item(label, fuente, tab), ok))
        if not ok:
            recomendaciones.append(_item(label, fuente, tab))

    # Bloque 1 — SRI
    ruc = _v(audit, "ruc")
    req("RUC de 13 dígitos terminado en 001", _FUENTE_SRI, "sri",
        bool(re.fullmatch(r"\d{10}001", ruc)))
    req("Razón social (SRI)", _FUENTE_SRI, "sri", bool(_v(profile, "razon_social_sri")))
    req("Estado del RUC", _FUENTE_SRI, "sri", bool(_v(profile, "estado_contribuyente")))
    req("Tipo de contribuyente", _FUENTE_SRI, "sri", bool(_v(profile, "tipo_contribuyente")))
    req("Régimen", _FUENTE_SRI, "sri", bool(_v(profile, "regimen")))
    req("Agente de retención", _FUENTE_SRI, "sri", bool(_v(profile, "agente_retencion")))
    req("Fecha de inicio de actividades (AAAA-MM-DD)", _FUENTE_SRI, "sri",
        _fecha(_v(profile, "fecha_inicio_actividades")) is not None)
    rec("Obligado a llevar contabilidad", _FUENTE_SRI, "sri", bool(_v(profile, "obligado_contabilidad")))
    rec("Contribuyente especial", _FUENTE_SRI, "sri", bool(_v(profile, "contribuyente_especial")))
    rec("Contribuyente fantasma / transacciones inexistentes", _FUENTE_SRI, "sri",
        bool(_v(profile, "contribuyente_fantasma")) and bool(_v(profile, "transacciones_inexistentes")))
    rec("Actividad económica principal", _FUENTE_SRI, "sri", bool(_v(profile, "actividad_economica")))
    rec("Representante legal (SRI)", _FUENTE_SRI, "sri", bool(_v(profile, "representante_legal_sri")))

    # Bloque 2 — Supercias
    req("Número de expediente (numérico)", _FUENTE_SUPERCIAS, "supercias",
        _v(profile, "expediente_supercias").isdigit())
    req("Fecha de constitución (AAAA-MM-DD)", _FUENTE_SUPERCIAS, "supercias",
        _fecha(_v(profile, "fecha_constitucion")) is not None)
    req("Tipo de compañía", _FUENTE_SUPERCIAS, "supercias", bool(_v(profile, "tipo_compania")))
    req("Objeto social", _FUENTE_SUPERCIAS, "supercias", bool(_v(profile, "objeto_social")))
    rec("Situación legal", _FUENTE_SUPERCIAS, "supercias", bool(_v(profile, "situacion_legal")))
    rec("Plazo social", _FUENTE_SUPERCIAS, "supercias", bool(_v(profile, "plazo_social")))
    rec("Oficina de control", _FUENTE_SUPERCIAS, "supercias", bool(_v(profile, "oficina_control")))

    # Bloque 3 — Ubicación
    for campo, label in (("provincia", "Provincia"), ("ciudad", "Ciudad"), ("calle", "Calle principal"),
                         ("numero", "Número (se admite S/N)"), ("interseccion", "Intersección")):
        req(label, _FUENTE_UBICACION, "ubicacion", bool(_v(location, campo)))
    rec("Barrio y referencia", _FUENTE_UBICACION, "ubicacion",
        bool(_v(location, "barrio")) and bool(_v(location, "referencia")))

    # Bloque 4 — Administradores
    admins = list(admins or [])
    req("Gerente general registrado", _FUENTE_ADMINS, "admins", any(_cargo(a, "GERENTE GENERAL") for a in admins))
    req("Presidente registrado", _FUENTE_ADMINS, "admins", any(_cargo(a, "PRESIDENTE") for a in admins))
    sin_id = [_v(a, "nombre") for a in admins if not _v(a, "identificacion")]
    req("Identificación de cada administrador" + (f" (falta: {', '.join(sin_id)})" if sin_id else ""),
        _FUENTE_ADMINS, "admins", bool(admins) and not sin_id)
    rec("Nacionalidad de los administradores", _FUENTE_ADMINS, "admins",
        all(_v(a, "nacionalidad") for a in admins))

    # Bloque 5 — Accionistas
    shareholders = list(shareholders or [])
    req("Al menos un accionista registrado", _FUENTE_ACCIONISTAS, "accionistas", bool(shareholders))
    sin_id = [_v(s, "nombre") for s in shareholders if not _v(s, "identificacion")]
    if shareholders:
        req("Identificación de cada accionista" + (f" (falta: {', '.join(sin_id)})" if sin_id else ""),
            _FUENTE_ACCIONISTAS, "accionistas", not sin_id)
    rec("Porcentaje o capital de participación", _FUENTE_ACCIONISTAS, "accionistas",
        all(_num(s, "participacion_porcentaje") is not None or _num(s, "capital") is not None for s in shareholders))
    rec("Beneficiario final", _FUENTE_ACCIONISTAS, "accionistas",
        all(_v(s, "beneficiario_final") for s in shareholders))

    # Bloque 6 — Financiero
    anio = row_get(snapshot, "anio_fiscal", None)
    req("Año fiscal de los estados financieros", _FUENTE_FINANCIERO, "indicadores", bool(anio))
    if anio:
        faltan = [c for campo, c in CASILLEROS_OBLIGATORIOS if _num(snapshot, campo) is None]
        req(f"Casilleros del ejercicio {anio}" + (f" (falta: {', '.join(faltan)})" if faltan else ""),
            _FUENTE_FINANCIERO, "indicadores", not faltan)
        rec("Utilidad antes de participación e impuestos", _FUENTE_FINANCIERO, "indicadores",
            _num(snapshot, "utilidad_antes_part_imp") is not None)
    return requisitos, recomendaciones, recomendaciones_todas


# ---------------------------------------------------------------------------
# Validaciones cruzadas
# ---------------------------------------------------------------------------

def _cruce(codigo: str, regla: str, estado: str, detalle: str, nivel: str, tab: str) -> dict[str, str]:
    return {"codigo": codigo, "regla": regla, "estado": estado, "detalle": detalle, "nivel": nivel, "tab": tab}


def _cruces(profile, admins, snapshot) -> list[dict]:
    cruces = []

    sri, sup = _v(profile, "razon_social_sri"), _v(profile, "razon_social_supercias")
    if sri and sup:
        igual = normalizar_razon_social(sri) == normalizar_razon_social(sup)
        detalle = f"SRI: {sri} | Supercias: {sup}"
        cruces.append(_cruce("CRUCE_RAZON_SOCIAL", "Razón social SRI = Supercias",
                             COINCIDE if igual else NO_COINCIDE, detalle, NIVEL_MEDIA, "supercias"))
    else:
        cruces.append(_cruce("CRUCE_RAZON_SOCIAL", "Razón social SRI = Supercias", PENDIENTE,
                             "Falta la razón social de " + ("SRI" if not sri else "Supercias"), NIVEL_MEDIA, "sri"))

    inicio, constitucion = _fecha(_v(profile, "fecha_inicio_actividades")), _fecha(_v(profile, "fecha_constitucion"))
    if inicio and constitucion:
        dias = (inicio - constitucion).days
        detalle = f"Inicio SRI: {inicio.isoformat()} | Constitución: {constitucion.isoformat()}"
        if dias:
            detalle += f" | Diferencia: {abs(dias)} día(s)"
        cruces.append(_cruce("CRUCE_FECHAS", "Fecha de inicio de actividades = fecha de constitución",
                             COINCIDE if dias == 0 else NO_COINCIDE, detalle, NIVEL_MEDIA, "supercias"))
    else:
        cruces.append(_cruce("CRUCE_FECHAS", "Fecha de inicio de actividades = fecha de constitución", PENDIENTE,
                             "Falta una de las fechas o no tiene el formato AAAA-MM-DD", NIVEL_MEDIA, "sri"))

    representante = _v(profile, "representante_legal_sri")
    gerentes = [a for a in admins or [] if _cargo(a, "GERENTE GENERAL")]
    regla = "Representante legal (SRI) = gerente general (Supercias)"
    if representante and gerentes:
        igual = any(_nombre_persona(representante) == _nombre_persona(_v(g, "nombre")) for g in gerentes)
        detalle = f"SRI: {representante} | Gerente general: {', '.join(_v(g, 'nombre') for g in gerentes)}"
        cruces.append(_cruce("CRUCE_REPRESENTANTE", regla, COINCIDE if igual else NO_COINCIDE,
                             detalle, NIVEL_ALTA, "admins"))
    else:
        falta = "el representante legal del SRI" if not representante else "el gerente general"
        cruces.append(_cruce("CRUCE_REPRESENTANTE", regla, PENDIENTE, f"Falta {falta}", NIVEL_ALTA,
                             "sri" if not representante else "admins"))

    activo, pasivo, patrimonio = (_num(snapshot, k) for k in ("activo_total", "pasivo_total", "patrimonio_neto"))
    regla = "Activo = Pasivo + Patrimonio"
    if None not in (activo, pasivo, patrimonio):
        diferencia = activo - (pasivo + patrimonio)
        detalle = (f"Activo: {formato_moneda(activo)} | Pasivo + Patrimonio: {formato_moneda(pasivo + patrimonio)} | "
                   f"Diferencia: {formato_moneda(diferencia)}")
        cruces.append(_cruce("CRUCE_BALANCE", regla,
                             COINCIDE if abs(diferencia) <= TOLERANCIA_BALANCE else NO_COINCIDE,
                             detalle, NIVEL_ALTA, "indicadores"))
    else:
        cruces.append(_cruce("CRUCE_BALANCE", regla, PENDIENTE, "Faltan los casilleros 1, 2 o 3",
                             NIVEL_ALTA, "indicadores"))

    ciiu_sri, ciiu_sup = _v(profile, "ciiu_sri"), _v(profile, "ciiu_nivel6")
    regla = "Código CIIU SRI = CIIU Supercias"
    if ciiu_sri and ciiu_sup:
        cruces.append(_cruce("CRUCE_CIIU", regla, COINCIDE if _ciiu(ciiu_sri) == _ciiu(ciiu_sup) else NO_COINCIDE,
                             f"SRI: {ciiu_sri} | Supercias: {ciiu_sup}", NIVEL_INFO, "supercias"))
    else:
        cruces.append(_cruce("CRUCE_CIIU", regla, PENDIENTE, "Falta el CIIU de una de las fuentes",
                             NIVEL_INFO, "supercias"))

    # La actividad económica frente al objeto social no se decide
    # automáticamente: se presentan juntos para que el auditor los compare.
    actividad, objeto = _v(profile, "actividad_economica"), _v(profile, "objeto_social")
    regla = "Actividad económica principal vs objeto social"
    if actividad and objeto:
        cruces.append(_cruce("CRUCE_ACTIVIDAD_OBJETO", regla, REVISAR,
                             f"Actividad (SRI): {actividad} | Objeto social: {objeto}", NIVEL_INFO, "supercias"))
    else:
        cruces.append(_cruce("CRUCE_ACTIVIDAD_OBJETO", regla, PENDIENTE,
                             "Falta la actividad económica o el objeto social", NIVEL_INFO, "supercias"))
    return cruces


# ---------------------------------------------------------------------------
# Alertas automáticas
# ---------------------------------------------------------------------------

def _alerta(codigo: str, nivel: str, mensaje: str, tab: str) -> dict[str, str]:
    return {"codigo": codigo, "nivel": nivel, "mensaje": mensaje, "tab": tab}


def _alertas(profile, cruces: list[dict]) -> list[dict]:
    alertas = []
    estado = _v(profile, "estado_contribuyente")
    if estado and normalizar_texto(estado) != "ACTIVO":
        alertas.append(_alerta("ALERTA_RUC_NO_ACTIVO", NIVEL_ALTA,
                               f"El RUC no está activo en el SRI (estado: {estado}).", "sri"))
    marcas = [label for campo, label in (("contribuyente_fantasma", "contribuyente fantasma"),
                                         ("transacciones_inexistentes", "transacciones inexistentes"))
              if normalizar_texto(_v(profile, campo)) == "SI"]
    if marcas:
        alertas.append(_alerta("ALERTA_FANTASMA", NIVEL_CRITICA,
                               f"El SRI registra al contribuyente como {' y '.join(marcas)}.", "sri"))
    situacion = _v(profile, "situacion_legal")
    categoria = clasificar_situacion_legal(situacion)
    if situacion and categoria != "Activa":
        alertas.append(_alerta("ALERTA_SITUACION_LEGAL", NIVEL_ALTA,
                               f"La situación legal en Supercias no es activa: {categoria} ({situacion}).",
                               "supercias"))
    balance = next((c for c in cruces if c["codigo"] == "CRUCE_BALANCE"), None)
    if balance and balance["estado"] == NO_COINCIDE:
        alertas.append(_alerta("ALERTA_BALANCE", NIVEL_ALTA,
                               f"El balance no cuadra (Activo ≠ Pasivo + Patrimonio). {balance['detalle']}",
                               "indicadores"))
    return alertas


# ---------------------------------------------------------------------------
# Evaluación completa
# ---------------------------------------------------------------------------

def evaluar_levantamiento(
    audit: Any,
    profile: Any,
    location: Any,
    admins: Iterable[Any] | None,
    shareholders: Iterable[Any] | None,
    snapshot: Any,
    tratamientos: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Evalúa el expediente contra el levantamiento de información.

    tratamientos son las observaciones registradas por el auditor para
    alertas críticas (filas con "codigo" y "observacion").
    """
    requisitos, recomendaciones, recomendaciones_todas = _requisitos(
        audit, profile, location, admins, shareholders, snapshot,
    )
    cruces = _cruces(profile, admins, snapshot)
    alertas = _alertas(profile, cruces)
    por_codigo = {row_get(t, "codigo"): t for t in tratamientos or []}
    for alerta in alertas:
        tratamiento = por_codigo.get(alerta["codigo"])
        alerta["tratamiento"] = _v(tratamiento, "observacion") if tratamiento else ""

    pendientes = [item for item, ok in requisitos if not ok]
    sin_tratamiento = [
        _item(f"Tratamiento de alerta crítica: {a['mensaje']}", _FUENTE_SISTEMA, "sri")
        for a in alertas if a["nivel"] == NIVEL_CRITICA and not a["tratamiento"]
    ]
    return {
        "requisitos": [{**item, "ok": ok} for item, ok in requisitos],
        "pendientes": pendientes + sin_tratamiento,
        "recomendaciones": recomendaciones,
        "recomendaciones_todas": [{**item, "ok": ok} for item, ok in recomendaciones_todas],
        "cruces": cruces,
        "alertas": alertas,
        "requisitos_total": len(requisitos) + len(sin_tratamiento),
        "requisitos_cumplidos": len(requisitos) - len(pendientes),
    }
