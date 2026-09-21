"""
services/company_search.py — Estado interpretado de busqueda por fuentes.

No consulta servicios externos. Resume la informacion ya capturada en Atlas para
que el auditor vea que fuentes estan completas, parciales o pendientes.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Union

from services.rowutil import row_get as _get


RowLike = Union[sqlite3.Row, dict[str, Any], None]

def _present(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"—", "-", "N/A", "n/a", "Pendiente de confirmar"})


def _short(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def find_source_check(
    source_checks: list[RowLike], include: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> RowLike:
    """Devuelve la fila de source_checks cuyo 'fuente' coincide con los tokens dados.

    Usada tanto por _consulted() (para el mapa de fuentes) como por page.py
    (para resolver a qué fila debe apuntar el botón 'Marcar consultada' de
    cada tab), de modo que la lógica de coincidencia de tokens vive en un
    solo lugar.
    """
    for check in source_checks:
        name = str(_get(check, "fuente", "")).lower()
        if any(token in name for token in include) and not any(token in name for token in exclude):
            return check
    return None


def _consulted(source_checks: list[RowLike], include: tuple[str, ...], exclude: tuple[str, ...] = ()) -> bool:
    check = find_source_check(source_checks, include, exclude)
    return bool(check) and _get(check, "estado") == "consultada"


def _row_count(rows: list[Any] | None) -> int:
    return len(rows or [])


def _card(
    *,
    key: str,
    title: str,
    tab: str,
    fields: list[tuple[str, Any]],
    required_labels: tuple[str, ...],
    next_action: str,
) -> dict[str, Any]:
    found = [
        {"label": label, "value": _short(value)}
        for label, value in fields
        if _present(value)
    ]
    missing = [label for label, value in fields if not _present(value)]
    required = set(required_labels)
    blocking_missing = [label for label in missing if label in required]
    warning_missing = [label for label in missing if label not in required]
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
        "missing": missing,
        "blocking_missing": blocking_missing,
        "warning_missing": warning_missing,
        "required_total": len(required),
        "required_completed": len(required) - len(blocking_missing),
        "next_action": next_action,
    }


def _readiness_item(label: str, source: str, tab: str) -> dict[str, str]:
    return {"label": label, "source": source, "tab": tab}


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

    admin_count = _row_count(admins)
    shareholder_count = _row_count(shareholders)

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
            required_labels=(
                "RUC validado",
                "Fuente SRI consultada",
                "Estado contribuyente",
                "Actividad economica",
            ),
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
            required_labels=(
                "Fuente Supercias consultada",
                "Situacion legal",
                "Representante legal",
                "Administradores registrados",
                "Accionistas registrados",
            ),
            next_action="Cruzar estado societario, representantes y estructura accionaria.",
        ),
    ]

    total_fields = sum(card["total"] for card in cards)
    completed_fields = sum(card["completed"] for card in cards)
    percent = int(round((completed_fields / total_fields) * 100)) if total_fields else 0
    complete_cards = sum(1 for card in cards if card["status"] == "complete")
    partial_cards = sum(1 for card in cards if card["status"] == "partial")
    pending_cards = sum(1 for card in cards if card["status"] == "pending")

    blockers = [
        _readiness_item(label, card["title"], card["tab"])
        for card in cards
        for label in card["blocking_missing"]
    ]
    warnings = [
        _readiness_item(label, card["title"], card["tab"])
        for card in cards
        for label in card["warning_missing"]
    ]

    location_checks = (
        ("Provincia de la empresa", _get(location, "provincia"), "ubicacion"),
        ("Ciudad o canton de la empresa", _get(location, "ciudad") or _get(location, "canton"), "ubicacion"),
        ("Direccion principal", _get(location, "calle") or _get(research, "address"), "ubicacion"),
        ("Informacion financiera", _get(snapshot, "activo_total"), "indicadores"),
        ("Observaciones del auditor", _get(research, "observations"), "resumen"),
    )
    warnings.extend(
        _readiness_item(label, "Complementario", tab)
        for label, value, tab in location_checks
        if not _present(value)
    )

    required_total = sum(card["required_total"] for card in cards)
    required_completed = sum(card["required_completed"] for card in cards)
    required_percent = int(round((required_completed / required_total) * 100)) if required_total else 0
    ready_for_summary = not blockers

    return {
        "cards": cards,
        "readiness": {
            "ready": ready_for_summary,
            "blockers": blockers,
            "warnings": warnings,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "required_completed": required_completed,
            "required_total": required_total,
            "required_percent": required_percent,
        },
        "totals": {
            "completed_fields": completed_fields,
            "total_fields": total_fields,
            "percent": percent,
            "complete_cards": complete_cards,
            "partial_cards": partial_cards,
            "pending_cards": pending_cards,
            "ready_for_summary": ready_for_summary,
            "required_percent": required_percent,
        },
    }
