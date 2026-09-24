"""
services/summary.py — Resumen preliminar del levantamiento de información general del cliente.

El resumen es sobrio, técnico y nunca inventa datos. Cuando falta información
dice explícitamente "pendiente de confirmar". No usa IA ni consulta servicios externos.

Secciones (orden del requisito de levantamiento de información):
  1. Identificación tributaria (SRI)
  2. Información societaria (Supercias)
  3. Ubicación
  4. Administradores
  5. Accionistas
  6. Información financiera
  7. Validaciones cruzadas y alertas
  8. Fuentes consultadas
  9. Hallazgos preliminares
  10. Pendientes de validación
  11. Recomendación preliminar

Los bloques 1 a 6 cierran con su fuente y fecha de consulta (trazabilidad).
"""
from __future__ import annotations

import re
import sqlite3

from services.normalizacion import clasificar_situacion_legal, clasificar_tipo_compania, con_valor_oficial
from services.financial import filas_comparativo, formato_moneda, indicators_summary_text
from services.rowutil import row_get
from services.validaciones import NO_COINCIDE, evaluar_levantamiento

_NIVELES = {"critica": "crítica", "alta": "alta", "media": "media", "informativa": "informativa"}
_ESTADOS_CRUCE = {
    "coincide": "coincide",
    "no_coincide": "NO COINCIDE",
    "pendiente": "pendiente de datos",
    "revisar": "requiere revisión del auditor",
}


# ---------------------------------------------------------------------------
# Señales automáticas
# ---------------------------------------------------------------------------

