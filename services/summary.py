"""
services/summary.py — Generador de resumen estructurado en 12 secciones para Atlas Radar Empresarial.

El resumen es sobrio, técnico y nunca inventa datos. Cuando falta información
dice explícitamente "pendiente de confirmar". No usa IA ni consulta servicios externos.

Secciones:
  1. Identificación de la empresa
  2. Estado tributario (SRI)
  3. Estado societario (Supercias)
  4. Actividad económica
  5. Ubicación
  6. Administradores y accionistas
  7. Indicadores financieros
  8. Fuentes consultadas
  9. Riesgos o alertas
  10. Riesgos o alertas
  11. Pendientes de validación
  12. Recomendación preliminar
"""
from __future__ import annotations

import re
import sqlite3

from services.normalizacion import clasificar_situacion_legal, clasificar_tipo_compania, con_valor_oficial
from services.rowutil import row_get


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


def risk_suggestions(
    audit: sqlite3.Row,
    data: dict[str, str],
    source_count: int,
    profile: sqlite3.Row | None = None,
    indicators: dict | None = None,
) -> list[str]:
    """Genera lista de alertas preliminares basadas en los datos capturados."""
    risks: list[str] = []
    legal_status = (data.get("legal_status") or "").lower()
    obligations = (data.get("tax_obligations") or "").lower()
    pasted = data.get("pasted_text") or ""

    # Estado de Supercias si hay perfil
    if profile:
        sit_legal = (profile["situacion_legal"] or "").lower()
        if any(term in sit_legal for term in _INACTIVE_STATES):
            risks.append(
                f"Estado societario Supercias: '{profile['situacion_legal']}'. "
                "Requiere verificación antes de continuar la auditoría."
            )

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
# Pendientes de validación
# ---------------------------------------------------------------------------

def _collect_pendientes(
    data: dict[str, str],
    audit: sqlite3.Row,
    profile: sqlite3.Row | None = None,
    snapshot: sqlite3.Row | None = None,
) -> list[str]:
    pendientes: list[str] = []
    if not audit["ruc"]:
        pendientes.append("Confirmar RUC de la empresa con SRI.")
    if profile:
        if not profile["estado_contribuyente"]:
            pendientes.append("Verificar estado de contribuyente en SRI.")
        if not profile["situacion_legal"]:
            pendientes.append("Verificar situación legal en Supercias.")
        if not profile["representante_legal"]:
            pendientes.append("Identificar representante legal actual.")
    else:
        if not data.get("legal_status"):
            pendientes.append("Verificar estado societario en Supercias.")
        if not data.get("representative"):
            pendientes.append("Identificar representante legal actual.")
    if not data.get("tax_obligations") and not (profile and profile["estado_contribuyente"]):
        pendientes.append("Revisar obligaciones tributarias en SRI.")
    if not data.get("economic_activity") and not (profile and profile["actividad_economica"]):
        pendientes.append("Confirmar actividad económica principal (CIIU).")
    if not data.get("sercop_info") and not data.get("public_contracting"):
        pendientes.append("Verificar historial de contratación pública en SERCOP.")
    if snapshot is None:
        pendientes.append("Registrar datos financieros desde documentos económicos.")
    return pendientes


# ---------------------------------------------------------------------------
# Generador de resumen estructurado
# ---------------------------------------------------------------------------

_SEP = "─" * 60


def _identificacion(row) -> str:
    identificacion = str(row_get(row, "identificacion") or "").strip()
    if not identificacion or identificacion in {"-", "—"}:
        return "identificación pendiente de confirmar"
    return identificacion


