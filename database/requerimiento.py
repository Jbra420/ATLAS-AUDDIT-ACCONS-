"""database/requerimiento.py — Requerimiento inicial: datos confirmados, marcas y cuadros del
Excel, generaciones de los cuatro documentos, archivos recibidos y su revisión,
envíos y respuestas.

Todo va ligado a la auditoría (audit_id) pero en tablas propias: nada de esto
modifica el levantamiento de información. Los archivos se guardan fuera de la
base, uno por contenido: adjuntos/<audit_id>/requerimiento/<sha256>.<ext>.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from services.requerimiento import (
    ADJUNTOS,
    RECEPCIONES,
    CAMPOS_ANIO,
    CAMPOS_CORREO,
    CAMPOS_FECHA,
    CAMPOS_TEXTO,
    CUADROS,
    DOCUMENTOS,
    ITEMS,
    MAX_FILAS_CUADRO,
    nombre_seguro,
    validar_fecha_envio,
    validar_items,
)

from database.base import DB_PATH, _touch_audit, connect, now_iso
from database.certificados import ADJUNTOS_DIR


# PDF firmados que el auditor revisa y deja constancia (el Excel se valida al importarlo).
REVISABLES = ("contrato", *(t for t in RECEPCIONES if t != "solicitud_respondida"))


def _guardar(audit_id: int, contenido: bytes, extension: str, adjuntos_dir: Path) -> tuple[str, str, Path | None]:
    """Escribe por SHA-256 sin reemplazar un archivo que otra solicitud creó.
    Devuelve (ruta relativa, sha256, archivo creado ahora o None)."""
    sha256 = hashlib.sha256(contenido).hexdigest()
    ruta = Path(str(int(audit_id))) / "requerimiento" / f"{sha256}.{extension}"
    destino = adjuntos_dir / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destino.parent, prefix=f".{sha256}.", suffix=".tmp",
                                     delete=False) as archivo:
        temporal = Path(archivo.name)
        archivo.write(contenido)
    try:
        try:
            os.link(temporal, destino)
            nuevo = destino
        except FileExistsError:
            nuevo = None
        return str(ruta), sha256, nuevo
    finally:
        temporal.unlink(missing_ok=True)


def ruta_archivo(ruta: str, adjuntos_dir: Path = ADJUNTOS_DIR) -> Path:
    """Ruta absoluta de un archivo guardado; rechaza cualquier ruta fuera de adjuntos/."""
    base = adjuntos_dir.resolve()
    destino = (base / ruta).resolve()
    if not destino.is_relative_to(base) or not destino.is_file():
        raise ValueError("Archivo no disponible")
    return destino


# ── Lectura ──────────────────────────────────────────────────────────────

def get_requerimiento_context(audit_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """Todo el requerimiento de una auditoría en una sola conexión."""
    with connect(db_path) as conn:
        guardado = conn.execute("SELECT * FROM requerimientos WHERE audit_id = ?", (audit_id,)).fetchone()
        items = {
            (row["hoja"], row["numero"]): row
            for row in conn.execute("SELECT * FROM requerimiento_items WHERE audit_id = ?", (audit_id,))
        }
        detalles: dict[str, list[list[str]]] = {clave: [] for clave in CUADROS}
        for row in conn.execute(
            "SELECT * FROM requerimiento_detalles WHERE audit_id = ? ORDER BY seccion, orden", (audit_id,)
        ):
            if row["seccion"] in detalles:
                detalles[row["seccion"]].append(json.loads(row["valores_json"]))
        versiones: dict[str, list[sqlite3.Row]] = {tipo: [] for tipo in DOCUMENTOS}
        por_paquete: dict[int, dict[str, sqlite3.Row]] = {}
        for row in conn.execute(
            """SELECT r.*, u.full_name AS autor FROM requerimiento_archivos r
               LEFT JOIN users u ON u.id = r.generado_por
               WHERE r.audit_id = ? ORDER BY r.tipo, r.version DESC""", (audit_id,)
        ):
            versiones.setdefault(row["tipo"], []).append(row)
            if row["paquete_id"] is not None:
                por_paquete.setdefault(row["paquete_id"], {})[row["tipo"]] = row
        # Generaciones completas, la más reciente primero: {**fila, "archivos": {tipo: fila}}.
        paquetes = [
            {**dict(row), "archivos": por_paquete.get(row["id"], {})}
            for row in conn.execute(
                """SELECT p.*, u.full_name AS autor FROM requerimiento_paquetes p
                   LEFT JOIN users u ON u.id = p.generado_por
                   WHERE p.audit_id = ? ORDER BY p.numero DESC""", (audit_id,)
            )
        ]
        adjuntos: dict[str, list[sqlite3.Row]] = {tipo: [] for tipo in ADJUNTOS}
        for row in conn.execute(
            """SELECT a.*, u.full_name AS autor, rv.full_name AS revisor FROM requerimiento_adjuntos a
               LEFT JOIN users u ON u.id = a.subido_por
               LEFT JOIN users rv ON rv.id = a.revisado_por
               WHERE a.audit_id = ? ORDER BY a.id DESC""", (audit_id,)
        ):
            adjuntos.setdefault(row["tipo"], []).append(row)
        envios = list(conn.execute(
            """SELECT e.*, u.full_name AS autor FROM requerimiento_envios e
               LEFT JOIN users u ON u.id = e.registrado_por
               WHERE e.audit_id = ? ORDER BY e.fecha DESC, e.id DESC""", (audit_id,)
        ))
        # Solo cuentan las respuestas de un Excel cuya procedencia se verificó.
        respuestas = list(conn.execute(
            """SELECT r.*, u.full_name AS autor FROM requerimiento_respuestas r
               JOIN requerimiento_adjuntos a ON a.id = r.adjunto_id AND a.importacion_estado = 'importado'
               LEFT JOIN users u ON u.id = r.importado_por
               WHERE r.audit_id = ? ORDER BY r.id DESC""", (audit_id,)
        ))
    return {
        "guardado": guardado, "items": items, "detalles": detalles, "versiones": versiones,
        "paquetes": paquetes, "adjuntos": adjuntos, "envios": envios, "respuestas": respuestas,
    }


def get_requerimiento_file(audit_id: int, origen: str, file_id: int, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    """Versión generada (origen "generado") o archivo subido ("adjunto") de la auditoría."""
    tabla = {"generado": "requerimiento_archivos", "adjunto": "requerimiento_adjuntos"}.get(origen)
    if tabla is None:
        return None
    with connect(db_path) as conn:
        return conn.execute(
            f"SELECT * FROM {tabla} WHERE id = ? AND audit_id = ?", (file_id, audit_id),  # noqa: S608
        ).fetchone()


def get_requerimiento_paquete(referencia: str, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    """Generación por la referencia que lleva su Excel, de cualquier auditoría:
    quien la usa debe comprobar que sea de la suya."""
    with connect(db_path) as conn:
        return conn.execute("SELECT * FROM requerimiento_paquetes WHERE referencia = ?", (referencia,)).fetchone()


def next_requerimiento_numero(audit_id: int, db_path: Path | str = DB_PATH) -> int:
    """Número de la próxima generación (sigue también a las versiones sueltas anteriores)."""
    with connect(db_path) as conn:
        return int(conn.execute(
            """SELECT MAX(COALESCE((SELECT MAX(numero) FROM requerimiento_paquetes WHERE audit_id = ?), 0),
                          COALESCE((SELECT MAX(version) FROM requerimiento_archivos WHERE audit_id = ?), 0)) + 1""",
            (audit_id, audit_id),
        ).fetchone()[0])


# ── Escritura ────────────────────────────────────────────────────────────

def save_requerimiento_datos(
    audit_id: int, datos: dict[str, Any], user_id: int, db_path: Path | str = DB_PATH,
) -> None:
    """Guarda los datos que el auditor confirmó (ya normalizados por
    services.requerimiento.normalizar_datos). No toca documentos generados."""
    columnas = (*CAMPOS_TEXTO, *CAMPOS_ANIO, *CAMPOS_FECHA)
    valores = [datos.get(c) if datos.get(c) not in ("", None) else None for c in columnas]
    ts = now_iso()
    with connect(db_path) as conn:
        empresa = conn.execute(
            "SELECT c.ruc FROM audits a JOIN companies c ON c.id = a.company_id WHERE a.id = ?",
            (audit_id,),
        ).fetchone()
        if empresa is None:
            raise ValueError("Auditoría no disponible")
        if str(datos.get("ruc") or "").strip() != str(empresa["ruc"] or "").strip():
            raise ValueError("El RUC del requerimiento debe coincidir con el de la auditoría. "
                             "Corríjalo primero en Levantamiento de información")
        conn.execute(
            f"""
            INSERT INTO requerimientos (audit_id, {", ".join(columnas)}, cronograma_json, equipo_json,
                                        confirmado_por, confirmado_at, updated_at)
            VALUES (?, {", ".join("?" for _ in columnas)}, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_id) DO UPDATE SET
                {", ".join(f"{c} = excluded.{c}" for c in columnas)},
                cronograma_json = excluded.cronograma_json,
                equipo_json = excluded.equipo_json,
                confirmado_por = excluded.confirmado_por,
                confirmado_at = excluded.confirmado_at,
                updated_at = excluded.updated_at
            """,  # noqa: S608 — columnas constantes del módulo
            (audit_id, *valores, json.dumps(datos.get("cronograma") or [], ensure_ascii=False),
             json.dumps(datos.get("equipo") or [], ensure_ascii=False), user_id, ts, ts),
        )
        _touch_audit(conn, audit_id)


def save_requerimiento_destinatario(
    audit_id: int, datos: dict[str, str], db_path: Path | str = DB_PATH,
) -> None:
    """Destinatario del correo (ya validado por normalizar_datos). Va aparte de
    los datos del requerimiento: no entra en los documentos."""
    with connect(db_path) as conn:
        cambiados = conn.execute(
            f"""UPDATE requerimientos SET {", ".join(f"{c} = ?" for c in CAMPOS_CORREO)}, updated_at = ?
                WHERE audit_id = ?""",  # noqa: S608 — columnas constantes del módulo
            (*(datos.get(c) or None for c in CAMPOS_CORREO), now_iso(), audit_id),
        ).rowcount
        if not cambiados:
            raise ValueError("Confirme primero los datos del requerimiento")
        _touch_audit(conn, audit_id)


def save_requerimiento_items(
    audit_id: int, marcas: dict[tuple[int, int], dict[str, Any]], db_path: Path | str = DB_PATH,
) -> None:
    """Marcas CUMPLIDO / NO APLICA y observaciones que el auditor prepara en
    las hojas 1 y 2 antes de enviar el Excel. Reemplaza las anteriores."""
    validar_items(marcas)
    validos = {(hoja, numero) for hoja, filas in ITEMS.items() for numero, _t in filas}
    with connect(db_path) as conn:
        conn.execute("DELETE FROM requerimiento_items WHERE audit_id = ?", (audit_id,))
        for (hoja, numero), marca in marcas.items():
            if (hoja, numero) not in validos:
                continue
            observacion = str(marca.get("observacion") or "").strip()[:500]
            if not (marca.get("cumplido") or marca.get("no_aplica") or observacion):
                continue
            conn.execute(
                """INSERT INTO requerimiento_items (audit_id, hoja, numero, cumplido, no_aplica, observacion)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (audit_id, hoja, numero, int(bool(marca.get("cumplido"))), int(bool(marca.get("no_aplica"))),
                 observacion),
            )
        conn.execute("UPDATE requerimientos SET updated_at = ? WHERE audit_id = ?", (now_iso(), audit_id))
        _touch_audit(conn, audit_id)


