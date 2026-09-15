"""
views/admin/users.py — Gestión de usuarios del sistema Atlas.
"""
from __future__ import annotations

import sqlite3

from database import list_users
from ui.helpers import esc, form_value, csrf_input
from ui.icons import SVG_ALERT, SVG_TRASH
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
    """Genera el HTML de la página de administración de usuarios."""
    users = list_users()
    err = form_value(query, "err")

    rows_html = ""
    for u in users:
        is_self = u["id"] == user["id"]
        delete_btn = f"""
            <button type="button" class="btn btn-sm btn-outline" style="color:var(--red-600); border-color:var(--red-200); padding:4px 8px;" title="Eliminar" onclick="openDeleteModal({u['id']}, '{esc(u['full_name'])}')">
              {SVG_TRASH}
            </button>
        """ if not is_self else ""
        
        rows_html += f"""<tr>
          <td><strong>{esc(u['full_name'])}</strong></td>
          <td><code style="font-size:13px; background:var(--line-2); padding:2px 6px; border-radius:4px;">{esc(u['username'])}</code></td>
          <td>{'<span class="badge badge-blue">Jefe auditor</span>' if u['role'] == 'admin' else '<span class="badge badge-gray">Auditor</span>'}</td>
          <td>{'<span class="badge badge-green">Activo</span>' if u['active'] else '<span class="badge badge-red">Inactivo</span>'}</td>
          <td style="text-align:right;">{delete_btn}</td>
        </tr>"""

    modal_html = """
    <div id="deleteModal" class="modal-overlay">
      <div class="modal-content">
        <div class="modal-title">Eliminar usuario</div>
        <div class="modal-desc">¿Estás seguro de que deseas eliminar a <strong id="deleteUserName"></strong>? Esta acción es permanente y no se puede deshacer.</div>
        <form method="post" action="/admin/users/delete" style="margin:0;">
          {csrf_input(csrf_token)}
          <input type="hidden" name="user_id" id="deleteUserId">
          <div class="modal-actions">
            <button type="button" class="btn btn-outline" onclick="closeDeleteModal()">Cancelar</button>
            <button type="submit" class="btn" style="background:var(--red-600);color:white;border:none;">Sí, eliminar</button>
          </div>
        </form>
      </div>
    </div>
    <script>
    function openDeleteModal(id, name) {
      document.getElementById('deleteUserId').value = id;
      document.getElementById('deleteUserName').textContent = name;
      document.getElementById('deleteModal').classList.add('active');
    }
    function closeDeleteModal() {
      document.getElementById('deleteModal').classList.remove('active');
    }
    </script>
    """

    content = f"""
    <div class="mb-16">
      <h1 class="page-title">Usuarios</h1>
      <p class="page-subtitle muted">Administración de cuentas y accesos al sistema.</p>
    </div>
    {'<div class="error-msg">' + SVG_ALERT + ' ' + esc(err) + '</div>' if err else ''}

    <div class="grid">
      <div class="panel col-4">
        <h2>Nuevo usuario</h2>
        <form method="post" action="/admin/users">
          {csrf_input(csrf_token)}
          <label for="full_name">Nombre completo</label>
          <input id="full_name" name="full_name" required placeholder="Ej. María García">
          <label for="username_new">Usuario</label>
          <input id="username_new" name="username" required placeholder="Ej. mgarcia"
                 autocomplete="off" minlength="3" maxlength="32" pattern="[A-Za-z0-9_.-]{3,32}"
                 title="Use entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.">
          <label for="role_sel">Rol</label>
          <select id="role_sel" name="role">
            <option value="auditor">Auditor</option>
            <option value="admin">Administrador / Jefe</option>
          </select>
          <label for="new_password">Contraseña temporal</label>
          <input id="new_password" name="password" type="password" required
                 minlength="6" autocomplete="new-password">
          <div class="actions" style="margin-top:24px;">
            <button class="btn btn-primary" type="submit" style="width:100%">Crear usuario</button>
          </div>
        </form>
      </div>
      <div class="panel col-8">
        <h2>Usuarios registrados ({len(users)})</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Nombre</th><th>Usuario</th><th>Rol</th><th>Estado</th><th style="text-align:right;">Acciones</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="5" style="color:var(--muted);text-align:center;">Sin usuarios.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>
    {modal_html}
    """

    flash = form_value(query, "msg")
    return layout("Usuarios", user, content, flash, active_path=active_path)
