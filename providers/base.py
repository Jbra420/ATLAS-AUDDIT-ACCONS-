"""
providers/base.py — Interfaz base para proveedores de información RUC en Atlas.

Un ProviderLink es un enlace de consulta pre-armado para una fuente oficial.
Los proveedores no hacen scraping: generan URLs y guías de uso.
La abstracción permite añadir conectores reales en el futuro sin cambiar la app.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderLink:
    """Resultado de un proveedor de información por RUC."""
    name: str                       # Nombre de la fuente (ej. "Supercias")
    url: str                        # URL directa o de búsqueda pre-armada
    instructions: str               # Guía breve para el auditor
    source_type: str                # Tipo para registrar en la ficha
    icon: str = "🔗"               # Ícono Unicode para la UI
    notes: str = ""                 # Notas adicionales (limitaciones, etc.)
    field_hint: str = ""            # Campo de la ficha donde pegar el resultado


class BaseProvider:
    """
    Clase base para proveedores de información de empresas.

    Para añadir un conector real en el futuro, sobrescribir `fetch()`
    en la subclase y retornar datos estructurados. Por ahora, los
    proveedores solo generan links e instrucciones.
    """
    name: str = "Proveedor base"
    source_type: str = "web"
    icon: str = "🔗"

    def get_links(self, ruc: str, company_name: str = "") -> list[ProviderLink]:
        """
        Retorna lista de ProviderLink para el RUC dado.
        Sobrescribir en subclases concretas.
        """
        raise NotImplementedError

    def fetch(self, ruc: str) -> dict:
        """
        Placeholder para futura extracción automática de datos.
        Retorna dict vacío hasta que se implemente el conector real.
        """
        return {}
