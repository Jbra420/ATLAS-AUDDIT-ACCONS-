"""
services/certificados.py — Lectura de los certificados de nómina de Supercias.

El auditor adjunta el certificado electrónico (PDF) de la compañía, que trae
la nómina de administradores y la de accionistas/socios. Este módulo extrae
su texto y propone las filas de ambas nóminas. No escribe en la base: la propuesta se
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

NOMINAS = ("administradores", "accionistas")
TITULO_CERTIFICADO = "Certificado de nómina de administradores y accionistas"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_CERTIFICADOS = 5  # PDF por envío

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


def _seccion(linea: str) -> str:
    """'administradores' o 'accionistas' si la línea es el título de una
    sección de la nómina; vacío si no lo es (o si nombra a ambas, como el
    título general del certificado)."""
    normal = normalizar_texto(linea)
    adm = "ADMINISTRADOR" in normal
    acc = "ACCIONISTA" in normal or "SOCIO" in normal
    return ("administradores" if adm else "accionistas") if adm != acc else ""


def _registros(texto: str, ruc_empresa: str) -> list[tuple[str, str, list[str]]]:
    """(sección, identificación, líneas de la fila). Una fila empieza en la
    línea que trae una identificación y sigue en las siguientes hasta la
    próxima, porque las celdas largas del PDF se parten en varias líneas. Un
    título de sección corta la fila en curso."""
    registros: list[tuple[str, str, list[str]]] = []
    seccion, en_fila = "", False
    for linea in texto.splitlines():
        ids = [i for i in _ID.findall(linea) if i != ruc_empresa]
        if ids:
            registros.append((seccion, ids[0], [_ID.sub(" ", linea)]))
            en_fila = True
        elif _seccion(linea):
            seccion, en_fila = _seccion(linea), False
        elif en_fila and linea.strip():
            registros[-1][2].append(linea)
    return registros


def _fila(identificacion: str, lineas: list[str], seccion: str) -> tuple[str, dict] | None:
    """Clasifica la fila y extrae sus datos. Un cargo la hace de
    administradores; un capital o porcentaje, de accionistas. Si no trae
    ninguno de los dos, decide la sección del documento en que aparece."""
    normal = normalizar_texto(" ".join(lineas))
    cargo = _buscar(CARGOS, normal)
    montos = _MONTO.findall(normal)
    porcentaje = _PORCENTAJE.search(normal)
    if cargo and not (montos or porcentaje):
        tipo = "administradores"
    elif (montos or porcentaje) and not cargo:
        tipo = "accionistas"
    else:
        tipo = seccion or ("administradores" if cargo else "accionistas")
    nacionalidad = _buscar(NACIONALIDADES, normal)
    fila = {"identificacion": identificacion, "tipo_identificacion": inferir_tipo(identificacion)}
    if tipo == "administradores":
        fila |= {"cargo": cargo, "nacionalidad": nacionalidad}
        quitar = [cargo, nacionalidad]
    else:
        fila |= {
            "capital": _monto(montos[0]) if montos else None,
            "participacion_porcentaje": float(porcentaje[1].replace(",", ".")) if porcentaje else None,
        }
        quitar = [nacionalidad]
    fila["nombre"] = _nombre(lineas, quitar)
    return (tipo, fila) if fila["nombre"] else None


def _faltantes(nomina: dict[str, list[dict]]) -> list[str]:
    return [
        f"No se reconocieron {tipo} con cédula o RUC: regístrelos manualmente si corresponde."
        for tipo in NOMINAS if not nomina[tipo]
    ]


def analizar_nomina(texto: str, ruc_empresa: str = "", avisar_faltantes: bool = True) -> dict:
    """Propuesta de administradores y accionistas del certificado para que el
    auditor la revise. Un mismo PDF alimenta las dos nóminas.

    Devuelve {"administradores": [...], "accionistas": [...],
    "fecha_certificado": "AAAA-MM-DD" | "", "advertencias": [...]}. Cada
    administrador trae identificacion, tipo_identificacion, nombre, cargo y
    nacionalidad; cada accionista trae identificacion, tipo_identificacion,
    nombre, capital y participacion_porcentaje.
    """
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

    nomina: dict[str, list[dict]] = {tipo: [] for tipo in NOMINAS}
    for seccion, identificacion, lineas in _registros(texto, ruc_empresa):
        resultado = _fila(identificacion, lineas, seccion)
        if resultado:
            nomina[resultado[0]].append(resultado[1])

    # Sin porcentaje explícito, la participación sale del capital de cada socio.
    accionistas = nomina["accionistas"]
    total = sum(a["capital"] or 0 for a in accionistas)
    for a in accionistas:
        if a["participacion_porcentaje"] is None and a["capital"] and total:
            a["participacion_porcentaje"] = round(a["capital"] * 100 / total, 4)

    if normal_doc and avisar_faltantes:
        advertencias += _faltantes(nomina)
    return nomina | {"fecha_certificado": fecha_del_certificado(texto), "advertencias": advertencias}


def _clave(tipo: str, fila: dict) -> tuple:
    """Identidad de una fila para no repetirla cuando dos PDF la traen: un
    administrador puede tener más de un cargo; un accionista, una sola fila."""
    return (fila["identificacion"], fila.get("cargo", "")) if tipo == "administradores" else (fila["identificacion"],)


def analizar_nominas(documentos: list[tuple[str, str]], ruc_empresa: str = "") -> dict:
    """Propuesta combinada de varios PDF [(nombre, texto)]: Supercias puede
    entregar la nómina de administradores y la de accionistas en documentos
    separados. Misma forma que analizar_nomina(); las filas repetidas entre
    documentos se proponen una sola vez y la fecha es la del certificado más
    reciente."""
    nomina: dict[str, list[dict]] = {tipo: [] for tipo in NOMINAS}
    vistas: set[tuple] = set()
    advertencias, fechas = [], []
    for nombre, texto in documentos:
        parcial = analizar_nomina(texto, ruc_empresa, avisar_faltantes=False)
        prefijo = f"{nombre}: " if len(documentos) > 1 else ""
        advertencias += [prefijo + a for a in parcial["advertencias"]]
        fechas.append(parcial["fecha_certificado"])
        for tipo in NOMINAS:
            for fila in parcial[tipo]:
                if (tipo, *_clave(tipo, fila)) not in vistas:
                    vistas.add((tipo, *_clave(tipo, fila)))
                    nomina[tipo].append(fila)
    if any(normalizar_texto(texto) for _, texto in documentos):
        advertencias += _faltantes(nomina)
    return nomina | {"fecha_certificado": max(fechas, default=""), "advertencias": advertencias}
