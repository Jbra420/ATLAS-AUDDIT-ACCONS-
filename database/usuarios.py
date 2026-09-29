"""database/usuarios.py — Usuarios, contraseñas, sesiones y token CSRF."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from database.base import DB_PATH, connect, now_iso


USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128


def _validar_clave(password: str, etiqueta: str) -> None:
    """Misma regla para toda clave nueva: la temporal y la que elige el usuario."""
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ValueError(f"{etiqueta} debe tener entre {PASSWORD_MIN} y {PASSWORD_MAX} caracteres")
    if password.strip() != password:
        raise ValueError(f"{etiqueta} no puede empezar ni terminar con espacios")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected_hash)


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(
    conn: sqlite3.Connection,
    username: str,
    full_name: str,
    role: str,
    password: str,
    *,
    must_change_password: bool = False,
) -> int:
    """Crea una cuenta. must_change_password obliga a cambiar la clave en el
    primer ingreso (clave inicial del jefe o temporal de un auditor)."""
    username = username.strip().lower()
    full_name = full_name.strip()
    if role not in {"admin", "auditor"}:
        raise ValueError("Rol no permitido")
    if not username or not full_name or not password:
        raise ValueError("Usuario, nombre y clave son obligatorios")
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("El usuario debe tener entre 3 y 32 caracteres y usar solo letras, números, punto, guion o guion bajo")
    _validar_clave(password, "La clave")

    existing = conn.execute(
        "SELECT deleted_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing:
        if existing["deleted_at"]:
            raise ValueError("El nombre de usuario pertenece a una cuenta histórica y no puede reutilizarse")
        raise ValueError("El nombre de usuario ya existe")

    salt, pw_hash = hash_password(password)
    try:
        cur = conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_salt, password_hash, active,
                               must_change_password, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (username, full_name, role, salt, pw_hash, int(must_change_password), now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("El nombre de usuario ya existe") from exc
    return int(cur.lastrowid)


def authenticate(username: str, password: str, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1 AND deleted_at IS NULL",
            (username.strip().lower(),),
        ).fetchone()
        if user and verify_password(password, user["password_salt"], user["password_hash"]):
            return user
        return None




def change_password(
    user_id: int,
    current_password: str,
    new_password: str,
    confirmation: str,
    keep_session_token: str = "",
    db_path: Path | str = DB_PATH,
) -> str:
    """Cambia la contraseña del propio usuario (cualquier rol). Exige la
    actual y cierra las demás sesiones abiertas de la cuenta; la sesión
    keep_session_token (la de quien hace el cambio) sigue activa."""
    if not current_password or not new_password:
        raise ValueError("Ingrese la contraseña actual y la nueva")
    if new_password != confirmation:
        raise ValueError("La nueva contraseña y su confirmación no coinciden")
    _validar_clave(new_password, "La nueva contraseña")
    with connect(db_path) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1 AND deleted_at IS NULL", (user_id,),
        ).fetchone()
        if user is None:
            raise ValueError("Usuario no disponible")
        if not verify_password(current_password, user["password_salt"], user["password_hash"]):
            raise ValueError("La contraseña actual no es correcta")
        if verify_password(new_password, user["password_salt"], user["password_hash"]):
            raise ValueError("La nueva contraseña debe ser distinta de la actual")
        salt, pw_hash = hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, must_change_password = 0 WHERE id = ?",
            (salt, pw_hash, user_id),
        )
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, session_hash(keep_session_token) if keep_session_token else ""),
        )
    return "Contraseña actualizada. Se cerraron las demás sesiones de su cuenta."


def list_users(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            """
            SELECT u.id, u.username, u.full_name, u.role, u.active, u.created_at,
                   u.deleted_at, u.deleted_by, u.deletion_reason,
                   actor.full_name AS deleted_by_name
            FROM users u
            LEFT JOIN users actor ON actor.id = u.deleted_by
            ORDER BY u.role, u.deleted_at IS NOT NULL, u.active DESC, u.full_name
            """
        ))


def list_auditors(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            """
            SELECT id, username, full_name
            FROM users
            WHERE role = 'auditor' AND active = 1 AND deleted_at IS NULL
            ORDER BY full_name
            """
        ))


def _admin_user_action(
    conn: sqlite3.Connection,
    user_id: int,
    performed_by: int,
) -> sqlite3.Row:
    actor = conn.execute(
        "SELECT id, role, active, deleted_at FROM users WHERE id = ?",
        (performed_by,),
    ).fetchone()
    if actor is None or actor["role"] != "admin" or actor["active"] != 1 or actor["deleted_at"]:
        raise ValueError("Solo un administrador activo puede gestionar usuarios")
    if user_id == performed_by:
        raise ValueError("No puedes cambiar el estado de tu propio usuario")

    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        raise ValueError("Usuario no encontrado")
    return target


def deactivate_user(
    user_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> str:
    """Suspende el acceso de una cuenta sin alterar su historial ni asignaciones."""
    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("El usuario ya tiene baja definitiva")
        if target["active"] != 1:
            raise ValueError("El usuario ya está inactivo")
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return "Usuario desactivado. Sus empresas y su historial se conservaron."


def reactivate_user(
    user_id: int,
    performed_by: int,
    db_path: Path | str = DB_PATH,
) -> str:
    """Restablece el acceso de una cuenta suspendida, pero nunca de una cuenta dada de baja."""
    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("Una cuenta con baja definitiva no puede reactivarse")
        if target["active"] == 1:
            raise ValueError("El usuario ya está activo")
        conn.execute("UPDATE users SET active = 1 WHERE id = ?", (user_id,))
    return "Usuario reactivado. Deberá iniciar una sesión nueva."


def soft_delete_user(
    user_id: int,
    performed_by: int,
    deletion_reason: str,
    db_path: Path | str = DB_PATH,
) -> str:
    """Registra una baja definitiva sin borrar la cuenta ni sus relaciones históricas."""
    reason = deletion_reason.strip()
    if len(reason) < 5:
        raise ValueError("Indica un motivo de baja de al menos 5 caracteres")
    if len(reason) > 250:
        raise ValueError("El motivo de baja no puede superar los 250 caracteres")

    with connect(db_path) as conn:
        target = _admin_user_action(conn, user_id, performed_by)
        if target["deleted_at"]:
            raise ValueError("El usuario ya tiene baja definitiva")
        if target["active"] == 1:
            raise ValueError("Primero debes desactivar al usuario antes de darle de baja")
        conn.execute(
            """
            UPDATE users
            SET active = 0, deleted_at = ?, deleted_by = ?, deletion_reason = ?
            WHERE id = ?
            """,
            (now_iso(), performed_by, reason, user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return "Baja definitiva registrada. Las empresas y el historial del usuario se conservaron."


def create_session(user_id: int, db_path: Path | str = DB_PATH) -> str:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_hex(24)  # 48-char hex CSRF token vinculado a la sesión
    created_at = now_iso()
    expires_at = (datetime.now() + timedelta(hours=8)).replace(microsecond=0).isoformat(sep=" ")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, csrf_token) VALUES (?, ?, ?, ?, ?)",
            (session_hash(token), user_id, created_at, expires_at, csrf),
        )
    return token


def get_csrf_token(session_token: str | None, db_path: Path | str = DB_PATH) -> str:
    """Retorna el CSRF token asociado a la sesión activa. Cadena vacía si no hay sesión."""
    if not session_token:
        return ""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT csrf_token FROM sessions WHERE token_hash = ? AND expires_at >= ?",
            (session_hash(session_token), now_iso()),
        ).fetchone()
    return row["csrf_token"] if row else ""


def validate_csrf_token(
    session_token: str | None,
    submitted_csrf: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    """Valida el CSRF token enviado en un formulario contra el almacenado en la sesión.

    Usa hmac.compare_digest para evitar timing attacks.
    """
    if not session_token or not submitted_csrf:
        return False
    expected = get_csrf_token(session_token, db_path)
    if not expected:
        return False
    return hmac.compare_digest(expected, submitted_csrf)


def user_from_session(token: str | None, db_path: Path | str = DB_PATH) -> sqlite3.Row | None:
    if not token:
        return None
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        return conn.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND u.active = 1 AND u.deleted_at IS NULL
              AND s.expires_at >= ?
            """,
            (session_hash(token), now_iso()),
        ).fetchone()


def destroy_session(token: str | None, db_path: Path | str = DB_PATH) -> None:
    if not token:
        return
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session_hash(token),))
