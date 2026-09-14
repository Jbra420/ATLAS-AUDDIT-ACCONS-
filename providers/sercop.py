"""
providers/sercop.py — Proveedor de consultas SERCOP para Atlas.

Genera enlaces al portal de Compras Públicas del Ecuador.
No hace scraping automático.
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .base import BaseProvider, ProviderLink


class SercopProvider(BaseProvider):
    name = "SERCOP"
    source_type = "SERCOP"
    icon = "📋"

    def get_links(self, ruc: str, company_name: str = "") -> list[ProviderLink]:
        ruc_encoded = quote_plus(ruc)
        name_encoded = quote_plus(company_name or ruc)
        return [
            ProviderLink(
                name="SERCOP — Búsqueda de proveedor por RUC",
                url=(
                    f"https://www.compraspublicas.gob.ec/ProcesoContratacion/compras/EP/"
                    f"BusquedaProveedorCpc.cpe?ruc={ruc_encoded}"
                ),
                instructions=(
                    f"1. Abra el enlace.\n"
                    f"2. Verifique si el RUC {ruc} aparece como proveedor del Estado.\n"
                    f"3. Anote: nombre registrado, tipo, habilitación y contratos activos.\n"
                    f"4. Revise si tiene inhabilitaciones o sanciones.\n"
                    f"5. Copie el texto y péguelo en 'Evidencia / SERCOP'."
                ),
                source_type="SERCOP",
                icon="📋",
                notes="Si el proveedor no aparece, documéntelo como 'No registrado en SERCOP'.",
                field_hint="sercop_info",
            ),
            ProviderLink(
                name="SERCOP — Portal de contratación pública",
                url="https://www.compraspublicas.gob.ec/",
                instructions=(
                    "Portal general de compras públicas. "
                    "Permite búsquedas avanzadas de procesos, contratos y proveedores."
                ),
                source_type="SERCOP",
                icon="📋",
                field_hint="sercop_info",
            ),
        ]
