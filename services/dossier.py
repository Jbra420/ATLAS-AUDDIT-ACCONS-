"""
services/dossier.py - Ficha final de resultados para Atlas.

Consolida la informacion ya capturada en el expediente. No consulta fuentes
externas ni inventa datos; solo organiza avances, evidencias, riesgos y
pendientes para lectura rapida y exportacion.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Union

from services.rowutil import row_get as _get


RowLike = Union[sqlite3.Row, dict[str, Any], None]


def _present(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"-", "--", "—", "N/A", "n/a", "Pendiente de confirmar"})


def _clean(value: Any, fallback: str = "Pendiente de confirmar") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _short(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _money(value: Any) -> str:
    if value is None or value == "":
        return "Pendiente de confirmar"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _manual_risks(research: RowLike) -> list[str]:
    raw = str(_get(research, "risk_flags", "") or "")
    items = [line.strip(" -\t") for line in raw.splitlines() if line.strip(" -\t")]
    return items


def _pending_from_source_map(source_map: dict[str, Any]) -> list[str]:
    pending: list[str] = []
    for card in source_map.get("cards", []):
        title = card.get("title", "Fuente")
        for missing in card.get("missing", []):
            item = f"{title}: {missing}"
            if item not in pending:
                pending.append(item)
    return pending


def _source_rows(sources: list[RowLike]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in sources or []:
        rows.append({
            "type": _clean(_get(source, "source_type"), "Otra fuente"),
            "title": _clean(_get(source, "title"), "Fuente sin titulo"),
            "url": _clean(_get(source, "url"), "Sin URL registrada"),
            "notes": _short(_get(source, "notes"), 180) or "Sin notas registradas",
            "created_at": _clean(_get(source, "created_at"), "Sin fecha"),
        })
    return rows


def _document_rows(docs: list[RowLike]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for doc in docs or []:
        rows.append({
            "name": _clean(_get(doc, "nombre"), "Documento sin nombre"),
            "status": _clean(_get(doc, "estado"), "pendiente"),
        })
    return rows


def build_dossier_model(
    audit: RowLike,
    research: RowLike,
    profile: RowLike,
    location: RowLike,
    admins: list[RowLike],
    shareholders: list[RowLike],
    docs: list[RowLike],
    snapshot: RowLike,
    indicators: dict[str, Any],
    source_map: dict[str, Any],
    sources: list[RowLike],
) -> dict[str, Any]:
    """Construye un modelo compacto para vista y exportacion de la ficha final."""
    totals = source_map.get("totals", {})
    reviewed_docs = sum(1 for doc in docs or [] if _get(doc, "estado") == "revisado")
    total_docs = len(docs or [])
    source_rows = _source_rows(sources)
    financial_alerts = [
        str(alert.get("mensaje", "")).strip()
        for alert in indicators.get("alertas", [])
        if alert.get("tipo") in {"alto", "medio"} and str(alert.get("mensaje", "")).strip()
    ]
    risk_items = _manual_risks(research) + financial_alerts
    if not totals.get("ready_for_summary"):
        risk_items.append("La base de fuentes aun requiere soporte antes de una conclusion definitiva.")
    pending_items = _pending_from_source_map(source_map)
    if not source_rows:
        pending_items.append("Registrar evidencia verificable de las fuentes consultadas.")
    if not indicators.get("tiene_datos"):
        pending_items.append("Completar datos financieros desde documentos economicos.")

    company_name = _clean(_get(profile, "razon_social") or _get(audit, "company_name"))
    ruc = _clean(_get(profile, "ruc") or _get(audit, "ruc"))
    city = _clean(_get(location, "ciudad") or _get(audit, "city"))
    status = "Lista para resumen preliminar" if totals.get("ready_for_summary") else "En construccion"

    return {
        "title": "Ficha final de resultados",
        "status": status,
        "metrics": {
            "source_percent": int(totals.get("percent", 0) or 0),
            "completed_sources": int(totals.get("complete_cards", 0) or 0),
            "partial_sources": int(totals.get("partial_cards", 0) or 0),
            "pending_sources": int(totals.get("pending_cards", 0) or 0),
            "evidence_count": len(source_rows),
            "reviewed_docs": reviewed_docs,
            "total_docs": total_docs,
            "risk_count": len(risk_items),
            "pending_count": len(pending_items),
        },
        "identity": [
            {"label": "Razon social", "value": company_name},
            {"label": "RUC", "value": ruc},
            {"label": "Periodo auditado", "value": _clean(_get(audit, "period"))},
            {"label": "Ciudad", "value": city},
            {"label": "Estado contribuyente", "value": _clean(_get(profile, "estado_contribuyente"))},
            {"label": "Situacion legal", "value": _clean(_get(profile, "situacion_legal") or _get(research, "legal_status"))},
            {"label": "Actividad economica", "value": _clean(_get(profile, "actividad_economica") or _get(research, "economic_activity"))},
            {"label": "Representante legal", "value": _clean(_get(profile, "representante_legal") or _get(research, "representative"))},
        ],
        "source_status": [
            {
                "title": card.get("title", "Fuente"),
                "status": card.get("status_label", "Pendiente"),
                "completed": f"{card.get('completed', 0)}/{card.get('total', 0)}",
                "missing": ", ".join(card.get("missing", [])) or "Sin pendientes criticos",
            }
            for card in source_map.get("cards", [])
        ],
        "evidence": source_rows[:10],
        "documents": _document_rows(docs)[:10],
        "financial": [
            {"label": "Activo total", "value": indicators.get("fmt_activo", _money(_get(snapshot, "activo_total")))},
            {"label": "Pasivo total", "value": indicators.get("fmt_pasivo", _money(_get(snapshot, "pasivo_total")))},
            {"label": "Patrimonio neto", "value": indicators.get("fmt_patrimonio", _money(_get(snapshot, "patrimonio_neto")))},
            {"label": "Ingresos totales", "value": indicators.get("fmt_ingresos_totales", "Pendiente de confirmar")},
            {"label": "Utilidad neta", "value": indicators.get("fmt_utilidad", _money(_get(snapshot, "utilidad_neta_707")))},
            {"label": "Endeudamiento", "value": indicators.get("fmt_endeudamiento", "Pendiente de confirmar")},
            {"label": "Margen neto", "value": indicators.get("fmt_margen_neto", "Pendiente de confirmar")},
        ],
        "people": {
            "admins": len(admins or []),
            "shareholders": len(shareholders or []),
        },
        "risks": risk_items[:8] or ["Sin alertas criticas registradas en esta etapa."],
        "pending": pending_items[:10] or ["Sin pendientes principales identificados."],
        "closing": (
            "El expediente cuenta con soporte suficiente para una lectura preliminar."
            if totals.get("ready_for_summary")
            else "El expediente debe completar los campos pendientes antes de usar la ficha como soporte definitivo."
        ),
    }


def build_dossier_text(dossier: dict[str, Any]) -> str:
    """Convierte la ficha final a texto plano descargable."""
    metrics = dossier.get("metrics", {})
    lines = [
        "ATLAS - FICHA FINAL DE RESULTADOS",
        "=" * 72,
        f"Estado: {dossier.get('status', 'En construccion')}",
        "",
        "1. IDENTIFICACION",
        "-" * 72,
    ]
    for item in dossier.get("identity", []):
        lines.append(f"{item['label']}: {item['value']}")

    lines += [
        "",
        "2. ESTADO DE FUENTES",
        "-" * 72,
        f"Avance global: {metrics.get('source_percent', 0)}%",
        f"Fuentes completas: {metrics.get('completed_sources', 0)}",
        f"Fuentes en avance: {metrics.get('partial_sources', 0)}",
        f"Fuentes pendientes: {metrics.get('pending_sources', 0)}",
    ]
    for row in dossier.get("source_status", []):
        lines.append(f"- {row['title']}: {row['status']} ({row['completed']}) | Pendiente: {row['missing']}")

    lines += ["", "3. EVIDENCIA REGISTRADA", "-" * 72]
    for row in dossier.get("evidence", []):
        lines.append(f"- [{row['type']}] {row['title']} | {row['url']} | {row['notes']}")
    if not dossier.get("evidence"):
        lines.append("Sin evidencias registradas.")

    lines += ["", "4. DOCUMENTOS ECONOMICOS", "-" * 72]
    lines.append(f"Revisados: {metrics.get('reviewed_docs', 0)}/{metrics.get('total_docs', 0)}")
    for row in dossier.get("documents", []):
        lines.append(f"- {row['name']}: {row['status']}")

    lines += ["", "5. INDICADORES FINANCIEROS", "-" * 72]
    for item in dossier.get("financial", []):
        lines.append(f"{item['label']}: {item['value']}")

    lines += ["", "6. RIESGOS Y PENDIENTES", "-" * 72, "Riesgos:"]
    for item in dossier.get("risks", []):
        lines.append(f"- {item}")
    lines.append("Pendientes:")
    for item in dossier.get("pending", []):
        lines.append(f"- {item}")

    lines += ["", "7. CIERRE PRELIMINAR", "-" * 72, dossier.get("closing", "Pendiente de confirmar.")]
    return "\n".join(lines)
