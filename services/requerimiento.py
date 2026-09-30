"""
services/requerimiento.py — Reglas del Requerimiento inicial (segundo paso del proceso).

Tres pasos: (1) el auditor confirma los datos del requerimiento, precargados
del levantamiento, y elige las fechas de la carta, del corte preliminar y del
levantamiento de inventarios; (2) genera los cuatro documentos: la carta de
encargo, los certificados de compañías relacionadas y de paraísos fiscales, y
la solicitud inicial de información en Excel; (3) prepara el correo al
cliente y registra su envío.

Este módulo no consulta la base: define los documentos, los ítems y cuadros
del Excel, la precarga desde el levantamiento, las validaciones, el estado del
proceso y los textos del correo y de WhatsApp.

Los textos de la carta, los certificados, los ítems y el correo se
transcribieron de los ejemplos del documento de proceso de Auddit
("AUDITORÍA", septiembre 2026). Son la plantilla de trabajo, no un texto
legal aprobado: Auddit debe validar o reemplazar la plantilla definitiva.
"""
from __future__ import annotations

import io
import json
import re
import secrets
from datetime import date, datetime
from typing import Any

from services.identificacion import validar_identificacion
from services.normalizacion import normalizar_texto
from services.rowutil import row_get

# Cambia cuando cambian los textos o el formato de los documentos: una
# plantilla nueva justifica regenerar aunque los datos sean los mismos.
PLANTILLA_VERSION = "2026-09 — ejemplos del documento de proceso"

AVISO_PLANTILLA = (
    "Plantilla provisional: textos transcritos de los ejemplos del documento de proceso de Auddit. "
    "La plantilla legal definitiva debe validarla Auddit."
)

# Atlas comprueba que un PDF se pueda leer, no quién lo firmó: la revisión de
# empresa, ejercicio, integridad y firmas la hace el auditor y queda registrada.
AVISO_FIRMAS = (
    "Atlas no valida criptográficamente firmas electrónicas: la revisión registra lo que el auditor "
    "verificó a la vista del documento."
)
# Lo que el auditor declara haber verificado al revisar un PDF firmado.
VERIFICACIONES_REVISION = (
    ("empresa", "Corresponde a la empresa y RUC de esta auditoría"),
    ("ejercicio", "Corresponde al ejercicio / año indicado"),
    ("integridad", "El documento está completo y legible, sin alteraciones aparentes"),
    ("firmas", "Tiene las firmas correspondientes (revisión visual; no es validación criptográfica)"),
)

# ── Documentos ───────────────────────────────────────────────────────────
# tipo -> (nombre visible, extensión, prefijo del archivo que recibe el cliente)
DOCUMENTOS = {
    "carta": ("Carta de encargo (compromiso)", "pdf", "CARTA DE ENCARGO"),
    "cert_relacionadas": ("Certificado de compañías relacionadas", "pdf", "CERTIFICADO EMPRESAS RELACIONADAS"),
    "cert_paraisos": ("Certificado de paraísos fiscales", "pdf", "CERTIFICADO PARAISOS FISCALES"),
    "solicitud": ("Solicitud inicial de información (Excel)", "xlsx", "REQUERIMIENTO DE INFORMACION"),
}

# Lo que el cliente devuelve: tipo de adjunto -> (nombre visible, formatos aceptados).
RECEPCIONES = {
    "carta_firmada": ("Carta de encargo firmada", ("pdf",)),
    "cert_relacionadas_firmado": ("Certificado de compañías relacionadas firmado", ("pdf",)),
    "cert_paraisos_firmado": ("Certificado de paraísos fiscales firmado", ("pdf",)),
    "solicitud_respondida": ("Solicitud de información respondida (Excel)", ("xlsx",)),
}

# Otros archivos que sube el auditor.
ADJUNTOS = {
    "contrato": ("Contrato de auditoría firmado", ("pdf",)),
    "evidencia_correo": ("Evidencia del correo enviado", ("pdf", "png", "jpg", "eml")),
    "evidencia_whatsapp": ("Evidencia del aviso por WhatsApp", ("pdf", "png", "jpg")),
    **RECEPCIONES,
}