def _val(v: str | None, fallback: str = "Pendiente de confirmar.") -> str:
    s = (v or "").strip()
    return s if s else fallback


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
) -> str:
    """
    Genera el resumen estructurado en 11 secciones.
    Nunca inventa datos: usa 'Pendiente de confirmar.' cuando falta información.
    Compatible con la firma anterior (profile=None, etc. son opcionales).
    """
    signals = extract_signals(data.get("pasted_text") or "")
    risks = risk_suggestions(audit, data, source_count, profile, indicators)
    pendientes = _collect_pendientes(data, audit, profile, snapshot)

    company = audit["company_name"]
    ruc = audit["ruc"] or "pendiente de confirmar"
    period = audit["period"]
    try:
        city = audit["city"] or ""
    except (IndexError, KeyError):
        city = ""
    try:
        activity_hint = audit["activity_hint"] or ""
    except (IndexError, KeyError):
        activity_hint = ""

    # ── Sección 1: Identificación ─────────────────────────────────────────
    sec1_lines = [
        f"  Razón social         : {company}",
        f"  RUC                  : {ruc}",
    ]
    if profile:
        sec1_lines += [
            f"  Tipo contribuyente   : {_val(profile['tipo_contribuyente'])}",
            f"  Régimen              : {_val(profile['regimen'])}",
            f"  Expediente Supercias : {_val(profile['expediente_supercias'])}",
            f"  Tipo compañía        : {_val(con_valor_oficial(clasificar_tipo_compania(profile['tipo_compania']), profile['tipo_compania']))}",
            f"  Nacionalidad         : {_val(profile['nacionalidad'])}",
        ]
    else:
        sec1_lines.append(f"  Nombre comercial     : {_val(data.get('commercial_name'))}")
    sec1_lines.append(f"  Período auditado     : {period}")
    sec1 = "\n".join(sec1_lines)

    # ── Sección 2: Estado tributario SRI ──────────────────────────────────
    if profile:
        sec2_lines = [
            f"  Razón social         : {_val(row_get(profile, 'razon_social_sri'))}",
            f"  Estado contribuyente : {_val(profile['estado_contribuyente'])}",
            f"  Tipo contribuyente   : {_val(profile['tipo_contribuyente'])}",
            f"  Régimen              : {_val(profile['regimen'])}",
            f"  Obligado contab.     : {_val(profile['obligado_contabilidad'])}",
            f"  Agente de retención  : {_val(profile['agente_retencion'])}",
            f"  Contribuyente esp.   : {_val(profile['contribuyente_especial'])}",
            f"  Contrib. fantasma    : {_val(row_get(profile, 'contribuyente_fantasma'))}",
            f"  Transacc. inexist.   : {_val(row_get(profile, 'transacciones_inexistentes'))}",
            f"  Fecha inicio act.    : {_val(profile['fecha_inicio_actividades'])}",
            f"  Última actualización : {_val(profile['fecha_actualizacion'])}",
            f"  Representante legal  : {_val(row_get(profile, 'representante_legal_sri'))}",
        ]
    else:
        sec2_lines = [
            f"  Estado tributario    : {_val(data.get('sri_info') or data.get('tax_obligations'))}",
            f"  Representante legal  : {_val(data.get('representative'))}",
        ]
    sec2 = "\n".join(sec2_lines)

    # ── Sección 3: Estado societario Supercias ────────────────────────────
    if profile:
        sec3_lines = [
            f"  Razón social         : {_val(row_get(profile, 'razon_social_supercias'))}",
            f"  Situación legal      : {_val(con_valor_oficial(clasificar_situacion_legal(profile['situacion_legal']), profile['situacion_legal']))}",
            f"  Tipo compañía        : {_val(con_valor_oficial(clasificar_tipo_compania(profile['tipo_compania']), profile['tipo_compania']))}",
            f"  Fecha constitución   : {_val(profile['fecha_constitucion'])}",
            f"  Oficina control      : {_val(profile['oficina_control'])}",
            f"  Objeto social        : {_val(row_get(profile, 'objeto_social'))}",
            f"  Expediente           : {_val(profile['expediente_supercias'])}",
            f"  Plazo social         : {_val(row_get(profile, 'plazo_social'))}",
            f"  Representante legal  : {_val(row_get(profile, 'representante_legal'))}",
        ]
    else:
        sec3_lines = [
            f"  Estado societario    : {_val(data.get('legal_status'))}",
            f"  Info Supercias       : {_val(data.get('supercias_info'))}",
        ]
    sec3 = "\n".join(sec3_lines)

    # ── Sección 4: Actividad económica ────────────────────────────────────
    act = (profile["actividad_economica"] if profile else None) or data.get("economic_activity") or activity_hint
    sec4_lines = [
        f"  Actividad económica  : {_val(act)}",
        f"  Obligaciones trib.   : {_val(data.get('tax_obligations'))}",
        f"  Contratación pública : {_val(data.get('public_contracting') or data.get('sercop_info'))}",
    ]
    sec4 = "\n".join(sec4_lines)

    # ── Sección 5: Ubicación ──────────────────────────────────────────────
    if location:
        addr_parts = [
            location["calle"], location["numero"],
            location["interseccion"], location["barrio"],
            location["ciudad"], location["provincia"],
        ]
        full_addr = ", ".join(p for p in addr_parts if p)
        sec5_lines = [
            f"  Provincia            : {_val(location['provincia'])}",
            f"  Ciudad               : {_val(location['ciudad'])}",
            f"  Dirección            : {_val(full_addr)}",
            f"  Referencia           : {_val(row_get(location, 'referencia'))}",
        ]
    else:
        sec5_lines = [
            f"  Ciudad / domicilio   : {_val(city or data.get('address'))}",
            f"  Dirección registrada : {_val(data.get('address'))}",
        ]
    sec5 = "\n".join(sec5_lines)

    # ── Sección 6: Administradores y accionistas ──────────────────────────
    sec6_lines: list[str] = []
    if admins:
        sec6_lines.append("  Administradores:")
        for a in admins:
            sec6_lines.append(
                f"    · {a['cargo']}: {a['nombre']} — {_identificacion(a)}"
            )
    else:
        sec6_lines.append("  Administradores: Pendiente de confirmar.")
    if shareholders:
        sec6_lines.append("  Accionistas:")
        for s in shareholders:
            detalle = [_identificacion(s)]
            porcentaje = row_get(s, "participacion_porcentaje", None)
            capital = row_get(s, "capital", None)
            if porcentaje is not None:
                detalle.append(f"{float(porcentaje):g} %")
            if capital is not None:
                detalle.append(f"capital ${float(capital):,.2f}")
            if row_get(s, "beneficiario_final"):
                detalle.append(f"beneficiario final: {row_get(s, 'beneficiario_final')}")
            sec6_lines.append(f"    · {s['nombre']} — {'; '.join(detalle)}")
    else:
        sec6_lines.append("  Accionistas: Pendiente de confirmar.")
    sec6 = "\n".join(sec6_lines)

    # ── Sección 7: Indicadores financieros ───────────────────────────────
    if indicators and indicators.get("tiene_datos"):
        from services.financial import indicators_summary_text
        sec7 = indicators_summary_text(indicators)
    else:
        sec7 = "  Sin datos financieros registrados. Pendiente de confirmar."

    # ── Sección 8: Fuentes consultadas ────────────────────────────────────
    fuentes_reg = f"  Fuentes registradas en la herramienta: {source_count}."
    fuente_detail = []
    if source_checks:
        for sc in source_checks:
            estado = "✓ Consultada" if sc["estado"] == "consultada" else "□ Pendiente"
            fuente_detail.append(f"  · {sc['fuente']}: {estado}")
            if sc["observacion"]:
                fuente_detail.append(f"    Nota: {sc['observacion'][:100]}")
    if sources:
        fuente_detail.append("  Evidencia registrada:")
        for src in sources[:6]:
            kind = src["source_type"] or "Fuente"
            title = src["title"] or "Sin titulo"
            note = (src["notes"] or "").replace("\n", " ")
            line = f"    · {kind}: {title}"
            if note:
                line += f" - {note[:120]}"
            fuente_detail.append(line)
    if not fuente_detail:
        sri_info = data.get("sri_info") or ""
        supercias_info = data.get("supercias_info") or ""
        sercop_info = data.get("sercop_info") or ""
        if supercias_info:
            fuente_detail.append(f"  · Supercias  : {supercias_info[:150]}")
        if sri_info:
            fuente_detail.append(f"  · SRI        : {sri_info[:150]}")
        if sercop_info:
            fuente_detail.append(f"  · SERCOP     : {sercop_info[:150]}")
        if not fuente_detail:
            fuente_detail.append("  Sin información específica por fuente registrada aún.")
    sec8 = fuentes_reg + "\n" + "\n".join(fuente_detail)

    # ── Sección 9: Hallazgos preliminares ────────────────────────────────
    hallazgos: list[str] = []
    if signals["rucs"]:
        hallazgos.append("  · RUC(s) detectados en texto: " + ", ".join(signals["rucs"]))
    if signals["emails"]:
        hallazgos.append("  · Correos detectados: " + ", ".join(signals["emails"]))
    if signals["phones"]:
        hallazgos.append("  · Teléfonos detectados: " + ", ".join(signals["phones"]))
    if data.get("observations"):
        hallazgos.append(f"  · Observaciones del auditor: {data['observations']}")
    if data.get("risk_flags"):
        hallazgos.append(f"  · Riesgos ingresados manualmente: {data['risk_flags']}")
    if not hallazgos:
        hallazgos.append("  Sin hallazgos adicionales registrados.")
    sec9 = "\n".join(hallazgos)

    # ── Sección 10: Riesgos o alertas ────────────────────────────────────
    if risks:
        sec10 = "\n".join(f"  ⚠ {r}" for r in risks)
    else:
        sec10 = "  Sin alertas preliminares automáticas con la información registrada."

    # ── Sección 11: Pendientes de validación ─────────────────────────────
    if pendientes:
        sec11 = "\n".join(f"  □ {p}" for p in pendientes)
    else:
        sec11 = "  Todos los campos principales fueron completados."

    # ── Sección 12: Recomendación ─────────────────────────────────────────
    risk_count = len(risks)
    pending_count = len(pendientes)
    if risk_count == 0 and pending_count == 0:
        recomendacion = (
            "  La investigación inicial está completa y no presenta alertas automáticas. "
            "Se recomienda proceder a la etapa de planificación de auditoría."
        )
    elif risk_count > 0:
        recomendacion = (
            f"  Se identificaron {risk_count} alerta(s) preliminar(es) y {pending_count} "
            "pendiente(s). Se recomienda revisar las alertas antes de cerrar el analisis inicial."
        )
    else:
        recomendacion = (
            f"  Hay {pending_count} campo(s) pendiente(s) de validación. "
            "Se recomienda completar la ficha antes de iniciar la auditoría formal."
        )

    # ── Ensamblar ──────────────────────────────────────────────────────────
    disclaimer = (
        "⚠ AVISO: Este resumen es PRELIMINAR. Fue generado con información registrada "
        "en Atlas. Debe validarse contra fuentes oficiales (SRI, Supercias, SERCOP) "
        "antes de utilizarse como soporte de auditoría formal."
    )

    parts = [
        "ATLAS — RESUMEN INICIAL DE INVESTIGACIÓN (RADAR EMPRESARIAL)",
        f"Empresa: {company} | Período: {period}",
        _SEP,
    ]
    sections = [
        ("1. IDENTIFICACIÓN DE LA EMPRESA", sec1),
        ("2. ESTADO TRIBUTARIO (SRI)", sec2),
        ("3. ESTADO SOCIETARIO (SUPERCIAS)", sec3),
        ("4. ACTIVIDAD ECONÓMICA", sec4),
        ("5. UBICACIÓN", sec5),
        ("6. ADMINISTRADORES Y ACCIONISTAS", sec6),
        ("7. INDICADORES FINANCIEROS", sec7),
        ("8. FUENTES CONSULTADAS", sec8),
        ("9. HALLAZGOS PRELIMINARES", sec9),
        ("10. RIESGOS O ALERTAS", sec10),
        ("11. PENDIENTES DE VALIDACIÓN", sec11),
        ("12. RECOMENDACIÓN PRELIMINAR", recomendacion),
    ]
    for title, content in sections:
        parts.append(f"\n{title}\n{content}")
        parts.append(_SEP)

    parts.append(f"\n{disclaimer}")
    return "\n".join(parts)
