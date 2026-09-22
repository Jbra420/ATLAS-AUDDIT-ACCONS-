"""Consulta automática de empresas usando el catastro local oficial del SRI."""
from __future__ import annotations

from pathlib import Path

from database import (
    DB_PATH,
    apply_balances_catalog_result,
    apply_sri_research_result,
    apply_supercias_research_result,
    lookup_balances_catalog,
    lookup_catastro,
    lookup_supercias_catalog,
)
from services.normalizacion import regimen_desde_clase
from services.supercias_catalog import build_supercias_result


def _clean_date(value: str) -> str:
    return (value or "").strip().split(" ", 1)[0]


def _yes_no(value: str) -> str:
    normalized = (value or "").strip().upper()
    return {"S": "SI", "N": "NO"}.get(normalized, normalized)


def build_sri_result(record: dict[str, str]) -> dict[str, dict[str, str]]:
    """Transforma una fila del catastro a los modelos del expediente."""
    name = (record.get("name") or "").strip()
    ruc = (record.get("ruc") or "").strip()
    activity = (record.get("activity_hint") or "").strip()
    province = (record.get("province") or "").strip()
    canton = (record.get("canton") or record.get("city") or "").strip()
    parish = (record.get("parish") or "").strip()
    trade_name = (record.get("trade_name") or "").strip()
    clase = (record.get("taxpayer_class") or "").strip()
    regimen = regimen_desde_clase(clase)
    if regimen:
        regimen_line = f"Régimen: {regimen} (clase {clase})"
    elif clase:
        regimen_line = f"Régimen: pendiente de confirmar (clase {clase} sin equivalencia confirmada)"
    else:
        regimen_line = ""

    sri_lines = [
        f"RUC: {ruc}",
        f"Estado del contribuyente: {(record.get('taxpayer_status') or '').strip()}",
        f"Tipo de contribuyente: {(record.get('taxpayer_type') or '').strip()}",
        regimen_line,
        f"Código CIIU: {(record.get('ciiu_code') or '').strip()}",
        f"Ubicación registrada: {', '.join(part for part in (parish, canton, province) if part)}",
        "Fuente: Catastro RUC SRI cargado localmente.",
    ]

    return {
        "company": {
            "ruc": ruc,
            "name": name,
            "city": canton,
            "activity_hint": activity,
        },
        "profile": {
            "ruc": ruc,
            "razon_social": name,
            "razon_social_sri": name,
            "estado_contribuyente": (record.get("taxpayer_status") or "").strip(),
            "tipo_contribuyente": (record.get("taxpayer_type") or "").strip(),
            "regimen": regimen,
            "categoria": clase,
            "obligado_contabilidad": _yes_no(record.get("accounting_required", "")),
            "agente_retencion": _yes_no(record.get("withholding_agent", "")),
            "contribuyente_especial": _yes_no(record.get("special_taxpayer", "")),
            "fecha_inicio_actividades": _clean_date(record.get("start_date", "")),
            "fecha_actualizacion": _clean_date(record.get("update_date", "")),
            "actividad_economica": activity,
            "ciiu_sri": (record.get("ciiu_code") or "").strip(),
        },
        "location": {
            "provincia": province,
            "canton": canton,
            "ciudad": canton,
        },
        "research": {
            "commercial_name": trade_name,
            "economic_activity": activity,
            "sri_info": "\n".join(line for line in sri_lines if line and not line.endswith(": ")),
        },
    }


def research_company_by_ruc(
    audit_id: int,
    ruc: str,
    user_id: int,
    db_path: Path | str = DB_PATH,
) -> dict[str, object]:
    """Consulta SRI, Directorio Supercias y balances por RUC de forma independiente.

    Un resultado parcial es util; falla solo si ninguno de los tres catalogos
    contiene el RUC. El año financiero se confirma despues en el expediente.
    """
    sri_record = lookup_catastro(ruc)
    supercias_record = lookup_supercias_catalog(ruc)
    balances = lookup_balances_catalog(ruc)

    if not sri_record and not supercias_record and not balances:
        raise ValueError(
            "El RUC no consta en los catálogos locales SRI, Supercías ni balances. "
            "Actualiza los catálogos locales antes de continuar."
        )

    result = None
    sri_found = bool(sri_record)
    populated = 0
    if sri_record:
        result = build_sri_result(sri_record)
        apply_sri_research_result(audit_id, user_id, result, db_path)
        populated = sum(
            1
            for section in ("profile", "location")
            for value in result[section].values()
            if value
        )

    supercias_found = bool(supercias_record)
    supercias_populated = 0
    if supercias_record:
        supercias_result = build_supercias_result(supercias_record)
        apply_supercias_research_result(audit_id, user_id, supercias_result, db_path)
        supercias_populated = sum(
            1
            for section in ("profile", "location")
            for value in supercias_result[section].values()
            if value
        )

    financial_years = apply_balances_catalog_result(audit_id, user_id, balances, db_path)

    if not sri_found and not supercias_found:
        pending_source = "SRI y Supercias"
    elif not sri_found:
        pending_source = "SRI"
    elif not supercias_found:
        pending_source = "Supercias"
    else:
        pending_source = None

    return {
        "result": result,
        "sri_found": sri_found,
        "populated_fields": populated,
        "supercias_found": supercias_found,
        "supercias_populated_fields": supercias_populated,
        "financial_years": financial_years,
        "pending_source": pending_source,
    }