MAX_ARCHIVO_BYTES = 10 * 1024 * 1024
_FIRMAS = {
    "pdf": (b"%PDF",),
    "xlsx": (b"PK\x03\x04",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
}
_EXTENSIONES = {"pdf": ("pdf",), "xlsx": ("xlsx",), "png": ("png",), "jpg": ("jpg", "jpeg"), "eml": ("eml",)}

# ── Carta de encargo ─────────────────────────────────────────────────────
# Entregables del cronograma y el mes en que vence cada uno, del año
# siguiente al auditado (carta de encargo 2026 de Auddit). El auditor cambia
# el mes y el año de cada entrega en el paso 1.
ENTREGAS_PREDETERMINADAS = (
    ("Informe de Control Interno", 2),
    ("Borrador del Informe de Auditoría", 3),
    ("Informe Final de Auditoria", 4),
    ("Informe de Cumplimiento Tributario", 7),
)
ENTREGABLES = tuple(entregable for entregable, _mes in ENTREGAS_PREDETERMINADAS)
# Datos de Auddit en la carta de encargo 2026; el auditor puede cambiarlos.
AUDDIT_EMPRESA = "AUDDIT S.A.S."
AUDDIT_REPRESENTANTE = "FERNANDO PARRA SUAREZ"
AUDDIT_CARGO = "GERENTE"
EQUIPO_PREDETERMINADO = (
    "Mgtr. Fernando Parra Suarez",
    "Mgtr. María de Lourdes Mosquera",
    "Lcda. Jennifer Barreto Zhagui",
    "Lcda. Camila Guevara Lucero",
)
MAX_EQUIPO = 12

# ── Solicitud de información: hojas 1 y 2 ────────────────────────────────
# {anio_auditado}, {anio_cerrado}, {rango} ("Enero a Julio de 2026") y
# {corte} ("31 de Julio de 2026") salen de los datos confirmados.
ITEMS_HOJA1 = (
    (1, "Carta de encargo debidamente aceptada y firmada.", ""),
    (2, "Organigrama de la Compañía.", ""),
    (3, "Listado de abogados con los que opera la Compañía, con indicación de dirección, número telefónico y mail.",
     "abogados"),
    (4, "Certificación de relación o no con empresas en paraísos fiscales, firmada electrónicamente por la Gerencia.",
     ""),
    (5, "Certificación de relación o no con empresas Locales o del exterior, firmada electrónicamente por la "
        "Gerencia.", ""),
    (6, "Detalle de los principales contratos con terceros, seguridad, transporte, arrendamientos, "
        "mantenimientos, etc., vigentes.", "contratos"),
    (7, "Informe de Control Interno e Informe de cumplimiento tributario del año {anio_cerrado} en formato PDF y "
        "los anexos del ICT en formato EXCEL.", ""),
    (8, "Requerimientos / notificaciones del SRI, IESS, Supercias, Ministerio de Trabajo Y otros generados "
        "durante el año {anio_auditado}.", ""),
    (9, "Comprobantes escaneados de los últimos pagos de impuesto predial, impuesto a los activos, patente "
        "municipal, contribución a la Superintendencia de Compañías.", ""),
    (10, "Manuales de procedimientos y funciones utilizados por la Compañía.", ""),
    (11, "Políticas de crédito de la empresa, política de calificación de clientes, política de descuentos, "
         "niveles de autorización.", ""),
    (12, "Políticas de negociación de compras, calificación de proveedores, niveles de autorización de compras, "
         "política de pago a proveedores.", ""),
    (13, "Identificación del marco de información financiera aplicado por la Compañía y políticas contables "
         "vigentes.", ""),
    (14, "Balance General, Estado de Resultados finales y Conciliación Tributaria al 31/12/{anio_cerrado} "
         "(Formato Excel Y PDF firmado electrónicamente).", ""),
    (15, "Catálogo de cuentas formato Excel.", ""),
)

# (sección, [(número, detalle, formato, cuadro enlazado)])
ITEMS_HOJA2 = (
    ("ANÁLISIS TRIBUTARIO", (
        (1, "Declaraciones mensuales Iva de {rango} (Formulario 104).", "PDF", ""),
        (2, "Declaraciones mensuales de retenciones del impuesto a la renta de {rango} (Formulario 103).", "PDF", ""),
        (3, "Talón Resumen Anexo Transaccional Simplificado de {rango}.", "PDF", ""),
        (4, "Reporte de Ventas mensuales desde el 1 de Enero hasta el {corte}.", "EXCEL", ""),
        (5, "Reporte de Compras mensuales desde el 1 de Enero hasta el {corte}.", "EXCEL", ""),
        (6, "Reporte de Retenciones en compras desde el 1 de Enero hasta el {corte}.", "EXCEL", ""),
        (7, "Reporte de Retenciones en ventas desde el 1 de Enero hasta el {corte}.", "EXCEL", ""),
    )),
    ("ANÁLISIS A NÓMINA", (
        (8, "Roles de pago consolidados de {rango}.", "EXCEL", ""),
        (9, "Roles de provisiones consolidados de {rango}.", "EXCEL", ""),
        (10, "Planilla consolidada del IESS de {rango}.", "EXCEL", ""),
        (11, "Liquidaciones de haberes de {rango}.", "EXCEL", ""),
    )),
    ("ANÁLISIS DE INFORMACIÓN FINANCIERA", (
        (12, "Estado de Resultados y Balance General al {corte}.", "EXCEL", ""),
        (13, "Mayores contables que sustenten el Estado de Resultados y Balance General al {corte}.", "EXCEL", ""),
    )),
    ("ANÁLISIS A DETALLES DE CONTROL", (
        (14, "Reporte de Cuentas por cobrar Clientes, incluyendo información con respecto a la antigüedad de la "
             "cartera al {corte}.", "EXCEL", ""),
        (15, "Reporte de existencia y costo de inventarios al {corte}.", "EXCEL", ""),
        (16, "Reporte detallado de Activos fijos con sus respectivas depreciaciones desde la fecha de adquisición "
             "al {corte}.", "EXCEL", ""),
        (17, "Reporte de detalle de cuentas por pagar Proveedores al {corte}.", "EXCEL", ""),
    )),
    ("ANÁLISIS DE INFORMACIÓN ADICIONAL", (
        (18, "Detalle de juicios, litigios o pasivos contingentes que la empresa mantenga con personas naturales o "
             "jurídicas, indicar estado del litigio, nombre, dirección, mail, telf. del Asesor Legal encargado.",
         "EXCEL", "juicios"),
        (19, "Listado de Cuentas Bancarias que maneja la Compañía detallando: tipo de cuenta, numero de cuenta, "
             "entidad financiera, firmas autorizadas y nombre del oficial de cuenta.", "EXCEL", "bancos"),
        (20, "Detalle de inversiones de la compañía, pólizas, acciones, papeles comerciales, factoring, bienes "
             "inmuebles de arriendo, etc.", "EXCEL", "inversiones"),
        (21, "Detalle de obligaciones bancarias con su respectiva tabla de amortización (corto y largo plazo).",
         "EXCEL", "obligaciones"),
        (22, "Conciliaciones bancarias mensuales desde el 1 de enero hasta al {corte}.", "EXCEL", ""),
    )),
)
ITEMS = {1: tuple((n, t) for n, t, _l in ITEMS_HOJA1),
         2: tuple((n, t) for _s, filas in ITEMS_HOJA2 for n, t, _f, _l in filas)}

# ── Solicitud de información: cuadros de detalle (hojas 3 y 4) ──────────
# clave -> (hoja, título, columnas, numerado). Los numerados llevan "No." al inicio.
CUADROS = {
    "abogados": (3, "DETALLE DE ABOGADOS CON QUE OPERA LA COMPAÑÍA",
                 ("Nombres y apellidos", "Dirección", "Número celular", "Número teléfono", "Correo electrónico"),
                 False),
    "contratos": (3, "DETALLE DE LOS PRINCIPALES CONTRATOS CON TERCEROS",
                  ("Nombre del contratista", "Naturaleza del contrato", "Lugar y fecha", "Vigencia", "Monto"), False),
    "activos_arrendados": (3, "DETALLE DE ACTIVOS ARRENDADOS",
                           ("Nombre del arrendatario", "Tipo del activo arrendado", "Lugar y fecha", "Vigencia",
                            "Monto"), False),
    "bancos": (4, "LISTADO DE CUENTAS BANCARIAS QUE MANEJA LA COMPAÑÍA",
               ("Nombre del banco / cooperativa", "Número de cuenta", "Tipo de cuenta",
                "Nombre del oficial de cuenta bancaria", "Firmas autorizadas"), True),
    "obligaciones": (4, "DETALLE DE OBLIGACIONES BANCARIAS CON SU RESPECTIVA TABLA DE AMORTIZACION",
                     ("Nombre de la entidad financiera que presta", "Número de préstamo", "Tipo de préstamo",
                      "Cuantía total del préstamo", "Tasa de interés"), True),
    "inversiones": (4, "DETALLE DE INVERSIONES, POLIZAS, ACCIONES, PAPELES COMERCIALES, FACTORING QUE LA "
                       "EMPRESA MANTIENE",
                    ("Tipo de inversión", "Entidad en la que se realiza", "Monto",
                     "Tasa de interés de rendimiento"), True),
    "juicios": (4, "DETALLE DE JUICIOS, LITIGIOS O PASIVOS CONTINGENTES",
                ("Tipo de caso (explicar brevemente)", "Fecha inicio", "Estado del litigio",
                 "Asesor legal encargado"), True),
}
FILAS_MINIMAS_CUADRO = 5
MAX_FILAS_CUADRO = 40

# Nombres de las cuatro hojas. El documento de proceso no los muestra:
# son una propuesta hasta que Auddit entregue su plantilla.
HOJAS = {
    1: "Req 1 Documentos",
    2: "Req 2 Contable-Tributaria",
    3: "Abogados-Contratos-Activos",
    4: "Bancos-Inversiones-Juicios",
}

# ── Campos del requerimiento ─────────────────────────────────────────────
CAMPOS_TEXTO = {
    "empresa": ("Empresa auditada", 200),
    "ruc": ("RUC de la empresa", 13),
    "representante_titulo": ("Título del representante (p. ej. MGTR.)", 20),
    "representante_nombre": ("Representante legal / gerente", 160),
    "representante_cargo": ("Cargo del representante", 80),
    "representante_identificacion": ("Cédula del representante", 32),
    "representante_nacionalidad": ("Nacionalidad del representante", 40),
    "representante_ciudad": ("Ciudad de domicilio del representante", 80),
    "fechas_inventario": ("Fechas tentativas de inventarios y activos fijos", 200),
    "auddit_representante": ("Representante de Auddit", 120),
    "auddit_cargo": ("Cargo del representante de Auddit", 60),
    "correo_para_nombre": ("Nombre del destinatario del correo", 120),
    "correo_para": ("Correo del destinatario", 300),
    "correo_cc": ("Copia oculta (CCO)", 300),
}
CAMPOS_ANIO = {
    "anio_auditado": "Año auditado (ejercicio de la carta)",
    "anio_certificados": "Año que certifican los certificados",
    "anio_cerrado": "Último ejercicio cerrado (Req. #1, ítems 7 y 14)",
}
CAMPOS_FECHA = {
    "fecha_documentos": "Fecha de la carta de encargo",
    "fecha_corte": "Corte de la auditoría preliminar",
    "inventario_desde": "Inicio tentativo del levantamiento de inventarios",
    "inventario_hasta": "Fin tentativo del levantamiento de inventarios",
}
# Lo que el auditor completa en el paso 1, en el orden de la hoja de datos de
# Auddit. Es también todo lo que se exige para generar: el resto de campos se
# deriva de estos (completar_derivados) o del levantamiento.
FORMULARIO = (
    "anio_auditado", "empresa", "representante_nombre", "fecha_documentos", "fecha_corte",
    "inventario_desde", "inventario_hasta", "representante_identificacion", "ruc",
)
# Requisitos editables de la carta de encargo (modal del paso 2), además del
# equipo y el cronograma (listas).
FORMULARIO_CARTA = ("representante_cargo", "auddit_representante", "auddit_cargo")
OBLIGATORIOS = FORMULARIO + FORMULARIO_CARTA
# El destinatario se registra en el paso del correo: no entra en los documentos.
CAMPOS_CORREO = ("correo_para_nombre", "correo_para", "correo_cc")
OBLIGATORIOS_CORREO = ("correo_para_nombre", "correo_para")
# Etiquetas del paso 1 (las de la hoja de datos de Auddit).
ETIQUETAS_FORMULARIO = {
    "anio_auditado": "Año de auditoría",
    "empresa": "Empresa",
    "representante_nombre": "Representante legal",
    "fecha_documentos": "Fecha carta de encargo",
    "fecha_corte": "Corte auditoría preliminar",
    "inventario_desde": "Levantamiento de inventarios: desde",
    "inventario_hasta": "Levantamiento de inventarios: hasta",
    "representante_identificacion": "Cédula del representante",
    "ruc": "RUC de la empresa",
    "representante_cargo": "Cargo del representante",
    "auddit_representante": "Firma de Auddit: nombre",
    "auddit_cargo": "Firma de Auddit: cargo",
}
# Cargo del representante cuando el levantamiento no registra al gerente.
CARGO_REPRESENTANTE = "GERENTE"

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")
_CORREO_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _texto(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ── Precarga desde el levantamiento ─────────────────────────────────────

def precarga(audit: Any, ctx: dict) -> dict[str, tuple[str, str]]:
    """Valores respaldados del levantamiento que el auditor puede confirmar:
    {campo: (valor, origen)}. Solo lo que consta en el expediente; nada se
    deduce (ni identificaciones, ni años que no estén registrados)."""
    profile, admins = ctx.get("profile"), ctx.get("admins") or []
    datos: dict[str, tuple[str, str]] = {}

    empresa = _texto(row_get(profile, "razon_social_supercias")) or _texto(row_get(profile, "razon_social_sri"))
    if empresa:
        datos["empresa"] = (empresa, "Levantamiento — razón social")
    elif _texto(row_get(audit, "company_name")):
        datos["empresa"] = (_texto(row_get(audit, "company_name")), "Empresa registrada por el jefe auditor")
    if _texto(row_get(audit, "ruc")):
        datos["ruc"] = (_texto(row_get(audit, "ruc")), "Levantamiento — RUC del expediente")

    gerente = next((a for a in admins if "GERENTE" in normalizar_texto(row_get(a, "cargo")).upper()), None)
    if gerente is not None:
        origen = f"Levantamiento — Administradores ({_texto(row_get(gerente, 'fuente')) or 'Supercias'})"
        datos["representante_nombre"] = (_texto(row_get(gerente, "nombre")), origen)
        datos["representante_cargo"] = (_texto(row_get(gerente, "cargo")), origen)
        for campo, columna in (("representante_identificacion", "identificacion"),
                               ("representante_nacionalidad", "nacionalidad")):
            if _texto(row_get(gerente, columna)):
                datos[campo] = (_texto(row_get(gerente, columna)), origen)
    elif _texto(row_get(profile, "representante_legal")):
        origen = "Levantamiento — representante legal (Supercias)"
        datos["representante_nombre"] = (_texto(row_get(profile, "representante_legal")), origen)
        if _texto(row_get(profile, "representante_cargo")):
            datos["representante_cargo"] = (_texto(row_get(profile, "representante_cargo")), origen)
    datos.setdefault("representante_cargo", (CARGO_REPRESENTANTE, "Carta de encargo de Auddit"))

    periodo = _texto(row_get(audit, "period"))
    if re.fullmatch(r"\d{4}", periodo):
        datos["anio_auditado"] = (periodo, "Período auditado registrado por el jefe auditor")
    if row_get(audit, "anio_fiscal_eeff", None):
        datos["anio_cerrado"] = (str(row_get(audit, "anio_fiscal_eeff")),
                                 "Levantamiento — año fiscal confirmado de los estados financieros")
    datos["auddit_representante"] = (AUDDIT_REPRESENTANTE, "Carta de encargo de Auddit")
    datos["auddit_cargo"] = (AUDDIT_CARGO, "Carta de encargo de Auddit")
    return datos


def datos_efectivos(guardado: Any, sugeridos: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """Datos del requerimiento. Una vez que el auditor los guarda, valen solo
    los suyos (un campo que vació queda vacío); antes, la precarga respaldada
    sirve de propuesta. Incluye cronograma y equipo."""
    if guardado is None:
        datos: dict[str, Any] = {campo: valor for campo, (valor, _o) in sugeridos.items()}
        datos["equipo"] = list(EQUIPO_PREDETERMINADO)
        datos["cronograma"] = cronograma_predeterminado(datos.get("anio_auditado"))
        return datos
    datos = {
        campo: row_get(guardado, campo, None)
        for campo in (*CAMPOS_TEXTO, *CAMPOS_ANIO, *CAMPOS_FECHA)
        if row_get(guardado, campo, None) not in (None, "")
    }
    datos["cronograma"] = json.loads(row_get(guardado, "cronograma_json", "") or "[]")
    datos["equipo"] = json.loads(row_get(guardado, "equipo_json", "") or "[]")
    return datos


# ── Validación ───────────────────────────────────────────────────────────

def _anio(valor: str, etiqueta: str, hoy: date) -> int | None:
    if not valor:
        return None
    if not re.fullmatch(r"\d{4}", valor):
        raise ValueError(f"{etiqueta}: use 4 dígitos, por ejemplo 2026")
    anio = int(valor)
    if not 1990 <= anio <= hoy.year + 1:
        raise ValueError(f"{etiqueta}: debe estar entre 1990 y {hoy.year + 1}")
    return anio


def _fecha(valor: str, etiqueta: str) -> str:
    if not valor:
        return ""
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError as exc:
        raise ValueError(f"{etiqueta}: use el formato AAAA-MM-DD") from exc


def _correos(valor: str, etiqueta: str) -> str:
    partes = [p.strip() for p in re.split(r"[;,]", valor) if p.strip()]
    for parte in partes:
        if not _CORREO_RE.fullmatch(parte):
            raise ValueError(f"{etiqueta}: «{parte}» no es un correo válido")
    return ", ".join(partes)


def normalizar_datos(form: dict[str, str], hoy: date | None = None) -> dict[str, Any]:
    """Valida el formato de lo que envía el formulario y lo normaliza. Un
    campo vacío se acepta (queda pendiente); un valor con formato inválido o
    incoherente no se guarda."""
    hoy = hoy or date.today()
    datos: dict[str, Any] = {}
    for campo, (etiqueta, largo) in CAMPOS_TEXTO.items():
        valor = " ".join(_texto(form.get(campo)).split())
        if len(valor) > largo:
            raise ValueError(f"{etiqueta}: máximo {largo} caracteres")
        datos[campo] = valor
    for campo, etiqueta in CAMPOS_ANIO.items():
        datos[campo] = _anio(_texto(form.get(campo)), etiqueta, hoy)
    for campo, etiqueta in CAMPOS_FECHA.items():
        datos[campo] = _fecha(_texto(form.get(campo)), etiqueta)

    if datos["ruc"] and not re.fullmatch(r"\d{10}001", datos["ruc"]):
        raise ValueError("RUC de la empresa: 13 dígitos terminados en 001")
    if datos["representante_identificacion"]:
        _tipo, identificacion, advertencia = validar_identificacion(
            datos["representante_identificacion"], "", ("cedula", "pasaporte"),
        )
        if advertencia:
            raise ValueError(f"Cédula del representante: {advertencia}")
        datos["representante_identificacion"] = identificacion
    datos["correo_para"] = _correos(datos["correo_para"], CAMPOS_TEXTO["correo_para"][0])
    datos["correo_cc"] = _correos(datos["correo_cc"], CAMPOS_TEXTO["correo_cc"][0])

    # La fecha de corte se mueve, pero dentro del año que se audita.
    if datos["fecha_corte"] and datos["anio_auditado"] and int(datos["fecha_corte"][:4]) != datos["anio_auditado"]:
        raise ValueError(f"La fecha de corte debe pertenecer al año auditado ({datos['anio_auditado']})")
    if datos["anio_cerrado"] and datos["anio_auditado"] and datos["anio_cerrado"] > datos["anio_auditado"]:
        raise ValueError("El último ejercicio cerrado no puede ser posterior al año auditado")
    desde, hasta = datos["inventario_desde"], datos["inventario_hasta"]
    if desde and hasta and desde > hasta:
        raise ValueError("Levantamiento de inventarios: la fecha final no puede ser anterior a la inicial")

    cronograma = []
    for i, entregable in enumerate(ENTREGABLES):
        valor = " ".join(_texto(form.get(f"cronograma_{i}")).split())
        if len(valor) > 60:
            raise ValueError(f"Fecha de entrega de «{entregable}»: máximo 60 caracteres")
        cronograma.append({"entregable": entregable, "fecha": valor})
    datos["cronograma"] = cronograma
    equipo = [" ".join(linea.split()) for linea in _texto(form.get("equipo")).splitlines() if linea.strip()]
    if len(equipo) > MAX_EQUIPO or any(len(n) > 120 for n in equipo):
        raise ValueError(f"Equipo de auditoría: hasta {MAX_EQUIPO} integrantes de 120 caracteres")
    datos["equipo"] = equipo
    return datos


def formulario_desde(datos: dict[str, Any]) -> dict[str, str]:
    """Datos efectivos como los enviaría el formulario completo, para volver a
    validarlos con normalizar_datos junto con lo que el auditor cambió."""
    form = {campo: "" if datos.get(campo) is None else str(datos[campo])
            for campo in (*CAMPOS_TEXTO, *CAMPOS_ANIO, *CAMPOS_FECHA)}
    fechas = {c.get("entregable"): c.get("fecha") or "" for c in datos.get("cronograma") or []}
    form.update({f"cronograma_{i}": fechas.get(e, "") for i, e in enumerate(ENTREGABLES)})
    form["equipo"] = "\n".join(datos.get("equipo") or [])
    return form


def texto_entrega(mes: int, anio: int) -> str:
    """Fecha de entrega del cronograma, como en la carta: "Hasta febrero 2027"."""
    return f"Hasta {_MESES[mes - 1]} {anio}"


def leer_entrega(texto: str) -> tuple[int, int] | None:
    """(mes, año) de un texto "Hasta febrero 2027"; None si tiene otro formato."""
    m = re.fullmatch(r"hasta\s+([a-záéíóú]+)\s+(\d{4})", _texto(texto).lower())
    if not m or m.group(1) not in _MESES:
        return None
    return _MESES.index(m.group(1)) + 1, int(m.group(2))


def cronograma_predeterminado(anio_auditado: Any) -> list[dict[str, str]]:
    """Entregas del año siguiente al auditado, en los meses de la carta de
    Auddit; sin año de auditoría quedan sin fecha."""
    anio = int(anio_auditado) if str(anio_auditado or "").isdigit() else None
    return [{"entregable": e, "fecha": texto_entrega(mes, anio + 1) if anio else ""}
            for e, mes in ENTREGAS_PREDETERMINADAS]


def texto_inventario(desde: str, hasta: str) -> str:
    """Rango tentativo como lo citan la carta y el correo: "entre el 15 de
    octubre y el 15 de diciembre" (con el año solo si el rango cruza de año)."""
    if not desde or not hasta:
        return ""
    inicio, fin = date.fromisoformat(desde), date.fromisoformat(hasta)

    def dia(f: date, con_anio: bool) -> str:
        return f"{f.day} de {_MESES[f.month - 1]}" + (f" de {f.year}" if con_anio else "")

    cruza = inicio.year != fin.year
    if inicio == fin:
        return f"el {dia(inicio, False)}"
    return f"entre el {dia(inicio, cruza)} y el {dia(fin, cruza)}"


def completar_derivados(datos: dict[str, Any], sugeridos: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """Completa lo que los documentos usan y el paso 1 no pide: los
    certificados y el último ejercicio cerrado se refieren al año anterior al
    de auditoría (en el ejemplo del proceso, auditoría 2026 y certificados
    2025), el texto del inventario sale de su rango, y la nacionalidad del
    representante del levantamiento cuando consta."""
    anio = datos.get("anio_auditado")
    datos["anio_certificados"] = datos["anio_cerrado"] = anio - 1 if anio else None
    datos["fechas_inventario"] = texto_inventario(datos.get("inventario_desde") or "",
                                                  datos.get("inventario_hasta") or "")
    for campo in ("representante_nacionalidad", "representante_ciudad"):
        if not datos.get(campo) and campo in sugeridos:
            datos[campo] = sugeridos[campo][0]
    return datos


def _vacios(datos: dict[str, Any], campos: tuple[str, ...]) -> list[str]:
    etiquetas = {**{c: e for c, (e, _l) in CAMPOS_TEXTO.items()}, **CAMPOS_ANIO, **CAMPOS_FECHA,
                 **ETIQUETAS_FORMULARIO}
    return [etiquetas[c] for c in campos if datos.get(c) in (None, "")]


def faltantes_paso1(datos: dict[str, Any]) -> list[str]:
    """Campos del paso 1 sin completar: con todos, se pasa al paso 2."""
    return _vacios(datos, FORMULARIO)


def faltantes_carta(datos: dict[str, Any]) -> list[str]:
    """Requisitos de la carta de encargo sin completar (modal del paso 2)."""
    lista = _vacios(datos, FORMULARIO_CARTA)
    if not datos.get("equipo"):
        lista.append("Equipo de auditoría")
    fechas = {c.get("entregable"): c.get("fecha") for c in datos.get("cronograma") or []}
    if any(not fechas.get(e) for e in ENTREGABLES):
        lista.append("Fechas del cronograma de entrega de informes")
    return lista


def faltantes(datos: dict[str, Any], *, para_correo: bool = False) -> list[str]:
    """Todo lo que falta para generar los documentos: el paso 1 y los
    requisitos de cada documento (y, para el correo, el destinatario)."""
    return (faltantes_paso1(datos) + faltantes_carta(datos)
            + (_vacios(datos, OBLIGATORIOS_CORREO) if para_correo else []))


def validar_items(marcas: dict[tuple[int, int], dict[str, Any]]) -> None:
    """CUMPLIDO y NO APLICA son excluyentes en cada ítem."""
    dobles = [f"Req. #{h} ítem {n}" for (h, n), m in sorted(marcas.items()) if m.get("cumplido") and m.get("no_aplica")]
    if dobles:
        raise ValueError("Marque CUMPLIDO o NO APLICA, no ambos: " + ", ".join(dobles))


def validar_fecha_envio(valor: str, ahora: datetime | None = None) -> str:
    """Fecha y hora de un envío ya hecho (AAAA-MM-DDTHH:MM); no puede ser futura."""
    try:
        fecha = datetime.fromisoformat(_texto(valor))
    except ValueError as exc:
        raise ValueError("Indique la fecha y hora del envío (AAAA-MM-DD HH:MM)") from exc
    if fecha > (ahora or datetime.now()):
        raise ValueError("La fecha del envío no puede ser posterior a este momento")
    return fecha.replace(second=0, microsecond=0).isoformat(sep=" ")


def validar_fecha_pasada(valor: str, etiqueta: str, hoy: date | None = None) -> str:
    """Fecha de algo que ya ocurrió (recepción, firma); no puede ser futura."""
    fecha = _fecha(_texto(valor), etiqueta)
    if not fecha:
        raise ValueError(f"Indique la {etiqueta.lower()}")
    if fecha > (hoy or date.today()).isoformat():
        raise ValueError(f"La {etiqueta.lower()} no puede ser posterior a hoy")
    return fecha


# ── Textos con fechas ────────────────────────────────────────────────────

def fecha_larga(iso: str, conector: str = "del") -> str:
    """2026-09-01 -> "01 de septiembre del 2026" (formato de la carta)."""
    f = date.fromisoformat(iso)
    return f"{f.day:02d} de {_MESES[f.month - 1]} {conector} {f.year}"


def _fecha_excel(iso: str) -> str:
    """2026-07-31 -> "31 de Julio de 2026" (formato del Excel del ejemplo)."""
    f = date.fromisoformat(iso)
    return f"{f.day} de {_MESES[f.month - 1].capitalize()} de {f.year}"


def texto_item(plantilla: str, datos: dict[str, Any]) -> str:
    corte = datos.get("fecha_corte") or ""
    valores = {
        "anio_auditado": datos.get("anio_auditado") or "____",
        "anio_cerrado": datos.get("anio_cerrado") or "____",
        "corte": _fecha_excel(corte) if corte else "____",
        "rango": (f"Enero a {_MESES[date.fromisoformat(corte).month - 1].capitalize()} de {corte[:4]}"
                  if corte else "____"),
    }
    return plantilla.format(**valores)


def representante_completo(datos: dict[str, Any]) -> str:
    return " ".join(p for p in (_texto(datos.get("representante_titulo")),
                                _texto(datos.get("representante_nombre"))) if p).upper()


# ── Estado del proceso ───────────────────────────────────────────────────

def revisado(adjunto: Any) -> bool:
    """Un PDF firmado cuenta solo cuando el auditor lo revisó y lo dejó conforme."""
    return row_get(adjunto, "revision_resultado", None) == "conforme"


def completado(tipo: str, adjunto: Any) -> bool:
    """Lo recibido cumple el requisito: el Excel importado o el PDF revisado."""
    if tipo == "solicitud_respondida":
        return row_get(adjunto, "importacion_estado", None) == "importado"
    return revisado(adjunto)


def estado_proceso(req: dict[str, Any]) -> dict[str, Any]:
    """Los tres pasos del requerimiento (datos, documentos y correo) a partir
    de lo registrado. req trae: confirmado, faltantes, paquetes (generaciones,
    la última primero), desactualizado y envios."""
    correo = [e for e in req["envios"] if row_get(e, "canal") == "correo"]
    generado = bool(req["paquetes"])
    paquete_vigente = req["paquetes"][0]["id"] if generado and not req["desactualizado"] else None
    correo_vigente = any(row_get(e, "paquete_id") == paquete_vigente and
                         not row_get(e, "registro_historico", 0) for e in correo) if paquete_vigente else False
    # El paso 1 se cumple con sus campos; generar exige además los requisitos
    # de cada documento (req["faltantes"] los incluye).
    datos_ok = bool(req["confirmado"]) and not req.get("faltantes_paso1", req["faltantes"])
    pasos = [
        ("datos", "Datos del requerimiento", datos_ok),
        ("generacion", "Documentos generados", generado and not req["desactualizado"]),
        ("correo", "Correo al cliente", correo_vigente),
    ]
    siguiente = next((clave for clave, _l, hecho in pasos if not hecho), None)
    return {
        "pasos": [{"clave": c, "label": l, "hecho": h, "actual": c == siguiente} for c, l, h in pasos],
        "paso1_completo": datos_ok,
        "puede_generar": datos_ok and not req["faltantes"],
        "generado": generado,
        "desactualizado": generado and req["desactualizado"],
        "correo_enviado": correo_vigente,
    }


# ── Generaciones (paquetes de los cuatro documentos) ────────────────────

def datos_paquete(datos: dict[str, Any], marcas: dict[tuple[int, int], Any], detalles: dict) -> dict[str, Any]:
    """Instantánea de todo lo que entra en los cuatro documentos y en el correo:
    datos confirmados, plantilla, marcas y cuadros. Se guarda con la generación
    y sirve para saber si quedó desactualizada."""
    usados = {
        **{c: v for c, v in datos.items() if c not in CAMPOS_CORREO}, "plantilla": PLANTILLA_VERSION,
        "marcas": [{"hoja": h, "numero": n, **{k: row_get(m, k) for k in ("cumplido", "no_aplica", "observacion")}}
                   for (h, n), m in sorted(marcas.items())],
        "cuadros": detalles,
    }
    return json.loads(json.dumps(usados, ensure_ascii=False, default=str))


def instantanea_actual(req: dict[str, Any]) -> dict[str, Any] | None:
    """Instantánea con la que se generaría hoy, a partir del contexto del
    requerimiento (None si los datos aún no se confirmaron)."""
    if req["guardado"] is None:
        return None
    return datos_paquete(datos_efectivos(req["guardado"], {}), req["items"], req["detalles"])


def paquete_desactualizado(paquete: Any, actuales: dict[str, Any] | None) -> bool:
    """True si los datos, marcas o cuadros cambiaron desde esa generación (o
    la plantilla): sus documentos siguen siendo válidos como historial, pero
    no para un envío nuevo."""
    if paquete is None or actuales is None:
        return False
    # El destinatario no está en los documentos: cambiarlo no los desactualiza
    # (las generaciones anteriores lo guardaban en su instantánea).
    usados = {c: v for c, v in json.loads(row_get(paquete, "datos_json")).items() if c not in CAMPOS_CORREO}
    return usados != actuales


def referencia_paquete(audit_id: int, numero: int) -> str:
    """Identificador que lleva el Excel de una generación: auditoría, número y
    un sufijo aleatorio para que no se pueda adivinar ni reutilizar."""
    return f"REQ-{int(audit_id)}-{int(numero)}-{secrets.token_hex(4).upper()}"


# ── Correo y WhatsApp ────────────────────────────────────────────────────

def nombre_archivo(tipo: str, datos: dict[str, Any], version: int) -> str:
    """Nombre que recibe el cliente, como en el ejemplo del proceso, más la versión."""
    _n, extension, prefijo = DOCUMENTOS[tipo]
    empresa = re.sub(r"[^A-Z0-9Ñ .&-]", "", normalizar_nombre(datos.get("empresa") or "EMPRESA"))[:60].strip()
    anio = f" {datos.get('anio_auditado')}" if tipo == "solicitud" else ""
    return f"{prefijo}{anio} {empresa} v{version}.{extension}"


def normalizar_nombre(texto: str) -> str:
    return " ".join(str(texto).upper().split())


def correo(datos: dict[str, Any]) -> dict[str, str]:
    """Asunto y cuerpo del correo de inicio de auditoría (ejemplo del proceso)."""
    anio = datos.get("anio_auditado") or "____"
    corte = fecha_larga(datos["fecha_corte"], "de") if datos.get("fecha_corte") else "____"
    asunto = f"INICIO DEL PROCESO DE AUDITORÍA EXTERNA {anio} - {normalizar_nombre(datos.get('empresa') or '')}"
    cuerpo = f"""Estimado Cliente:

Reciba un cordial saludo de parte de AUDDIT S.A.S.

Por medio del presente, damos inicio al proceso de Auditoría Externa correspondiente al ejercicio económico {anio}. En este sentido, adjuntamos para su revisión y gestión la siguiente documentación:

1. Carta de encargo de inicio de auditoría, la cual deberá ser firmada electrónicamente por el Gerente.
2. Requerimiento inicial de información (revisar cada uno de los cuadros y completar la información solicitada en caso de que aplique).
3. Certificados correspondientes al ejercicio {datos.get('anio_certificados') or '____'}, relacionados con Paraísos Fiscales y Empresas Relacionadas.

NOTAS IMPORTANTES

La auditoría preliminar se efectuará con base en los Estados Financieros con corte al {corte}.
Durante el desarrollo de la auditoría se remitirán recordatorios y requerimientos adicionales de información, de acuerdo con el avance de nuestros procedimientos.
El levantamiento físico de inventarios y activos fijos está previsto {datos.get('fechas_inventario') or '____'}. Por tal motivo, solicitamos que la fecha establecida por la entidad nos sea comunicada con al menos 15 días de anticipación, a fin de coordinar nuestra asistencia y evidenciar el procedimiento.
Agradecemos contar con el apoyo de la Gerencia y del personal involucrado para atender oportunamente los requerimientos de información y facilitar el adecuado desarrollo del proceso de auditoría.

En caso de cualquier consulta o inquietud, quedamos a su disposición.

Agradecemos confirmar la recepción del presente correo y de la documentación adjunta."""
    return {"asunto": asunto, "cuerpo": cuerpo}


def mensaje_whatsapp(datos: dict[str, Any], envio: Any) -> str:
    """Aviso al grupo del cliente de que se envió el correo, pidiendo confirmación."""
    fecha = _texto(row_get(envio, "fecha"))[:10]
    cuando = f" el {fecha_larga(fecha, 'de')}" if fecha else ""
    return (
        f"Estimados, buen día. Les informamos que{cuando} enviamos al correo "
        f"{_texto(row_get(envio, 'destinatario'))} la documentación de inicio de la Auditoría Externa "
        f"{datos.get('anio_auditado') or ''} de {normalizar_nombre(datos.get('empresa') or '')}: carta de "
        "encargo, requerimiento inicial de información y certificados de Paraísos Fiscales y Empresas "
        "Relacionadas. Les agradecemos confirmar la recepción. Saludos cordiales, AUDDIT S.A.S."
    )


# ── Archivos ─────────────────────────────────────────────────────────────

def nombre_seguro(nombre: str) -> str:
    """Nombre de archivo para mostrar y descargar: sin rutas ni caracteres de control."""
    base = re.split(r"[\\/]", nombre or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", base).strip(" .")
    return base[:120] or "archivo"


def validar_archivo(nombre: str, contenido: bytes, formatos: tuple[str, ...]) -> str:
    """Comprueba tamaño, extensión y contenido real; devuelve el formato."""
    if not contenido:
        raise ValueError("El archivo está vacío")
    if len(contenido) > MAX_ARCHIVO_BYTES:
        raise ValueError("El archivo supera el máximo de 10 MB")
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    formato = next((f for f in formatos if extension in _EXTENSIONES[f]), None)
    if formato is None:
        raise ValueError("Formato no permitido. Use: " + ", ".join(f.upper() for f in formatos))
    if formato == "eml":
        cabecera = contenido[:4096].decode("utf-8", errors="replace").lower()
        if "from:" not in cabecera or "subject:" not in cabecera:
            raise ValueError("El archivo .eml no parece un correo (faltan From/Subject)")
    elif not contenido.startswith(_FIRMAS[formato]):
        raise ValueError(f"El contenido no corresponde a un archivo {formato.upper()}")
    if formato == "pdf":
        describir_pdf(contenido)
    return formato


def describir_pdf(contenido: bytes) -> str:
    """Comprueba que el PDF se pueda abrir y leer y describe lo que se
    observa. La cabecera %PDF solo indica el formato; esto solo prueba que es
    legible: no identifica a quién firmó ni valida firmas electrónicas."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ValueError("Falta la librería pypdf: instálela con pip install -r requirements.txt") from exc
    try:
        reader = PdfReader(io.BytesIO(contenido))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("El PDF está protegido con contraseña: no se puede revisar")
        paginas = len(reader.pages)
        campos = reader.get_fields() or {}
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — archivo externo: cualquier fallo es "ilegible"
        raise ValueError("No se pudo leer el PDF: el archivo parece dañado o incompleto") from exc
    if paginas < 1:
        raise ValueError("El PDF no tiene páginas")
    firmas = sum(1 for c in campos.values() if c.get("/FT") == "/Sig" and c.get("/V") is not None)
    detalle = f"PDF legible, {paginas} página(s)."
    if firmas:
        detalle += (f" Contiene {firmas} firma(s) electrónica(s) incrustada(s); Atlas no verifica su validez "
                    "criptográfica.")
    else:
        detalle += " No se detectan firmas electrónicas incrustadas (puede estar firmado a mano y escaneado)."
    return detalle
