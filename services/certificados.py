"""
services/certificados.py — Lectura de los certificados de nómina de Supercias.

El auditor adjunta el certificado electrónico (PDF) de nómina de
administradores o de accionistas/socios. Este módulo extrae su texto y
propone las filas que encuentra. No escribe en la base: la propuesta se
muestra al auditor, que la corrige y confirma antes de importar nada.

El análisis es heurístico: busca cada fila por su identificación (cédula de
10 dígitos o RUC de 13) y reconoce en el texto cercano el nombre, el cargo,
la nacionalidad, el capital y la participación. Por eso la revisión del
auditor es obligatoria y las filas que no pueda leer quedan para la captura
manual.
"""
from __future__ import annotations

import io
import re
from datetime import date

from services.identificacion import inferir_tipo
from services.normalizacion import normalizar_texto

TIPOS_CERTIFICADO = {
    "administradores": "Certificado de nómina de administradores",
    "accionistas": "Certificado de nómina de accionistas/socios",
}
MAX_PDF_BYTES = 10 * 1024 * 1024

# Cargos de administración más frecuentes; los compuestos van antes que sus
# versiones cortas para que "GERENTE GENERAL" no se lea como "GERENTE".
CARGOS = sorted((
    "GERENTE GENERAL", "PRESIDENTE EJECUTIVO", "PRESIDENTE", "VICEPRESIDENTE",
    "GERENTE", "SUBGERENTE", "GERENTE FINANCIERO", "APODERADO", "APODERADO ESPECIAL",
    "LIQUIDADOR", "DIRECTOR", "DIRECTOR EJECUTIVO", "ADMINISTRADOR", "REPRESENTANTE LEGAL",
    "SECRETARIO", "TESORERO", "COMISARIO", "PRESIDENTE DEL DIRECTORIO",
), key=len, reverse=True)

NACIONALIDADES = sorted((
    "ECUADOR", "ECUATORIANA", "ECUATORIANO", "COLOMBIA", "COLOMBIANA", "COLOMBIANO",
    "PERU", "PERUANA", "PERUANO", "VENEZUELA", "VENEZOLANA", "VENEZOLANO",
    "ESPANA", "ESPANOLA", "ESPANOL", "ESTADOS UNIDOS", "ESTADOUNIDENSE", "CHILE",
    "CHILENA", "CHILENO", "ARGENTINA", "ARGENTINO", "MEXICO", "MEXICANA", "MEXICANO",
    "CHINA", "CHINO", "ITALIA", "ITALIANA", "ITALIANO", "ALEMANIA", "ALEMANA", "ALEMAN",
    "FRANCIA", "FRANCESA", "FRANCES", "PANAMA", "PANAMENA", "PANAMENO", "CANADA",
    "CUBA", "CUBANA", "CUBANO", "BRASIL", "BRASILENA", "BRASILENO",
), key=len, reverse=True)

# Palabras de encabezados y pies de tabla que nunca forman parte de un nombre.
_RUIDO = {
    "NOMBRE", "NOMBRES", "APELLIDOS", "IDENTIFICACION", "CEDULA", "RUC", "PASAPORTE",
    "CARGO", "NACIONALIDAD", "CAPITAL", "PARTICIPACION", "PORCENTAJE", "FECHA",
    "NOMBRAMIENTO", "REGISTRO", "MERCANTIL", "PERIODO", "ANOS", "TIPO", "INVERSION",
    "NACIONAL", "EXTRANJERA", "USD", "US", "DOLARES", "TOTAL", "NO", "N",
    "SOCIO", "ACCIONISTA", "ADMINISTRADOR", "REPRESENTANTE", "LEGAL", "SI",
}

# Pies y encabezados de página que el PDF intercala entre filas.
_PIE = {"PAGINA", "HOJA", "FIRMA", "CERTIFICA", "CERTIFICADO", "SUPERINTENDENCIA", "EMITIDO", "GENERADO"}

_ID = re.compile(r"(?<!\d)(\d{13}|\d{10})(?!\d)")
_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_FECHA_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
_FECHA_TEXTO = re.compile(r"\b(\d{1,2}) DE ([A-Z]+) DE(?:L)? (\d{4})\b")
_PORCENTAJE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_MONTO = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{2})(?![\d])")
_PALABRA = re.compile(r"^[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ'\-]*$")
_NO_LETRAS = re.compile(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ'\-\s]+")


# ---------------------------------------------------------------------------
# Texto del PDF
# ---------------------------------------------------------------------------

def extraer_texto(pdf: bytes) -> str:
    """Texto de todas las páginas del PDF. Lanza ValueError con un mensaje
    para el auditor si el archivo no es un PDF legible."""
    if not pdf.startswith(b"%PDF"):
        raise ValueError("El archivo no es un PDF")
    if len(pdf) > MAX_PDF_BYTES:
        raise ValueError("El PDF supera el tamaño máximo de 10 MB")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ValueError("Falta la librería pypdf: instálela con pip install -r requirements.txt") from exc
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted:
            raise ValueError("El PDF está protegido con contraseña")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise ValueError("No se pudo leer el PDF: el archivo parece dañado") from exc


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def _a_iso(anio: int, mes: int, dia: int) -> str:
    try:
        return date(anio, mes, dia).isoformat()
    except ValueError:
        return ""


def fecha_del_certificado(texto: str) -> str:
    """Primera fecha válida (no futura) del documento: la de emisión suele
    estar en el encabezado. Vacío si no se reconoce ninguna."""
    normal = normalizar_texto(texto)
    candidatas = []
    for m in _FECHA_ISO.finditer(normal):
        candidatas.append((m.start(), _a_iso(int(m[1]), int(m[2]), int(m[3]))))
    for m in _FECHA_DMY.finditer(normal):
        candidatas.append((m.start(), _a_iso(int(m[3]), int(m[2]), int(m[1]))))
    for m in _FECHA_TEXTO.finditer(normal):
        if m[2] in _MESES:
            candidatas.append((m.start(), _a_iso(int(m[3]), _MESES[m[2]], int(m[1]))))
    hoy = date.today().isoformat()
    return next((f for _, f in sorted(candidatas) if f and f <= hoy), "")


def _monto(texto: str) -> float:
    """'1.234,56', '1,234.56' o '800.00' -> float (el último separador con dos
    decimales es el decimal)."""
    entero, decimales = texto[:-3], texto[-2:]
    return float(re.sub(r"[.,]", "", entero) + "." + decimales)


def _buscar(frases: list[str], normal: str) -> str:
    return next((f for f in frases if re.search(rf"(?<![A-Z]){f}(?![A-Z])", normal)), "")


def _palabras(texto: str, quitar: list[str]) -> list[list[str]]:
    """Tramos de palabras de un nombre, con su ortografía original (tildes y
    Ñ). Cargo, nacionalidad, números y encabezados cortan los tramos."""
    tokens = _NO_LETRAS.sub(" ", texto).upper().split()
    normales = [normalizar_texto(t) for t in tokens]
    corte = [False] * len(tokens)
    for frase in (f.split() for f in quitar if f):
        for i in range(len(tokens) - len(frase) + 1):
            if normales[i:i + len(frase)] == frase:
                corte[i:i + len(frase)] = [True] * len(frase)
    tramos, actual = [], []
    for token, normal, cortar in zip(tokens, normales, corte):
        if not cortar and _PALABRA.match(token) and normal not in _RUIDO:
            actual.append(token)
        else:
            if actual:
                tramos.append(actual)
            actual = []
    if actual:
        tramos.append(actual)
    return tramos


def _nombre(lineas: list[str], quitar: list[str]) -> str:
    """Tramo de palabras más largo de la primera línea de la fila, más las
    líneas siguientes que solo traen palabras (el resto de un nombre largo
    que el PDF partió en dos líneas)."""
    mejor = max(_palabras(lineas[0], quitar), key=len, default=[])
    for linea in lineas[1:]:
        tokens = linea.upper().split()
        tramos = _palabras(linea, quitar)
        solo_nombre = (
            len(tramos) == 1 and tramos[0] == tokens and len(tokens) <= 4
            and not {normalizar_texto(t) for t in tokens} & _PIE
        )
        if solo_nombre:
            mejor = mejor + tramos[0]
    return " ".join(mejor) if len(mejor) >= 2 else ""


def _registros(texto: str, ruc_empresa: str) -> list[tuple[str, list[str]]]:
    """(identificación, líneas de la fila). Una fila empieza en la línea que
    trae una identificación y sigue en las líneas siguientes hasta la próxima,
    porque las celdas largas del PDF se parten en varias líneas."""
    registros: list[tuple[str, list[str]]] = []
    for linea in texto.splitlines():
        ids = [i for i in _ID.findall(linea) if i != ruc_empresa]
        if ids:
            registros.append((ids[0], [_ID.sub(" ", linea)]))
        elif registros and linea.strip():
            registros[-1][1].append(linea)
    return registros


def analizar_certificado(texto: str, tipo: str, ruc_empresa: str = "") -> dict:
    """Propuesta de filas del certificado para que el auditor la revise.

    Devuelve {"filas": [...], "fecha_certificado": "AAAA-MM-DD" | "",
    "advertencias": [...]}. Cada fila de administradores trae identificacion,
    tipo_identificacion, nombre, cargo y nacionalidad; cada fila de
    accionistas trae identificacion, tipo_identificacion, nombre, capital y
    participacion_porcentaje.
    """
    if tipo not in TIPOS_CERTIFICADO:
        raise ValueError("Tipo de certificado no reconocido")
    advertencias = []
    normal_doc = normalizar_texto(texto)
    if not normal_doc:
        advertencias.append(
            "El PDF no tiene texto seleccionable (parece escaneado): registre la nómina manualmente."
        )
    elif ruc_empresa and ruc_empresa not in normal_doc:
        advertencias.append(
            f"El certificado no menciona el RUC del expediente ({ruc_empresa}). "
            "Verifique que corresponde a esta compañía."
        )

    filas = []
    for identificacion, lineas in _registros(texto, ruc_empresa):
        normal = normalizar_texto(" ".join(lineas))
        nacionalidad = _buscar(NACIONALIDADES, normal)
        propuesta = {
            "identificacion": identificacion,
            "tipo_identificacion": inferir_tipo(identificacion),
        }
        if tipo == "administradores":
            cargo = _buscar(CARGOS, normal)
            propuesta |= {"cargo": cargo, "nacionalidad": nacionalidad}
            quitar = [cargo, nacionalidad]
        else:
            montos = _MONTO.findall(normal)
            porcentaje = _PORCENTAJE.search(normal)
            propuesta |= {
                "capital": _monto(montos[0]) if montos else None,
                "participacion_porcentaje": float(porcentaje[1].replace(",", ".")) if porcentaje else None,
            }
            quitar = [nacionalidad]
        propuesta["nombre"] = _nombre(lineas, quitar)
        if propuesta["nombre"]:
            filas.append(propuesta)

    if tipo == "accionistas":
        # Sin porcentaje explícito, la participación sale del capital de cada socio.
        total = sum(f["capital"] or 0 for f in filas)
        for f in filas:
            if f["participacion_porcentaje"] is None and f["capital"] and total:
                f["participacion_porcentaje"] = round(f["capital"] * 100 / total, 4)

    if normal_doc and not filas:
        advertencias.append(
            "No se reconocieron filas con cédula o RUC. El PDF quedó como evidencia: "
            "registre la nómina manualmente."
        )
    return {
        "filas": filas,
        "fecha_certificado": fecha_del_certificado(texto),
        "advertencias": advertencias,
    }
