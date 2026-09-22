"""
scripts/update_catastro.py — Herramienta para cargar el Catastro RUC del SRI.

El SRI publica mensualmente el padrón completo en datosabiertos.gob.ec.
Este script lee el archivo CSV descomprimido y lo carga en una base de datos
local ultrarrápida (sri_catastro.db) para que Atlas la consulte instantáneamente.

El SRI publica el catastro por provincia. Cada carga actualiza los RUC del
archivo sin borrar los de otras provincias, así que se pueden importar varias
provincias (en una o en varias ejecuciones) y la base conserva todas.

Uso:
  python scripts/update_catastro.py SRI_RUC_Azuay.csv
  python scripts/update_catastro.py SRI_RUC_Azuay.csv SRI_RUC_Pichincha.csv SRI_RUC_Guayas.csv
  python scripts/update_catastro.py --reemplazar SRI_RUC_*.csv   # vacía la base antes de cargar
"""
import csv
import sqlite3
from datetime import datetime
import sys
from pathlib import Path

# Buscamos la BD en la raíz del proyecto
DB_PATH = Path(__file__).parent.parent / "sri_catastro.db"

EXTRA_COLUMNS = {
    "jurisdiction": "TEXT",
    "taxpayer_status": "TEXT",
    "taxpayer_class": "TEXT",
    "start_date": "TEXT",
    "update_date": "TEXT",
    "suspension_date": "TEXT",
    "restart_date": "TEXT",
    "accounting_required": "TEXT",
    "taxpayer_type": "TEXT",
    "establishment_number": "TEXT",
    "trade_name": "TEXT",
    "establishment_status": "TEXT",
    "province": "TEXT",
    "canton": "TEXT",
    "parish": "TEXT",
    "ciiu_code": "TEXT",
    "withholding_agent": "TEXT",
    "special_taxpayer": "TEXT",
}

INSERT_COLUMNS = [
    "ruc", "name", "city", "activity_hint", *EXTRA_COLUMNS,
]

CSV_FIELDS = {
    "ruc": "NUMERO_RUC",
    "name": "RAZON_SOCIAL",
    "city": "DESCRIPCION_CANTON_EST",
    "activity_hint": "ACTIVIDAD_ECONOMICA",
    "jurisdiction": "CODIGO_JURISDICCION",
    "taxpayer_status": "ESTADO_CONTRIBUYENTE",
    "taxpayer_class": "CLASE_CONTRIBUYENTE",
    "start_date": "FECHA_INICIO_ACTIVIDADES",
    "update_date": "FECHA_ACTUALIZACION",
    "suspension_date": "FECHA_SUSPENSION_DEFINITIVA",
    "restart_date": "FECHA_REINICIO_ACTIVIDADES",
    "accounting_required": "OBLIGADO",
    "taxpayer_type": "TIPO_CONTRIBUYENTE",
    "establishment_number": "NUMERO_ESTABLECIMIENTO",
    "trade_name": "NOMBRE_FANTASIA_COMERCIAL",
    "establishment_status": "ESTADO_ESTABLECIMIENTO",
    "province": "DESCRIPCION_PROVINCIA_EST",
    "canton": "DESCRIPCION_CANTON_EST",
    "parish": "DESCRIPCION_PARROQUIA_EST",
    "ciiu_code": "CODIGO_CIIU",
    "withholding_agent": "AGENTE_RETENCION",
    "special_taxpayer": "ESPECIAL",
}

def init_db(db_path: Path = None) -> None:
    with sqlite3.connect(db_path or DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sri_catastro (
            ruc TEXT PRIMARY KEY,
            name TEXT,
            city TEXT,
            activity_hint TEXT
        )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sri_catastro)")}
        for column, column_type in EXTRA_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE sri_catastro ADD COLUMN {column} {column_type}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catastro_ruc ON sri_catastro(ruc)")
        # Registro de cada archivo importado: permite saber qué provincias y
        # qué corte contiene la base local (trazabilidad de la fuente).
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sri_catastro_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archivo TEXT NOT NULL,
            filas INTEGER NOT NULL,
            modo TEXT NOT NULL,
            importado_at TEXT NOT NULL
        )
        """)


def _is_main_establishment(value: str) -> bool:
    normalized = value.strip().lstrip("0")
    return normalized == "1"


def _insert_batch(conn: sqlite3.Connection, batch: list) -> None:
    conn.executemany(
        f"INSERT OR REPLACE INTO sri_catastro ({', '.join(INSERT_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})",
        batch,
    )


def load_csv(csv_path: Path, db_path: Path = None, modo: str = "incremental") -> int:
    """Carga un archivo del catastro. Actualiza los RUC presentes en el archivo
    y conserva los demás. Devuelve la cantidad de RUC importados."""
    db_path = db_path or DB_PATH
    print(f"Cargando catastro desde {csv_path}...")
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")

        count = 0
        batch = []
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, delimiter="|")

            for row in reader:
                if not _is_main_establishment(row.get("NUMERO_ESTABLECIMIENTO", "")):
                    continue

                values = tuple(row.get(CSV_FIELDS[column], "").strip() for column in INSERT_COLUMNS)
                ruc, name = values[:2]
                if ruc and name:
                    batch.append(values)
                    count += 1

                if len(batch) >= 10000:
                    _insert_batch(conn, batch)
                    batch.clear()
                    if count % 50000 == 0:
                        print(f"{count} registros procesados...")

            if batch:
                _insert_batch(conn, batch)

        conn.execute(
            "INSERT INTO sri_catastro_meta (archivo, filas, modo, importado_at) VALUES (?, ?, ?, ?)",
            (csv_path.name, count, modo, datetime.now().replace(microsecond=0).isoformat(sep=" ")),
        )

    print(f"  {count} empresas importadas desde {csv_path.name}.")
    return count


def clear_catastro(db_path: Path = None) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path or DB_PATH) as conn:
        conn.execute("DELETE FROM sri_catastro")


def print_coverage(db_path: Path = None) -> None:
    with sqlite3.connect(db_path or DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM sri_catastro").fetchone()[0]
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(jurisdiction), ''), 'SIN JURISDICCION'), COUNT(*) "
            "FROM sri_catastro GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    print(f"Base {db_path or DB_PATH}: {total} RUC en total.")
    for jurisdiccion, filas in rows:
        print(f"  {jurisdiccion}: {filas}")


def main(argv: list) -> int:
    reemplazar = "--reemplazar" in argv
    archivos = [Path(a) for a in argv if a != "--reemplazar"]
    if not archivos:
        print("Uso: python scripts/update_catastro.py [--reemplazar] <archivo.csv> [<archivo.csv> ...]")
        print("Puedes descargar los CSV desde: https://www.sri.gob.ec/datasets")
        return 1
    faltantes = [str(a) for a in archivos if not a.exists()]
    if faltantes:
        # Se valida todo antes de tocar la base: con --reemplazar, un archivo
        # inexistente dejaría la base vacía.
        print(f"Error: no existen los archivos: {', '.join(faltantes)}")
        return 1

    if reemplazar:
        print("Modo --reemplazar: se vacía la base antes de cargar.")
        clear_catastro()
    for archivo in archivos:
        load_csv(archivo, modo="reemplazo" if reemplazar else "incremental")
    print_coverage()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
