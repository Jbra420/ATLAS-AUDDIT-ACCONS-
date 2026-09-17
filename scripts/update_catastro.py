"""
scripts/update_catastro.py — Herramienta para cargar el Catastro RUC del SRI.

El SRI publica mensualmente el padrón completo en datosabiertos.gob.ec.
Este script lee el archivo CSV descomprimido y lo carga en una base de datos
local ultrarrápida (sri_catastro.db) para que Atlas la consulte instantáneamente.

Uso:
  python scripts/update_catastro.py ruta/al/catastro.csv
"""
import sqlite3
import sys
import csv
from pathlib import Path

# Buscamos la BD en la raíz del proyecto
DB_PATH = Path(__file__).parent.parent / "sri_catastro.db"

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
        # Índice para búsquedas rápidas (aunque ruc ya es PK)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catastro_ruc ON sri_catastro(ruc)")

def load_csv(csv_path: Path):
    if not csv_path.exists():
        print(f"Error: El archivo {csv_path} no existe.")
        sys.exit(1)

    print(f"Iniciando carga de catastro desde {csv_path}...")
    init_db()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        
        count = 0
        batch = []
        # Ajustar el delimitador al pipe (|) usado por el SRI
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, delimiter="|") 
            
            for row in reader:
                ruc = row.get("NUMERO_RUC", "").strip()
                name = row.get("RAZON_SOCIAL", "").strip()
                city = row.get("DESCRIPCION_CANTON_EST", "").strip()
                activity = row.get("ACTIVIDAD_ECONOMICA", "").strip()
                
                # Opcional: Solo cargar empresas ACTIVAS
                estado = row.get("ESTADO_CONTRIBUYENTE", "").strip().upper()
                if estado != "ACTIVO":
                    continue
                
                if ruc and name:
                    batch.append((ruc, name, city, activity))
                    count += 1
                
                if len(batch) >= 10000:
                    conn.executemany(
                        "INSERT OR REPLACE INTO sri_catastro (ruc, name, city, activity_hint) VALUES (?, ?, ?, ?)",
                        batch
                    )
                    batch.clear()
                    print(f"{count} registros procesados...")
            
            if batch:
                conn.executemany(
                    "INSERT OR REPLACE INTO sri_catastro (ruc, name, city, activity_hint) VALUES (?, ?, ?, ?)",
                    batch
                )
                
    print(f"Carga completa. {count} empresas importadas a {DB_PATH}.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/update_catastro.py <ruta_al_archivo.csv>")
        print("Puedes descargar el CSV desde: https://www.sri.gob.ec/datasets")
        sys.exit(1)
        
    load_csv(Path(sys.argv[1]))
