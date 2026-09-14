"""
services/company_search.py — Estado interpretado de busqueda por fuentes.

No consulta servicios externos. Resume la informacion ya capturada en Atlas para
que el auditor vea que fuentes estan completas, parciales o pendientes.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Union


RowLike = Union[sqlite3.Row, dict[str, Any], None]

# ── Umbrales para determinar si el expediente está listo para generar resumen ──
# SUMMARY_MIN_PERCENT: porcentaje mínimo de campos clave completados (sobre el total
#   de todos los campos de todas las fuentes) para habilitar la generación de resumen.
# SUMMARY_MAX_PENDING: número máximo de tarjetas de fuente que pueden quedar en estado
#   "pendiente" sin bloquear la generación del resumen.
# Estos valores reflejan el umbral mínimo acordado con el equipo auditor para garantizar
# que el resumen cuente con suficiente respaldo de información antes de ser emitido.
SUMMARY_MIN_PERCENT: int = 60
SUMMARY_MAX_PENDING: int = 1


def _get(row: RowLike, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _present(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"—", "-", "N/A", "n/a", "Pendiente de confirmar"})


def _short(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _consulted(source_checks: list[RowLike], include: tuple[str, ...], exclude: tuple[str, ...] = ()) -> bool:
    for check in source_checks:
        name = str(_get(check, "fuente", "")).lower()
        if any(token in name for token in include) and not any(token in name for token in exclude):
            return _get(check, "estado") == "consultada"
    return False


def _row_count(rows: list[Any] | None) -> int:
    return len(rows or [])


def _reviewed_docs(docs: list[RowLike]) -> int:
    return sum(1 for doc in docs if _get(doc, "estado") == "revisado")


def _source_count(sources: list[RowLike], *tokens: str) -> int:
    total = 0
    for source in sources:
        haystack = " ".join(
            str(_get(source, key, "") or "")
            for key in ("title", "source_type", "notes", "url")
        ).lower()
        if any(token in haystack for token in tokens):
            total += 1
    return total


def _card(
    *,
    key: str,
    title: str,
    tab: str,
    fields: list[tuple[str, Any]],
    next_action: str,
) -> dict[str, Any]:
    found = [
        {"label": label, "value": _short(value)}
        for label, value in fields
        if _present(value)
    ]
    missing = [label for label, value in fields if not _present(value)]
    total = len(fields)
    completed = len(found)
    if completed == total:
        status = "complete"
        status_label = "Completa"
    elif completed:
        status = "partial"
        status_label = "En avance"
    else:
        status = "pending"
        status_label = "Pendiente"
    return {
        "key": key,
        "title": title,
        "tab": tab,
        "status": status,
        "status_label": status_label,
        "completed": completed,
        "total": total,
        "found": found[:4],
        "missing": missing[:4],
        "next_action": next_action,
    }


def build_source_map(
    audit: RowLike,
    research: RowLike,
    profile: RowLike,
    location: RowLike,
    admins: list[RowLike],
    shareholders: list[RowLike],
    docs: list[RowLike],
    snapshot: RowLike,
    source_checks: list[RowLike],
    sources: list[RowLike],
) -> dict[str, Any]:
    """Construye el mapa interpretado de fuentes para el Radar Empresarial."""
    sri_consulted = "Consultada" if _consulted(source_checks, ("sri",)) else ""
    supercias_consulted = "Consultada" if _consulted(source_checks, ("supercias",), ("documento",)) else ""
    docs_consulted = "Consultada" if _consulted(source_checks, ("documento",)) else ""
    sercop_consulted = "Consultada" if _consulted(source_checks, ("sercop",)) else ""
    web_consulted = "Consultada" if _consulted(source_checks, ("web",)) else ""

    admin_count = _row_count(admins)
    shareholder_count = _row_count(shareholders)
    reviewed_docs = _reviewed_docs(docs)
    docs_total = _row_count(docs)
    registered_sources = _row_count(sources)

    cards = [
        _card(
            key="sri",
            title="SRI",
            tab="sri",
            fields=[
                ("RUC validado", _get(audit, "ruc")),
                ("Fuente SRI consultada", sri_consulted),
                ("Estado contribuyente", _get(profile, "estado_contribuyente") or _get(research, "sri_info")),
                ("Tipo contribuyente", _get(profile, "tipo_contribuyente")),
                ("Obligado a contabilidad", _get(profile, "obligado_contabilidad")),
                ("Actividad economica", _get(profile, "actividad_economica") or _get(research, "economic_activity")),
            ],
            next_action="Confirmar estado, regimen y obligaciones tributarias.",
        ),
        _card(
            key="supercias",
            title="Supercias",
            tab="supercias",
            fields=[
                ("Fuente Supercias consultada", supercias_consulted),
                ("Situacion legal", _get(profile, "situacion_legal") or _get(research, "legal_status")),
                ("Expediente Supercias", _get(profile, "expediente_supercias")),
                ("Tipo de compania", _get(profile, "tipo_compania")),
                ("Representante legal", _get(profile, "representante_legal") or _get(research, "representative")),
                ("Administradores registrados", f"{admin_count} registro(s)" if admin_count else ""),
                ("Accionistas registrados", f"{shareholder_count} registro(s)" if shareholder_count else ""),
            ],
            next_action="Cruzar estado societario, representantes y estructura accionaria.",
        ),
        _card(
            key="sercop",
            title="SERCOP",
            tab="fuentes",
            fields=[
                ("Fuente SERCOP consultada", sercop_consulted),
                ("Resultado de contratacion publica", _get(research, "sercop_info") or _get(research, "public_contracting")),
                ("Evidencia registrada", _source_count(sources, "sercop") or ""),
            ],
            next_action="Registrar si existen contratos, inhabilitaciones o ausencia de resultados.",
        ),
        _card(
            key="documentos",
            title="Documentos economicos",
            tab="documentos",
            fields=[
                ("Fuente documentos consultada", docs_consulted),
                ("Documentos revisados", f"{reviewed_docs}/{docs_total}" if docs_total else ""),
                ("Datos financieros capturados", "Registrados" if snapshot else ""),
            ],
            next_action="Marcar documentos revisados y capturar cifras base para indicadores.",
        ),
        _card(
            key="web",
            title="Busqueda web general",
            tab="fuentes",
            fields=[
                ("Fuente web consultada", web_consulted),
                ("Fuentes registradas", f"{registered_sources} fuente(s)" if registered_sources else ""),
                ("Texto de respaldo pegado", _get(research, "pasted_text")),
                ("Observaciones del auditor", _get(research, "observations") or _get(research, "risk_flags")),
            ],
            next_action="Agregar enlaces, noticias, sanciones o referencias complementarias.",
        ),
    ]

    total_fields = sum(card["total"] for card in cards)
    completed_fields = sum(card["completed"] for card in cards)
    percent = int(round((completed_fields / total_fields) * 100)) if total_fields else 0
    complete_cards = sum(1 for card in cards if card["status"] == "complete")
    partial_cards = sum(1 for card in cards if card["status"] == "partial")
    pending_cards = sum(1 for card in cards if card["status"] == "pending")

    return {
        "cards": cards,
        "totals": {
            "completed_fields": completed_fields,
            "total_fields": total_fields,
            "percent": percent,
            "complete_cards": complete_cards,
            "partial_cards": partial_cards,
            "pending_cards": pending_cards,
            "ready_for_summary": percent >= SUMMARY_MIN_PERCENT and pending_cards <= SUMMARY_MAX_PENDING,
        },
    }
