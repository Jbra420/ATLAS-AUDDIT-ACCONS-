"""views/auditor/radar/tab_accionistas.py — Tab de accionistas de la empresa.

Este tab es de solo visualización (no contiene formularios de edición);
el parámetro read_only se acepta por consistencia con el resto de tabs.
"""
from __future__ import annotations
from ui.helpers import esc
from ui.components import people_avatar as _avatar


def build(audit_id: int, audit, shareholders: list, *, read_only: bool = False) -> str:  # noqa: ARG001
    """Genera el HTML del tab de accionistas.

    Args:
        audit_id: ID de la auditoría activa.
        audit: Fila de la auditoría.
        shareholders: Lista de accionistas registrados.
        read_only: Si True, el usuario es jefe auditor (modo solo lectura).
                   Este tab no tiene formularios, por lo que el valor no altera el HTML.
    """
    rows = "".join(
        f'<tr>'
        f'<td style="font-size:12px;color:var(--muted);width:40px;">{esc(str(s["numero"] or "—"))}</td>'
        f'<td>{_avatar(s["nombre"])}{esc(s["nombre"])}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(s["identificacion"] or "—")}</td>'
        f'</tr>'
        for s in shareholders
    ) if shareholders else (
        '<tr><td colspan="3" style="color:var(--muted-2);font-style:italic;">'
        'Sin accionistas registrados. Consulte Supercias.</td></tr>'
    )
    return f"""
    <h3 style="margin:0 0 16px;font-size:15px;">Nómina de socios / accionistas</h3>
    <table class="people-table">
      <thead><tr><th>#</th><th>Nombre</th><th>Identificación</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """
