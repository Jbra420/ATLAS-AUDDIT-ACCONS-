"""Tab de captura y consulta de administradores de la empresa."""
from __future__ import annotations
from services.rowutil import row_get
from services.trazabilidad import BLOQUE_ADMINISTRADORES
from views.auditor.radar.certificado import assisted_panel as assisted_panel_html
from ui.helpers import hidden_inputs, esc
from ui.components import (
    fecha_consulta_field,
    fuente_text as _fuente_text,
    identificacion_select as _tipo_select,
    identificacion_text as _identificacion_text,
    people_avatar as _avatar,
    provenance_history,
)
from ui.icons import SVG_CROSS, SVG_SAVE, SVG_TRASH


def _complete_form(audit_id: int, a, csrf_token: str) -> str:
    """Formulario por fila para completar identificación y nacionalidad usando el Popover API nativo
    como un hermoso modal flotante e interactivo."""
    popover_id = f"edit-admin-{a['id']}"
    initials = "".join(p[0] for p in a["nombre"].split()[:2] if p)

    return f"""
    <!-- Botón disparador del Popover nativo -->
    <button type="button" popovertarget="{popover_id}" class="btn btn-sm" style="color: var(--accent-base); border-color: var(--accent-base); background: var(--accent-light);">
      {SVG_SAVE} Completar
    </button>

    <!-- Modal Popover -->
    <div popover="auto" id="{popover_id}" class="admin-popover">
      <div class="admin-popover-card">
        <div class="admin-popover-header">
          <div class="admin-popover-avatar">{esc(initials)}</div>
          <div class="admin-popover-title">
            <strong>{esc(a["nombre"])}</strong>
            <span class="cargo-badge" style="margin-top: 4px;">{esc(a["cargo"])}</span>
          </div>
          <button type="button" class="admin-popover-close" popovertarget="{popover_id}" popovertargetaction="hide" aria-label="Cerrar">
            {SVG_CROSS}
          </button>
        </div>

        <form method="post" action="/auditor/radar/administrator">
          {hidden_inputs(csrf_token, audit_id=audit_id, administrator_id=a['id'], action="update")}

          <div style="margin-bottom: 12px;">
            <label style="margin-top:0;">Tipo de identificación</label>
            {_tipo_select(row_get(a, "tipo_identificacion") or "cedula")}
          </div>

          <div class="grid" style="gap: 16px; margin-bottom: 12px;">
            <div class="col-6">
              <label style="margin-top:0;">Identificación</label>
              <input name="identificacion" maxlength="32" value="{esc(row_get(a, 'identificacion') or '')}" placeholder="Número...">
            </div>
            <div class="col-6">
              <label style="margin-top:0;">Nacionalidad</label>
              <input name="nacionalidad" maxlength="80" value="{esc(row_get(a, 'nacionalidad') or '')}" placeholder="Ej. Ecuatoriana">
            </div>
          </div>

          <div style="margin-bottom: 24px;">
            {fecha_consulta_field("")}
          </div>

          <div class="actions" style="justify-content: flex-end;">
            <button type="button" class="btn" popovertarget="{popover_id}" popovertargetaction="hide">Cancelar</button>
            <button type="submit" class="btn btn-primary">{SVG_SAVE} Guardar cambios</button>
          </div>
        </form>
      </div>
    </div>
    """


def build(
    audit_id: int,
    audit,
    admins: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
    sources: list | None = None,
    provenance: list | None = None,
    certificado: dict | None = None,
) -> str:
    rows = "".join(
        f'<tr><td>{_avatar(a["nombre"])}{esc(a["nombre"])}</td>'
        f'<td><span class="cargo-badge">{esc(a["cargo"])}</span></td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(_identificacion_text(a))}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["nacionalidad"] or "—")}</td>'
        f'<td style="font-size:12px;color:var(--muted);">{esc(_fuente_text(a))}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          {_complete_form(audit_id, a, csrf_token)}
          <form method="post" action="/auditor/radar/administrator">
            {hidden_inputs(csrf_token, audit_id=audit_id, administrator_id=a['id'], action="delete")}
            <button type="submit" class="btn-icon-danger" title="Eliminar administrador" aria-label="Eliminar administrador">{SVG_TRASH}</button>
          </form>
        </td>''')
        + '</tr>'
        for a in admins
    ) if admins else (
        f'<tr><td colspan="{5 if read_only else 6}" style="color:var(--muted-2);font-style:italic;">'
        'Sin administradores registrados. Consulte Supercias.</td></tr>'
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria — Supercias, Administradores actuales</span>
        <h4>Registrar administrador</h4>
      </div>
      <p style="font-size:13px;color:var(--muted);margin:0 0 12px;">
        Se requiere al menos el gerente general y el presidente, cada uno con su identificación.
      </p>
      <form method="post" action="/auditor/radar/administrator">
        {hidden_inputs(csrf_token, audit_id=audit_id, action="add")}
        <div class="grid">
          <div class="col-4"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
          <div class="col-4"><label>Cargo *</label><input name="cargo" maxlength="120" required placeholder="Gerente general"></div>
          <div class="col-4"><label>Nacionalidad</label><input name="nacionalidad" maxlength="80"></div>
          <div class="col-4"><label>Tipo de identificación</label>{_tipo_select("cedula")}</div>
          <div class="col-4"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="10 dígitos si es cédula"></div>
          {fecha_consulta_field()}
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    assisted_panel = "" if read_only else assisted_panel_html(
        audit_id, audit, sources or [], "admins", csrf_token, certificado,
    )
    historial = [r for r in provenance or [] if row_get(r, "bloque") == BLOQUE_ADMINISTRADORES]
    return f"""
    {assisted_panel}
    <div class="people-section-head">
      <div><h3>Administradores registrados</h3><p>{len(admins)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>Nombre</th><th>Cargo</th><th>Identificación</th><th>Nacionalidad</th><th>Fuente</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {editor}
    {provenance_history(historial, {}, "Historial de administradores")}
    """
