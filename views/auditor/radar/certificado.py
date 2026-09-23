"""views/auditor/radar/certificado.py — Adjuntar el certificado de nómina (PDF)
y revisar lo que el sistema extrajo antes de importarlo. Lo usan las pestañas
Administradores y Accionistas."""
from __future__ import annotations

from providers.supercias import SuperciasProvider
from services.certificados import TIPOS_CERTIFICADO
from services.company_search import find_certificate_evidence
from ui.components import fecha_consulta_field
from ui.helpers import esc, hidden_inputs
from ui.icons import SVG_CHECK, SVG_EXTERNAL, SVG_FILE, SVG_SAVE

# Columnas editables de la revisión por tipo: (campo, etiqueta, tipo de input).
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


# Por tipo: palabras que identifican su certificado en la bitácora de evidencia
# y texto de ayuda del flujo asistido.
_FLUJO = {
    "administradores": (
        ("administrador",),
        "El Directorio de Compañías solo trae el representante legal actual, no la nómina "
        "completa de administradores.",
    ),
    "accionistas": (
        ("accionista", "socio"),
        "El Directorio de Compañías no incluye accionistas.",
    ),
}


def assisted_panel(
    audit_id: int, audit, sources: list, tipo: str, return_tab: str, csrf_token: str,
    propuesta: dict | None = None,
) -> str:
    """Flujo asistido de la nómina: enlaces a las fuentes oficiales, adjuntar
    el certificado PDF para extraerla y revisar lo extraído. La captura
    manual sigue disponible en el formulario de la pestaña."""
    claves, contexto = _FLUJO[tipo]
    registrado = any(find_certificate_evidence(sources, clave) for clave in claves)
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
        {esc(contexto)} Descargue el {esc(TIPOS_CERTIFICADO[tipo].lower())} desde los enlaces y
        adjúntelo: Atlas extrae la nómina para que la revise antes de importarla. También puede
        registrarla manualmente más abajo.
      </p>
      <div class="cert-links">{estado}{enlaces}</div>
      {upload_form(audit_id, tipo, return_tab, csrf_token)}
      {review_panel(audit_id, propuesta, return_tab, csrf_token)}
    </section>
    <hr class="section-divider">
    """


def upload_form(audit_id: int, tipo: str, return_tab: str, csrf_token: str) -> str:
    """Formulario para adjuntar el certificado PDF y extraer la nómina."""
    return f"""
    <form method="post" action="/auditor/radar/certificado" enctype="multipart/form-data" class="cert-upload">
      {hidden_inputs(csrf_token, audit_id=audit_id, tipo=tipo, return_tab=return_tab)}
      <label>Adjuntar {esc(TIPOS_CERTIFICADO[tipo].lower())} (PDF, máx. 10 MB)</label>
      <div class="cert-upload-row">
        <input type="file" name="archivo" accept="application/pdf,.pdf" required>
        <button type="submit" class="btn btn-sm btn-primary">{SVG_FILE} Adjuntar y extraer</button>
      </div>
      <small>El PDF queda como evidencia. Revise lo extraído antes de importarlo.</small>
    </form>
    """


def _celda(campo: str, tipo_input: str, valor: object, i: int) -> str:
    valor = "" if valor is None else valor
    paso = ' step="0.0001" min="0"' if tipo_input == "number" else ""
    return f'<td><input type="{tipo_input}" name="{campo}_{i}" value="{esc(valor)}"{paso}></td>'


def review_panel(audit_id: int, propuesta: dict | None, return_tab: str, csrf_token: str) -> str:
    """Filas propuestas por el lector del certificado, editables. Solo se
    importan las marcadas; descartar conserva el PDF como evidencia."""
    if not propuesta:
        return ""
    columnas = _COLUMNAS[propuesta["tipo"]]
    advertencias = "".join(
        f'<li>{esc(a)}</li>' for a in propuesta["advertencias"]
    )
    filas = "".join(
        f"""<tr><td><input type="checkbox" name="incluir" value="{i}" checked
                aria-label="Importar fila {i + 1}"></td>
            {''.join(_celda(campo, tipo_input, fila.get(campo), i) for campo, _, tipo_input in columnas)}</tr>"""
        for i, fila in enumerate(propuesta["filas"])
    )
    tabla = f"""
      <div class="table-wrap">
        <table class="cert-review-table">
          <thead><tr><th>Importar</th>{''.join(f'<th>{esc(e)}</th>' for _, e, _ in columnas)}</tr></thead>
          <tbody>{filas}</tbody>
        </table>
      </div>
    """ if filas else ""
    importar = (
        f'<button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Importar filas marcadas</button>'
        if filas else ""
    )
    return f"""
    <section class="cert-review">
      <div class="people-editor-head">
        <span class="workflow-eyebrow">Certificado adjunto — revisión</span>
        <h4>{esc(propuesta["archivo"])}</h4>
        <p>{len(propuesta["filas"])} fila(s) detectada(s). Corrija lo necesario y desmarque lo que no corresponda.</p>
      </div>
      {f'<ul class="cert-warnings">{advertencias}</ul>' if advertencias else ''}
      <form method="post" action="/auditor/radar/certificado/importar">
        {hidden_inputs(csrf_token, audit_id=audit_id, import_id=propuesta["id"], return_tab=return_tab)}
        {tabla}
        <div class="grid">{fecha_consulta_field("col-4", propuesta.get("fecha_certificado") or "")}</div>
        <div class="actions people-editor-actions">
          <button type="submit" class="btn btn-sm" formaction="/auditor/radar/certificado/descartar" formnovalidate>
            Descartar propuesta</button>
          {importar}
        </div>
      </form>
    </section>
    """
