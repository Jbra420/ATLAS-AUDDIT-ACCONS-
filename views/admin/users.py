"""
views/admin/users.py — Gestión de usuarios del sistema Atlas.
"""
from __future__ import annotations

import sqlite3

from database import list_users
from ui.components import modal
from ui.helpers import esc, form_value, csrf_input, hidden_inputs
from ui.icons import SVG_ALERT, SVG_PAUSE, SVG_REFRESH, SVG_TRASH
from ui.layout import layout


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
    """Genera el HTML de la página de administración de usuarios."""
    users = list_users()
    err = form_value(query, "err")

    rows_html = ""
    for u in users:
        is_self = u["id"] == user["id"]
        is_deleted = bool(u["deleted_at"])

        if is_deleted:
            deleted_date = esc(u["deleted_at"][:16])
            deleted_by = esc(u["deleted_by_name"] or "Administrador")
            reason = esc(u["deletion_reason"] or "Sin motivo registrado")
            status_html = f"""
              <span class="badge badge-red">Baja definitiva</span>
              <span class="user-state-meta" title="{reason}">{deleted_date} · por {deleted_by}</span>
            """
            actions_html = '<span class="user-no-actions" title="Registro histórico sin acciones">—</span>'
        elif u["active"]:
            status_html = '<span class="badge badge-green">Activo</span>'
            actions_html = ""
            if not is_self:
                actions_html = f"""
                  <button type="button" class="user-action-btn user-action-warning"
                          data-user-id="{u['id']}" data-user-name="{esc(u['full_name'])}"
                          title="Desactivar usuario" aria-label="Desactivar usuario"
                          onclick="openDeactivateModal(this)">{SVG_PAUSE}</button>
                """
        else:
            status_html = """
              <span class="badge badge-amber">Inactivo</span>
              <span class="user-state-meta">Acceso suspendido; historial conservado</span>
            """
            actions_html = ""
            if not is_self:
                actions_html = f"""
                  <form method="post" action="/admin/users/reactivate" class="user-inline-form">
                    {hidden_inputs(csrf_token, user_id=u['id'])}
                    <button type="submit" class="user-action-btn user-action-success"
                            title="Reactivar usuario" aria-label="Reactivar usuario">{SVG_REFRESH}</button>
                  </form>
                  <button type="button" class="user-action-btn user-action-danger"
                          data-user-id="{u['id']}" data-user-name="{esc(u['full_name'])}"
                          title="Registrar baja definitiva" aria-label="Registrar baja definitiva"
                          onclick="openDeleteModal(this)">{SVG_TRASH}</button>
                """

        rows_html += f"""<tr>
          <td><strong>{esc(u['full_name'])}</strong></td>
          <td><code style="font-size:13px; background:var(--line-2); padding:2px 6px; border-radius:4px;">{esc(u['username'])}</code></td>
          <td>{'<span class="badge badge-blue">Jefe auditor</span>' if u['role'] == 'admin' else '<span class="badge badge-gray">Auditor</span>'}</td>
          <td>{status_html}</td>
          <td><div class="user-actions">{actions_html}</div></td>
        </tr>"""

    modal_html = modal("deactivateModal", "Desactivar usuario", f"""
        <div class="modal-desc">
          Se suspenderá el acceso de <strong id="deactivateUserName"></strong> y se cerrarán sus sesiones.
          Sus empresas, expedientes y registros se conservarán sin cambios.
        </div>
        <form method="post" action="/admin/users/deactivate" style="margin:0;">
          {csrf_input(csrf_token)}
          <input type="hidden" name="user_id" id="deactivateUserId">
          <div class="modal-actions">
            <button type="button" class="btn" onclick="closeModal('deactivateModal')">Cancelar</button>
            <button type="submit" class="btn user-confirm-warning">Desactivar</button>
          </div>
        </form>
    """) + modal("deleteModal", "Registrar baja definitiva", f"""
        <div class="modal-desc">
          <strong id="deleteUserName"></strong> no podrá reactivarse. La cuenta no se borrará:
          sus empresas, expedientes y autoría permanecerán en el historial.
        </div>
        <form method="post" action="/admin/users/delete" style="margin:0;">
          {csrf_input(csrf_token)}
          <input type="hidden" name="user_id" id="deleteUserId">
          <label for="deletionReason">Motivo de la baja *</label>
          <textarea id="deletionReason" name="deletion_reason" minlength="5" maxlength="250"
                    required placeholder="Ej. Finalización de relación laboral"></textarea>
          <div class="modal-actions">
            <button type="button" class="btn" onclick="closeModal('deleteModal')">Cancelar</button>
            <button type="submit" class="btn user-confirm-danger">Confirmar baja</button>
          </div>
        </form>
    """) + """
    <script>
    function openDeactivateModal(button) {
      document.getElementById('deactivateUserId').value = button.dataset.userId;
      document.getElementById('deactivateUserName').textContent = button.dataset.userName;
      openModal('deactivateModal');
    }
    function openDeleteModal(button) {
      document.getElementById('deleteUserId').value = button.dataset.userId;
      document.getElementById('deleteUserName').textContent = button.dataset.userName;
      document.getElementById('deletionReason').value = '';
      openModal('deleteModal');
    }
    </script>
    """

    content = f"""
    <div class="mb-16">
      <h1 class="page-title">Usuarios</h1>
      <p class="page-subtitle muted">Administración de cuentas y accesos al sistema.</p>
    </div>
    {'<div class="error-msg toast">' + SVG_ALERT + ' ' + esc(err) + '</div>' if err else ''}

    <div class="grid">
      <div class="panel col-4">
        <h2>Nuevo auditor</h2>
        <form method="post" action="/admin/users">
          {csrf_input(csrf_token)}
          <label for="full_name">Nombre completo</label>
          <input id="full_name" name="full_name" required placeholder="Ej. María García">
          <label for="username_new">Usuario</label>
          <input id="username_new" name="username" required placeholder="Ej. mgarcia"
                 autocomplete="off" minlength="3" maxlength="32" pattern="[A-Za-z0-9_.-]{{3,32}}"
                 title="Use entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.">
          <label for="new_password">Contraseña temporal</label>
          <input id="new_password" name="password" type="password" required
                 minlength="6" autocomplete="new-password">
          <div class="field-hint">El auditor la cambia desde "Mi cuenta" al ingresar.</div>
          <div class="actions" style="margin-top:24px;">
            <button class="btn btn-primary" type="submit" style="width:100%">Crear auditor</button>
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
