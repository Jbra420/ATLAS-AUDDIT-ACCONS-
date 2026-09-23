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
            ProviderLink(
                name="Supercias — Certificado de administradores y accionistas",
                url=(
                    "https://www.gob.ec/scvs/tramites/emision-certificados-electronicos-"
                    "cumplimiento-obligaciones-datos-generales-administradores-accionistas-"
                    "actos-juridicos"
                ),
                instructions=(
                    "1. Abra el trámite oficial (gratuito, sin clave de acceso previa).\n"
                    "2. En 'CONSULTA DE COMPAÑÍAS' busque por expediente, RUC o nombre.\n"
                    f"3. Ingrese el RUC: {ruc}\n"
                    "4. Genere el certificado de 'Nómina de administradores' y el de "
                    "'Nómina de accionistas/socios' por separado (son documentos distintos).\n"
                    "5. Registre cada certificado como evidencia (fuente 'Supercias', título "
                    "que incluya la palabra 'certificado') antes de transcribir la nómina."
                ),
                source_type="Supercias",
                icon="🏛️",
                notes=(
                    "El Directorio de Compañías (catálogo local) solo trae el representante "
                    "legal actual, no la nómina completa. Esta es la única fuente oficial "
                    "para administradores y accionistas completos."
                ),
                field_hint="nomina",
            ),
            ProviderLink(
                name="Supercias — Portal de información",
                url="https://www.supercias.gob.ec/portalscvs/index.htm",
                instructions=(
                    "1. Abra el portal de información de la Superintendencia de Compañías.\n"
                    f"2. Busque la compañía por RUC ({ruc}) o nombre.\n"
                    "3. Descargue los documentos de administradores y de accionistas/socios.\n"
                    "4. Transcriba la nómina en esta pestaña."
                ),
                source_type="Supercias",
                icon="🏛️",
                field_hint="nomina",
            ),
        ]

    def nomina_links(self, ruc: str, company_name: str = "") -> list[ProviderLink]:
        """Fuentes oficiales de la nómina de administradores y accionistas."""
        return [lnk for lnk in self.get_links(ruc, company_name) if lnk.field_hint == "nomina"]
