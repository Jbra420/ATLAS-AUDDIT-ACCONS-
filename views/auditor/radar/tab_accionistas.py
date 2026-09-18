"""Tab de captura y consulta de socios o accionistas."""
from __future__ import annotations
from ui.helpers import csrf_input, esc
from ui.components import people_avatar as _avatar
from ui.icons import SVG_SAVE, SVG_TRASH


def build(
    audit_id: int,
    audit,
    shareholders: list,
    *,
    read_only: bool = False,
    csrf_token: str = "",
) -> str:  # noqa: ARG001
    rows = "".join(
        f'<tr>'
        f'<td style="font-size:12px;color:var(--muted);width:40px;">{esc(str(s["numero"] or "—"))}</td>'
        f'<td>{_avatar(s["nombre"])}{esc(s["nombre"])}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(s["identificacion"] or "—")}</td>'
        + ("" if read_only else f'''
        <td class="people-table-actions">
          <form method="post" action="/auditor/radar/shareholder">
            {csrf_input(csrf_token)}
            <input type="hidden" name="audit_id" value="{audit_id}">
            <input type="hidden" name="shareholder_id" value="{s['id']}">
            <input type="hidden" name="action" value="delete">
            <button type="submit" class="btn-icon-danger" title="Eliminar accionista" aria-label="Eliminar accionista">{SVG_TRASH}</button>
          </form>
        </td>''')
        + f'</tr>'
        for s in shareholders
    ) if shareholders else (
        f'<tr><td colspan="{3 if read_only else 4}" style="color:var(--muted-2);font-style:italic;">'
        'Sin accionistas registrados. Consulte Supercias.</td></tr>'
    )
    actions_header = "" if read_only else "<th>Acciones</th>"
    editor = "" if read_only else f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Captura societaria</span>
        <h4>Registrar socio o accionista</h4>
      </div>
      <form method="post" action="/auditor/radar/shareholder">
        {csrf_input(csrf_token)}
        <input type="hidden" name="audit_id" value="{audit_id}">
        <input type="hidden" name="action" value="add">
        <div class="grid">
          <div class="col-3"><label>Número</label><input name="numero" type="number" min="1" placeholder="Automático"></div>
          <div class="col-4"><label>Identificación</label><input name="identificacion" maxlength="32" placeholder="Cédula, RUC o pasaporte"></div>
          <div class="col-5"><label>Nombre completo *</label><input name="nombre" maxlength="160" required></div>
        </div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar</button>
        </div>
      </form>
    </section>
    """
    return f"""
    <div class="people-section-head">
      <div><h3>Nómina de socios / accionistas</h3><p>{len(shareholders)} registro(s) vinculados al expediente.</p></div>
      {'<span class="badge badge-gray">Modo solo lectura</span>' if read_only else ''}
    </div>
    <div class="table-wrap">
      <table class="people-table">
        <thead><tr><th>#</th><th>Nombre</th><th>Identificación</th>{actions_header}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {editor}
    """
