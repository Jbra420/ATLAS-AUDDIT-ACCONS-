"""views/auditor/radar/tab_admins.py — Tab de administradores de la empresa.

Este tab es de solo visualización (no contiene formularios de edición);
el parámetro read_only se acepta por consistencia con el resto de tabs.
"""
from __future__ import annotations
from ui.helpers import esc


def _avatar(name: str) -> str:
    initials = "".join(p[0] for p in name.strip().split()[:2]).upper()
    return f'<span class="people-avatar">{esc(initials)}</span>'


def build(audit_id: int, audit, admins: list, *, read_only: bool = False) -> str:  # noqa: ARG001
    """Genera el HTML del tab de administradores.

    Args:
        audit_id: ID de la auditoría activa.
        audit: Fila de la auditoría.
        admins: Lista de administradores registrados.
        read_only: Si True, el usuario es jefe auditor (modo solo lectura).
                   Este tab no tiene formularios, por lo que el valor no altera el HTML.
    """
    rows = "".join(
        f'<tr><td>{_avatar(a["nombre"])}{esc(a["nombre"])}</td>'
        f'<td><span class="cargo-badge">{esc(a["cargo"])}</span></td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["identificacion"] or "—")}</td>'
        f'<td style="font-size:13px;color:var(--muted);">{esc(a["nacionalidad"])}</td></tr>'
        for a in admins
    ) if admins else (
        '<tr><td colspan="4" style="color:var(--muted-2);font-style:italic;">'
        'Sin administradores registrados. Consulte Supercias.</td></tr>'
    )
    return f"""
    <h3 style="margin:0 0 16px;font-size:15px;">Administradores registrados</h3>
    <table class="people-table">
      <thead><tr><th>Nombre</th><th>Cargo</th><th>Identificación</th><th>Nacionalidad</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """
