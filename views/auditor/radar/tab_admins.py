"""Tab de captura y consulta de administradores de la empresa."""
from __future__ import annotations
from ui.helpers import csrf_input, esc
from ui.components import people_avatar as _avatar
from ui.icons import SVG_SAVE, SVG_TRASH


def build(
    audit_id: int,
    audit,
    admins: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
) -> str:  # noqa: ARG001
    rows = "".join(
        f'<tr><td>{_avatar(a["nombre"])}{esc(a["nombre"])}</td>'
        f'<td><span class="cargo-badge">{esc(a["cargo"])}</span></td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["identificacion"] or "—")}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["nacionalidad"] or "—")}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          <form method="post" action="/auditor/radar/administrator">
            {csrf_input(csrf_token)}
            <input type="hidden" name="audit_id" value="{audit_id}">
            <input type="hidden" name="administrator_id" value="{a['id']}">
            <input type="hidden" name="action" value="delete">
            <button type="submit" class="btn-icon-danger" title="Eliminar administrador" aria-label="Eliminar administrador">{SVG_TRASH}</button>
          </form>
        </td>''')
        + '</tr>'
        for a in admins
    ) if admins else (
        f'<tr><td colspan="{4 if read_only else 5}" style="color:var(--muted-2);font-style:italic;">'
        'Sin administradores registrados. Consulte Supercias.</td></tr>'
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria</span>
        <h4>Registrar administrador</h4>
      </div>
      <form method="post" action="/auditor/radar/administrator">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="action" value="add">
        <div class="grid">
          <div class="col-3"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="Cédula o pasaporte"></div>
          <div class="col-3"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
          <div class="col-3"><label>Cargo *</label><input name="cargo" maxlength="120" required placeholder="Gerente general"></div>
          <div class="col-3"><label>Nacionalidad</label><input name="nacionalidad" maxlength="80"></div>
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    return f"""
    <div class="people-section-head">
      <div><h3>Administradores registrados</h3><p>{len(admins)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>Nombre</th><th>Cargo</th><th>Identificación</th><th>Nacionalidad</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {editor}
    """