def extract_signals(text: str) -> dict[str, list[str]]:
    """Extrae RUCs, correos y teléfonos del texto pegado por el auditor."""
    text = text or ""
    rucs = sorted(set(re.findall(r"\b\d{13}\b", text)))
    emails = sorted(set(re.findall(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}", text)))
    phones = sorted(set(re.findall(r"(?:\+593\s*)?\b\d{7,10}\b", text)))
    return {"rucs": rucs[:5], "emails": emails[:5], "phones": phones[:5]}


# ---------------------------------------------------------------------------
# Evaluador de riesgos
# ---------------------------------------------------------------------------

_NEGATED_OBLIGATION_TERMS = {
    "sin pendiente", "sin deuda", "sin mora",
    "no registra pendiente", "no registra deuda", "no registra mora",
    "sin obligaciones pendientes", "al dia", "al día",
}

_RISK_OBLIGATION_TERMS = {"pendiente", "deuda", "mora", "omiso", "incumpl"}

_INACTIVE_STATES = {"inactiva", "disuelta", "liquidacion", "liquidación", "cancelada", "suspendida"}


def _sin_punto(texto: str) -> str:
    """Quita el punto final para no duplicarlo al cerrar la frase."""
    return texto.rstrip().rstrip(".")


def risk_suggestions(
    audit: sqlite3.Row,
    data: dict[str, str],
    source_count: int,
    profile: sqlite3.Row | None = None,
    indicators: dict | None = None,
    validacion: dict | None = None,
) -> list[str]:
    """Genera lista de alertas preliminares basadas en los datos capturados.

    Las alertas del levantamiento (RUC no activo, contribuyente fantasma,
    situación legal, balance) y los cruces que no coinciden provienen de
    services/validaciones.py (validacion); aquí solo se redactan.
    """
    risks: list[str] = []
    legal_status = (data.get("legal_status") or "").lower()
    obligations = (data.get("tax_obligations") or "").lower()
    pasted = data.get("pasted_text") or ""

    for alerta in (validacion or {}).get("alertas", []):
        texto = f"[Alerta {_NIVELES[alerta['nivel']]}] {alerta['mensaje']}"
        if alerta.get("tratamiento"):
            texto += f" Tratamiento del auditor: {alerta['tratamiento']}"
        risks.append(texto)
    for cruce in (validacion or {}).get("cruces", []):
        if cruce["estado"] == NO_COINCIDE:
            risks.append(f"[Validación cruzada] {cruce['regla']}: no coincide. {_sin_punto(cruce['detalle'])}.")

    negated = any(term in obligations for term in _NEGATED_OBLIGATION_TERMS)

    if not audit["ruc"]:
        risks.append("RUC no registrado en la ficha inicial.")

    if any(term in legal_status for term in _INACTIVE_STATES):
        risks.append("Estado societario sugiere empresa inactiva, disuelta o en liquidación: "
                     "requiere verificación antes de continuar la auditoría.")

    if not negated and any(term in obligations for term in _RISK_OBLIGATION_TERMS):
        risks.append("Posibles obligaciones tributarias pendientes según información registrada.")

    if source_count < 2:
        risks.append(
            "Pocas fuentes registradas. Se recomienda validar con al menos Supercias, SRI y SERCOP."
        )

    if len(pasted.strip()) < 80:
        risks.append(
            "Evidencia textual escasa. Se recomienda pegar texto de las fuentes consultadas "
            "para respaldar el resumen."
        )

    # Riesgos financieros
    if indicators:
        for alerta in indicators.get("alertas", []):
            if alerta["tipo"] in {"alto", "medio"}:
                risks.append(f"[Financiero] {alerta['mensaje']}")

    return risks


# ---------------------------------------------------------------------------
# Generador de resumen estructurado
# ---------------------------------------------------------------------------

_SEP = "─" * 60

class _CamposTolerantes(dict):
    """Fila del expediente en la que un campo ausente vale None."""

    def __missing__(self, key: str) -> None:
        return None


def _identificacion(row) -> str:
    identificacion = str(row_get(row, "identificacion") or "").strip()
    if not identificacion or identificacion in {"-", "—"}:
        return "identificación pendiente de confirmar"
    return identificacion


def _val(v: str | None, fallback: str = "Pendiente de confirmar.") -> str:
    s = (v or "").strip() if isinstance(v, str) or v is None else str(v)
    return s if s else fallback


def _line(label: str, value) -> str:
    return f"  {label:<28}: {_val(value)}"


def _fuentes_bloque(provenance: list | None, bloque: str, anio_fiscal: int | None = None) -> str:
    """Fuentes del bloque con su última fecha de consulta, en orden de aparición."""
    ultimas: dict[str, str] = {}
    for row in provenance or []:
        if row_get(row, "bloque") != bloque:
            continue
        campo = str(row_get(row, "campo") or "")
        if bloque == "financiero" and anio_fiscal is not None and campo != "anio_fiscal":
            if not campo.startswith(f"{anio_fiscal}."):
                continue
        fuente = str(row_get(row, "fuente") or "").strip()
        fecha = str(row_get(row, "fecha_consulta") or "").strip()
        if fuente and fecha >= ultimas.get(fuente, ""):
            ultimas[fuente] = fecha
    if not ultimas:
        return "  Fuente: sin trazabilidad registrada."
    return "  Fuente: " + "; ".join(f"{f} (consulta {d})" for f, d in ultimas.items())


def generate_summary(
    audit: sqlite3.Row,
    data: dict[str, str],
    source_count: int,
    profile: sqlite3.Row | None = None,
    location: sqlite3.Row | None = None,
    admins: list[sqlite3.Row] | None = None,
    shareholders: list[sqlite3.Row] | None = None,
    snapshot: sqlite3.Row | None = None,
    indicators: dict | None = None,
    source_checks: list[sqlite3.Row] | None = None,
    sources: list[sqlite3.Row] | None = None,
    alert_treatments: list[sqlite3.Row] | None = None,
    provenance: list[sqlite3.Row] | None = None,
) -> str:
    """
    Genera el resumen en el orden del levantamiento de información:
    bloques 1 a 6, validaciones cruzadas y alertas, fuentes, hallazgos,
    pendientes y recomendación. Cada bloque indica su fuente y fecha de
    consulta (trazabilidad por dato).
    Nunca inventa datos: usa 'Pendiente de confirmar.' cuando falta información.
    Compatible con la firma anterior (profile=None, etc. son opcionales).
    """
    signals = extract_signals(data.get("pasted_text") or "")
    # Un campo ausente se lee como vacío ("Pendiente de confirmar"), no como
    # error: el resumen se genera con lo que exista en el expediente.
    profile = _CamposTolerantes(dict(profile)) if profile is not None else None
    location = _CamposTolerantes(dict(location)) if location is not None else None
    validacion = evaluar_levantamiento(
        audit, profile, location, admins, shareholders, snapshot, alert_treatments,
    )
    risks = risk_suggestions(audit, data, source_count, profile, indicators, validacion)
    pv = profile or _CamposTolerantes()
    lv = location or _CamposTolerantes()

    company = audit["company_name"]
    ruc = audit["ruc"] or "pendiente de confirmar"
    period = audit["period"]
    anio_fiscal = row_get(snapshot, "anio_fiscal", None)

    # ── 1. Identificación tributaria (SRI) ────────────────────────────────
    sec1 = [
        _line("RUC", ruc),
        _line("Razón social", pv["razon_social_sri"]),
        _line("Estado del RUC", pv["estado_contribuyente"]),
        _line("Tipo de contribuyente", pv["tipo_contribuyente"]),
        _line("Régimen", pv["regimen"]),
        _line("Agente de retención", pv["agente_retencion"]),
        _line("Inicio de actividades", pv["fecha_inicio_actividades"]),
        _line("Obligado a llevar contab.", pv["obligado_contabilidad"]),
        _line("Contribuyente especial", pv["contribuyente_especial"]),
        _line("Contribuyente fantasma", pv["contribuyente_fantasma"]),
        _line("Transacciones inexistentes", pv["transacciones_inexistentes"]),
        _line("Actividad económica", pv["actividad_economica"]),
        _line("Código CIIU", pv["ciiu_sri"]),
        _line("Representante legal (SRI)", pv["representante_legal_sri"]),
    ]
    if not profile and data.get("sri_info"):
        sec1.append(_line("Información SRI registrada", data.get("sri_info")))
    sec1.append(_fuentes_bloque(provenance, "sri"))

    # ── 2. Información societaria (Supercias) ─────────────────────────────
    tipo = con_valor_oficial(clasificar_tipo_compania(pv["tipo_compania"]), pv["tipo_compania"])
    situacion = con_valor_oficial(clasificar_situacion_legal(pv["situacion_legal"]), pv["situacion_legal"])
    sec2 = [
        _line("Razón social (Supercias)", pv["razon_social_supercias"]),
        _line("Número de expediente", pv["expediente_supercias"]),
        _line("Fecha de constitución", pv["fecha_constitucion"]),
        _line("Tipo de compañía", tipo),
        _line("Situación legal", situacion or data.get("legal_status")),
        _line("Plazo social", pv["plazo_social"]),
        _line("Oficina de control", pv["oficina_control"]),
        _line("Objeto social", pv["objeto_social"]),
    ]
    if not profile and data.get("supercias_info"):
        sec2.append(_line("Información Supercias", data.get("supercias_info")))
    sec2.append(_fuentes_bloque(provenance, "supercias"))

    # ── 3. Ubicación ──────────────────────────────────────────────────────
    sec3 = [
        _line("Provincia", lv["provincia"]),
        _line("Ciudad", lv["ciudad"]),
        _line("Calle principal", lv["calle"]),
        _line("Número", lv["numero"]),
        _line("Intersección", lv["interseccion"]),
        _line("Barrio", lv["barrio"]),
        _line("Referencia", lv["referencia"]),
    ]
    if not location and data.get("address"):
        sec3.append(_line("Dirección registrada", data.get("address")))
    sec3.append(_fuentes_bloque(provenance, "ubicacion"))

    # ── 4. Administradores ────────────────────────────────────────────────
    sec4 = [
        f"  · {a['cargo']}: {a['nombre']} — {_identificacion(a)}"
        + (f" — {row_get(a, 'nacionalidad')}" if _val(row_get(a, "nacionalidad"), "") else "")
        for a in admins or []
    ] or ["  Pendiente de confirmar."]
    sec4.append(_fuentes_bloque(provenance, "administradores"))

    # ── 5. Accionistas ────────────────────────────────────────────────────
    sec5 = []
    for s in shareholders or []:
        detalle = [_identificacion(s)]
        porcentaje = row_get(s, "participacion_porcentaje", None)
        capital = row_get(s, "capital", None)
        if porcentaje is not None:
            detalle.append(f"{float(porcentaje):g} %")
        if capital is not None:
            detalle.append(f"capital {formato_moneda(float(capital))}")
        if row_get(s, "beneficiario_final"):
            detalle.append(f"beneficiario final: {row_get(s, 'beneficiario_final')}")
        sec5.append(f"  · {s['nombre']} — {'; '.join(detalle)}")
    sec5 = sec5 or ["  Pendiente de confirmar."]
    sec5.append(_fuentes_bloque(provenance, "accionistas"))

    # ── 6. Información financiera ─────────────────────────────────────────
    if anio_fiscal:
        anio_line = _line("Año fiscal", f"{anio_fiscal} (EEFF al {row_get(snapshot, 'fecha_corte')})")
    elif snapshot:
        anio_line = _line("Año fiscal", "Pendiente de confirmar (cifras registradas sin año fiscal).")
    else:
        anio_line = _line("Año fiscal", None)
    if indicators and indicators.get("tiene_datos"):
        casilleros = [
            f"  {etiqueta}: {'Pendiente de confirmar.' if valor is None else formato_moneda(valor)}"
            for etiqueta, valor, _calculado in filas_comparativo(snapshot)
        ]
        indicadores = [
            line for line in indicators_summary_text(indicators).splitlines()
            if any(k in line for k in ("Razón endeudamiento", "Margen neto", "Patrimonio / Activo"))
        ]
        sec6 = [anio_line, "", *casilleros, "", "  Indicadores calculados:", *indicadores]
    else:
        sec6 = [anio_line, "  Sin datos financieros registrados. Pendiente de confirmar."]
    sec6.append(_fuentes_bloque(provenance, "financiero", anio_fiscal))

    # ── 7. Validaciones cruzadas y alertas ────────────────────────────────
    sec7 = ["  Validaciones cruzadas:"] + [
        f"    · {c['regla']}: {_ESTADOS_CRUCE[c['estado']]}. {_sin_punto(c['detalle'])}."
        for c in validacion["cruces"]
    ]
    sec7.append("")
    sec7 += [f"  ⚠ {r}" for r in risks] or [
        "  Sin alertas preliminares automáticas con la información registrada."
    ]

    # ── 8. Fuentes consultadas ────────────────────────────────────────────
    sec8 = [f"  Fuentes registradas en la herramienta: {source_count}."]
    for sc in source_checks or []:
        estado = "✓ Consultada" if sc["estado"] == "consultada" else "□ Pendiente"
        sec8.append(f"  · {sc['fuente']}: {estado}")
        if sc["observacion"]:
            sec8.append(f"    Nota: {sc['observacion'][:100]}")
    if sources:
        sec8.append("  Evidencia registrada:")
        for src in sources[:6]:
            note = (src["notes"] or "").replace("\n", " ")
            line = f"    · {src['source_type'] or 'Fuente'}: {src['title'] or 'Sin titulo'}"
            sec8.append(line + (f" - {note[:120]}" if note else ""))
    if data.get("sercop_info") or data.get("public_contracting"):
        sec8.append(_line("Contratación pública", data.get("public_contracting") or data.get("sercop_info")))
    if data.get("tax_obligations"):
        sec8.append(_line("Obligaciones tributarias", data.get("tax_obligations")))

    # ── 9. Hallazgos preliminares ─────────────────────────────────────────
    sec9: list[str] = []
    if signals["rucs"]:
        sec9.append("  · RUC(s) detectados en texto: " + ", ".join(signals["rucs"]))
    if signals["emails"]:
        sec9.append("  · Correos detectados: " + ", ".join(signals["emails"]))
    if signals["phones"]:
        sec9.append("  · Teléfonos detectados: " + ", ".join(signals["phones"]))
    if data.get("observations"):
        sec9.append(f"  · Observaciones del auditor: {data['observations']}")
    if data.get("risk_flags"):
        sec9.append(f"  · Riesgos ingresados manualmente: {data['risk_flags']}")
    sec9 = sec9 or ["  Sin hallazgos adicionales registrados."]

    # ── 10. Pendientes de validación ──────────────────────────────────────
    pendientes = [p["label"] for p in validacion["pendientes"]]
    if snapshot and not anio_fiscal:
        pendientes.append("Confirmar el año fiscal de las cifras financieras registradas.")
    recomendados = [r["label"] for r in validacion["recomendaciones"]]
    sec10 = [f"  □ Obligatorio: {p}" for p in pendientes] + [f"  ○ Recomendado: {r}" for r in recomendados]
    sec10 = sec10 or ["  Todos los campos del levantamiento fueron completados."]

    # ── 11. Recomendación preliminar ──────────────────────────────────────
    if not risks and not pendientes:
        recomendacion = (
            "  El levantamiento de información está completo y no presenta alertas automáticas. "
            "Se recomienda proceder a la etapa de planificación de auditoría."
        )
    elif risks:
        recomendacion = (
            f"  Se identificaron {len(risks)} alerta(s) o riesgo(s) preliminar(es) y "
            f"{len(pendientes)} dato(s) obligatorio(s) pendiente(s). Se recomienda revisarlos "
            "antes de cerrar el análisis inicial."
        )
    else:
        recomendacion = (
            f"  Hay {len(pendientes)} dato(s) obligatorio(s) pendiente(s). "
            "Se recomienda completar el levantamiento antes de iniciar la auditoría formal."
        )

    disclaimer = (
        "⚠ AVISO: Este resumen es PRELIMINAR. Fue generado con información registrada "
        "en Atlas. Debe validarse contra fuentes oficiales (SRI, Supercias, SERCOP) "
        "antes de utilizarse como soporte de auditoría formal."
    )
    parts = [
        "ATLAS — LEVANTAMIENTO DE INFORMACIÓN GENERAL DEL CLIENTE (RESUMEN PRELIMINAR)",
        f"Empresa: {company} | RUC: {ruc} | Período: {period} | "
        f"Año fiscal EEFF: {anio_fiscal or 'pendiente de confirmar'}",
        _SEP,
    ]
    sections = [
        ("1. IDENTIFICACIÓN TRIBUTARIA (SRI)", sec1),
        ("2. INFORMACIÓN SOCIETARIA (SUPERCIAS)", sec2),
        ("3. UBICACIÓN", sec3),
        ("4. ADMINISTRADORES", sec4),
        ("5. ACCIONISTAS", sec5),
        ("6. INFORMACIÓN FINANCIERA", sec6),
        ("7. VALIDACIONES CRUZADAS Y ALERTAS", sec7),
        ("8. FUENTES CONSULTADAS", sec8),
        ("9. HALLAZGOS PRELIMINARES", sec9),
        ("10. PENDIENTES DE VALIDACIÓN", sec10),
        ("11. RECOMENDACIÓN PRELIMINAR", [recomendacion]),
    ]
    for title, lines in sections:
        parts.append(f"\n{title}\n" + "\n".join(lines))
        parts.append(_SEP)

    parts.append(f"\n{disclaimer}")
    return "\n".join(parts)
