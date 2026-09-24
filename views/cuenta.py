"""
views/cuenta.py — Mi cuenta: datos del usuario y cambio de su propia contraseña.
Disponible para todos los roles.
"""
from __future__ import annotations

import sqlite3

from database.usuarios import PASSWORD_MAX, PASSWORD_MIN
from ui.helpers import csrf_input, esc, form_value
from ui.icons import SVG_ALERT
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
    err = form_value(query, "err")
    rol = "Jefe auditor" if user["role"] == "admin" else "Auditor"
    content = f"""
    <div class="mb-16">
      <h1 class="page-title">Mi cuenta</h1>
      <p class="page-subtitle muted">Sus datos de acceso a Atlas.</p>
    </div>
    {'<div class="error-msg toast">' + SVG_ALERT + ' ' + esc(err) + '</div>' if err else ''}

    <div class="grid">
      <div class="panel col-4">
        <h2>Datos</h2>
        <label>Nombre</label>
        <p>{esc(user["full_name"])}</p>
        <label>Usuario</label>
        <p><code>{esc(user["username"])}</code></p>
        <label>Rol</label>
        <p>{rol}</p>
      </div>
      <div class="panel col-8">
        <h2>Cambiar contraseña</h2>
        <form method="post" action="/cuenta/password">
          {csrf_input(csrf_token)}
          <input type="text" name="username" value="{esc(user["username"])}" autocomplete="username" hidden>
          <label for="current_password">Contraseña actual *</label>
          <input id="current_password" name="current_password" type="password" required
                 autocomplete="current-password">
          <label for="new_password">Nueva contraseña *</label>
          <input id="new_password" name="new_password" type="password" required
                 minlength="{PASSWORD_MIN}" maxlength="{PASSWORD_MAX}" autocomplete="new-password">
          <div class="field-hint">Entre {PASSWORD_MIN} y {PASSWORD_MAX} caracteres, distinta de la actual.</div>
          <label for="confirm_password">Confirmar nueva contraseña *</label>
          <input id="confirm_password" name="confirm_password" type="password" required
                 minlength="{PASSWORD_MIN}" maxlength="{PASSWORD_MAX}" autocomplete="new-password">
          <div class="actions" style="margin-top:24px;">
            <button class="btn btn-primary" type="submit">Cambiar contraseña</button>
          </div>
          <div class="field-hint">Se cerrarán las sesiones abiertas de su cuenta en otros equipos.</div>
        </form>
      </div>
    </div>
    """
    return layout("Mi cuenta", user, content, form_value(query, "msg"), active_path=active_path)
