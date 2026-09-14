"""
providers/supercias.py — Proveedor de consultas Supercias para Atlas.

Genera enlaces directos al portal de Superintendencia de Compañías del Ecuador.
No hace scraping automático: las instrucciones guían al auditor manualmente.
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .base import BaseProvider, ProviderLink


class SuperciasProvider(BaseProvider):
    name = "Supercias"
    source_type = "Supercias"
    icon = "🏛️"

    def get_links(self, ruc: str, company_name: str = "") -> list[ProviderLink]:
        name_encoded = quote_plus(company_name or ruc)
        ruc_encoded = quote_plus(ruc)
        return [
            ProviderLink(
                name="Supercias — Consulta por RUC",
                url=(
                    "https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/"
                    f"informacionCompanias.jsf"
                ),
                instructions=(
                    f"1. Abra el enlace.\n"
                    f"2. En el campo RUC ingrese: {ruc}\n"
                    f"3. Anote: razón social, estado, representante legal, "
                    f"capital, fecha de constitución y actividad.\n"
                    f"4. Copie el texto relevante y péguelo en 'Evidencia / Supercias'."
                ),
                source_type="Supercias",
                icon="🏛️",
                notes="Requiere RUC exacto de 13 dígitos. "
                      "El portal puede requerir CAPTCHA en algunas consultas.",
                field_hint="supercias_info",
            ),
            ProviderLink(
                name="Supercias — Búsqueda por nombre",
                url=(
                    "https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/"
                    "informacionCompanias.jsf"
                ),
                instructions=(
                    f"1. Use el campo 'Nombre o Razón Social'.\n"
                    f"2. Busque: {company_name or 'nombre de la empresa'}\n"
                    f"3. Verifique que el RUC coincida con {ruc}."
                ),
                source_type="Supercias",
                icon="🏛️",
                field_hint="supercias_info",
            ),
        ]
