"""Importa estados financieros por ramo de Supercias a un catalogo local.

Uso: python3 scripts/update_balances_catalog.py /ruta/estadosFinancieros_2025
Los TXT son tabulados, usan cp1252 y coma decimal. Solo se almacenan los
ocho casilleros exigidos por el levantamiento; el catalogo conserva sus nombres.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "supercias_balances.db"
FIELDS = {
    "1": "activo_total",
    "2": "pasivo_total",
    "3": "patrimonio_neto",
    "401": "ingresos_401",
    "403": "otros_ingresos_403",
    "501": "costo_ventas_501",
    "502": "gastos_502",
    "707": "utilidad_neta_707",
}
SOURCE_URL = "https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/estadosFinancierosPorRamo.jsf"


def _amount(raw: str, line: int, code: str) -> float:
    value = raw.strip().replace(".", "").replace(",", ".")
    if not value:
        raise ValueError(f"Linea {line}: CUENTA_{code} vacia; no se puede asumir cero")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Linea {line}: CUENTA_{code} no es numerica: {raw!r}") from exc
    if not number.is_finite():
        raise ValueError(f"Linea {line}: CUENTA_{code} no es finita")
    return float(number)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog(source_dir: Path, db_path: Path = DB_PATH) -> tuple[int, int, int]:
    source_dir = Path(source_dir)
    db_path = Path(db_path)
    balances = sorted(source_dir.glob("balances_*.txt"))
    catalogs = sorted(source_dir.glob("catalogo_*.txt"))
    if len(balances) != 1 or len(catalogs) != 1:
        raise ValueError("La carpeta debe contener un balances_*.txt y un catalogo_*.txt")
    balance_path, catalog_path = balances[0], catalogs[0]
    with catalog_path.open(encoding="cp1252", newline="") as stream:
        labels = {}
        for line, row in enumerate(csv.reader(stream, delimiter="\t"), 1):
            if len(row) < 2 or not row[0].strip() or not row[1].strip():
                raise ValueError(f"Catalogo, linea {line}: codigo o descripcion faltante")
            code = row[0].strip()
            if code in labels:
                raise ValueError(f"Catalogo: cuenta duplicada {code}")
            labels[code] = row[1].strip()
    missing = set(FIELDS) - labels.keys()
    if missing:
        raise ValueError(f"Catalogo sin casilleros requeridos: {sorted(missing)}")

    with balance_path.open(encoding="cp1252", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        header = [part.strip() for part in next(reader)]
        required = {"AÑO", "EXPEDIENTE", "RUC", "NOMBRE", "CIIU", *(f"CUENTA_{c}" for c in FIELDS)}
        missing = required - set(header)
        if missing or len(header) != len(set(header)):
            raise ValueError(f"Encabezado invalido; faltan {sorted(missing)}")
        positions = {name: header.index(name) for name in required}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS balances (
                ruc TEXT NOT NULL, anio_fiscal INTEGER NOT NULL,
                expediente TEXT, nombre TEXT, ciiu TEXT,
                activo_total REAL, pasivo_total REAL, patrimonio_neto REAL,
                ingresos_401 REAL, otros_ingresos_403 REAL, costo_ventas_501 REAL,
                gastos_502 REAL, utilidad_neta_707 REAL,
                PRIMARY KEY (ruc, anio_fiscal))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS catalogo_cuentas (
                codigo TEXT PRIMARY KEY, descripcion TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS importaciones (
                anio_fiscal INTEGER PRIMARY KEY, archivo TEXT NOT NULL,
                sha256 TEXT NOT NULL, total_filas INTEGER NOT NULL,
                omitidos INTEGER NOT NULL DEFAULT 0,
                importado_at TEXT NOT NULL, url TEXT NOT NULL)""")
            if "omitidos" not in {row[1] for row in conn.execute("PRAGMA table_info(importaciones)")}:
                conn.execute("ALTER TABLE importaciones ADD COLUMN omitidos INTEGER NOT NULL DEFAULT 0")
            year = None
            total = 0
            skipped = 0
            for line, row in enumerate(reader, 2):
                if len(row) < len(header) - (1 if header[-1] == "" else 0):
                    raise ValueError(f"Linea {line}: columnas incompletas")
                raw_year = row[positions["AÑO"]].strip()
                if not re.fullmatch(r"\d{4}", raw_year):
                    raise ValueError(f"Linea {line}: año fiscal invalido")
                current_year = int(raw_year)
                if not 1990 <= current_year <= date.today().year:
                    raise ValueError(f"Linea {line}: año fiscal fuera de rango")
                if year is None:
                    year = current_year
                    conn.execute("DELETE FROM balances WHERE anio_fiscal = ?", (year,))
                elif current_year != year:
                    raise ValueError(f"Linea {line}: hay mas de un año fiscal en el archivo")
                ruc = row[positions["RUC"]].strip()
                if not re.fullmatch(r"\d{13}", ruc):
                    skipped += 1
                    continue
                values = [_amount(row[positions[f"CUENTA_{code}"]], line, code) for code in FIELDS]
                try:
                    conn.execute(
                        "INSERT INTO balances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ruc, year, row[positions["EXPEDIENTE"]].strip(),
                         row[positions["NOMBRE"]].strip(), row[positions["CIIU"]].strip(), *values),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"Linea {line}: RUC/año duplicado: {ruc}/{year}") from exc
                total += 1
            if not total:
                raise ValueError("El archivo de balances no contiene registros")
            conn.executemany(
                "INSERT OR REPLACE INTO catalogo_cuentas VALUES (?, ?)", labels.items()
            )
            conn.execute(
                """INSERT OR REPLACE INTO importaciones
                   (anio_fiscal, archivo, sha256, total_filas, omitidos, importado_at, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (year, balance_path.name, _sha256(balance_path), total, skipped,
                 datetime.now().replace(microsecond=0).isoformat(sep=" "), SOURCE_URL),
            )
    return year, total, skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("carpeta", type=Path)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    imported_year, count, skipped = load_catalog(args.carpeta, args.db)
    print(f"Importados {count} balances del ejercicio {imported_year} en {args.db}")
    print(f"Filas omitidas por RUC invalido: {skipped}")
