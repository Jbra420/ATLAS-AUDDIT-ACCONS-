import sqlite3
from database import DB_PATH, connect

def check_schema():
    with connect(DB_PATH) as conn:
        cursor = conn.execute("PRAGMA table_info(audits)")
        for row in cursor.fetchall():
            print(dict(row))

if __name__ == "__main__":
    check_schema()
