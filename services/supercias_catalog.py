"""
services/supercias_catalog.py — Mapeo del catálogo local de Supercías para Atlas.

Transforma una fila de supercias_catalog.db (importada por
scripts/update_supercias_catalog.py desde el Directorio de Compañías oficial)
a los modelos del expediente. No consulta la base de datos ni hace red:
esa responsabilidad es de database.lookup_supercias_catalog().
"""
from __future__ import annotations


def _clean_date(value: str) -> str:
    """Convierte fechas DD/MM/YYYY (formato del Directorio) a YYYY-MM-DD.

    Si el valor no calza con ese formato se devuelve tal cual: preferimos
    guardar el dato crudo a perderlo silenciosamente.
    """
    value = (value or "").strip()
    if not value:
        return ""
    parts = value.split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        d, m, y = parts
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return value


def build_supercias_result(record: dict[str, str]) -> dict[str, dict[str, str]]:
    """Transforma una fila del catálogo local de Supercías a los modelos del expediente."""
    profile = {
        "expediente_supercias": (record.get("expediente") or "").strip(),
        "razon_social": (record.get("razon_social") or "").strip(),
        "situacion_legal": (record.get("situacion_legal") or "").strip(),
        "fecha_constitucion": _clean_date(record.get("fecha_constitucion", "")),
        "tipo_compania": (record.get("tipo_compania") or "").strip(),
        "nacionalidad": (record.get("pais") or "").strip(),
        "representante_legal": (record.get("representante") or "").strip(),
        "representante_cargo": (record.get("representante_cargo") or "").strip(),
        "capital_suscrito": (record.get("capital_suscrito") or "").strip(),
        "ciiu_nivel1": (record.get("ciiu_nivel1") or "").strip(),
        "ciiu_nivel6": (record.get("ciiu_nivel6") or "").strip(),
        "ultimo_anio_balance": (record.get("ultimo_balance") or "").strip(),
        "telefono": (record.get("telefono") or "").strip(),
    }
    location = {
        "provincia": (record.get("provincia") or "").strip(),
        "canton": (record.get("canton") or "").strip(),
        "ciudad": (record.get("ciudad") or "").strip(),
        "calle": (record.get("calle") or "").strip(),
        "numero": (record.get("numero") or "").strip(),
        "interseccion": (record.get("interseccion") or "").strip(),
        "barrio": (record.get("barrio") or "").strip(),
    }
    cargo_txt = f" ({profile['representante_cargo']})" if profile["representante_cargo"] else ""
    research_lines = [
        f"Expediente: {profile['expediente_supercias']}" if profile["expediente_supercias"] else "",
        f"Situación legal: {profile['situacion_legal']}" if profile["situacion_legal"] else "",
        (
            f"Representante legal: {profile['representante_legal']}{cargo_txt}"
            if profile["representante_legal"] else ""
        ),
        "Fuente: Directorio de Compañías Supercías (catálogo local).",
    ]
    return {
        "profile": profile,
        "location": location,
        "research": {
            "legal_status": profile["situacion_legal"],
            "representative": profile["representante_legal"],
            "supercias_info": "\n".join(line for line in research_lines if line),
        },
        "catalogo": {
            "fecha_actualizacion": (record.get("_catalogo_fecha_actualizacion") or "").strip(),
            "total_filas": (record.get("_catalogo_total_filas") or ""),
        },
    }
