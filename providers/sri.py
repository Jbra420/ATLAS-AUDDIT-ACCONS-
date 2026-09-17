"""
providers/sri.py — Proveedor de consultas SRI para Atlas.

Genera enlaces al Servicio de Rentas Internas del Ecuador.
No hace scraping automático.
"""
from __future__ import annotations

from .base import BaseProvider, ProviderLink


class SriProvider(BaseProvider):
    name = "SRI"
    source_type = "SRI"
    icon = "📊"

    def get_links(self, ruc: str, company_name: str = "") -> list[ProviderLink]:
        return [
            ProviderLink(
                name="SRI — Consulta de RUC",
                url="https://srienlinea.sri.gob.ec/sri-en-linea/SriRucWeb/ConsultaRuc/Consultas/consultaRuc",
                instructions=(
                    f"1. Abra el enlace.\n"
                    f"2. Ingrese el RUC: {ruc}\n"
                    f"3. Anote: razón social, estado del RUC (activo/suspendido/cancelado), "
                    f"tipo de contribuyente, actividad económica, obligaciones y fecha de inicio.\n"
                    f"4. Copie el texto y péguelo en 'Evidencia / SRI'."
                ),
                source_type="SRI",
                icon="📊",
                notes="Acceso público. No requiere login. "
                      "Si el RUC está suspendido o cancelado, documéntelo en riesgos.",
                field_hint="sri_info",
            ),
            ProviderLink(
                name="SRI — Portal principal",
                url="https://www.sri.gob.ec/",
                instructions=(
                    "Portal principal del SRI. Use para acceder a "
                    "consultas adicionales: declaraciones, catastro de contribuyentes, etc."
                ),
                source_type="SRI",
                icon="📊",
                field_hint="sri_info",
            ),
        ]
