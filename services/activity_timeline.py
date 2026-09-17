"""Construccion de la linea de tiempo del expediente de Atlas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from services.rowutil import row_get as _get


RowLike = Mapping[str, Any]


def _display_date(value: str) -> str:
    if not value:
        return "Fecha no disponible"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d/%m/%Y %H:%M")


def _has_research_content(research: RowLike | None) -> bool:
    fields = (
        "commercial_name",
        "economic_activity",
        "legal_status",
        "representative",
        "address",
        "tax_obligations",
        "public_contracting",
        "supercias_info",
        "sri_info",
        "sercop_info",
        "observations",
        "risk_flags",
        "pasted_text",
        "generated_summary",
    )
    return any(str(_get(research, field, "") or "").strip() for field in fields)


def build_activity_timeline(
    audit: RowLike,
    research: RowLike | None,
    profile: RowLike | None,
    location: RowLike | None,
    documents: list[RowLike],
    snapshot: RowLike | None,
    source_checks: list[RowLike],
    sources: list[RowLike],
) -> dict[str, Any]:
    """Genera eventos de trazabilidad usando las fechas ya guardadas en SQLite."""
    events: list[dict[str, str]] = []

    def add(category: str, title: str, detail: str, timestamp: str, actor: str = "") -> None:
        if not timestamp:
            return
        events.append(
            {
                "category": category,
                "title": title,
                "detail": detail,
                "timestamp": timestamp,
                "date": _display_date(timestamp),
                "actor": actor,
            }
        )

    company_name = str(_get(audit, "company_name", "Empresa") or "Empresa")
    auditor_name = str(_get(audit, "auditor_name", "Auditor asignado") or "Auditor asignado")
    add(
        "asignacion",
        "Expediente asignado",
        f"{company_name} quedo asignada a {auditor_name} para el periodo {_get(audit, 'period', 'pendiente')}.",
        str(_get(audit, "created_at", "")),
        "Jefe auditor",
    )

    profile_fields = (
        "razon_social",
        "estado_contribuyente",
        "actividad_economica",
        "representante_legal",
        "situacion_legal",
    )
    if profile and any(str(_get(profile, field, "") or "").strip() for field in profile_fields):
        add(
            "datos",
            "Ficha empresarial actualizada",
            "Se registraron o actualizaron datos de SRI y Supercias.",
            str(_get(profile, "updated_at", "")),
            auditor_name,
        )

    location_fields = ("provincia", "canton", "ciudad", "calle", "referencia")
    if location and any(str(_get(location, field, "") or "").strip() for field in location_fields):
        add(
            "datos",
            "Ubicacion empresarial actualizada",
            "La direccion y referencias de la empresa fueron registradas en el expediente.",
            str(_get(location, "updated_at", "")),
            auditor_name,
        )

    if snapshot and any(
        _get(snapshot, field, None) not in (None, "")
        for field in ("activo_total", "pasivo_total", "patrimonio_neto", "ingresos_401", "utilidad_neta_707")
    ):
        add(
            "financiero",
            "Datos financieros actualizados",
            "Se registraron cifras base para calcular los indicadores preliminares.",
            str(_get(snapshot, "updated_at", "")),
            auditor_name,
        )

    for source in sources:
        source_type = str(_get(source, "source_type", "Fuente") or "Fuente")
        title = str(_get(source, "title", "Evidencia registrada") or "Evidencia registrada")
        add(
            "fuentes",
            f"Evidencia registrada: {title}",
            f"Fuente: {source_type}.",
            str(_get(source, "created_at", "")),
            auditor_name,
        )

    for check in source_checks:
        if str(_get(check, "estado", "")) != "consultada":
            continue
        source_name = str(_get(check, "fuente", "Fuente guiada") or "Fuente guiada")
        observation = str(_get(check, "observacion", "") or "").strip()
        detail = "La fuente fue marcada como consultada."
        if observation:
            detail = f"Consulta verificada. Observacion: {observation}"
        add(
            "fuentes",
            f"Consulta verificada: {source_name}",
            detail,
            str(_get(check, "consultada_at", "")),
            auditor_name,
        )

    for document in documents:
        if str(_get(document, "estado", "")) != "revisado":
            continue
        name = str(_get(document, "nombre", "Documento economico") or "Documento economico")
        add(
            "documentos",
            f"Documento revisado: {name}",
            "El auditor confirmo la revision del documento dentro del expediente.",
            str(_get(document, "revisado_at", "")),
            auditor_name,
        )

    if _has_research_content(research):
        has_summary = bool(str(_get(research, "generated_summary", "") or "").strip())
        add(
            "resumen" if has_summary else "datos",
            "Resumen preliminar generado" if has_summary else "Notas de investigacion actualizadas",
            (
                "Atlas consolido la informacion disponible en el resumen preliminar."
                if has_summary
                else "Se actualizaron los hallazgos y observaciones del auditor."
            ),
            str(_get(research, "updated_at", "")),
            auditor_name,
        )

    events.sort(key=lambda event: (event["timestamp"], event["title"]), reverse=True)
    categories = {
        "asignacion": "Asignacion",
        "datos": "Datos",
        "fuentes": "Fuentes",
        "documentos": "Documentos",
        "financiero": "Financiero",
        "resumen": "Resumen",
    }
    counts = {key: sum(event["category"] == key for event in events) for key in categories}
    return {
        "events": events,
        "categories": categories,
        "counts": counts,
        "total": len(events),
        "latest": events[0]["date"] if events else "Sin actividad registrada",
    }
