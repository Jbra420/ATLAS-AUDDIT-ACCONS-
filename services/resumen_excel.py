"""
services/resumen_excel.py — Levantamiento de información del expediente en Excel (.xlsx).

Reúne en un libro todo lo que pide el levantamiento de información: los
bloques 1 a 6 (SRI, Supercias, ubicación, administradores, accionistas e
información financiera) con la fuente y la fecha de consulta de cada dato, el
estado de los requisitos, las validaciones cruzadas y alertas, las fuentes y la
evidencia, la trazabilidad, los hallazgos y la recomendación preliminar.

Lee el expediente tal como está: no genera ni guarda el resumen. Usa las
mismas reglas que el resumen en texto (services/summary.py y
services/validaciones.py), así que ambos coinciden.

Los valores se escriben siempre como datos: un texto que empieza con "=" (por
ejemplo, pegado desde una web) se guarda como texto y nunca como fórmula.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from services.financial import CASILLEROS, compute_indicators, filas_comparativo
from services.rowutil import row_get
from services.summary import (
    CAMPOS_SRI,
    CAMPOS_UBICACION,
    _CamposTolerantes,
    cierre_levantamiento,
    extract_signals,
    risk_suggestions,
    valores_supercias,
)
from services.trazabilidad import (
    BLOQUE_ACCIONISTAS,
    BLOQUE_ADMINISTRADORES,
    BLOQUE_FINANCIERO,
    BLOQUE_SRI,
    BLOQUE_SUPERCIAS,
    BLOQUE_UBICACION,
    ultimo_por_campo,
)
from services.validaciones import TOLERANCIA_BALANCE, evaluar_levantamiento

PENDIENTE = "Pendiente de confirmar"
AVISO = (
    "Documento PRELIMINAR generado con la información registrada en Atlas. Debe validarse contra "
    "fuentes oficiales (SRI, Supercias, SERCOP) antes de utilizarse como soporte de auditoría formal."
)

_FORMATO_USD = '"$"#,##0.00;[Red]-"$"#,##0.00'
_FORMATO_PCT = "0.00%"
_ESTADOS_CRUCE = {
    "coincide": "Coincide", "no_coincide": "No coincide",
    "pendiente": "Pendiente de datos", "revisar": "Revisar",
}
_NIVELES = {"critica": "Crítica", "alta": "Alta", "media": "Media", "informativa": "Informativa"}
_BLOQUES = {
    BLOQUE_SRI: "SRI", BLOQUE_SUPERCIAS: "Supercias", BLOQUE_UBICACION: "Ubicación",
    BLOQUE_ADMINISTRADORES: "Administradores", BLOQUE_ACCIONISTAS: "Accionistas",
    BLOQUE_FINANCIERO: "Financiero",
}

# Estilo del libro: marca Atlas, sobrio y legible impreso.
_MORADO = "5B2A86"
_TINTA = "203044"
_VERDE = "167D72"
_TITULO = Font(bold=True, size=15, color=_TINTA)
_SUBTITULO = Font(bold=True, size=11, color=_MORADO)
_ENCABEZADO = Font(bold=True, color="FFFFFF")
_RELLENO_ENCABEZADO = PatternFill("solid", fgColor=_TINTA)
_RELLENO_ALTERNO = PatternFill("solid", fgColor="F3F7FA")
_RELLENO_TOTAL = PatternFill("solid", fgColor="EEE8F5")
_RELLENO_PENDIENTE = PatternFill("solid", fgColor="FFF4E5")
_RELLENO_ALERTA = PatternFill("solid", fgColor="FDECEC")
_BORDE = Border(bottom=Side(style="thin", color="DEE5EC"))
_AJUSTE = Alignment(wrap_text=True, vertical="top")


# ---------------------------------------------------------------------------
# Escritura segura
# ---------------------------------------------------------------------------

def _texto(value: Any) -> Any:
    """Texto sin caracteres de control (openpyxl los rechaza)."""
    return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def _poner(ws: Worksheet, fila: int, columna: int, value: Any, formato: str = "") -> Any:
    """Escribe un valor como dato. openpyxl interpreta como fórmula todo texto
    que empieza con "=": se fuerza a texto para evitar inyección de fórmulas."""
    cell = ws.cell(row=fila, column=columna, value=_texto(value))
    if isinstance(cell.value, str) and cell.value.startswith("="):
        cell.data_type = "s"
    if formato:
        cell.number_format = formato
    cell.alignment = _AJUSTE
    return cell


def _vacio(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "-", "—"})


def _o_pendiente(value: Any) -> Any:
    return PENDIENTE if _vacio(value) else value


class _Hoja:
    """Hoja que se llena de arriba hacia abajo: título, pares campo/valor y tablas."""

    def __init__(self, libro: Workbook, titulo: str, anchos: Iterable[int]) -> None:
        self.ws = libro.create_sheet(titulo[:31])
        self.fila = 1
        anchos = tuple(anchos)
        self.columnas = len(anchos)
        for i, ancho in enumerate(anchos, start=1):
            self.ws.column_dimensions[get_column_letter(i)].width = ancho
        self.ws.sheet_view.showGridLines = False
        self.ws.sheet_properties.tabColor = _VERDE if titulo == "Resumen" else _MORADO
        self.ws.page_setup.orientation = "landscape"
        self.ws.page_setup.paperSize = self.ws.PAPERSIZE_A4
        self.ws.page_setup.fitToWidth = 1
        self.ws.page_setup.fitToHeight = 0
        self.ws.sheet_properties.pageSetUpPr.fitToPage = True
        self.ws.oddHeader.center.text = "ATLAS  |  AUDDIT"
        self.ws.oddFooter.right.text = "Página &P de &N"
        self.ws.print_options.horizontalCentered = True

    def titulo(self, texto: str, subtitulo: str = "") -> None:
        _poner(self.ws, self.fila, 1, texto).font = _TITULO
        if self.columnas > 1:
            self.ws.merge_cells(start_row=self.fila, start_column=1, end_row=self.fila, end_column=self.columnas)
        self.ws.row_dimensions[self.fila].height = 26
        self.fila += 1
        if subtitulo:
            _poner(self.ws, self.fila, 1, subtitulo).font = Font(italic=True, color="666666")
            if self.columnas > 1:
                self.ws.merge_cells(start_row=self.fila, start_column=1, end_row=self.fila, end_column=self.columnas)
            self.fila += 1
        self.fila += 1

    def seccion(self, texto: str) -> None:
        _poner(self.ws, self.fila, 1, texto).font = _SUBTITULO
        self.ws.row_dimensions[self.fila].height = 22
        self.fila += 1

    def tabla(
        self, encabezados: list[str], filas: Iterable[Iterable[Any]], formatos: dict[int, str] | None = None,
        vacia: str = "Sin registros.", rellenos: Iterable[PatternFill | None] | None = None,
        fijar_encabezado: bool = False,
    ) -> tuple[int, int]:
        """Escribe una tabla y devuelve (primera, última) fila de datos.
        fijar_encabezado deja visible la fila de títulos al desplazarse."""
        formatos = formatos or {}
        filas = list(filas)
        for col, encabezado in enumerate(encabezados, start=1):
            cell = _poner(self.ws, self.fila, col, encabezado)
            cell.font, cell.fill = _ENCABEZADO, _RELLENO_ENCABEZADO
        self.ws.row_dimensions[self.fila].height = 27
        if fijar_encabezado:
            self.ws.freeze_panes = self.ws.cell(row=self.fila + 1, column=1)
            self.ws.auto_filter.ref = f"A{self.fila}:{get_column_letter(len(encabezados))}{self.fila + len(filas)}"
        self.fila += 1
        primera = self.fila
        rellenos = list(rellenos or [])
        for i, fila in enumerate(filas):
            for col, value in enumerate(fila, start=1):
                cell = _poner(self.ws, self.fila, col, value, formatos.get(col, ""))
                cell.border = _BORDE
                if i < len(rellenos) and rellenos[i] is not None:
                    cell.fill = rellenos[i]
                elif i % 2:
                    cell.fill = _RELLENO_ALTERNO
            lineas = 1
            for col, value in enumerate(fila, start=1):
                ancho = max(10, int(self.ws.column_dimensions[get_column_letter(col)].width or 20) - 3)
                for part in str(value or "").splitlines():
                    lineas = max(lineas, (len(part) + ancho - 1) // ancho)
            self.ws.row_dimensions[self.fila].height = min(120, max(23, 18 * lineas + 5))
            self.fila += 1
        if not filas:
            _poner(self.ws, self.fila, 1, vacia).font = Font(italic=True, color="666666")
            self.fila += 1
        ultima = self.fila - 1
        self.fila += 1
        return primera, ultima

    def espacio(self) -> None:
        self.fila += 1


# ---------------------------------------------------------------------------
# Datos del expediente
# ---------------------------------------------------------------------------

def _trazabilidad(provenance: list) -> dict[tuple[str, str], Any]:
    """Último registro de trazabilidad de cada (bloque, campo)."""
    return ultimo_por_campo(provenance)


def _fuente(traza: dict, bloque: str, campo: str) -> tuple[str, str]:
    row = traza.get((bloque, campo))
    if row is None:
        return "Sin trazabilidad registrada", ""
    return row_get(row, "fuente") or "", row_get(row, "fecha_consulta") or ""


_CRUCE_POR_CAMPO = {
    (BLOQUE_SRI, "razon_social_sri"): "CRUCE_RAZON_SOCIAL",
    (BLOQUE_SUPERCIAS, "razon_social_supercias"): "CRUCE_RAZON_SOCIAL",
    (BLOQUE_SRI, "fecha_inicio_actividades"): "CRUCE_FECHAS",
    (BLOQUE_SUPERCIAS, "fecha_constitucion"): "CRUCE_FECHAS",
    (BLOQUE_SRI, "representante_legal_sri"): "CRUCE_REPRESENTANTE",
    (BLOQUE_SRI, "ciiu_sri"): "CRUCE_CIIU",
    (BLOQUE_SUPERCIAS, "ciiu_nivel6"): "CRUCE_CIIU",
    (BLOQUE_SRI, "actividad_economica"): "CRUCE_ACTIVIDAD_OBJETO",
    (BLOQUE_SUPERCIAS, "objeto_social"): "CRUCE_ACTIVIDAD_OBJETO",
}


def _estado_soporte(value: Any, fuente: str) -> str:
    if _vacio(value):
        return "Pendiente de dato"
    return "Fuente registrada" if fuente and fuente != "Sin trazabilidad registrada" else "Sin trazabilidad"


def _estado_persona(row: Any, campos: tuple[str, ...]) -> str:
    if any(_vacio(row_get(row, campo)) for campo in campos):
        return "Datos incompletos"
    return _estado_soporte(row_get(row, "nombre"), row_get(row, "fuente") or "")


def _campos(hoja: _Hoja, filas: list[tuple[str, str, Any]], bloque: str, traza: dict, cruces: dict) -> None:
    """Datos, procedencia y resultado del cruce automático, sin declarar verificación documental."""
    datos, rellenos = [], []
    for campo, etiqueta, valor in filas:
        fuente, fecha = _fuente(traza, bloque, campo)
        codigo = _CRUCE_POR_CAMPO.get((bloque, campo))
        cruce = cruces.get(codigo) if codigo else None
        control = (_ESTADOS_CRUCE.get(cruce["estado"], cruce["estado"]) if cruce
                   else "Sin cruce automático")
        datos.append((etiqueta, _o_pendiente(valor), fuente, fecha,
                      _estado_soporte(valor, fuente), control))
        rellenos.append(_RELLENO_PENDIENTE if _vacio(valor) else
                        _RELLENO_ALERTA if cruce and cruce["estado"] == "no_coincide" else None)
    hoja.tabla(["Campo", "Valor", "Fuente", "Fecha de consulta", "Soporte", "Control"],
               datos, rellenos=rellenos, fijar_encabezado=True)


def _numero(valor: Any) -> float | None:
    """Monto registrado como número; vacío o ilegible -> None (celda vacía)."""
    try:
        return None if _vacio(valor) else float(valor)
    except (TypeError, ValueError):
        return None


def _porcentaje(valor: Any) -> float | None:
    """Participación registrada como 40 (%) -> 0.40 para el formato de Excel."""
    numero = _numero(valor)
    return None if numero is None else numero / 100


# ---------------------------------------------------------------------------
# Hojas
# ---------------------------------------------------------------------------

def _filas_sri(audit, profile) -> list[tuple[str, str, Any]]:
    return [("ruc", "RUC", audit["ruc"])] + [
        (campo, label, profile[campo]) for campo, label in CAMPOS_SRI
    ] + [
        ("categoria", "Categoría tributaria", profile["categoria"]),
        ("fecha_actualizacion", "Actualización en SRI", profile["fecha_actualizacion"]),
    ]


def _filas_supercias(profile, data: dict) -> list[tuple[str, str, Any]]:
    return valores_supercias(profile, data) + [
        ("ciiu_nivel6", "Código CIIU (Supercias)", profile["ciiu_nivel6"]),
        ("ciiu_nivel1", "Sector CIIU", profile["ciiu_nivel1"]),
        ("nacionalidad", "Nacionalidad de la compañía", profile["nacionalidad"]),
        ("telefono", "Teléfono", profile["telefono"]),
        ("representante_legal", "Representante legal", profile["representante_legal"]),
        ("representante_cargo", "Cargo del representante", profile["representante_cargo"]),
        ("capital_suscrito", "Capital suscrito", profile["capital_suscrito"]),
        ("ultimo_anio_balance", "Último balance reportado", profile["ultimo_anio_balance"]),
        ("supercias_catalogo_fecha", "Corte del catálogo local", profile["supercias_catalogo_fecha"]),
    ]


def _filas_ubicacion(location) -> list[tuple[str, str, Any]]:
    return [(campo, label, location[campo]) for campo, label in CAMPOS_UBICACION] + [
        ("canton", "Cantón", location["canton"]),
    ]


def _resumen_campos(hoja: _Hoja, filas: list[tuple[str, str, Any]], bloque: str, traza: dict) -> None:
    datos = []
    for campo, etiqueta, valor in filas:
        fuente, fecha = _fuente(traza, bloque, campo)
        if campo == "ruc":
            fuente = "Expediente Atlas"
        datos.append((etiqueta, _o_pendiente(valor), fuente, fecha))
    hoja.tabla(["Campo", "Valor", "Fuente", "Consulta"], datos,
               rellenos=[_RELLENO_PENDIENTE if _vacio(valor) else None for _, _, valor in filas])


def _hoja_resumen(libro, audit, ctx, *, data, profile, location, traza,
                 validacion, cierre, risks, generado: str) -> None:
    hoja = _Hoja(libro, "Resumen", (44, 53, 66, 25))
    hoja.ws.sheet_view.zoomScale = 85
    hoja.ws.freeze_panes = "A4"
    hoja.ws.print_title_rows = "1:2"
    hoja.titulo("Levantamiento de información general del cliente", "Atlas · Auddit | Expediente preliminar")
    snapshot = ctx.get("snapshot")
    indicators = compute_indicators(snapshot)
    anio = row_get(snapshot, "anio_fiscal")
    hoja.tabla(["Dato", "Valor"], [
        ("Empresa", audit["company_name"]),
        ("RUC", _o_pendiente(audit["ruc"])),
        ("Período auditado", audit["period"]),
        ("Año fiscal de los EEFF", anio or PENDIENTE),
        ("Auditor asignado", _o_pendiente(row_get(audit, "auditor_name"))),
        ("Generado", generado),
    ])
    hoja.seccion("Datos principales")
    principal = [
        ("Estado del RUC", _o_pendiente(profile["estado_contribuyente"]), "SRI"),
        ("Situación legal", _o_pendiente(profile["situacion_legal"]), "Supercias"),
        ("Total activo", _o_pendiente(indicators["activo_total"]), f"EEFF {anio or 'sin año'}"),
        ("Total pasivo", _o_pendiente(indicators["pasivo_total"]), f"EEFF {anio or 'sin año'}"),
        ("Patrimonio neto", _o_pendiente(indicators["patrimonio_neto"]), f"EEFF {anio or 'sin año'}"),
        ("Ingresos totales", _o_pendiente(_total_completo(snapshot, "ingresos_401", "otros_ingresos_403")),
         f"EEFF {anio or 'sin año'}"),
        ("Utilidad neta", _o_pendiente(indicators["utilidad_neta_707"]), f"EEFF {anio or 'sin año'}"),
    ]
    primera, ultima = hoja.tabla(["Dato", "Valor", "Origen"], principal,
                                rellenos=[_RELLENO_PENDIENTE if value == PENDIENTE else None
                                          for _, value, _ in principal])
    for row in range(primera + 2, ultima + 1):
        hoja.ws.cell(row=row, column=2).number_format = _FORMATO_USD

    hoja.seccion("1. Identificación tributaria · SRI")
    _resumen_campos(hoja, _filas_sri(audit, profile), BLOQUE_SRI, traza)

    hoja.seccion("2. Información societaria · Supercias")
    _resumen_campos(hoja, _filas_supercias(profile, data), BLOQUE_SUPERCIAS, traza)

    hoja.seccion("3. Ubicación")
    _resumen_campos(hoja, _filas_ubicacion(location), BLOQUE_UBICACION, traza)

    hoja.seccion("4. Administradores")
    hoja.tabla(["Nombre", "Cargo / nacionalidad", "Identificación", "Fuente / consulta"], [
        (row_get(a, "nombre"),
         " · ".join(v for v in (row_get(a, "cargo"), row_get(a, "nacionalidad")) if v) or PENDIENTE,
         _o_pendiente(row_get(a, "identificacion")),
         f"{row_get(a, 'fuente') or 'Sin fuente'} · {row_get(a, 'fecha_consulta') or 'Sin fecha'}")
        for a in ctx.get("admins") or []
    ], vacia="No hay administradores registrados en el expediente.")

    hoja.seccion("5. Accionistas")
    accionistas = [
        (f"{row_get(s, 'nombre')} · Beneficiario final: {row_get(s, 'beneficiario_final')}"
         if row_get(s, "beneficiario_final") else row_get(s, "nombre"),
         _o_pendiente(row_get(s, "identificacion")),
         _porcentaje(row_get(s, "participacion_porcentaje")), _numero(row_get(s, "capital")))
        for s in ctx.get("shareholders") or []
    ]
    hoja.tabla(["Nombre", "Identificación", "Participación", "Capital (USD)"], accionistas,
               formatos={3: _FORMATO_PCT, 4: _FORMATO_USD},
               vacia="No hay accionistas registrados en el expediente.")

    hoja.seccion(f"6. Información financiera · ejercicio {anio or 'pendiente'}")
    hoja.tabla(["Dato", "Valor"], [
        ("Fecha de corte", _o_pendiente(row_get(snapshot, "fecha_corte"))),
        ("Fuente", _o_pendiente(row_get(snapshot, "fuente"))),
        ("Fecha de consulta", _o_pendiente(row_get(snapshot, "fecha_consulta"))),
    ])
    cifras = [
        (label, _o_pendiente(value), "Cálculo Atlas" if calculated else "Cifra del expediente",
         "Ver hoja 6. Financiero")
        for label, value, calculated in filas_comparativo(snapshot)
    ] if snapshot else []
    hoja.tabla(["Concepto", "Importe (USD)", "Tipo", "Detalle"], cifras,
               formatos={2: _FORMATO_USD}, vacia="No hay cifras financieras registradas.",
               rellenos=[_RELLENO_PENDIENTE if value == PENDIENTE else None
                         for _, value, _, _ in cifras])
    hoja.tabla(["Indicador", "Resultado"], [
        ("Razón de endeudamiento", _o_pendiente(indicators["razon_endeudamiento"])),
        ("Margen neto", _o_pendiente(indicators["margen_neto"])),
        ("Patrimonio / Activo", _o_pendiente(indicators["patrimonio_sobre_activo"])),
    ], formatos={2: _FORMATO_PCT})

    hoja.seccion("7. Validaciones cruzadas y alertas")
    hoja.tabla(["Control", "Estado", "Detalle"], [
        (c["regla"], _ESTADOS_CRUCE.get(c["estado"], c["estado"]), c["detalle"])
        for c in validacion["cruces"]
    ], rellenos=[_RELLENO_ALERTA if c["estado"] == "no_coincide" else None
                for c in validacion["cruces"]])
    hoja.tabla(["Riesgo preliminar"], [(r,) for r in risks],
               vacia="Sin alertas preliminares automáticas.")

    hoja.seccion("8. Fuentes consultadas")
    hoja.tabla(["Fuente", "Estado", "Consultada el", "Observación"], [
        (row_get(s, "fuente"), "Consultada" if row_get(s, "estado") == "consultada" else "Pendiente",
         row_get(s, "consultada_at") or "", row_get(s, "observacion") or "")
        for s in ctx.get("source_checks") or []
    ])
    hoja.tabla(["Evidencia", "URL", "Notas", "Registrada el"], [
        (f"{row_get(s, 'source_type') or 'Fuente'}: {row_get(s, 'title') or 'Sin título'}",
         row_get(s, "url") or "", row_get(s, "notes") or "", row_get(s, "created_at") or "")
        for s in ctx.get("sources") or []
    ], vacia="Sin evidencia registrada.")
    otras_fuentes = [
        ("Contratación pública", data.get("public_contracting") or data.get("sercop_info")),
        ("Obligaciones tributarias", data.get("tax_obligations")),
    ]
    if any(value for _, value in otras_fuentes):
        hoja.tabla(["Consulta", "Resultado registrado"],
                   [(label, value) for label, value in otras_fuentes if value])

    hoja.seccion("9. Hallazgos preliminares")
    signals = extract_signals(data.get("pasted_text") or "")
    hallazgos = [
        ("RUC detectados en texto", ", ".join(signals["rucs"])),
        ("Correos detectados", ", ".join(signals["emails"])),
        ("Teléfonos detectados", ", ".join(signals["phones"])),
        ("Observaciones del auditor", data.get("observations") or ""),
        ("Riesgos ingresados manualmente", data.get("risk_flags") or ""),
    ]
    hoja.tabla(["Hallazgo", "Detalle"], [(label, value) for label, value in hallazgos if value],
               vacia="Sin hallazgos adicionales registrados.")

    hoja.seccion("10. Pendientes de validación")
    hoja.tabla(["Tipo", "Dato por completar"],
               [("Obligatorio", label) for label in cierre["pendientes"]] +
               [("Recomendado", label) for label in cierre["recomendados"]],
               vacia="Sin pendientes de validación.",
               rellenos=[_RELLENO_PENDIENTE] * len(cierre["pendientes"]))

    hoja.seccion("11. Recomendación preliminar")
    primera, _ = hoja.tabla(["Conclusión", "Detalle"], [("Recomendación preliminar", cierre["recomendacion"])])
    hoja.ws.row_dimensions[primera].height = 75
    _poner(hoja.ws, hoja.fila, 1, AVISO).font = Font(bold=True, color="9C5700")
    hoja.ws.merge_cells(start_row=hoja.fila, start_column=1, end_row=hoja.fila, end_column=4)
    hoja.ws.row_dimensions[hoja.fila].height = 45


def _hoja_personas(libro, titulo, encabezados, filas, formatos=None, total=None) -> None:
    hoja = _Hoja(libro, titulo, (18, 20, 38, 24, 20, 20, 30, 44, 18, 23)[:len(encabezados)])
    hoja.titulo(titulo, "Personas registradas en el expediente; cotejar integridad con el certificado original")
    primera, ultima = hoja.tabla(encabezados, filas, formatos, fijar_encabezado=True)
    if total and filas:
        fila = ultima + 1
        _poner(hoja.ws, fila, 1, "Total").font = Font(bold=True)
        for col, formato in total.items():
            valores = [row[col - 1] for row in filas]
            if all(isinstance(value, (int, float)) for value in valores):
                letra = get_column_letter(col)
                cell = hoja.ws.cell(row=fila, column=col, value=f"=SUM({letra}{primera}:{letra}{ultima})")
                cell.number_format, cell.font = formato, Font(bold=True)
            else:
                _poner(hoja.ws, fila, col, "Incompleto").font = Font(bold=True, color="9C5700")
        for col in range(1, len(encabezados) + 1):
            hoja.ws.cell(row=fila, column=col).fill = _RELLENO_TOTAL


def _total_completo(row: Any, primero: str, segundo: str) -> float | None:
    a, b = _numero(row_get(row, primero)), _numero(row_get(row, segundo))
    return a + b if a is not None and b is not None else None


def _hoja_financiero(libro, financial: dict, traza: dict) -> None:
    hoja = _Hoja(libro, "6. Financiero", (55, 23, 18, 64, 19, 25))
    years = financial.get("years") or []
    snapshot = financial.get("snapshot")
    anio_actual = row_get(snapshot, "anio_fiscal")
    hoja.titulo("6. Información financiera", "Casilleros y respaldo por ejercicio fiscal")
    hoja.tabla(["Dato", "Valor"], [
        ("Año fiscal", anio_actual or PENDIENTE),
        ("Ejercicios con registro", len(years)),
        ("Criterio", "Las cifras proceden del expediente; su presencia no acredita cotejo documental."),
    ])
    ejercicios = years or ([snapshot] if snapshot else [])
    if not ejercicios:
        hoja.seccion("Sin estados financieros registrados")
        hoja.tabla(["Estado", "Acción"], [("Pendiente", "Obtener documentos económicos del año fiscal auditado")])
        return

    for estado in ejercicios:
        anio = row_get(estado, "anio_fiscal")
        titulo = f"Ejercicio {anio}" if anio else "Cifras sin año fiscal confirmado"
        hoja.seccion(titulo)
        hoja.tabla(["Dato", "Valor"], [
            ("Fecha de corte", _o_pendiente(row_get(estado, "fecha_corte"))),
            ("Fecha de aprobación de la junta", _o_pendiente(row_get(estado, "fecha_junta_aprobacion"))),
            ("Fuente general", _o_pendiente(row_get(estado, "fuente"))),
            ("Fecha de consulta", _o_pendiente(row_get(estado, "fecha_consulta"))),
        ])
        filas, rellenos = [], []
        for campo, etiqueta, casillero, _admite_negativos in CASILLEROS:
            valor = _numero(row_get(estado, campo))
            tiene_traza = (BLOQUE_FINANCIERO, f"{anio}.{campo}") in traza if anio else False
            fuente, fecha = _fuente(traza, BLOQUE_FINANCIERO, f"{anio}.{campo}") if anio else ("", "")
            fuente = fuente if fuente != "Sin trazabilidad registrada" else row_get(estado, "fuente") or fuente
            fecha = fecha or row_get(estado, "fecha_consulta") or ""
            soporte = ("Pendiente de dato" if valor is None else "Trazabilidad por dato" if tiene_traza else
                       "Fuente del estado" if fuente != "Sin trazabilidad registrada" else "Sin trazabilidad")
            filas.append((f"{etiqueta} (casillero {casillero})" if casillero else
                          f"{etiqueta} (casillero por confirmar)",
                          valor if valor is not None else PENDIENTE, casillero or "Por confirmar",
                          fuente, fecha, soporte))
            rellenos.append(_RELLENO_PENDIENTE if valor is None else None)
            if campo in {"otros_ingresos_403", "gastos_502"}:
                ingreso = campo == "otros_ingresos_403"
                total = (_total_completo(estado, "ingresos_401", "otros_ingresos_403") if ingreso
                         else _total_completo(estado, "costo_ventas_501", "gastos_502"))
                nombre = "Total ingresos (401 + 403) — calculado" if ingreso else "Total gastos (501 + 502) — calculado"
                filas.append((nombre, total if total is not None else PENDIENTE, "Cálculo Atlas",
                              "Suma de los dos casilleros" if total is not None else "Falta un casillero", "",
                              "Calculado" if total is not None else "Pendiente de dato"))
                rellenos.append(_RELLENO_TOTAL if total is not None else _RELLENO_PENDIENTE)
        hoja.tabla(["Concepto", "Valor (USD)", "Casillero", "Fuente", "Fecha de consulta", "Soporte"],
                   filas, {2: _FORMATO_USD}, rellenos=rellenos)

        indicadores = compute_indicators(dict(estado))
        activo = _numero(row_get(estado, "activo_total"))
        pasivo = _numero(row_get(estado, "pasivo_total"))
        patrimonio = _numero(row_get(estado, "patrimonio_neto"))
        diferencia = activo - pasivo - patrimonio if None not in (activo, pasivo, patrimonio) else None
        ingresos = _total_completo(estado, "ingresos_401", "otros_ingresos_403")
        hoja.seccion("Controles e indicadores del ejercicio")
        primera, _ultima = hoja.tabla(["Control", "Resultado", "Estado"], [
            ("Activo - Pasivo - Patrimonio", diferencia if diferencia is not None else PENDIENTE,
             ("Coincide" if abs(diferencia) <= TOLERANCIA_BALANCE else "No coincide")
             if diferencia is not None else "Pendiente de datos"),
            ("Razón de endeudamiento (Pasivo / Activo)",
             indicadores["razon_endeudamiento"] if activo not in (None, 0) and pasivo is not None else PENDIENTE,
             "Calculado" if activo not in (None, 0) and pasivo is not None else "Pendiente de datos"),
            ("Margen neto (Utilidad / Ingresos)",
             indicadores["margen_neto"] if ingresos not in (None, 0) and
             _numero(row_get(estado, "utilidad_neta_707")) is not None else PENDIENTE,
             "Calculado" if ingresos not in (None, 0) and
             _numero(row_get(estado, "utilidad_neta_707")) is not None else "Pendiente de datos"),
            ("Patrimonio / Activo",
             indicadores["patrimonio_sobre_activo"] if activo not in (None, 0) and patrimonio is not None else PENDIENTE,
             "Calculado" if activo not in (None, 0) and patrimonio is not None else "Pendiente de datos"),
        ])
        hoja.ws.cell(row=primera, column=2).number_format = _FORMATO_USD
        for fila in range(primera + 1, primera + 4):
            hoja.ws.cell(row=fila, column=2).number_format = _FORMATO_PCT


_ETIQUETAS_CATALOGO = {
    "ruc": "RUC", "name": "Razón social", "razon_social": "Razón social",
    "city": "Ciudad", "activity_hint": "Actividad económica", "jurisdiction": "Jurisdicción",
    "taxpayer_status": "Estado contribuyente", "taxpayer_class": "Clase contribuyente",
    "start_date": "Inicio de actividades", "update_date": "Actualización del registro",
    "suspension_date": "Fecha de suspensión", "restart_date": "Fecha de reinicio",
    "accounting_required": "Obligado a contabilidad", "taxpayer_type": "Tipo contribuyente",
    "establishment_number": "Número de establecimiento", "trade_name": "Nombre comercial",
    "establishment_status": "Estado del establecimiento", "province": "Provincia",
    "provincia": "Provincia", "canton": "Cantón", "parish": "Parroquia",
    "ciiu_code": "Código CIIU", "withholding_agent": "Agente de retención",
    "special_taxpayer": "Contribuyente especial", "barrio": "Barrio", "calle": "Calle",
    "capital_suscrito": "Capital suscrito (texto fuente)", "ciiu_nivel1": "Sector CIIU",
    "ciiu_nivel6": "Actividad CIIU", "ciudad": "Ciudad", "expediente": "N.º de expediente",
    "fecha_constitucion": "Fecha de constitución", "interseccion": "Intersección",
    "numero": "Número de calle", "pais": "País", "representante": "Representante legal",
    "representante_cargo": "Cargo del representante", "situacion_legal": "Situación legal",
    "telefono": "Teléfono", "tipo_compania": "Tipo de compañía",
    "ultimo_balance": "Último balance reportado",
}


def _hoja_catalogo(libro: Workbook, titulo: str, fuente: str, catalogo: dict | None,
                   corte: str = "") -> None:
    hoja = _Hoja(libro, titulo, (37, 54, 83))
    hoja.titulo(titulo, "Registro original del catálogo local, separado de las correcciones del expediente")
    hoja.tabla(["Dato", "Valor"], [
        ("Fuente", fuente),
        ("Corte del catálogo", corte or "No informado"),
        ("Alcance", "Transcripción del catálogo local; contraste con el documento oficial vigente."),
    ])
    filas = [(_ETIQUETAS_CATALOGO.get(key, key.replace("_", " ").capitalize()), key,
              value if not _vacio(value) else "Sin dato en el catálogo")
             for key, value in (catalogo or {}).items() if not key.startswith("_")]
    hoja.tabla(["Campo", "Código en el catálogo", "Valor original"], filas,
               vacia="Este RUC no consta en el catálogo local disponible.", fijar_encabezado=True)


def _hoja_estado_detallado(libro: Workbook, titulo: str, balance_details: list[dict],
                           prefijos: set[str]) -> None:
    hoja = _Hoja(libro, titulo, (16, 24, 74, 25, 67))
    hoja.titulo(titulo, "Todas las cuentas del reporte por ramo de Supercias, incluidos saldos cero")
    hoja.tabla(["Nota", "Alcance"], [
        ("Origen", "TXT oficial importado localmente; no sustituye el estado financiero firmado ni sus notas."),
        ("Ediciones", "Los valores son los del TXT original. Las correcciones del auditor están en 6. Financiero."),
    ])
    hoja.seccion("Archivo y trazabilidad")
    hoja.tabla(["Ejercicio", "Archivo", "SHA-256", "Importado el", "URL de origen"], [
        (year["anio_fiscal"], year["archivo"], year["sha256"], year["importado_at"], year["url"])
        for year in balance_details
    ], vacia="No se ha importado un reporte detallado para este RUC.")
    hoja.seccion("Cuentas")
    filas = [
        (year["anio_fiscal"], code, description, amount,
         "Con saldo" if amount != 0 else "Sin saldo")
        for year in balance_details for code, description, amount in year["cuentas"]
        if code[:1] in prefijos
    ]
    primera, ultima = hoja.tabla(
        ["Ejercicio", "Casillero", "Cuenta", "Valor (USD)", "Estado"], filas,
        {4: _FORMATO_USD}, vacia="Sin cuentas detalladas para este estado.", fijar_encabezado=True,
    )
    for row in range(primera, ultima + 1):
        if len(str(hoja.ws.cell(row=row, column=2).value or "")) <= 3:
            for cell in hoja.ws[row][:5]:
                cell.fill = _RELLENO_TOTAL
                cell.font = Font(bold=True, color=_TINTA)


def _hoja_requisitos(libro, validacion, cierre) -> None:
    hoja = _Hoja(libro, "Requisitos", (18, 70, 16, 16))
    hoja.titulo("Requisitos del levantamiento",
                "Cumplido significa dato registrado; la revisión documental se controla por separado.")
    filas, rellenos = [], []
    for req in validacion["requisitos"]:
        filas.append((req["source"], req["label"], "Obligatorio", "Cumplido" if req["ok"] else "Pendiente"))
        rellenos.append(None if req["ok"] else _RELLENO_PENDIENTE)
    requisitos = {r["label"] for r in validacion["requisitos"]}
    for pendiente in cierre["pendientes"]:
        if pendiente not in requisitos:  # tratamiento de alertas críticas, año fiscal
            filas.append(("Validaciones", pendiente, "Obligatorio", "Pendiente"))
            rellenos.append(_RELLENO_PENDIENTE)
    for rec in validacion["recomendaciones_todas"]:
        filas.append((rec["source"], rec["label"], "Recomendado", "Cumplido" if rec["ok"] else "Pendiente"))
        rellenos.append(None if rec["ok"] else _RELLENO_PENDIENTE)
    hoja.tabla(["Bloque", "Requisito", "Tipo", "Estado"], filas, rellenos=rellenos, fijar_encabezado=True)


def _hoja_validaciones(libro, validacion, risks) -> None:
    hoja = _Hoja(libro, "Validaciones y alertas", (42, 18, 14, 80))
    hoja.titulo("Validaciones cruzadas y alertas")
    hoja.seccion("Validaciones cruzadas")
    cruces = validacion["cruces"]
    hoja.tabla(
        ["Regla", "Resultado", "Nivel", "Detalle"],
        [(c["regla"], _ESTADOS_CRUCE.get(c["estado"], c["estado"]), _NIVELES.get(c["nivel"], c["nivel"]),
          c["detalle"]) for c in cruces],
        rellenos=[_RELLENO_ALERTA if c["estado"] == "no_coincide" else None for c in cruces],
    )
    hoja.seccion("Alertas automáticas")
    hoja.tabla(
        ["Nivel", "Alerta", "Tratamiento del auditor"],
        [(_NIVELES.get(a["nivel"], a["nivel"]), a["mensaje"], a["tratamiento"] or "Sin tratamiento registrado")
         for a in validacion["alertas"]],
        vacia="Sin alertas automáticas.",
    )
    hoja.seccion("Riesgos preliminares")
    hoja.tabla(["Riesgo"], [(r,) for r in risks], vacia="Sin riesgos preliminares con la información registrada.")


def _hoja_fuentes(libro, source_checks, sources, provenance) -> None:
    hoja = _Hoja(libro, "Fuentes y evidencia", (30, 44, 28, 40, 30, 16, 20))
    hoja.titulo("Fuentes consultadas y evidencia")
    hoja.seccion("Fuentes del levantamiento")
    hoja.tabla(
        ["Fuente", "Uso", "Estado", "Observación", "Consultada el"],
        [(row_get(s, "fuente"), row_get(s, "uso"),
          "Consultada" if row_get(s, "estado") == "consultada" else "Pendiente",
          row_get(s, "observacion") or "", row_get(s, "consultada_at") or "") for s in source_checks],
    )
    hoja.seccion("Evidencia registrada")
    primera, _ultima = hoja.tabla(
        ["Tipo", "Título", "URL", "Notas", "Registrada el"],
        [(row_get(s, "source_type") or "Fuente", row_get(s, "title") or "Sin título", row_get(s, "url") or "",
          row_get(s, "notes") or "", row_get(s, "created_at") or "") for s in sources],
    )
    for fila, evidencia in enumerate(sources, start=primera):
        url = str(row_get(evidencia, "url") or "").strip()
        if url.startswith(("https://", "http://")):
            celda = hoja.ws.cell(row=fila, column=3)
            celda.hyperlink = url
            celda.font = Font(color="236DA8", underline="single")
    hoja.seccion("Trazabilidad por dato")
    hoja.tabla(
        ["Bloque", "Campo", "Valor anterior", "Valor nuevo", "Fuente", "Fecha de consulta", "Registrado el"],
        [(_BLOQUES.get(row_get(p, "bloque"), row_get(p, "bloque")), row_get(p, "campo"),
          row_get(p, "valor_anterior") or "", row_get(p, "valor_nuevo") or "", row_get(p, "fuente"),
          row_get(p, "fecha_consulta"), row_get(p, "registrado_at")) for p in provenance],
    )


def _hoja_documentos(libro, docs) -> None:
    hoja = _Hoja(libro, "Documentos", (52, 20, 22, 30, 24))
    hoja.titulo("Documentos económicos y de respaldo", "Estado de revisión registrado en el expediente")
    hoja.tabla(["Indicador", "Valor"], [
        ("Documentos revisados", f"{sum(d['estado'] == 'revisado' for d in docs)} de {len(docs)}"),
        ("Alcance", "La marca 'Revisado' es el registro del auditor; la exportación no inspecciona archivos adjuntos."),
        ("Período", "La referencia temporal es la de la auditoría; no es la fecha del documento original."),
    ])
    filas = [
        (row_get(d, "nombre"), _o_pendiente(row_get(d, "fecha")),
         "Revisado" if row_get(d, "estado") == "revisado" else "Pendiente",
         row_get(d, "revisado_por_nombre") or "", row_get(d, "revisado_at") or "")
        for d in docs
    ]
    hoja.tabla(["Documento", "Período de auditoría", "Estado", "Revisado por", "Fecha de revisión"], filas,
               rellenos=[None if row_get(d, "estado") == "revisado" else _RELLENO_PENDIENTE for d in docs],
               fijar_encabezado=True)


_NOTAS_INVESTIGACION = (
    ("Nombre comercial", "commercial_name"),
    ("Actividad económica - investigación", "economic_activity"),
    ("Estado legal - investigación", "legal_status"),
    ("Representante - investigación", "representative"),
    ("Dirección - investigación", "address"),
    ("Obligaciones tributarias", "tax_obligations"),
    ("Contratación pública", "public_contracting"),
    ("Información Supercias", "supercias_info"),
    ("Información SRI", "sri_info"),
    ("Información SERCOP", "sercop_info"),
    ("Observaciones del auditor", "observations"),
    ("Riesgos ingresados manualmente", "risk_flags"),
    ("Texto de evidencia aportado", "pasted_text"),
    ("Resumen guardado", "generated_summary"),
)


def _hoja_hallazgos(libro, data: dict) -> None:
    hoja = _Hoja(libro, "Hallazgos", (38, 100))
    hoja.titulo("Hallazgos preliminares e información complementaria")
    signals = extract_signals(data.get("pasted_text") or "")
    filas = []
    for etiqueta, campo in _NOTAS_INVESTIGACION:
        valor = data.get(campo)
        if _vacio(valor):
            continue
        texto = str(valor)
        partes = [texto[i:i + 1000] for i in range(0, len(texto), 1000)]
        filas.extend((etiqueta if len(partes) == 1 else f"{etiqueta} ({i}/{len(partes)})", parte)
                     for i, parte in enumerate(partes, start=1))
    filas.extend([
        ("RUC detectados en texto", ", ".join(signals["rucs"])),
        ("Correos detectados", ", ".join(signals["emails"])),
        ("Teléfonos detectados", ", ".join(signals["phones"])),
    ])
    datos = [(k, v) for k, v in filas if not _vacio(v)]
    primera, _ultima = hoja.tabla(["Hallazgo", "Detalle"], datos,
                                 vacia="Sin hallazgos adicionales registrados.")
    for fila, (_etiqueta, detalle) in enumerate(datos, start=primera):
        hoja.ws.row_dimensions[fila].height = min(180, max(23, 18 * (1 + len(str(detalle)) // 90)))


# ---------------------------------------------------------------------------
# Libro completo
# ---------------------------------------------------------------------------

def build_resumen_xlsx(
    audit: Any, ctx: dict[str, Any], generado: datetime | None = None, *,
    catalog_sri: dict | None = None, catalog_supercias: dict | None = None,
    balance_details: list[dict] | None = None,
) -> bytes:
    """Libro .xlsx del levantamiento de información del expediente.

    audit es la fila de get_audit() y ctx el contexto de get_audit_context().
    """
    research = ctx.get("research")
    data = {k: (research[k] or "") for k in research.keys()} if research is not None else {}
    profile = _CamposTolerantes(dict(ctx["profile"])) if ctx.get("profile") is not None else _CamposTolerantes()
    location = _CamposTolerantes(dict(ctx["location"])) if ctx.get("location") is not None else _CamposTolerantes()
    admins, shareholders = ctx.get("admins") or [], ctx.get("shareholders") or []
    snapshot = dict(ctx["snapshot"]) if ctx.get("snapshot") else None
    indicators = compute_indicators(snapshot) if snapshot else {}
    sources = ctx.get("sources") or []
    provenance = ctx.get("provenance") or []
    traza = _trazabilidad(provenance)

    validacion = evaluar_levantamiento(
        audit, profile, location, admins, shareholders, snapshot, ctx.get("alert_treatments"),
    )
    cruces = {c["codigo"]: c for c in validacion["cruces"]}
    risks = risk_suggestions(audit, data, len(sources), profile, indicators, validacion)
    cierre = cierre_levantamiento(validacion, snapshot, risks)
    docs = ctx.get("docs") or []
    source_checks = ctx.get("source_checks") or []
    balance_details = balance_details or []

    libro = Workbook()
    libro.remove(libro.active)
    _hoja_resumen(
        libro, audit, ctx, data=data, profile=profile, location=location,
        traza=traza, validacion=validacion, cierre=cierre, risks=risks,
        generado=(generado or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )

    hoja = _Hoja(libro, "1. SRI", (32, 54, 55, 18, 24, 24))
    hoja.titulo("1. Identificación tributaria (SRI)")
    _campos(hoja, _filas_sri(audit, profile), BLOQUE_SRI, traza, cruces)
    _hoja_catalogo(libro, "SRI - registro original", "Catastro RUC SRI (base local)", catalog_sri)

    hoja = _Hoja(libro, "2. Supercias", (32, 54, 55, 18, 24, 24))
    hoja.titulo("2. Información societaria (Supercias)")
    _campos(hoja, _filas_supercias(profile, data), BLOQUE_SUPERCIAS, traza, cruces)
    _hoja_catalogo(libro, "Supercias - registro original", "Directorio de Compañías (base local)",
                   catalog_supercias, (catalog_supercias or {}).get("_catalogo_fecha_actualizacion", ""))

    hoja = _Hoja(libro, "3. Ubicación", (32, 54, 55, 18, 24, 24))
    hoja.titulo("3. Ubicación")
    _campos(hoja, _filas_ubicacion(location), BLOQUE_UBICACION, traza, cruces)

    _hoja_personas(
        libro, "4. Administradores",
        ["Identificación", "Tipo de identificación", "Nombre", "Cargo", "Nacionalidad", "Fuente",
         "Fecha de consulta", "Soporte"],
        [(_o_pendiente(row_get(a, "identificacion")), row_get(a, "tipo_identificacion") or "",
          row_get(a, "nombre"), _o_pendiente(row_get(a, "cargo")), _o_pendiente(row_get(a, "nacionalidad")),
          row_get(a, "fuente") or "", row_get(a, "fecha_consulta") or "",
          _estado_persona(a, ("identificacion", "nombre", "cargo"))) for a in admins],
    )
    _hoja_personas(
        libro, "5. Accionistas",
        ["N.º", "Identificación", "Nombre", "Tipo de identificación", "Capital (USD)", "Participación",
         "Beneficiario final", "Fuente", "Fecha de consulta", "Soporte"],
        [(row_get(s, "numero") or "", _o_pendiente(row_get(s, "identificacion")), row_get(s, "nombre"),
          row_get(s, "tipo_identificacion") or "", _numero(row_get(s, "capital")),
          _porcentaje(row_get(s, "participacion_porcentaje")), row_get(s, "beneficiario_final") or "",
          row_get(s, "fuente") or "", row_get(s, "fecha_consulta") or "",
          _estado_persona(s, ("identificacion", "nombre"))) for s in shareholders],
        formatos={5: _FORMATO_USD, 6: _FORMATO_PCT},
        total={5: _FORMATO_USD, 6: _FORMATO_PCT},
    )
    _hoja_financiero(libro, ctx.get("financial") or {"snapshot": snapshot, "years": []}, traza)
    _hoja_estado_detallado(libro, "EEFF - Situación", balance_details, {"1", "2", "3"})
    _hoja_estado_detallado(libro, "EEFF - Resultados", balance_details, {"4", "5", "6", "7", "8"})
    _hoja_requisitos(libro, validacion, cierre)
    _hoja_validaciones(libro, validacion, risks)
    _hoja_documentos(libro, docs)
    _hoja_fuentes(libro, source_checks, sources, provenance)
    _hoja_hallazgos(libro, data)

    libro.properties.title = f"Levantamiento de información — {audit['company_name']}"
    libro.properties.creator = "Atlas · Auddit"
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
