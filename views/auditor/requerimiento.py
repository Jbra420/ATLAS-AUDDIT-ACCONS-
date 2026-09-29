"""
views/auditor/requerimiento.py — Pestaña principal "Requerimiento inicial".

Segundo paso del proceso, al mismo nivel que "Levantamiento de información":
comparte la auditoría y sus datos confirmados, pero tiene su propia página,
rutas y registros. El auditor asignado adjunta y revisa el contrato, confirma
los datos, genera los cuatro documentos (una generación a la vez), registra
el correo y el aviso por WhatsApp y la recepción y revisión de lo que
devuelve el cliente. El jefe auditor ve lo mismo en solo lectura: descarga lo
registrado, pero no arma vistas previas ni borradores de correo.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from urllib.parse import quote

from database import get_audit, get_audit_context, get_requerimiento_context
from services.requerimiento import (
    ADJUNTOS,
    AVISO_FIRMAS,
    AVISO_PLANTILLA,
    CAMPOS_ANIO,
    CAMPOS_FECHA,
    CAMPOS_TEXTO,
    CUADROS,
    DOCUMENTOS,
    ENTREGABLES,
    FILAS_MINIMAS_CUADRO,
    ITEMS_HOJA1,
    ITEMS_HOJA2,
    RECEPCIONES,
    VERIFICACIONES_REVISION,
    correo,
    datos_efectivos,
    estado_proceso,
    faltantes,
    instantanea_actual,
    mensaje_whatsapp,
    paquete_desactualizado,
    precarga,
    texto_item,
)
from services.rowutil import row_get
from ui.components import proceso_nav
from ui.helpers import esc, form_id, form_value, hidden_inputs
from ui.icons import SVG_CHECK, SVG_DOWNLOAD, SVG_FILE, SVG_INFO, SVG_SAVE
from ui.layout import layout


def _url_archivo(audit_id: int, origen: str, file_id: int) -> str:
    return f"/requerimiento/archivo?audit_id={audit_id}&origen={origen}&id={int(file_id)}"


def _descarga(audit_id: int, origen: str, row, texto: str = "") -> str:
    return (f'<a class="req-file" href="{_url_archivo(audit_id, origen, row["id"])}">'
            f'{SVG_DOWNLOAD} {esc(texto or row["nombre"])}</a>')


def _huella(row) -> str:
    return (f'<span class="req-meta">SHA-256 {esc(row["sha256"][:12])}… · '
            f'{max(1, round(row["bytes"] / 1024))} KB</span>')


def _cuando(ts: str | None) -> str:
    return esc((ts or "")[:16])


def _seccion(ancla: str, numero: int, titulo: str, cuerpo: str, estado: str = "") -> str:
    return f"""
    <section class="panel req-section" id="{ancla}">
      <div class="req-section-head">
        <span class="req-section-num">{numero}</span>
        <h2>{esc(titulo)}</h2>
        {estado}
      </div>
      {cuerpo}
    </section>"""


def _estado_badge(hecho: bool, texto_hecho: str, texto_pendiente: str) -> str:
    if hecho:
        return f'<span class="badge badge-green">{SVG_CHECK} {esc(texto_hecho)}</span>'
    return f'<span class="badge badge-gray">{esc(texto_pendiente)}</span>'


def _archivo_input(nombre: str, formatos: tuple[str, ...], requerido: bool = True) -> str:
    accept = ",".join(f".{f}" for f in formatos) + (",.jpeg" if "jpg" in formatos else "")
    return (f'<input type="file" name="{nombre}" accept="{accept}"{" required" if requerido else ""}>'
            f'<div class="field-hint">{", ".join(f.upper() for f in formatos)} · máximo 10 MB</div>')


# ── 1. Estado del proceso ────────────────────────────────────────────────

def _pasos(estado: dict) -> str:
    items = "".join(
        f'<li class="req-step{" hecho" if p["hecho"] else ""}{" actual" if p["actual"] else ""}">'
        f'<span class="req-step-dot">{SVG_CHECK if p["hecho"] else i}</span>'
        f'<span class="req-step-label">{esc(p["label"])}</span></li>'
        for i, p in enumerate(estado["pasos"], start=1)
    )
    return f'<ol class="req-steps" aria-label="Estado del requerimiento inicial">{items}</ol>'


# ── 2. Contrato y revisión de documentos firmados ───────────────────────

def _revision(audit_id: int, adjunto, read_only: bool, csrf_token: str) -> str:
    """Estado de revisión de un PDF firmado y, si falta, el formulario para
    dejar constancia. Recibir no es revisar."""
    detalle = (f'<span class="req-meta">{esc(adjunto["detalle_archivo"])}</span>'
               if row_get(adjunto, "detalle_archivo") else "")
    if row_get(adjunto, "revisado_at"):
        conforme = adjunto["revision_resultado"] == "conforme"
        badge = ('<span class="badge badge-green">Revisado conforme</span>' if conforme else
                 '<span class="badge badge-red">Revisado con observaciones</span>')
        nota = f' · {esc(adjunto["revision_nota"])}' if adjunto["revision_nota"] else ""
        return (f'{badge}{detalle}<span class="req-meta">Revisión de {esc(row_get(adjunto, "revisor") or "—")} '
                f'el {_cuando(adjunto["revisado_at"])}{nota}</span>')
    html = f'<span class="badge badge-amber">Adjuntado, pendiente de revisión</span>{detalle}'
    if read_only:
        return html
    checks = "".join(
        f'<label class="req-check"><input type="checkbox" name="verif_{clave}" value="1"> {esc(texto)}</label>'
        for clave, texto in VERIFICACIONES_REVISION
    )
    return html + f"""
      <details class="req-details req-revision">
        <summary>Revisar y dejar constancia</summary>
        <form method="post" action="/auditor/requerimiento/revisar">
          {hidden_inputs(csrf_token, audit_id=audit_id, adjunto_id=adjunto["id"])}
          <p class="field-hint">{esc(AVISO_FIRMAS)}</p>
          {checks}
          <div class="grid req-grid">
            <div class="col-4"><label>Resultado *</label><select name="resultado" required>
              <option value="conforme">Conforme</option><option value="observado">Con observaciones</option>
            </select></div>
            <div class="col-8"><label>Nota (obligatoria si hay observaciones)</label>
              <input name="nota" maxlength="500"></div>
          </div>
          <div class="actions req-actions"><button type="submit" class="btn btn-sm btn-primary">{SVG_CHECK} Registrar revisión</button></div>
        </form>
      </details>"""


def _contrato(audit_id: int, contratos: list, estado: dict, read_only: bool, csrf_token: str) -> str:
    lista = "".join(
        f'<li>{_descarga(audit_id, "adjunto", c)} {_huella(c)}'
        f'<span class="req-meta">Firmado: {esc(c["fecha"] or "sin fecha")} · subido {_cuando(c["subido_at"])}'
        f' por {esc(c["autor"] or "—")}</span>{_revision(audit_id, c, read_only, csrf_token)}</li>'
        for c in contratos
    )
    cuerpo = (f'<ul class="req-list">{lista}</ul>' if contratos else
              '<p class="req-empty">Aún no se adjunta el contrato de auditoría firmado por el auditor y el '
              'gerente. Es requisito para generar los documentos del requerimiento.</p>')
    if contratos and not estado["contrato_revisado"]:
        cuerpo += (f'<div class="fin-alert alert-medium">{SVG_INFO}<div>El contrato está adjuntado pero '
                   'nadie dejó constancia de su revisión: los documentos no se pueden generar hasta que un '
                   'contrato quede revisado conforme.</div></div>')
    if not read_only:
        cuerpo += f"""
        <form method="post" action="/auditor/requerimiento/contrato" enctype="multipart/form-data" class="req-inline-form">
          {hidden_inputs(csrf_token, audit_id=audit_id)}
          <div><label>Contrato firmado (PDF) *</label>{_archivo_input("archivo", ADJUNTOS["contrato"][1])}</div>
          <div><label>Fecha de firma</label><input type="date" name="fecha" max="{date.today().isoformat()}"></div>
          <button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Adjuntar contrato</button>
        </form>"""
    if estado["contrato_revisado"]:
        badge = _estado_badge(True, "Contrato revisado", "")
    elif estado["contrato_pendiente"]:
        badge = '<span class="badge badge-amber">Pendiente de revisión</span>'
    else:
        badge = _estado_badge(False, "", "Pendiente")
    return _seccion("contrato", 1, "Contrato de auditoría firmado", cuerpo, badge)


# ── 3. Datos del requerimiento ──────────────────────────────────────────

_GRUPOS = (
    ("Empresa y representante", ("empresa", "ruc", "representante_titulo", "representante_nombre",
                                 "representante_cargo", "representante_identificacion",
                                 "representante_nacionalidad", "representante_ciudad")),
    ("Años y fechas", ("anio_auditado", "anio_certificados", "anio_cerrado", "fecha_documentos", "fecha_corte",
                       "fechas_inventario")),
    ("Auddit y correo", ("auddit_representante", "auddit_cargo", "correo_para_nombre", "correo_para",
                         "correo_cc")),
)
_AYUDAS = {
    "anio_certificados": "Año sobre el que el gerente certifica. No se deduce del año auditado.",
    "anio_cerrado": "Se cita en el Req. #1 (informe de control interno, ICT y balance al 31/12).",
    "fecha_corte": "Puede cambiar, pero dentro del año auditado.",
    "fecha_documentos": "Fecha que llevan la carta y los certificados.",
    "fechas_inventario": "Texto que completa la frase de la carta, p. ej. «entre el 15 de octubre y el 15 de diciembre».",
    "correo_cc": "Separe varios correos con coma.",
    "representante_identificacion": "Cédula o pasaporte tal como consta en el documento de identidad.",
}


def _campo(campo: str, datos: dict, sugeridos: dict, confirmado: bool, read_only: bool,
           ruc_auditoria: str) -> str:
    etiquetas = {**{c: e for c, (e, _l) in CAMPOS_TEXTO.items()}, **CAMPOS_ANIO, **CAMPOS_FECHA}
    valor = ruc_auditoria if campo == "ruc" else datos.get(campo)
    valor = "" if valor is None else valor
    if campo == "ruc":
        control = f'<input name="ruc" value="{esc(valor)}" readonly aria-readonly="true">'
    elif campo in CAMPOS_FECHA:
        control = f'<input type="date" name="{campo}" value="{esc(valor)}">'
    elif campo in CAMPOS_ANIO:
        control = f'<input name="{campo}" value="{esc(valor)}" inputmode="numeric" maxlength="4" placeholder="AAAA">'
    else:
        control = f'<input name="{campo}" value="{esc(valor)}" maxlength="{CAMPOS_TEXTO[campo][1]}">'
    if read_only:
        control = f'<div class="req-valor">{esc(valor) or "<span class=muted>Pendiente</span>"}</div>'
    ayuda = ("RUC de la auditoría. Valídelo en Levantamiento de información." if campo == "ruc" else
             _AYUDAS.get(campo, ""))
    if not confirmado and campo in sugeridos:
        ayuda = f"Precargado: {sugeridos[campo][1]}. " + ayuda
    ayuda_html = f'<div class="field-hint">{esc(ayuda)}</div>' if ayuda else ""
    return f'<div class="col-4"><label>{esc(etiquetas[campo])}</label>{control}{ayuda_html}</div>'


def _datos(audit_id: int, datos: dict, sugeridos: dict, guardado, falt: list, read_only: bool, csrf_token: str,
           ruc_auditoria: str) -> str:
    confirmado = guardado is not None
    grupos = "".join(
        f'<h3 class="req-subtitle">{esc(titulo)}</h3><div class="grid req-grid">'
        + "".join(_campo(c, datos, sugeridos, confirmado, read_only, ruc_auditoria) for c in campos) + "</div>"
        for titulo, campos in _GRUPOS
    )
    cronograma = {c["entregable"]: c["fecha"] for c in datos.get("cronograma") or []}
    filas_crono = "".join(
        f'<div class="col-3"><label>{esc(e)}</label>'
        + (f'<div class="req-valor">{esc(cronograma.get(e, "")) or "Pendiente"}</div>' if read_only else
           f'<input name="cronograma_{i}" value="{esc(cronograma.get(e, ""))}" maxlength="60" '
           f'placeholder="p. ej. Hasta febrero 2027">')
        + "</div>"
        for i, e in enumerate(ENTREGABLES)
    )
    equipo = "\n".join(datos.get("equipo") or [])
    equipo_html = (f'<div class="req-valor">{esc(equipo).replace(chr(10), "<br>") or "Pendiente"}</div>' if read_only
                   else f'<textarea name="equipo" rows="4" placeholder="Un integrante por línea, con su título">'
                        f'{esc(equipo)}</textarea>')
    aviso = ""
    if falt:
        aviso = (f'<div class="fin-alert alert-medium">{SVG_INFO}<div><strong>Faltan datos para generar:</strong> '
                 f'{esc(", ".join(falt))}.</div></div>')
    estado = (_estado_badge(True, f"Confirmados {_cuando(row_get(guardado, 'confirmado_at'))}", "")
              if confirmado and not falt else
              _estado_badge(False, "", "Por confirmar" if not confirmado else "Incompletos"))
    cuerpo = f"""
      <p class="req-help">La precarga toma solo lo que consta en el levantamiento (razón social, RUC,
        administradores, período y año fiscal confirmado). Revise, corrija y complete: nada se deduce.</p>
      {aviso}
      {grupos}
      <h3 class="req-subtitle">Equipo de auditoría y cronograma de informes</h3>
      <div class="grid req-grid">
        <div class="col-12"><label>Equipo de auditoría</label>{equipo_html}</div>
        {filas_crono}
      </div>"""
    if not read_only:
        cuerpo = f"""
      <form method="post" action="/auditor/requerimiento/datos">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        {cuerpo}
        <div class="actions req-actions">
          <button type="submit" class="btn btn-primary">{SVG_SAVE} Confirmar datos</button>
        </div>
      </form>"""
    return _seccion("datos", 2, "Datos del requerimiento", cuerpo, estado)


# ── 4. Solicitud de información: marcas y cuadros ───────────────────────

def _fila_item(hoja: int, numero: int, texto: str, datos: dict, marca, read_only: bool) -> str:
    valor = "cumplido" if row_get(marca, "cumplido", 0) else "no_aplica" if row_get(marca, "no_aplica", 0) else ""
    observacion = row_get(marca, "observacion", "")
    if read_only:
        estado = {"cumplido": "Cumplido", "no_aplica": "No aplica"}.get(valor, "—")
        controles = f"<td>{estado}</td><td>{esc(observacion)}</td>"
    else:
        opciones = "".join(
            f'<option value="{v}"{" selected" if v == valor else ""}>{t}</option>'
            for v, t in (("", "—"), ("cumplido", "Cumplido"), ("no_aplica", "No aplica"))
        )
        controles = (f'<td><select name="item_{hoja}_{numero}" aria-label="Estado del ítem {numero}">{opciones}'
                     f'</select></td><td><input name="obs_{hoja}_{numero}" value="{esc(observacion)}" '
                     f'maxlength="500" aria-label="Observación del ítem {numero}"></td>')
    return f"<tr><td>{numero}</td><td>{esc(texto_item(texto, datos))}</td>{controles}</tr>"


def _tabla_items(hoja: int, datos: dict, marcas: dict, read_only: bool) -> str:
    filas = ""
    if hoja == 1:
        filas = "".join(_fila_item(1, n, t, datos, marcas.get((1, n)), read_only) for n, t, _e in ITEMS_HOJA1)
    else:
        for seccion, items in ITEMS_HOJA2:
            filas += f'<tr class="req-table-group"><td colspan="4">{esc(seccion)}</td></tr>'
            filas += "".join(_fila_item(2, n, t, datos, marcas.get((2, n)), read_only) for n, t, _f, _e in items)
    titulo = "Req. #1 — Documentos de la compañía" if hoja == 1 else "Req. #2 — Información contable - tributaria"
    return f"""
      <details class="req-details"{" open" if hoja == 1 else ""}>
        <summary>{esc(titulo)}</summary>
        <div class="table-wrap"><table class="req-table">
          <thead><tr><th>Nro.</th><th>Detalle</th><th>Estado</th><th>Observaciones</th></tr></thead>
          <tbody>{filas}</tbody>
        </table></div>
      </details>"""


def _cuadro(audit_id: int, clave: str, filas: list, read_only: bool, csrf_token: str) -> str:
    _hoja, titulo, columnas, _numerado = CUADROS[clave]
    cabecera = "".join(f"<th>{esc(c)}</th>" for c in columnas)
    if read_only:
        cuerpo = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in f) + "</tr>" for f in filas) or (
            f'<tr><td colspan="{len(columnas)}" class="muted">Sin filas precargadas</td></tr>')
        tabla = f'<div class="table-wrap"><table class="req-table"><thead><tr>{cabecera}</tr></thead><tbody>{cuerpo}</tbody></table></div>'
    else:
        total = max(len(filas) + 2, 3)
        cuerpo = ""
        for r in range(total):
            valores = filas[r] if r < len(filas) else [""] * len(columnas)
            cuerpo += "<tr>" + "".join(
                f'<td><input name="{clave}_{r}_{c}" value="{esc(v)}" maxlength="200" '
                f'aria-label="{esc(columnas[c])}, fila {r + 1}"></td>'
                for c, v in enumerate(valores)
            ) + "</tr>"
        tabla = f"""
        <form method="post" action="/auditor/requerimiento/detalle">
          {hidden_inputs(csrf_token, audit_id=audit_id, seccion=clave, filas=total)}
          <div class="table-wrap"><table class="req-table req-table-edit"><thead><tr>{cabecera}</tr></thead>
            <tbody>{cuerpo}</tbody></table></div>
          <div class="actions req-actions"><button type="submit" class="btn btn-sm">{SVG_SAVE} Guardar cuadro</button></div>
        </form>"""
    return (f'<details class="req-details"><summary>{esc(titulo.capitalize())} '
            f'<span class="req-meta">{len(filas)} fila(s) precargada(s)</span></summary>{tabla}</details>')


def _solicitud(audit_id: int, datos: dict, req: dict, read_only: bool, csrf_token: str) -> str:
    marcas = req["items"]
    tablas = _tabla_items(1, datos, marcas, read_only) + _tabla_items(2, datos, marcas, read_only)
    if not read_only:
        tablas = f"""
        <form method="post" action="/auditor/requerimiento/items">
          {hidden_inputs(csrf_token, audit_id=audit_id)}
          {tablas}
          <div class="actions req-actions"><button type="submit" class="btn btn-sm">{SVG_SAVE} Guardar marcas</button></div>
        </form>"""
    cuadros = "".join(_cuadro(audit_id, clave, req["detalles"].get(clave) or [], read_only, csrf_token)
                      for clave in CUADROS)
    cuerpo = f"""
      <p class="req-help">Hojas 1 y 2 del Excel: puede marcar desde ya CUMPLIDO o NO APLICA (nunca ambos) y
        dejar observaciones, p. ej. «ACTUALIZAR INFORMACIÓN». Hojas 3 y 4: precargue las filas conocidas; el
        Excel agrega filas en blanco (al menos {FILAS_MINIMAS_CUADRO} por cuadro) para que el cliente complete.</p>
      {tablas}
      <h3 class="req-subtitle">Cuadros de detalle (hojas 3 y 4)</h3>
      {cuadros}"""
    return _seccion("solicitud", 3, "Solicitud inicial de información", cuerpo)


# ── 5. Vista previa, generación y versiones ─────────────────────────────

_ENVIADA = ' <span class="badge badge-blue">Enviada</span>'


def _enviadas(envios: list) -> set[int]:
    ids: set[int] = set()
    for e in envios:
        if row_get(e, "canal") == "correo":
            ids.update(json.loads(row_get(e, "archivos_json", "[]") or "[]"))
    return ids


def _alerta_desactualizada(paquete) -> str:
    return (f'<div class="fin-alert alert-medium">{SVG_INFO}<div><strong>Documentos desactualizados.</strong> '
            f'Los datos, marcas o cuadros cambiaron después de la generación {paquete["numero"]}. Sus documentos y '
            'los envíos ya registrados no cambian, pero para un envío nuevo genere los documentos otra vez antes '
            'de preparar el correo.</div></div>')


def _documentos(audit_id: int, req: dict, estado: dict, falt: list, confirmado: bool, read_only: bool,
                csrf_token: str) -> str:
    enviadas = _enviadas(req["envios"])
    enviados = {row_get(e, "paquete_id") for e in req["envios"] if row_get(e, "paquete_id")}
    previas = ""
    if confirmado and not falt and not read_only:
        previas = '<div class="req-inline-actions"><span class="req-meta">Vista previa sin guardar:</span>' + "".join(
            f'<a class="req-file" href="/requerimiento/vista-previa?audit_id={audit_id}&doc={tipo}">'
            f'{SVG_FILE} {esc(nombre)}</a>' for tipo, (nombre, _e, _p) in DOCUMENTOS.items()
        ) + "</div>"
    generaciones = ""
    for i, paquete in enumerate(req["paquetes"]):
        etiquetas = ""
        if i == 0:
            etiquetas += ('<span class="badge badge-amber">Desactualizada</span>' if estado["desactualizado"] else
                          '<span class="badge badge-green">Vigente</span>')
        if paquete["id"] in enviados:
            etiquetas += _ENVIADA
        archivos = "".join(
            f'<li>{_descarga(audit_id, "generado", paquete["archivos"][tipo])}{_huella(paquete["archivos"][tipo])}</li>'
            for tipo in DOCUMENTOS if tipo in paquete["archivos"]
        )
        generaciones += f"""
        <div class="req-doc">
          <div class="req-doc-head"><strong>Generación {paquete["numero"]}</strong>{etiquetas}
            <span class="req-meta">{_cuando(paquete["generado_at"])} · {esc(paquete["autor"] or "—")} ·
              ref. {esc(paquete["referencia"])}</span></div>
          <ul class="req-list">{archivos}</ul>
        </div>"""
    # Versiones anteriores a las generaciones (sin paquete): solo historial.
    sueltas = [v for tipo in DOCUMENTOS for v in req["versiones"].get(tipo) or [] if not row_get(v, "paquete_id")]
    if sueltas:
        generaciones += f"""
        <div class="req-doc">
          <div class="req-doc-head"><strong>Versiones sin generación identificable</strong>
            <span class="req-meta">Historial: no se pueden registrar como envío nuevo</span></div>
          <ul class="req-list">{"".join(
              f'<li>{_descarga(audit_id, "generado", v)}{_ENVIADA if v["id"] in enviadas else ""}{_huella(v)}</li>'
              for v in sueltas)}</ul>
        </div>"""
    if read_only:
        accion = ""
    elif estado["puede_generar"] and confirmado:
        accion = f"""
        <form method="post" action="/auditor/requerimiento/generar" class="req-generar">
          {hidden_inputs(csrf_token, audit_id=audit_id)}
          <button type="submit" class="btn btn-primary">{SVG_FILE} Generar los cuatro documentos</button>
          <span class="field-hint">Cada generación crea los cuatro documentos juntos; las anteriores y las ya
            enviadas no cambian.</span>
        </form>"""
    else:
        motivos = []
        if not req["adjuntos"]["contrato"]:
            motivos.append("adjuntar el contrato firmado")
        elif not estado["contrato_revisado"]:
            motivos.append("registrar la revisión del contrato firmado")
        if not confirmado:
            motivos.append("confirmar los datos del requerimiento")
        elif falt:
            motivos.append("completar los datos pendientes")
        accion = (f'<div class="fin-alert alert-medium">{SVG_INFO}<div>Para generar los documentos falta: '
                  f'{esc(", ".join(motivos))}.</div></div>')
    alerta = _alerta_desactualizada(req["paquetes"][0]) if estado["desactualizado"] else ""
    cuerpo = f"""
      <p class="req-help">{esc(AVISO_PLANTILLA)} Las firmas no se reproducen: cada documento deja la línea
        para firmar.</p>
      {alerta}
      {accion}
      {previas}
      <div class="req-docs">{generaciones or '<p class="req-empty">Sin documentos generados.</p>'}</div>"""
    if not estado["generado"]:
        badge = _estado_badge(False, "", "Sin generar")
    elif estado["desactualizado"]:
        badge = '<span class="badge badge-amber">Desactualizados</span>'
    else:
        badge = _estado_badge(True, f"Generación {req['paquetes'][0]['numero']} vigente", "")
    return _seccion("documentos", 4, "Vista previa y documentos generados", cuerpo, badge)


# ── 6. Correo y WhatsApp ────────────────────────────────────────────────

def _ahora_local() -> str:
    return datetime.now().replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _correo(audit_id: int, datos: dict, req: dict, estado: dict, read_only: bool, csrf_token: str) -> str:
    vigente = req["paquetes"][0] if req["paquetes"] else None
    # El correo se muestra con la instantánea de la generación, igual que el .eml.
    if vigente is not None:
        datos = json.loads(vigente["datos_json"])
    mensaje = correo(datos)
    adjuntos = "".join(
        f'<li>{_descarga(audit_id, "generado", vigente["archivos"][t])}</li>'
        if vigente is not None and t in vigente["archivos"] else
        f'<li class="muted">{esc(DOCUMENTOS[t][0])}: sin generar</li>'
        for t in DOCUMENTOS
    )
    borrador = ""
    alerta = _alerta_desactualizada(vigente) if vigente is not None and estado["desactualizado"] else ""
    if vigente is not None and not estado["desactualizado"] and not read_only:
        borrador = (f'<a class="btn btn-sm" href="/requerimiento/correo.eml?audit_id={audit_id}">{SVG_DOWNLOAD} '
                    f'Borrador de correo (.eml) de la generación {vigente["numero"]}</a>')
    envios = [e for e in req["envios"] if row_get(e, "canal") == "correo"]
    registrados = "".join(
        f'<li><strong>{_cuando(e["fecha"])}</strong> a {esc(e["destinatario"])}'
        f'{" · CCO " + esc(e["copia"]) if e["copia"] else ""}'
        f'<span class="req-meta">Registrado {_cuando(e["registrado_at"])} por {esc(e["autor"] or "—")} · '
        f'{_generacion_enviada(req, e)}'
        f'{" · Registro histórico" if row_get(e, "registro_historico", 0) else ""}</span>'
        + (f'<span class="req-meta">Motivo: {esc(e["justificacion_historica"])}</span>'
           if row_get(e, "registro_historico", 0) else "")
        + (_descarga(audit_id, "adjunto", {"id": e["evidencia_id"], "nombre": "Evidencia"}) if e["evidencia_id"] else "")
        + "</li>"
        for e in envios
    )
    cuerpo = f"""
      <p class="req-help">Atlas no envía correos: prepare el mensaje, envíelo desde el correo corporativo y
        registre el envío efectivo con su evidencia. Generar o descargar documentos no lo marca como enviado.
        El asunto, el cuerpo y los adjuntos corresponden a la última generación.</p>
      {alerta}
      <div class="grid req-grid">
        <div class="col-6"><label>Para</label><div class="req-valor">{esc(datos.get("correo_para_nombre") or "")}
          &lt;{esc(datos.get("correo_para") or "pendiente")}&gt;</div></div>
        <div class="col-6"><label>CCO</label><div class="req-valor">{esc(datos.get("correo_cc") or "—")}</div></div>
        <div class="col-12"><label for="req-asunto">Asunto</label>
          <input id="req-asunto" value="{esc(mensaje["asunto"])}" readonly>
          <button type="button" class="btn btn-sm req-copy" data-copy="req-asunto">Copiar asunto</button></div>
        <div class="col-12"><label for="req-cuerpo">Cuerpo</label>
          <textarea id="req-cuerpo" rows="14" readonly>{esc(mensaje["cuerpo"])}</textarea>
          <button type="button" class="btn btn-sm req-copy" data-copy="req-cuerpo">Copiar cuerpo</button></div>
        <div class="col-12"><label>Adjuntos ({f"generación {vigente['numero']}" if vigente else "sin generar"})</label>
          <ul class="req-list">{adjuntos}</ul>{borrador}</div>
      </div>
      <h3 class="req-subtitle">Envíos registrados</h3>
      <ul class="req-list">{registrados or '<li class="muted">Aún no se registra el envío del correo</li>'}</ul>"""
    if not read_only and vigente is not None and not estado["desactualizado"]:
        cuerpo += f"""
      <form method="post" action="/auditor/requerimiento/envio" enctype="multipart/form-data" class="req-registro">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        <input type="hidden" name="modo" value="actual">
        <input type="hidden" name="paquete_id" value="{vigente['id']}">
        <h3 class="req-subtitle">Registrar el envío efectivo</h3>
        <div class="grid req-grid">
          <div class="col-4"><label>Fecha y hora del envío *</label>
            <input type="datetime-local" name="fecha" max="{_ahora_local()}" required></div>
          <div class="col-4"><label>Destinatario(s) *</label>
            <input name="destinatario" value="{esc(datos.get("correo_para") or "")}" required maxlength="300"></div>
          <div class="col-4"><label>CCO</label><input name="copia" value="{esc(datos.get("correo_cc") or "")}" maxlength="300"></div>
          <div class="col-12"><label>Asunto enviado</label><input name="asunto" value="{esc(mensaje["asunto"])}" maxlength="300"></div>
          <div class="col-6"><label>Evidencia del envío *</label>{_archivo_input("evidencia", ADJUNTOS["evidencia_correo"][1])}</div>
        </div>
        <div class="actions req-actions"><button type="submit" class="btn btn-primary">{SVG_SAVE} Registrar envío</button></div>
      </form>"""
    historicos = req["paquetes"] if estado["desactualizado"] else req["paquetes"][1:]
    if not read_only and historicos:
        opciones = "".join(
            f'<option value="{p["id"]}">Generación {p["numero"]} · {_cuando(p["generado_at"])}</option>'
            for p in historicos
        )
        cuerpo += f"""
      <details class="req-registro">
        <summary>Registrar un correo enviado anteriormente</summary>
        <p class="req-help">Use este registro solo si el correo se envió antes de que cambiara la generación.
          Indique la fecha real y adjunte la evidencia del envío.</p>
        <form method="post" action="/auditor/requerimiento/envio" enctype="multipart/form-data">
          {hidden_inputs(csrf_token, audit_id=audit_id)}
          <input type="hidden" name="modo" value="historico">
          <div class="grid req-grid">
            <div class="col-4"><label>Fecha y hora del envío *</label>
              <input type="datetime-local" name="fecha" max="{_ahora_local()}" required></div>
            <div class="col-4"><label>Destinatario(s) *</label>
              <input name="destinatario" required maxlength="300"></div>
            <div class="col-4"><label>CCO</label><input name="copia" maxlength="300"></div>
            <div class="col-6"><label>Generación enviada *</label>
              <select name="paquete_id" required>{opciones}</select></div>
            <div class="col-6"><label>Evidencia del envío *</label>
              {_archivo_input("evidencia", ADJUNTOS["evidencia_correo"][1])}</div>
            <div class="col-12"><label>Asunto enviado</label><input name="asunto" maxlength="300"></div>
            <div class="col-12"><label>Motivo del registro posterior *</label>
              <textarea name="justificacion_historica" rows="2" minlength="15" maxlength="500" required></textarea></div>
          </div>
          <div class="actions req-actions"><button type="submit" class="btn btn-primary">{SVG_SAVE} Registrar envío histórico</button></div>
        </form>
      </details>"""
    return _seccion("correo", 5, "Correo al cliente", cuerpo,
                    _estado_badge(estado["correo_enviado"], "Envío registrado", "Sin registrar"))


def _generacion_enviada(req: dict, envio) -> str:
    paquete = next((p for p in req["paquetes"] if p["id"] == row_get(envio, "paquete_id")), None)
    if paquete is None:
        return f'{len(json.loads(row_get(envio, "archivos_json", "[]") or "[]"))} documentos'
    return f'Generación {paquete["numero"]} (4 documentos)'


def _whatsapp(audit_id: int, datos: dict, req: dict, read_only: bool, csrf_token: str) -> str:
    correos = [e for e in req["envios"] if row_get(e, "canal") == "correo"]
    avisos = [e for e in req["envios"] if row_get(e, "canal") == "whatsapp"]
    if not correos:
        cuerpo = '<p class="req-empty">Disponible después de registrar el envío del correo.</p>'
    else:
        enviado = next((p for p in req["paquetes"] if p["id"] == row_get(correos[0], "paquete_id")), None)
        texto = mensaje_whatsapp(json.loads(enviado["datos_json"]) if enviado else datos, correos[0])
        cuerpo = f"""
      <p class="req-help">Copie el mensaje y envíelo al grupo de WhatsApp del cliente. Atlas no lo envía:
        registre cuándo se notificó.</p>
      <textarea id="req-whatsapp" rows="5" readonly>{esc(texto)}</textarea>
      <div class="req-inline-actions">
        <button type="button" class="btn btn-sm req-copy" data-copy="req-whatsapp">Copiar mensaje</button>
        <a class="btn btn-sm" href="https://wa.me/?text={quote(texto)}" target="_blank" rel="noopener">Abrir WhatsApp</a>
      </div>"""
        if not read_only:
            cuerpo += f"""
      <form method="post" action="/auditor/requerimiento/whatsapp" enctype="multipart/form-data" class="req-registro">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        <div class="grid req-grid">
          <div class="col-4"><label>Fecha y hora del aviso *</label>
            <input type="datetime-local" name="fecha" max="{_ahora_local()}" required></div>
          <div class="col-4"><label>Grupo o contacto notificado *</label><input name="destinatario" required maxlength="300"></div>
          <div class="col-4"><label>Evidencia (opcional)</label>{_archivo_input("evidencia", ADJUNTOS["evidencia_whatsapp"][1], False)}</div>
        </div>
        <div class="actions req-actions"><button type="submit" class="btn btn-sm btn-primary">{SVG_SAVE} Registrar aviso</button></div>
      </form>"""
    lista = "".join(
        f'<li><strong>{_cuando(a["fecha"])}</strong> · {esc(a["destinatario"])}'
        f'<span class="req-meta">Registrado por {esc(a["autor"] or "—")}</span>'
        + (_descarga(audit_id, "adjunto", {"id": a["evidencia_id"], "nombre": "Evidencia"}) if a["evidencia_id"] else "")
        + "</li>"
        for a in avisos
    )
    if lista:
        cuerpo += f'<h3 class="req-subtitle">Avisos registrados</h3><ul class="req-list">{lista}</ul>'
    return _seccion("whatsapp", 6, "Aviso por WhatsApp", cuerpo,
                    _estado_badge(bool(avisos), "Cliente notificado", "Sin registrar"))


# ── 7. Recepción ─────────────────────────────────────────────────────────

def _respuesta(req: dict) -> str:
    if not req["respuestas"]:
        return ""
    ultima = req["respuestas"][0]
    items = json.loads(ultima["items_json"])
    cumplidos = sum(1 for i in items if i["cumplido"])
    no_aplica = sum(1 for i in items if i["no_aplica"])
    detalles = json.loads(ultima["detalles_json"])
    filas = "".join(
        f'<tr><td>Req. #{i["hoja"]}</td><td>{i["numero"]}</td>'
        f'<td>{"Cumplido" if i["cumplido"] else "No aplica" if i["no_aplica"] else "Pendiente"}</td>'
        f'<td>{esc(i["observacion"])}</td></tr>'
        for i in items if i["cumplido"] or i["no_aplica"] or i["observacion"]
    )
    cuadros = ", ".join(f"{CUADROS[c][1].capitalize()}: {len(f)}" for c, f in detalles.items() if f) or "sin filas"
    return f"""
      <h3 class="req-subtitle">Respuestas importadas del Excel ({_cuando(ultima["importado_at"])})</h3>
      <p class="req-help">{cumplidos} cumplido(s), {no_aplica} no aplica, {len(items) - cumplidos - no_aplica}
        pendiente(s). Cuadros: {esc(cuadros)}.</p>
      <details class="req-details"><summary>Ítems respondidos y observaciones</summary>
        <div class="table-wrap"><table class="req-table"><thead><tr><th>Hoja</th><th>Nro.</th><th>Estado</th>
        <th>Observación</th></tr></thead><tbody>{filas or '<tr><td colspan="4" class="muted">Sin respuestas</td></tr>'}</tbody></table></div>
      </details>"""


_IMPORTACION = {
    "importado": ("badge-green", "Respuestas importadas"),
    "rechazado": ("badge-red", "Recibido con error: no importado"),
    "revision_manual": ("badge-amber", "Requiere revisión manual: no importado"),
}


def _estado_recibido(audit_id: int, tipo: str, adjunto, read_only: bool, csrf_token: str) -> str:
    """Excel: resultado de su validación. PDF: revisión del auditor."""
    if tipo != "solicitud_respondida":
        return _revision(audit_id, adjunto, read_only, csrf_token)
    clase, texto = _IMPORTACION.get(row_get(adjunto, "importacion_estado"),
                                    ("badge-amber", "Sin validar: no importado"))
    detalle = row_get(adjunto, "importacion_detalle")
    return (f'<span class="badge {clase}">{texto}</span>'
            + (f'<span class="req-meta">{esc(detalle)}</span>' if detalle else ""))


def _recepcion(audit_id: int, req: dict, estado: dict, read_only: bool, csrf_token: str) -> str:
    filas = ""
    for tipo, (nombre, _f) in RECEPCIONES.items():
        recibidos = req["adjuntos"].get(tipo) or []
        archivos = "".join(
            f'<li>{_descarga(audit_id, "adjunto", r)}'
            f'<span class="req-meta">Recibido {esc(r["fecha"] or "")}'
            f' · registrado por {esc(r["autor"] or "—")}</span>{_huella(r)}'
            f'{"<span class=req-meta>" + esc(r["nota"]) + "</span>" if r["nota"] else ""}'
            f'{_estado_recibido(audit_id, tipo, r, read_only, csrf_token)}</li>'
            for r in recibidos
        )
        if tipo in estado["completos"]:
            badge = _estado_badge(True, "Importado" if tipo == "solicitud_respondida" else "Recibido y revisado", "")
        elif recibidos:
            badge = ('<span class="badge badge-amber">Recibido con error</span>' if tipo == "solicitud_respondida"
                     else '<span class="badge badge-amber">Recibido, pendiente de revisión</span>')
        else:
            badge = _estado_badge(False, "", "Pendiente")
        filas += f"""
        <div class="req-doc">
          <div class="req-doc-head"><strong>{esc(nombre)}</strong>{badge}</div>
          {f'<ul class="req-list">{archivos}</ul>' if archivos else ""}
        </div>"""
    resumen = ""
    if estado["recepcion_parcial"]:
        resumen = (f'<div class="fin-alert alert-medium">{SVG_INFO}<div><strong>Recepción incompleta.</strong> '
                   f'Falta recibir, revisar o importar: {esc(", ".join(estado["pendientes_recepcion"]))}.</div></div>')
    cuerpo = f"{resumen}<div class=\"req-docs\">{filas}</div>{_respuesta(req)}"
    if not read_only:
        opciones = "".join(f'<option value="{t}">{esc(n)}</option>' for t, (n, _f) in RECEPCIONES.items())
        cuerpo += f"""
      <form method="post" action="/auditor/requerimiento/recepcion" enctype="multipart/form-data" class="req-registro">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        <h3 class="req-subtitle">Registrar un documento recibido</h3>
        <div class="grid req-grid">
          <div class="col-4"><label>Documento *</label><select name="tipo" required>{opciones}</select></div>
          <div class="col-4"><label>Fecha de recepción *</label>
            <input type="date" name="fecha" max="{date.today().isoformat()}" value="{date.today().isoformat()}" required></div>
          <div class="col-4"><label>Archivo recibido *</label>{_archivo_input("archivo", ("pdf", "xlsx"))}</div>
          <div class="col-12"><label>Nota</label><input name="nota" maxlength="500"
            placeholder="p. ej. recibido por correo de la contadora"></div>
        </div>
        <p class="field-hint">Los PDF firmados quedan pendientes hasta que registre su revisión. El Excel
          respondido se importa solo si corresponde a una generación enviada de esta auditoría (referencia, RUC,
          ejercicio y estructura); si no, se conserva como evidencia y puede cargar una versión corregida.</p>
        <div class="actions req-actions"><button type="submit" class="btn btn-primary">{SVG_SAVE} Registrar recepción</button></div>
      </form>"""
    todo = not estado["pendientes_recepcion"]
    return _seccion("recepcion", 7, "Recepción de documentos del cliente", cuerpo,
                    _estado_badge(todo, "Todo recibido y revisado",
                                  f"{len(estado['completos'])} de {len(RECEPCIONES)} completos"))


_COPIAR_JS = """
<script>
document.querySelectorAll('.req-copy').forEach(function (boton) {
  boton.addEventListener('click', function () {
    var campo = document.getElementById(boton.dataset.copy);
    if (!campo) return;
    var original = boton.textContent;
    navigator.clipboard.writeText(campo.value).then(function () {
      boton.textContent = 'Copiado';
      setTimeout(function () { boton.textContent = original; }, 1500);
    });
  });
});
</script>
"""


def render(user: sqlite3.Row, query: dict, active_path: str, csrf_token: str = "") -> str:
    """Página completa de la pestaña "Requerimiento inicial"."""
    audit = get_audit(form_id(query, "audit_id"), user)
    if not audit:
        return layout("Acceso denegado", user,
                      '<div class="error-msg">Auditoría no disponible o no asignada.</div>',
                      active_path="/auditor")
    audit_id = audit["id"]
    read_only = user["role"] == "admin"
    req = get_requerimiento_context(audit_id)
    sugeridos = precarga(audit, get_audit_context(audit_id))
    datos = datos_efectivos(req["guardado"], sugeridos)
    falt = faltantes(datos)
    ruc_inconsistente = bool(req["guardado"]) and (datos.get("ruc") or "") != (audit["ruc"] or "")
    if ruc_inconsistente:
        falt.append("RUC del requerimiento: confirme los datos con el RUC de la auditoría")
    estado = estado_proceso({
        "paquetes": req["paquetes"], "contratos": req["adjuntos"]["contrato"],
        "confirmado": req["guardado"] is not None, "faltantes": falt, "envios": req["envios"],
        "recepciones": {t: req["adjuntos"].get(t) for t in RECEPCIONES},
        "desactualizado": bool(req["paquetes"]) and (ruc_inconsistente or
                                                       paquete_desactualizado(req["paquetes"][0],
                                                                            instantanea_actual(req))),
    })
    solo_lectura = (f'<span class="badge badge-gray">{SVG_INFO} Modo solo lectura</span>' if read_only else "")
    content = f"""
    <div class="{"readonly-mode" if read_only else ""}">
      {proceso_nav(audit_id, audit["company_name"], "requerimiento", read_only)}
      <div class="req-head">
        <div>
          <span class="radar-lookup-kicker">Paso 2 del proceso</span>
          <h1>Requerimiento inicial</h1>
          <p class="muted">Carta de encargo, certificados y solicitud de información para
            {esc(audit["company_name"])} · RUC {esc(audit["ruc"] or "pendiente")}</p>
        </div>
        {solo_lectura}
      </div>
      {_pasos(estado)}
      {_contrato(audit_id, req["adjuntos"]["contrato"], estado, read_only, csrf_token)}
      {_datos(audit_id, datos, sugeridos, req["guardado"], falt, read_only, csrf_token, audit["ruc"] or "")}
      {_solicitud(audit_id, datos, req, read_only, csrf_token)}
      {_documentos(audit_id, req, estado, falt, req["guardado"] is not None, read_only, csrf_token)}
      {_correo(audit_id, datos, req, estado, read_only, csrf_token)}
      {_whatsapp(audit_id, datos, req, read_only, csrf_token)}
      {_recepcion(audit_id, req, estado, read_only, csrf_token)}
    </div>
    {_COPIAR_JS}
    """
    flash = form_value(query, "msg")
    err = form_value(query, "err")
    return layout("Requerimiento inicial", user, content, flash or (f"Error: {err}" if err else ""),
                  active_path="/admin" if read_only else "/auditor")
