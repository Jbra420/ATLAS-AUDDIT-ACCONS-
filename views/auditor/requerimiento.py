"""
views/auditor/requerimiento.py — Pestaña principal "Requerimiento inicial".

Segundo paso del proceso, al mismo nivel que "Levantamiento de información":
comparte la auditoría y sus datos confirmados, pero tiene su propia página,
rutas y registros. Tres pasos: el auditor asignado confirma los datos
(precargados del levantamiento) y elige las fechas en el calendario, genera
los cuatro documentos (una generación a la vez) y prepara y registra el
correo al cliente. El jefe auditor ve lo mismo en solo lectura: descarga lo
registrado, pero no arma vistas previas ni borradores de correo.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from database import get_audit, get_audit_context, get_requerimiento_context
from services.requerimiento import (
    ADJUNTOS,
    AVISO_PLANTILLA,
    CAMPOS_TEXTO,
    DOCUMENTOS,
    ENTREGABLES,
    EQUIPO_PREDETERMINADO,
    ETIQUETAS_FORMULARIO,
    MAX_EQUIPO,
    cronograma_predeterminado,
    correo,
    datos_efectivos,
    estado_proceso,
    faltantes,
    faltantes_carta,
    faltantes_paso1,
    fecha_larga,
    instantanea_actual,
    leer_entrega,
    paquete_desactualizado,
    precarga,
    texto_inventario,
)
from services.rowutil import row_get
from ui.components import modal, proceso_nav
from ui.helpers import esc, form_id, form_value, hidden_inputs
from ui.icons import SVG_ARROW_RIGHT, SVG_CHECK, SVG_DOWNLOAD, SVG_FILE, SVG_INFO, SVG_SAVE, SVG_TRASH
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


# ── Estado del proceso ───────────────────────────────────────────────────

def _pasos(estado: dict) -> str:
    items = "".join(
        f'<li class="req-step{" hecho" if p["hecho"] else ""}{" actual" if p["actual"] else ""}">'
        f'<span class="req-step-dot">{SVG_CHECK if p["hecho"] else i}</span>'
        f'<span class="req-step-label">{esc(p["label"])}</span></li>'
        for i, p in enumerate(estado["pasos"], start=1)
    )
    return f'<ol class="req-steps" aria-label="Estado del requerimiento inicial">{items}</ol>'


# ── 1. Datos del requerimiento ──────────────────────────────────────────

_AYUDAS = {
    "anio_auditado": "Ejercicio económico que se audita.",
    "ruc": "RUC de la auditoría: se corrige en Levantamiento de información.",
    "fecha_documentos": "Fecha en que se envía el correo: la llevan la carta de encargo y los certificados.",
    "representante_cargo": "Va bajo la firma del representante en la carta.",
    "fecha_corte": "Fecha de la información financiera de la auditoría preliminar, dentro del año de auditoría.",
}
_PENDIENTE = '<span class="muted">Pendiente</span>'


def _valor(campo: str, datos: dict, ruc_auditoria: str) -> str:
    valor = ruc_auditoria if campo == "ruc" else datos.get(campo)
    return "" if valor is None else str(valor)


def _origen(campo: str, valor: str, sugeridos: dict) -> str:
    """Si el valor es el del levantamiento o el auditor lo cambió."""
    if campo == "ruc":
        return '<span class="req-origen">Del levantamiento</span>'
    if campo not in sugeridos:
        return ""
    if valor == sugeridos[campo][0]:
        texto = "Del levantamiento" if sugeridos[campo][1].startswith("Levantamiento") else "Predeterminado"
        return f'<span class="req-origen" title="{esc(sugeridos[campo][1])}">{texto}</span>'
    return f'<span class="req-origen editado" title="Levantamiento: {esc(sugeridos[campo][0])}">Editado</span>'


def _fecha_texto(iso: str) -> str:
    return fecha_larga(iso, "de") if iso else ""


def _campo_texto(campo: str, valor: str, sugeridos: dict, read_only: bool, clase: str) -> str:
    if read_only:
        control = f'<div class="req-valor">{esc(valor) or _PENDIENTE}</div>'
    elif campo == "ruc":
        control = f'<input id="req-ruc" name="ruc" value="{esc(valor)}" readonly aria-readonly="true">'
    elif campo == "anio_auditado":
        control = (f'<input id="req-anio_auditado" name="anio_auditado" value="{esc(valor)}" inputmode="numeric" '
                   f'maxlength="4" pattern="\\d{{4}}" placeholder="AAAA">')
    else:
        control = (f'<input id="req-{campo}" name="{campo}" value="{esc(valor)}" '
                   f'maxlength="{CAMPOS_TEXTO[campo][1]}">')
    ayuda = _AYUDAS.get(campo, "")
    return f"""
        <div class="{clase}">
          <div class="req-label"><label for="req-{campo}">{esc(ETIQUETAS_FORMULARIO[campo])}</label>
            {_origen(campo, valor, sugeridos)}</div>
          {control}
          {f'<div class="field-hint">{esc(ayuda)}</div>' if ayuda else ""}
        </div>"""


def _campo_fecha(campo: str, valor: str, read_only: bool, anio: str) -> str:
    if read_only:
        control = f'<div class="req-valor">{esc(_fecha_texto(valor)) or _PENDIENTE}</div>'
    else:
        limites = ""
        if campo == "fecha_corte":
            # El corte cae en el año de auditoría: el calendario sigue al campo del año.
            limites = ' data-dp-anio="req-anio_auditado"'
            if anio.isdigit():
                limites += f' min="{anio}-01-01" max="{anio}-12-31"'
        control = (f'<div class="dp" data-dp="single">'
                   f'<input type="date" id="req-{campo}" name="{campo}" value="{esc(valor)}" class="dp-input"'
                   f'{limites}></div>')
    return f"""
        <div class="col-4">
          <div class="req-label"><label for="req-{campo}">{esc(ETIQUETAS_FORMULARIO[campo])}</label></div>
          {control}
          <div class="field-hint">{esc(_AYUDAS[campo])}</div>
        </div>"""


def _campo_inventario(datos: dict, read_only: bool) -> str:
    desde, hasta = datos.get("inventario_desde") or "", datos.get("inventario_hasta") or ""
    texto = texto_inventario(desde, hasta)
    if read_only:
        control = f'<div class="req-valor">{esc(texto) or _PENDIENTE}</div>'
    else:
        control = f"""<div class="dp" data-dp="range" data-dp-texto="req-inventario-texto">
            <input type="date" id="req-inventario_desde" name="inventario_desde" value="{esc(desde)}"
              class="dp-input" aria-label="Levantamiento de inventarios: desde">
            <input type="date" id="req-inventario_hasta" name="inventario_hasta" value="{esc(hasta)}"
              class="dp-input" aria-label="Levantamiento de inventarios: hasta"></div>"""
    return f"""
        <div class="col-4">
          <div class="req-label"><label for="req-inventario_desde">Fecha tentativa levantamiento de
            inventarios</label></div>
          {control}
          <div class="field-hint"><span>En la carta y el correo:
            <strong id="req-inventario-texto">{esc(texto) or "elija el rango"}</strong></span></div>
        </div>"""


def _hoja_datos(datos: dict, ruc_auditoria: str) -> str:
    """Los datos como quedan en los documentos, en el orden de la hoja de
    datos de Auddit. Se actualiza mientras el auditor edita el formulario."""
    celdas = (
        ("anio_auditado", "Año de auditoría", _valor("anio_auditado", datos, ruc_auditoria)),
        ("empresa", "Empresa", _valor("empresa", datos, ruc_auditoria).upper()),
        ("representante_nombre", "Representante legal", _valor("representante_nombre", datos, "").upper()),
        ("fecha_documentos", "Fecha carta de encargo", _fecha_texto(datos.get("fecha_documentos") or "")),
        ("fecha_corte", "Corte auditoría preliminar", _fecha_texto(datos.get("fecha_corte") or "")),
        ("inventario", "Fecha tentativa levantamiento de inventarios",
         texto_inventario(datos.get("inventario_desde") or "", datos.get("inventario_hasta") or "")),
        ("representante_identificacion", "Cédula del representante",
         _valor("representante_identificacion", datos, "")),
        ("ruc", "RUC de la empresa", ruc_auditoria),
    )
    cabecera = "".join(f"<th>{esc(etiqueta)}</th>" for _c, etiqueta, _v in celdas)
    fila = "".join(f'<td data-resumen="{c}">{esc(v) or "—"}</td>' for c, _e, v in celdas)
    return f"""
      <details class="req-datos-preview">
        <summary>{SVG_FILE} Ver datos en los documentos</summary>
        <div class="table-wrap req-hoja"><table>
          <thead><tr>{cabecera}</tr></thead><tbody><tr>{fila}</tr></tbody>
        </table></div>
      </details>"""


def _resumen_datos(datos: dict, ruc_auditoria: str) -> str:
    empresa = (_valor("empresa", datos, "") or "Empresa pendiente").upper()
    representante = _valor("representante_nombre", datos, "") or "Pendiente"
    carta = _fecha_texto(datos.get("fecha_documentos") or "") or "Pendiente"
    return f"""
      <div class="req-datos-overview" aria-label="Resumen de los datos del requerimiento">
        <div class="req-datos-empresa">
          <span class="req-datos-caption">Empresa</span>
          <strong data-resumen="empresa">{esc(empresa)}</strong>
          <span>RUC <span data-resumen="ruc">{esc(ruc_auditoria) or '—'}</span></span>
        </div>
        <dl class="req-datos-facts">
          <div><dt>Ejercicio</dt><dd data-resumen="anio_auditado">{esc(_valor('anio_auditado', datos, '')) or '—'}</dd></div>
          <div><dt>Representante</dt><dd data-resumen="representante_nombre">{esc(representante)}</dd></div>
          <div><dt>Fecha de la carta</dt><dd data-resumen="fecha_documentos">{esc(carta)}</dd></div>
        </dl>
      </div>"""


def _datos(audit_id: int, datos: dict, sugeridos: dict, guardado, falt: list, read_only: bool, csrf_token: str,
           ruc_auditoria: str) -> str:
    confirmado = guardado is not None
    anio = _valor("anio_auditado", datos, "")
    empresa = (_campo_texto("anio_auditado", anio, sugeridos, read_only, "col-3")
               + _campo_texto("empresa", _valor("empresa", datos, ""), sugeridos, read_only, "col-5")
               + _campo_texto("ruc", ruc_auditoria, sugeridos, read_only, "col-4")
               + _campo_texto("representante_nombre", _valor("representante_nombre", datos, ""), sugeridos,
                              read_only, "col-8")
               + _campo_texto("representante_identificacion", _valor("representante_identificacion", datos, ""),
                              sugeridos, read_only, "col-4"))
    fechas = (_campo_fecha("fecha_documentos", datos.get("fecha_documentos") or "", read_only, anio)
              + _campo_fecha("fecha_corte", datos.get("fecha_corte") or "", read_only, anio)
              + _campo_inventario(datos, read_only))
    pendientes = ""
    if falt:
        items = "".join(f"<li>{esc(campo)}</li>" for campo in falt)
        pendientes = (f'<div class="req-datos-pendientes"><strong>{len(falt)} dato(s) por completar</strong>'
                      f'<ul>{items}</ul></div>')
    estado = (_estado_badge(True, f"Confirmados {_cuando(row_get(guardado, 'confirmado_at'))}", "")
              if confirmado and not falt else
              _estado_badge(False, "", "Por confirmar" if not confirmado else "Incompletos"))
    campos = f"""
      <p class="req-help">Los datos de la empresa y del representante vienen del Levantamiento de información;
        revíselos y elija las fechas del requerimiento en el calendario.</p>
      {pendientes}
      <h3 class="req-subtitle">Empresa y representante</h3>
      <div class="grid req-grid">{empresa}</div>
      <h3 class="req-subtitle">Fechas del requerimiento</h3>
      <div class="grid req-grid">{fechas}</div>"""
    if not read_only:
        campos = f"""
      <form method="post" action="/auditor/requerimiento/datos" id="req-datos-form">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        {campos}
        <div class="actions req-actions">
          <button type="submit" class="btn btn-primary">{SVG_SAVE} Confirmar y continuar</button>
        </div>
      </form>"""
    accion = ("Ver detalles" if read_only else "Completar datos" if falt else "Editar datos")
    total = f"{len(falt)} pendiente(s)" if falt else "Datos completos"
    cuerpo = f"""
      {_resumen_datos(datos, ruc_auditoria)}
      <details class="req-datos-editor" id="req-datos-editor">
        <summary><span>{accion}</span><span class="req-datos-toggle-meta">{total}</span>
          <span class="req-datos-chevron">{SVG_ARROW_RIGHT}</span></summary>
        <div class="req-datos-editor-body">{campos}</div>
      </details>
      {_hoja_datos(datos, ruc_auditoria)}"""
    return _seccion("datos", 1, "Datos del requerimiento", cuerpo, estado)


# ── 2a. Requisitos editables de cada documento (modales del paso 2) ────

_MESES_NOMBRE = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
                 "Octubre", "Noviembre", "Diciembre")


def _equipo(datos: dict, read_only: bool) -> str:
    """Integrantes del equipo: se editan, se quitan y se agregan. Sin datos
    guardados se proponen los de la carta de Auddit."""
    integrantes = datos.get("equipo") or list(EQUIPO_PREDETERMINADO)
    if read_only:
        return ('<ul class="req-list">' + "".join(f"<li>{esc(n)}</li>" for n in integrantes) + "</ul>")
    filas = "".join(
        f"""<li class="req-lista-fila">
          <input name="equipo_integrante" value="{esc(n)}" maxlength="120"
            aria-label="Integrante {i} del equipo" placeholder="Título y nombre, p. ej. Lcda. Ana Pérez">
          <button type="button" class="btn btn-sm req-lista-quitar" title="Quitar integrante"
            aria-label="Quitar a {esc(n) or f'integrante {i}'}">{SVG_TRASH}</button></li>"""
        # Sin JavaScript no se pueden agregar filas: van dos vacías de reserva.
        for i, n in enumerate([*integrantes, "", ""][:MAX_EQUIPO], start=1)
    )
    return f"""
      <ol class="req-lista" data-max="{MAX_EQUIPO}">{filas}</ol>
      <button type="button" class="btn btn-sm req-lista-agregar" hidden>+ Agregar integrante</button>
      <div class="field-hint">Uno por fila, con su título. Se imprimen en la sección 4 de la carta en este orden.</div>"""


def _cronograma(datos: dict, read_only: bool) -> str:
    """Mes y año de entrega de cada informe ("Hasta febrero 2027")."""
    propuestas = {c["entregable"]: c["fecha"] for c in cronograma_predeterminado(datos.get("anio_auditado"))}
    guardadas = {c.get("entregable"): c.get("fecha") for c in datos.get("cronograma") or []}
    anio_base = int(datos["anio_auditado"]) if str(datos.get("anio_auditado") or "").isdigit() else date.today().year
    filas = ""
    for i, entregable in enumerate(ENTREGABLES):
        texto = guardadas.get(entregable) or propuestas.get(entregable) or ""
        if read_only:
            filas += f"<tr><td>{esc(entregable)}</td><td>{esc(texto) or _PENDIENTE}</td></tr>"
            continue
        mes, anio = leer_entrega(texto) or (0, 0)
        meses = '<option value="">Mes</option>' + "".join(
            f'<option value="{m}"{" selected" if m == mes else ""}>{nombre}</option>'
            for m, nombre in enumerate(_MESES_NOMBRE, start=1))
        anios = '<option value="">Año</option>' + "".join(
            f'<option value="{a}"{" selected" if a == anio else ""}>{a}</option>'
            for a in sorted({*range(anio_base, anio_base + 4), *([anio] if anio else [])}))
        filas += f"""<tr><td>{esc(entregable)}</td><td><div class="req-entrega">
          <span>Hasta</span>
          <select name="entrega_mes_{i}" aria-label="Mes de entrega: {esc(entregable)}">{meses}</select>
          <select name="entrega_anio_{i}" aria-label="Año de entrega: {esc(entregable)}">{anios}</select>
        </div></td></tr>"""
    return f"""
      <div class="table-wrap"><table class="req-table req-cronograma">
        <thead><tr><th>Entregable</th><th>Fecha de entrega</th></tr></thead><tbody>{filas}</tbody>
      </table></div>"""


def _modal_carta(audit_id: int, datos: dict, sugeridos: dict, csrf_token: str) -> str:
    """Requisitos editables de la carta de encargo: cargo del representante,
    firma de Auddit, equipo y cronograma."""
    firmas = (_campo_texto("representante_cargo", _valor("representante_cargo", datos, ""), sugeridos, False,
                           "col-4")
              + _campo_texto("auddit_representante", _valor("auddit_representante", datos, ""), sugeridos,
                             False, "col-4")
              + _campo_texto("auddit_cargo", _valor("auddit_cargo", datos, ""), sugeridos, False, "col-4"))
    contenido = f"""
      <p class="modal-desc">Solo se usan en la carta. La empresa, el gerente, el año y las fechas vienen del
        paso 1.</p>
      <form method="post" action="/auditor/requerimiento/carta">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        <div class="grid req-grid">{firmas}</div>
        <div class="grid req-grid">
          <div class="col-6"><div class="req-label"><label>Equipo de auditoría</label></div>
            {_equipo(datos, False)}</div>
          <div class="col-6"><div class="req-label"><label>Cronograma de entrega de informes</label></div>
            {_cronograma(datos, False)}</div>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn" onclick="closeModal('req-modal-carta')">Cancelar</button>
          <button type="submit" class="btn btn-primary">{SVG_SAVE} Guardar requisitos</button>
        </div>
      </form>"""
    return modal("req-modal-carta", "Carta de encargo: requisitos editables", contenido, wide=True)


def _resumen_carta(datos: dict) -> list[str]:
    equipo = datos.get("equipo") or []
    ultima = next((c.get("fecha") for c in reversed(datos.get("cronograma") or []) if c.get("fecha")), "")
    cargo = _valor("representante_cargo", datos, "") or "sin cargo"
    return [
        f"Gerente: {_valor('representante_nombre', datos, '') or '—'} ({cargo})",
        f"Equipo: {len(equipo)} integrante{'' if len(equipo) == 1 else 's'}",
        f"Cronograma: {ultima.lower() if ultima else 'sin fechas'}",
        f"Firma de Auddit: {_valor('auddit_representante', datos, '') or '—'}",
    ]


def _tarjetas(audit_id: int, datos: dict, sugeridos: dict, estado: dict, read_only: bool, csrf_token: str) -> str:
    """Una tarjeta por documento: sus requisitos, el botón que abre su modal
    y la vista previa sin guardar."""
    tarjetas, modales = "", ""
    for tipo, (nombre, extension, _p) in DOCUMENTOS.items():
        editar = ""
        if tipo == "carta":
            pendientes = faltantes_carta(datos)
            lineas = _resumen_carta(datos)
            estado_doc = (f'<span class="badge badge-amber">Falta: {esc(", ".join(pendientes))}</span>'
                          if pendientes else _estado_badge(True, "Requisitos completos", ""))
            if not read_only:
                editar = ('<button type="button" class="btn btn-sm btn-primary" '
                          'onclick="openModal(\'req-modal-carta\')">Editar requisitos</button>')
                modales += _modal_carta(audit_id, datos, sugeridos, csrf_token)
        else:
            lineas = ["Usa los datos del paso 1."]
            estado_doc = '<span class="badge badge-gray">Requisitos por definir</span>'
        previa = ("" if read_only or not estado["puede_generar"] else
                  f'<a class="btn btn-sm" href="/requerimiento/vista-previa?audit_id={audit_id}&doc={tipo}">'
                  f'{SVG_FILE} Vista previa</a>')
        tarjetas += f"""
        <article class="req-doc-card">
          <div class="req-doc-card-head">
            <strong>{esc(nombre)}</strong><span class="badge badge-gray">{extension.upper()}</span>
          </div>
          <div>{estado_doc}</div>
          <ul class="req-doc-card-lista">{"".join(f"<li>{esc(linea)}</li>" for linea in lineas)}</ul>
          <div class="req-doc-card-acciones">{editar}{previa}</div>
        </article>"""
    return f'<div class="req-doc-cards">{tarjetas}</div>{modales}'


# ── 2. Vista previa, generación y versiones ─────────────────────────────

_ENVIADA = ' <span class="badge badge-blue">Enviada</span>'


def _enviadas(envios: list) -> set[int]:
    ids: set[int] = set()
    for e in envios:
        if row_get(e, "canal") == "correo":
            ids.update(json.loads(row_get(e, "archivos_json", "[]") or "[]"))
    return ids


def _alerta_desactualizada(paquete) -> str:
    return (f'<div class="fin-alert alert-medium">{SVG_INFO}<div><strong>Documentos desactualizados.</strong> '
            f'Los datos cambiaron después de la generación {paquete["numero"]}. Sus documentos y '
            'los envíos ya registrados no cambian, pero para un envío nuevo genere los documentos otra vez antes '
            'de preparar el correo.</div></div>')


def _documentos(audit_id: int, req: dict, estado: dict, datos: dict, sugeridos: dict, read_only: bool,
                csrf_token: str) -> str:
    enviadas = _enviadas(req["envios"])
    enviados = {row_get(e, "paquete_id") for e in req["envios"] if row_get(e, "paquete_id")}
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
    elif estado["puede_generar"]:
        accion = f"""
        <form method="post" action="/auditor/requerimiento/generar" class="req-generar">
          {hidden_inputs(csrf_token, audit_id=audit_id)}
          <button type="submit" class="btn btn-primary">{SVG_FILE} Generar los cuatro documentos</button>
          <span class="field-hint">Cada generación crea los cuatro documentos juntos; las anteriores y las ya
            enviadas no cambian.</span>
        </form>"""
    else:
        accion = (f'<div class="fin-alert alert-medium">{SVG_INFO}<div>Para generar los documentos complete los '
                  'requisitos de cada documento.</div></div>')
    alerta = _alerta_desactualizada(req["paquetes"][0]) if estado["desactualizado"] else ""
    historial = f'<div class="req-docs">{generaciones or "<p class=req-empty>Sin documentos generados.</p>"}</div>'
    if not estado["paso1_completo"]:
        # Hasta completar el paso 1 no se editan requisitos ni se genera.
        cuerpo = (f'<div class="fin-alert alert-medium">{SVG_INFO}<div>Complete y confirme los datos del paso 1 '
                  'para continuar.</div></div>' + (historial if req["paquetes"] else ""))
    else:
        cuerpo = f"""
      <p class="req-help">Revise los requisitos de cada documento con «Editar requisitos» y genere los cuatro
        juntos. {esc(AVISO_PLANTILLA)}</p>
      {alerta}
      {_tarjetas(audit_id, datos, sugeridos, estado, read_only, csrf_token)}
      {accion}
      <h3 class="req-subtitle">Generaciones</h3>
      {historial}"""
    if not estado["generado"]:
        badge = _estado_badge(False, "", "Sin generar")
    elif estado["desactualizado"]:
        badge = '<span class="badge badge-amber">Desactualizados</span>'
    else:
        badge = _estado_badge(True, f"Generación {req['paquetes'][0]['numero']} vigente", "")
    return _seccion("documentos", 2, "Documentos del requerimiento", cuerpo, badge)


# ── 3. Correo al cliente ────────────────────────────────────────────────

def _ahora_local() -> str:
    return datetime.now().replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _destinatario(audit_id: int, datos: dict, csrf_token: str) -> str:
    return f"""
      <form method="post" action="/auditor/requerimiento/destinatario" class="req-registro">
        {hidden_inputs(csrf_token, audit_id=audit_id)}
        <div class="grid req-grid">
          <div class="col-4"><label for="req-correo_para_nombre">Nombre del destinatario</label>
            <input id="req-correo_para_nombre" name="correo_para_nombre"
              value="{esc(datos.get("correo_para_nombre") or "")}" maxlength="{CAMPOS_TEXTO["correo_para_nombre"][1]}"></div>
          <div class="col-4"><label for="req-correo_para">Correo del destinatario</label>
            <input id="req-correo_para" name="correo_para" value="{esc(datos.get("correo_para") or "")}"
              maxlength="{CAMPOS_TEXTO["correo_para"][1]}" placeholder="gerencia@cliente.com"></div>
          <div class="col-4"><label for="req-correo_cc">Copia oculta (CCO)</label>
            <input id="req-correo_cc" name="correo_cc" value="{esc(datos.get("correo_cc") or "")}"
              maxlength="{CAMPOS_TEXTO["correo_cc"][1]}">
            <div class="field-hint">Separe varios correos con coma.</div></div>
        </div>
        <div class="actions req-actions"><button type="submit" class="btn btn-sm">{SVG_SAVE} Guardar destinatario</button></div>
      </form>"""


def _correo(audit_id: int, datos: dict, req: dict, estado: dict, read_only: bool, csrf_token: str) -> str:
    vigente = req["paquetes"][0] if req["paquetes"] else None
    # Asunto y cuerpo salen de la instantánea de la generación, igual que el
    # .eml; el destinatario es el vigente (no forma parte de los documentos).
    mensaje = correo(json.loads(vigente["datos_json"]) if vigente is not None else datos)
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
      {_destinatario(audit_id, datos, csrf_token) if not read_only and req["guardado"] is not None else ""}
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
    return _seccion("correo", 3, "Correo al cliente", cuerpo,
                    _estado_badge(estado["correo_enviado"], "Envío registrado", "Sin registrar"))


def _generacion_enviada(req: dict, envio) -> str:
    paquete = next((p for p in req["paquetes"] if p["id"] == row_get(envio, "paquete_id")), None)
    if paquete is None:
        return f'{len(json.loads(row_get(envio, "archivos_json", "[]") or "[]"))} documentos'
    return f'Generación {paquete["numero"]} (4 documentos)'


# Calendario del paso 1 (static/js/calendario.js) y hoja de datos en vivo.
_CALENDARIO_JS = ('<script src="/static/js/calendario.js" defer></script>'
                  '<script src="/static/js/lista_editable.js" defer></script>')

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
    falt, falt_paso1 = faltantes(datos), faltantes_paso1(datos)
    ruc_inconsistente = bool(req["guardado"]) and (datos.get("ruc") or "") != (audit["ruc"] or "")
    if ruc_inconsistente:
        aviso_ruc = "RUC del requerimiento: confirme los datos con el RUC de la auditoría"
        falt.append(aviso_ruc)
        falt_paso1.append(aviso_ruc)
    estado = estado_proceso({
        "paquetes": req["paquetes"], "confirmado": req["guardado"] is not None, "faltantes": falt,
        "faltantes_paso1": falt_paso1,
        "envios": req["envios"],
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
          <p class="muted">Datos, documentos y correo de inicio de la auditoría de
            {esc(audit["company_name"])} · RUC {esc(audit["ruc"] or "pendiente")}</p>
        </div>
        {solo_lectura}
      </div>
      {_pasos(estado)}
      {_datos(audit_id, datos, sugeridos, req["guardado"], falt_paso1, read_only, csrf_token, audit["ruc"] or "")}
      {_documentos(audit_id, req, estado, datos, sugeridos, read_only, csrf_token)}
      {_correo(audit_id, datos, req, estado, read_only, csrf_token)}
    </div>
    {_COPIAR_JS}
    {"" if read_only else _CALENDARIO_JS}
    """
    flash = form_value(query, "msg")
    err = form_value(query, "err")
    return layout("Requerimiento inicial", user, content, flash or (f"Error: {err}" if err else ""),
                  active_path="/admin" if read_only else "/auditor")