def save_requerimiento_detalle(
    audit_id: int, seccion: str, filas: list[list[str]], db_path: Path | str = DB_PATH,
) -> int:
    """Filas precargadas de un cuadro de detalle (hojas 3 y 4). Descarta las vacías."""
    if seccion not in CUADROS:
        raise ValueError("Cuadro de detalle no válido")
    columnas = CUADROS[seccion][2]
    limpias = []
    for fila in filas:
        valores = [" ".join(str(v or "").split())[:200] for v in list(fila)[:len(columnas)]]
        valores += [""] * (len(columnas) - len(valores))
        if any(valores):
            limpias.append(valores)
    if len(limpias) > MAX_FILAS_CUADRO:
        raise ValueError(f"Máximo {MAX_FILAS_CUADRO} filas por cuadro")
    with connect(db_path) as conn:
        conn.execute("DELETE FROM requerimiento_detalles WHERE audit_id = ? AND seccion = ?", (audit_id, seccion))
        for orden, valores in enumerate(limpias, start=1):
            conn.execute(
                "INSERT INTO requerimiento_detalles (audit_id, seccion, orden, valores_json) VALUES (?, ?, ?, ?)",
                (audit_id, seccion, orden, json.dumps(valores, ensure_ascii=False)),
            )
        conn.execute("UPDATE requerimientos SET updated_at = ? WHERE audit_id = ?", (now_iso(), audit_id))
        _touch_audit(conn, audit_id)
    return len(limpias)


