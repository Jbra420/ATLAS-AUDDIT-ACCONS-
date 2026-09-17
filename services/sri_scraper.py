"""
services/sri_scraper.py — Nivel 2: Scraper de respaldo para consultas RUC.

Este módulo se invoca cuando un RUC no es encontrado en la caché local (Catastro).
Se encarga de realizar peticiones web (Scraping) al portal público para extraer los datos de empresas recién creadas.
"""
from __future__ import annotations

import logging
import requests
from typing import Any

def scrape_ruc_data(ruc: str) -> dict[str, Any] | None:
    """
    Intenta extraer datos del RUC utilizando un endpoint proxy abierto de la comunidad.
    Retorna un diccionario con los datos si tiene éxito, o None si falla/es bloqueado.
    """
    logging.info(f"Fallback scraper activado para RUC: {ruc}")
    
    # Desactivamos el proxy comunitario temporalmente porque su DNS está caído
    # y bloquea el hilo principal del servidor de desarrollo.
    # TODO: Configurar proveedor de API comercial cuando se requiera Nivel 2.
    
    return {
        "name": f"Datos de web para {ruc} (Pendiente)",
        "city": "Validación manual requerida",
        "activity_hint": "Empresa muy reciente, no encontrada en Catastro."
    }
