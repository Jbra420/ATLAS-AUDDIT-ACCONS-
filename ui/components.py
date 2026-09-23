"""
ui/components.py — Componentes HTML reutilizables de la interfaz Atlas.
Depende de ui/icons y ui/helpers únicamente. Nunca importa de views/.
"""
from __future__ import annotations

from datetime import date

from database import AUDIT_STATUSES
from services.identificacion import TIPOS_IDENTIFICACION
from services.ruc_validator import validate_ruc
from services.rowutil import row_get
from ui.helpers import esc, hidden_inputs
from ui.icons import (
    SVG_ALERT,
    SVG_CHECK,
    SVG_CIRCLE,
    SVG_CLOCK,
    SVG_CROSS,
    SVG_INFO,
    SVG_SAVE,
)


def badge(status: str) -> str:
    """Genera un badge HTML coloreado para un estado de auditoría."""
    css_map = {
        "pendiente": "badge-gray",
        "en_investigacion": "badge-amber",
    }
    icon_map = {
        "pendiente": SVG_CIRCLE,
        "en_investigacion": SVG_CLOCK,
    }
    css = css_map.get(status, "badge-gray")
    icon = icon_map.get(status, SVG_CIRCLE)
    label = AUDIT_STATUSES.get(status, status)
    return f'<span class="badge {css}">{icon} {esc(label)}</span>'


def avatar_initials(name: str) -> str:
    """Extrae las iniciales de un nombre completo para mostrar en el avatar."""
    parts = (name or "?").split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "?"


def identificacion_select(current: str, permitidos: tuple[str, ...] = ("cedula", "pasaporte")) -> str:
    """Selector del tipo de identificación de administradores y accionistas."""
    options = "".join(
        f'<option value="{tipo}"{" selected" if current == tipo else ""}>{TIPOS_IDENTIFICACION[tipo]}</option>'
        for tipo in permitidos
    )
    return f'<select name="tipo_identificacion">{options}</select>'


def identificacion_text(row) -> str:
    """"Cédula 0102030405", o "Pendiente" si la identificación no se registró."""
    identificacion = (row_get(row, "identificacion") or "").strip()
    if not identificacion or identificacion in {"-", "—"}:
        return "Pendiente"
    tipo = TIPOS_IDENTIFICACION.get(row_get(row, "tipo_identificacion") or "", "")
    return f"{tipo} {identificacion}".strip()


def fuente_text(row) -> str:
    """"Fuente · fecha de consulta" de un registro de persona."""
    partes = ((row_get(row, "fuente") or "").strip(), (row_get(row, "fecha_consulta") or "").strip())
    return " · ".join(p for p in partes if p) or "Sin fuente registrada"


def people_avatar(name: str) -> str:
    """Avatar circular con iniciales para filas de tablas de personas (admins, accionistas)."""
    initials = "".join(p[0] for p in (name or "").strip().split()[:2]).upper()
    return f'<span class="people-avatar">{esc(initials)}</span>'


def info_card(label: str, value: str, css_extra: str = "", trace: str = "") -> str:
    """Tarjeta de dato de solo lectura usada en los tabs del Radar Empresarial (SRI, Supercias, etc.).

    trace es el texto "fuente · fecha de consulta" del dato (ver
    services/trazabilidad.etiqueta_traza); se omite si está vacío.
    """
    v = value.strip() if value else ""
    val_cls = "info-card-value" if v else "info-card-value pending"
    val_text = esc(v) if v else "Pendiente de confirmar"
    trace_html = f'<div class="info-card-trace">{esc(trace)}</div>' if trace and v else ""
    return (
        f'<div class="info-card {css_extra}">'
        f'<div class="info-card-label">{esc(label)}</div>'
        f'<div class="{val_cls}">{val_text}</div>'
        f'{trace_html}'
        f'</div>'
    )


def fecha_consulta_field(css_col: str = "col-4") -> str:
    """Campo "Fecha de consulta" de los formularios de captura: fecha en que el
    auditor revisó la fuente oficial. Por defecto hoy; no admite fechas futuras
    (el servidor lo vuelve a validar)."""
    today = date.today().isoformat()
    return (
        f'<div class="{css_col}"><label>Fecha de consulta de la fuente *</label>'
        f'<input type="date" name="fecha_consulta" value="{today}" max="{today}" required></div>'
    )


def modal(modal_id: str, title: str, content_html: str, *, wide: bool = False) -> str:
    """Diálogo modal oculto; se abre con openModal('<modal_id>') (ver ui/layout.py).

    content_html lleva la descripción, el formulario y los botones
    (.modal-desc, .modal-actions).
    """
    return f"""
    <div id="{modal_id}" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="{modal_id}Title">
      <div class="modal-content{' modal-wide' if wide else ''}">
        <div class="modal-title" id="{modal_id}Title">{esc(title)}</div>
        {content_html}
      </div>
    </div>
    """


