"""views/auditor/radar/certificado.py — Adjuntar el certificado de nómina (PDF)
y revisar lo que el sistema extrajo antes de importarlo. Un mismo PDF trae
administradores y accionistas: el panel es el mismo en las dos pestañas."""
from __future__ import annotations

from providers.supercias import SuperciasProvider
from services.certificados import MAX_CERTIFICADOS, NOMINAS
from services.company_search import find_certificate_evidence
from ui.components import fecha_consulta_field
from ui.helpers import esc, hidden_inputs
from ui.icons import SVG_CHECK, SVG_EXTERNAL, SVG_FILE, SVG_SAVE

# Columnas editables de la revisión por nómina: (campo, etiqueta, tipo de input).
_COLUMNAS = {
    "administradores": (
        ("identificacion", "Identificación", "text"), ("nombre", "Nombre", "text"),
        ("cargo", "Cargo", "text"), ("nacionalidad", "Nacionalidad", "text"),
    ),
    "accionistas": (
        ("identificacion", "Identificación", "text"), ("nombre", "Nombre", "text"),
        ("capital", "Capital (USD)", "number"), ("participacion_porcentaje", "Participación (%)", "number"),
    ),
}
# Palabras que identifican en la bitácora de evidencia el certificado de cada pestaña.
_EVIDENCIA = {"admins": ("administrador",), "accionistas": ("accionista", "socio")}


def assisted_panel(
    audit_id: int, audit, sources: list, return_tab: str, csrf_token: str,
    propuesta: dict | None = None,
) -> str:
    """Flujo asistido de la nómina: enlaces a las fuentes oficiales, adjuntar
    el certificado PDF para extraer ambas nóminas y revisar lo extraído. La
    captura manual sigue disponible en el formulario de la pestaña."""
    registrado = any(find_certificate_evidence(sources, clave) for clave in _EVIDENCIA[return_tab])
    estado = (
        f'<span class="badge badge-green">{SVG_CHECK} Certificado registrado como evidencia</span>'
        if registrado else '<span class="badge badge-gray">Certificado aún no registrado</span>'
    )
    enlaces = "".join(
        f'<a class="btn-ext-link" href="{esc(lnk.url)}" target="_blank" rel="noopener">'
        f'{SVG_EXTERNAL} {esc(lnk.name)}</a>'
        for lnk in SuperciasProvider().nomina_links(audit["ruc"] or "", audit["company_name"])
    )
    return f"""
    <section class="people-editor">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Flujo asistido — certificado oficial</span>
        <h4>Obtener la nómina desde Supercias</h4>
      </div>
      <p class="cert-help">
        El Directorio de Compañías solo trae el representante legal actual. Descargue el
        certificado de nómina desde los enlaces y adjúntelo una sola vez (uno o varios PDF),
        aquí o en la otra pestaña: Atlas extrae administradores y accionistas para que los revise antes de
        importarlos. También puede registrarlos manualmente más abajo.
      </p>
      <div class="cert-links">{estado}{enlaces}</div>
      {upload_form(audit_id, return_tab, csrf_token)}
      {review_panel(audit_id, propuesta, return_tab, csrf_token)}
    </section>
    <hr class="section-divider">
    """


def upload_form(audit_id: int, return_tab: str, csrf_token: str) -> str:
    """Formulario para adjuntar los certificados PDF (uno o varios) y extraer la nómina."""
    return f"""
    <form method="post" action="/auditor/radar/certificado" enctype="multipart/form-data" class="cert-upload">
      {hidden_inputs(csrf_token, audit_id=audit_id, return_tab=return_tab)}
      <label>Adjuntar certificados de nómina de administradores y accionistas
        (PDF, hasta {MAX_CERTIFICADOS} archivos de máx. 10 MB)</label>
      <div class="cert-upload-row">
        <input type="file" name="archivo" accept="application/pdf,.pdf" multiple required>
        <button type="submit" class="btn btn-sm btn-primary">{SVG_FILE} Adjuntar y extraer</button>
      </div>
      <small>Si la nómina viene en varios documentos, selecciónelos juntos (Ctrl o ⌘ + clic):
        se revisan en una sola propuesta. Cada PDF queda como evidencia.</small>
    </form>
    """


def _celda(nomina: str, campo: str, tipo_input: str, valor: object, i: int) -> str:
    valor = "" if valor is None else valor
    paso = ' step="0.0001" min="0"' if tipo_input == "number" else ""
    return f'<td><input type="{tipo_input}" name="{nomina}_{campo}_{i}" value="{esc(valor)}"{paso}></td>'


def _tabla(nomina: str, filas: list[dict]) -> str:
    """Filas propuestas de una nómina, editables y marcadas para importar."""
    titulo = f'<h5 class="cert-review-title">{nomina.capitalize()} ({len(filas)})</h5>'
    if not filas:
        return titulo + f'<p class="cert-help">No se detectaron {nomina} en el certificado.</p>'
    columnas = _COLUMNAS[nomina]
    cuerpo = "".join(
        f"""<tr><td><input type="checkbox" name="incluir_{nomina}" value="{i}" checked
                aria-label="Importar {nomina} fila {i + 1}"></td>
            {''.join(_celda(nomina, campo, tipo_input, fila.get(campo), i) for campo, _, tipo_input in columnas)}</tr>"""
        for i, fila in enumerate(filas)
    )
    return f"""
      {titulo}
      <div class="table-wrap">
        <table class="cert-review-table">
          <thead><tr><th>Importar</th>{''.join(f'<th>{esc(e)}</th>' for _, e, _ in columnas)}</tr></thead>
          <tbody>{cuerpo}</tbody>
        </table>
      </div>
    """


def review_panel(audit_id: int, propuesta: dict | None, return_tab: str, csrf_token: str) -> str:
    """Administradores y accionistas propuestos por el lector del certificado.
    Solo se importan las filas marcadas; descartar conserva el PDF como evidencia."""
    if not propuesta:
        return ""
    advertencias = "".join(f"<li>{esc(a)}</li>" for a in propuesta["advertencias"])
    hay_filas = any(propuesta[n] for n in NOMINAS)
    importar = (
        f'<button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Importar filas marcadas</button>'
        if hay_filas else ""
    )
    return f"""
    <section class="cert-review">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Certificado adjunto — revisión</span>
        <h4>{esc(" · ".join(propuesta["archivo"].splitlines()))}</h4>
        <p>Corrija lo necesario y desmarque lo que no corresponda. Se importa a las dos pestañas.</p>
      </div>
      {f'<ul class="cert-warnings">{advertencias}</ul>' if advertencias else ''}
      <form method="post" action="/auditor/radar/certificado/importar">
        {hidden_inputs(csrf_token, audit_id=audit_id, import_id=propuesta["id"], return_tab=return_tab)}
        {''.join(_tabla(n, propuesta[n]) for n in NOMINAS)}
        <div class="grid">{fecha_consulta_field("col-4", propuesta.get("fecha_certificado") or "")}</div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm" formaction="/auditor/radar/certificado/descartar" formnovalidate>
            Descartar propuesta</button>
          {importar}
        </div>
      </form>
    </section>
    """
