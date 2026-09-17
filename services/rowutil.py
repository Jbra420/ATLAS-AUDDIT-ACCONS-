"""
services/rowutil.py — Lectura segura de sqlite3.Row / dict compartida por
services/ y views/. No depende de nada más (módulo hoja).
"""
from __future__ import annotations

from typing import Any, Mapping


def row_get(row: Mapping[str, Any] | None, key: str, default: Any = "") -> Any:
    """Lee row[key] sin lanzar si row es None, no tiene la clave, o el valor es NULL.

    Un valor None almacenado en la fila se trata igual que una clave ausente,
    para que quien llama no tenga que distinguir NULL de "no vino".
    """
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(key, default)
        return default
    return default if value is None else value
