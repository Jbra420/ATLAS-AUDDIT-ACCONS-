"""
scripts/update_catastro.py — Herramienta para cargar el Catastro RUC del SRI.

El SRI publica mensualmente el padrón completo en datosabiertos.gob.ec.
Este script lee el archivo CSV descomprimido y lo carga en una base de datos
local ultrarrápida (sri_catastro.db) para que Atlas la consulte instantáneamente.

Uso:
  python scripts/update_catastro.py ruta/al/catastro.csv
"""
import csv
import sqlite3
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

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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


def _is_main_establishment(value: str) -> bool:
    normalized = value.strip().lstrip("0")
    return normalized == "1"

def load_csv(csv_path: Path):
    if not csv_path.exists():
        print(f"Error: El archivo {csv_path} no existe.")
        sys.exit(1)

    print(f"Iniciando carga de catastro desde {csv_path}...")
    init_db()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("DELETE FROM sri_catastro")

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
                    conn.executemany(
                        f"INSERT OR REPLACE INTO sri_catastro ({', '.join(INSERT_COLUMNS)}) "
                        f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})",
                        batch,
                    )
                    batch.clear()
                    if count % 50000 == 0:
                        print(f"{count} registros procesados...")

            if batch:
                conn.executemany(
                    f"INSERT OR REPLACE INTO sri_catastro ({', '.join(INSERT_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})",
                    batch,
                )

    print(f"Carga completa. {count} empresas importadas a {DB_PATH}.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/update_catastro.py <ruta_al_archivo.csv>")
        print("Puedes descargar el CSV desde: https://www.sri.gob.ec/datasets")
        sys.exit(1)
        
    load_csv(Path(sys.argv[1]))