def form_field(
    name: str, label: str, value: object = "", *, col: str = "col-6",
    placeholder: str = "", textarea: bool = False,
) -> str:
    """Campo etiquetado de un formulario en grilla (input de texto o textarea)."""
    ph = f' placeholder="{esc(placeholder)}"' if placeholder else ""
    control = (
        f'<textarea name="{name}" style="min-height:60px;"{ph}>{esc(value)}</textarea>'
        if textarea else f'<input name="{name}" value="{esc(value)}"{ph}>'
    )
    return f'<div class="{col}"><label>{esc(label)}</label>{control}</div>'


def edit_panel(
    title: str, csrf_token: str, audit_id: int, return_tab: str, fields_html: str, submit_label: str,
    fecha_col: str = "col-4",
) -> str:
    """Formulario plegable "✎ Editar …" de un bloque del expediente.

    Envía a /auditor/radar/profile solo los campos del bloque más la fecha de
    consulta de la fuente, y vuelve a la pestaña return_tab.
    """
    return f"""
    <details style="margin-top:0">
      <summary style="font-size:13px;font-weight:600;color:var(--accent-base);cursor:pointer;margin-bottom:14px;">
        ✎ {esc(title)}
      </summary>
      <form method="post" action="/auditor/radar/profile">
        {hidden_inputs(csrf_token, audit_id=audit_id, return_tab=return_tab)}
        <div class="grid">
          {fields_html}
          {fecha_consulta_field(fecha_col)}
        </div>
        <div class="actions" style="justify-content:flex-end;margin-top:12px;">
          <button type="submit" class="btn btn-primary btn-sm">{SVG_SAVE} {esc(submit_label)}</button>
        </div>
      </form>
    </details>
    """


def provenance_history(rows: list, labels: dict[str, str], title: str = "Historial del dato") -> str:
    """Historial de trazabilidad de un bloque, del cambio más reciente al más antiguo.

    rows son filas de data_provenance ya filtradas por bloque; labels traduce
    el nombre técnico del campo a su etiqueta visible.
    """
    if not rows:
        return ""
    body = "".join(
        "<tr>"
        f'<td style="white-space:nowrap;">{esc(row_get(r, "fecha_consulta"))}</td>'
        f'<td>{esc(labels.get(row_get(r, "campo"), "Registro"))}</td>'
        f'<td class="trace-old">{esc(row_get(r, "valor_anterior") or "—")}</td>'
        f'<td>{esc(row_get(r, "valor_nuevo") or "Eliminado")}</td>'
        f'<td>{esc(row_get(r, "fuente"))}</td>'
        f'<td style="white-space:nowrap;">{esc(row_get(r, "registrado_at"))}</td>'
        "</tr>"
        for r in reversed(list(rows)[-50:])
    )
    return f"""
    <details class="trace-history">
      <summary>{esc(title)} ({len(rows)} cambio(s))</summary>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Fecha de consulta</th><th>Dato</th><th>Valor anterior</th>
          <th>Valor nuevo</th><th>Fuente</th><th>Registrado</th></tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </details>
    """


def source_check_control(
    audit_id: int, check: object | None, csrf_token: str = "", return_tab: str = "sri",
) -> str:
    """Badge + botón toggle para marcar una fuente guiada (source_checks) como consultada.

    Usado en tab_sri, tab_supercias y tab_documentos del Radar Empresarial.
    'check' es la fila de source_checks ya resuelta por page.py (o None si esa
    fuente no está configurada para el expediente, en cuyo caso no se dibuja nada).
    """
    if not check:
        return ""
    consulted = row_get(check, "estado") == "consultada"
    badge_html = (
        f'<span class="badge {"badge-green" if consulted else "badge-gray"}">'
        f'{"Consultada" if consulted else "Pendiente de consultar"}</span>'
    )
    accion = "revertir" if consulted else "consultar"
    btn_text = "Marcar pendiente" if consulted else "Marcar consultada"
    obs_input = (
        "" if consulted else
        '<input name="observacion" placeholder="Observación (opcional)" class="source-check-obs-input">'
    )
    return f"""
    <div class="source-check-inline">
      {badge_html}
      <form method="post" action="/auditor/radar/source-check" class="source-check-inline-form">
        {hidden_inputs(csrf_token, audit_id=audit_id, check_id=row_get(check, "id"),
                       accion=accion, return_tab=return_tab)}
        {obs_input}
        <button type="submit" class="btn btn-sm">{btn_text}</button>
      </form>
    </div>
    """


def ruc_banner_html(ruc: str | None) -> str:
    """Genera el banner de estado del RUC (válido / advertencia / inválido / no registrado)."""
    if not ruc:
        return f'<div class="ruc-banner neutral">{SVG_INFO} <span>El RUC no ha sido registrado. El jefe debe añadirlo antes de iniciar la investigación.</span></div>'  # noqa: E501

    valid, warn, msg = validate_ruc(ruc)
    if warn:
        css, icon = "warning", SVG_ALERT
    elif valid:
        css, icon = "valid", SVG_CHECK
    else:
        css, icon = "invalid", SVG_CROSS

    return f'<div class="ruc-banner {css}">{icon} <div><span class="ruc-val">{esc(ruc)}</span> <span style="margin-left:8px">{esc(msg)}</span></div></div>'  # noqa: E501