def save_requerimiento_adjunto(
    audit_id: int, tipo: str, nombre: str, contenido: bytes, formato: str, user_id: int,
    *, fecha: str = "", nota: str = "", detalle: str = "", db_path: Path | str = DB_PATH,
    adjuntos_dir: Path = ADJUNTOS_DIR,
) -> int:
    """Guarda un archivo subido por el auditor (ya validado) y lo registra.
    Subir otra vez el mismo tipo agrega un registro: el anterior se conserva.
    detalle describe lo que Atlas comprobó del archivo (p. ej. PDF legible)."""
    if tipo not in ADJUNTOS:
        raise ValueError("Tipo de archivo no válido")
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        nuevo = None
        try:
            ruta, sha256, nuevo = _guardar(audit_id, contenido, formato, adjuntos_dir)
            cur = conn.execute(
                """INSERT INTO requerimiento_adjuntos
                       (audit_id, tipo, nombre, ruta, sha256, bytes, fecha, nota, detalle_archivo, subido_por, subido_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, tipo, nombre_seguro(nombre), ruta, sha256, len(contenido), fecha or None,
                 nota.strip()[:500], detalle[:500], user_id, now_iso()),
            )
            _touch_audit(conn, audit_id)
            return int(cur.lastrowid)
        except BaseException:
            if nuevo is not None:
                nuevo.unlink(missing_ok=True)
            raise


def review_requerimiento_adjunto(
    audit_id: int, adjunto_id: int, resultado: str, nota: str, user_id: int, db_path: Path | str = DB_PATH,
) -> None:
    """Constancia de que el auditor revisó un PDF firmado (empresa, ejercicio,
    integridad aparente y firmas): resultado, nota, autor y fecha. Una
    revisión no se reemplaza; si el documento no sirve, se sube otro."""
    if resultado not in ("conforme", "observado"):
        raise ValueError("Resultado de la revisión no válido")
    nota = " ".join(nota.split())[:500]
    if resultado == "observado" and not nota:
        raise ValueError("Indique qué se observó en el documento")
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT tipo, revisado_at FROM requerimiento_adjuntos WHERE id = ? AND audit_id = ?",
            (adjunto_id, audit_id),
        ).fetchone()
        if row is None or row["tipo"] not in REVISABLES:
            raise ValueError("El documento no pertenece a esta auditoría o no requiere revisión")
        if row["revisado_at"]:
            raise ValueError("Ese documento ya fue revisado; si no es válido, adjunte uno nuevo")
        conn.execute(
            """UPDATE requerimiento_adjuntos SET revision_resultado = ?, revision_nota = ?, revisado_por = ?,
                      revisado_at = ? WHERE id = ?""",
            (resultado, nota, user_id, now_iso(), adjunto_id),
        )
        _touch_audit(conn, audit_id)


def register_requerimiento_paquete(
    audit_id: int, numero: int, referencia: str, documentos: dict[str, tuple[str, bytes]],
    datos: dict[str, Any], user_id: int, *, db_path: Path | str = DB_PATH, adjuntos_dir: Path = ADJUNTOS_DIR,
) -> int:
    """Registra una generación completa: los cuatro documentos ya armados
    ({tipo: (nombre, contenido)}) con su instantánea de datos. Primero guarda
    los archivos y después inserta la generación y sus cuatro versiones en
    una sola transacción: si algo falla no queda ninguna fila ni archivo
    nuevo. La transacción serializa las escrituras para que el archivo no
    pueda ser referenciado por otra solicitud antes de confirmarse."""
    if set(documentos) != set(DOCUMENTOS):
        raise ValueError("Una generación debe tener los cuatro documentos")
    datos_json = json.dumps(datos, ensure_ascii=False, default=str)
    ts = now_iso()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        creados: list[Path] = []
        try:
            guardados = {}
            for tipo in DOCUMENTOS:
                nombre, contenido = documentos[tipo]
                ruta, sha256, nuevo = _guardar(audit_id, contenido, DOCUMENTOS[tipo][1], adjuntos_dir)
                if nuevo is not None:
                    creados.append(nuevo)
                guardados[tipo] = (nombre_seguro(nombre), ruta, sha256, len(contenido))
            try:
                paquete_id = conn.execute(
                    """INSERT INTO requerimiento_paquetes
                           (audit_id, numero, referencia, datos_json, generado_por, generado_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (audit_id, numero, referencia, datos_json, user_id, ts),
                ).lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError("Se registró otra generación al mismo tiempo: vuelva a generar") from exc
            for tipo, (nombre, ruta, sha256, tamano) in guardados.items():
                conn.execute(
                    """INSERT INTO requerimiento_archivos
                           (audit_id, paquete_id, tipo, version, nombre, ruta, sha256, bytes, datos_json,
                            generado_por, generado_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (audit_id, paquete_id, tipo, numero, nombre, ruta, sha256, tamano, datos_json, user_id, ts),
                )
            _touch_audit(conn, audit_id)
            return int(paquete_id)
        except BaseException:
            for archivo in creados:
                archivo.unlink(missing_ok=True)
            raise


def register_requerimiento_envio(
    audit_id: int, canal: str, destinatario: str, fecha: str, user_id: int, *,
    copia: str = "", asunto: str = "", paquete_id: int | None = None, evidencia_id: int | None = None,
    registro_historico: bool = False, justificacion_historica: str = "",
    db_path: Path | str = DB_PATH,
) -> int:
    """Registra un envío que el auditor ya hizo por su cuenta. Un correo
    indica qué generación se adjuntó: sus cuatro documentos quedan
    identificados como enviados (nunca una mezcla de generaciones) y no
    cambian aunque se regenere después."""
    if canal not in ("correo", "whatsapp"):
        raise ValueError("Canal no válido")
    destinatario = " ".join(destinatario.split())[:300]
    if not destinatario:
        raise ValueError("Indique el destinatario" if canal == "correo" else "Indique el grupo o contacto notificado")
    fecha = validar_fecha_envio(fecha)
    justificacion_historica = " ".join(justificacion_historica.split())[:500]
    if registro_historico and (canal != "correo" or len(justificacion_historica) < 15):
        raise ValueError("El registro histórico de correo requiere una justificación de al menos 15 caracteres")
    if not registro_historico:
        justificacion_historica = ""
    archivo_ids: list[int] = []
    with connect(db_path) as conn:
        if canal == "correo":
            if paquete_id is None or not conn.execute(
                "SELECT 1 FROM requerimiento_paquetes WHERE id = ? AND audit_id = ?", (paquete_id, audit_id),
            ).fetchone():
                raise ValueError("Indique la generación de documentos que se envió")
            filas = list(conn.execute(
                "SELECT id, tipo FROM requerimiento_archivos WHERE audit_id = ? AND paquete_id = ?",
                (audit_id, paquete_id),
            ))
            if sorted(f["tipo"] for f in filas) != sorted(DOCUMENTOS):
                raise ValueError("La generación indicada no tiene los cuatro documentos")
            archivo_ids = sorted(f["id"] for f in filas)
        else:
            paquete_id = None
            enviado = conn.execute(
                "SELECT 1 FROM requerimiento_envios WHERE audit_id = ? AND canal = 'correo'", (audit_id,),
            ).fetchone()
            if not enviado:
                raise ValueError("Registre primero el envío del correo")
        if evidencia_id is not None and not conn.execute(
            "SELECT 1 FROM requerimiento_adjuntos WHERE id = ? AND audit_id = ?", (evidencia_id, audit_id),
        ).fetchone():
            raise ValueError("La evidencia no pertenece a esta auditoría")
        cur = conn.execute(
            """INSERT INTO requerimiento_envios
                   (audit_id, canal, destinatario, copia, asunto, fecha, archivos_json, paquete_id, evidencia_id,
                    registro_historico, justificacion_historica, registrado_por, registrado_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, canal, destinatario, " ".join(copia.split())[:300], asunto.strip()[:300], fecha,
             json.dumps(archivo_ids), paquete_id, evidencia_id, int(registro_historico),
             justificacion_historica, user_id, now_iso()),
        )
        _touch_audit(conn, audit_id)
        return int(cur.lastrowid)


def save_requerimiento_importacion(
    audit_id: int, adjunto_id: int, estado: str, detalle: str, user_id: int, *,
    paquete_id: int | None = None, respuesta: dict | None = None, db_path: Path | str = DB_PATH,
) -> None:
    """Resultado de validar un Excel respondido ya guardado: "importado" (con
    sus respuestas, tal como llegaron), "rechazado" o "revision_manual". El
    archivo recibido se conserva en todos los casos."""
    if estado not in ("importado", "rechazado", "revision_manual"):
        raise ValueError("Estado de importación no válido")
    if (estado == "importado") != (respuesta is not None):
        raise ValueError("Solo un Excel importado registra respuestas")
    with connect(db_path) as conn:
        if not conn.execute(
            "SELECT 1 FROM requerimiento_adjuntos WHERE id = ? AND audit_id = ? AND tipo = 'solicitud_respondida'",
            (adjunto_id, audit_id),
        ).fetchone():
            raise ValueError("El Excel recibido no pertenece a esta auditoría")
        if paquete_id is not None and not conn.execute(
            "SELECT 1 FROM requerimiento_paquetes WHERE id = ? AND audit_id = ?", (paquete_id, audit_id),
        ).fetchone():
            raise ValueError("La generación no pertenece a esta auditoría")
        conn.execute(
            """UPDATE requerimiento_adjuntos SET importacion_estado = ?, importacion_detalle = ?, paquete_id = ?
               WHERE id = ?""",
            (estado, detalle[:1000], paquete_id, adjunto_id),
        )
        if respuesta is not None:
            conn.execute(
                """INSERT INTO requerimiento_respuestas
                       (audit_id, adjunto_id, items_json, detalles_json, importado_por, importado_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (audit_id, adjunto_id, json.dumps(respuesta["items"], ensure_ascii=False),
                 json.dumps(respuesta["detalles"], ensure_ascii=False), user_id, now_iso()),
            )
        _touch_audit(conn, audit_id)
