"""tests/_base.py — Montaje común de las pruebas que usan una base SQLite.

Cada prueba recibe una base desechable en una carpeta temporal propia (que se
borra al terminar) con el jefe y el auditor demo: nunca toca auddit.db ni
adjuntos/. No es un archivo de pruebas (no empieza con test_); se importa
como `from tests._base import BaseTemporal`, que funciona tanto con
`python3 -m unittest discover -s tests` como con `python3 -m unittest tests.test_x`.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import authenticate, create_company_audit, init_db


def carpeta_temporal(test: unittest.TestCase) -> Path:
    """Carpeta vacía que se borra al terminar la prueba (tempfile.mkdtemp no se borra)."""
    carpeta = tempfile.TemporaryDirectory()
    test.addCleanup(carpeta.cleanup)
    return Path(carpeta.name)


def base_temporal(test: unittest.TestCase, nombre: str = "atlas.db") -> Path:
    """Crea una base con los datos demo en una carpeta que se borra al terminar la prueba."""
    db = carpeta_temporal(test) / nombre
    init_db(db, demo=True)
    return db


class BaseTemporal(unittest.TestCase):
    """self.tmp (carpeta temporal), self.db, self.admin y self.auditor."""

    def setUp(self) -> None:
        super().setUp()
        self.db = base_temporal(self)
        self.tmp = self.db.parent
        self.admin = authenticate("admin", "admin123", self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)

    def crear_auditoria(self, nombre: str, ruc: str, ciudad: str = "Cuenca", periodo: str = "2026",
                        actividad: str = "", auditor_id: int | None = None) -> int:
        """Empresa y auditoría asignada al auditor demo (o al indicado), creada por el jefe."""
        return create_company_audit(
            nombre, ruc, ciudad, actividad, periodo,
            self.auditor["id"] if auditor_id is None else auditor_id, self.admin["id"], self.db,
        )
