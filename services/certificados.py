"""
services/certificados.py — Lectura de los certificados de nómina de Supercias.

El auditor adjunta los documentos de Supercias (PDF) de la compañía: la
información general, que trae los administradores, y la nómina de accionistas
o socios, que pueden venir en un mismo PDF o en varios. Este módulo extrae su
texto y propone las filas de ambas nóminas. No escribe en la base: la
propuesta se muestra al auditor, que la corrige y confirma antes de importar.

El análisis prefiere descartar a adivinar:
  - Antes de extraer nada se verifica la compañía (verificar_compania): el
    documento debe contener el RUC del expediente. Si declara otro RUC, no
    trae ninguno o no tiene texto, se rechaza.
  - Una fila empieza en una identificación válida: cédula con provincia,
    tercer dígito y dígito verificador correctos, o RUC con su dígito
    verificador. Así no se toma por cédula un teléfono o un número de
    registro. El RUC de la propia compañía nunca es una fila.
  - Es administrador si trae un cargo reconocido y accionista si trae capital
    o porcentaje o está en la sección de accionistas. Una identificación sin
    ninguna de esas señales se omite con un aviso.
  - El nombre son las palabras de la fila que no son encabezados, cargo ni
    nacionalidad.

Está calibrado con documentos reales del portal de Supercias, en los que el
texto del PDF pega números y letras ("384732.0000CANDO"), une el número de
registro con la cédula ("2315" + "0102960085") y parte cargos y nombres en
varias líneas ("PRESIDEN" / "TE"). La revisión del auditor sigue siendo
obligatoria.
"""
from __future__ import annotations

import io
import re
from datetime import date

from services.identificacion import cedula_valida, inferir_tipo, ruc_valido
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

# Palabras de encabezados, etiquetas y columnas de los documentos que nunca
# forman parte de un nombre. No incluye palabras que también son apellidos
# frecuentes (CALLE, BARRIO...).
_RUIDO = {
    "NOMBRE", "NOMBRES", "APELLIDOS", "IDENTIFICACION", "CEDULA", "RUC", "PASAPORTE",
    "CARGO", "NACIONALIDAD", "CAPITAL", "PARTICIPACION", "PORCENTAJE", "FECHA",
    "NOMBRAMIENTO", "REGISTRO", "MERCANTIL", "PERIODO", "ANOS", "ANO", "TIPO", "INVERSION",
    "NACIONAL", "EXTRANJERA", "USD", "US", "DOLARES", "TOTAL", "NO", "N",
    "SOCIO", "SOCIOS", "ACCIONISTA", "ACCIONISTAS", "ADMINISTRADOR", "ADMINISTRADORES",
    "REPRESENTANTE", "LEGAL", "SI", "RAZON", "SOCIAL", "DENOMINACION", "EXPEDIENTE",
    "DIRECCION", "FORMULARIO", "NOMINA", "VALOR", "RL", "ADM", "ART", "REG", "FECH", "NOMB",
    "DATOS", "GENERALES", "COMPANIA", "INFORMACION", "PROVINCIA", "CANTON", "PARROQUIA",
    "CIUDAD", "INTERSECCION", "REFERENCIA", "UBICACION", "TELEFONO", "CORREO", "ELECTRONICO",
    "OFICINA", "SITUACION", "CONSTITUCION", "PLAZO", "OBJETO", "ACTIVIDAD", "ECONOMICA",
    "CIIU", "DECLARA", "RESPONSABILIZA", "VERACIDAD",
}

# Pies y encabezados de página que el PDF intercala entre filas.
_PIE = {"PAGINA", "HOJA", "FIRMA", "CERTIFICA", "CERTIFICADO", "SUPERINTENDENCIA", "EMITIDO", "GENERADO"}

# Una fila sigue a lo sumo en estas líneas después de la de su identificación.
_MAX_LINEAS_FILA = 3
# Una línea con tantas palabras es prosa (declaraciones, pies legales): cierra la fila.
_PALABRAS_PROSA = 8

_DIGITOS = re.compile(r"\d{10,}")
_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_FECHA_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
_FECHA_TEXTO = re.compile(r"\b(\d{1,2}) DE ([A-Z]+) DE(?:L)? (\d{4})\b")
_EMISION = re.compile(r"FECHA DE EMISION\W*")
_PORCENTAJE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
# Montos con 2 o 4 decimales ("600,00", "1.234,56", "384732.0000"). Con 3
# cifras tras el separador es un separador de miles, no un decimal.
_MONTO = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+[.,](?:\d{4}|\d{2})|\d+[.,](?:\d{4}|\d{2}))(?![\d])")
_PALABRA = re.compile(r"^[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ'\-]*$")
_NO_LETRAS = re.compile(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ'\-\s]+")
_LETRA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_PEGADOS = re.compile(rf"(?<=\d)(?=[{_LETRA}])|(?<=[{_LETRA}])(?=\d)")


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
# Fechas y montos
# ---------------------------------------------------------------------------

def _a_iso(anio: int, mes: int, dia: int) -> str:
    try:
        return date(anio, mes, dia).isoformat()
    except ValueError:
        return ""


def _fechas(texto: str) -> list[tuple[int, str]]:
    """(posición, AAAA-MM-DD) de las fechas válidas del texto normalizado."""
    candidatas = []
    for m in _FECHA_ISO.finditer(texto):
        candidatas.append((m.start(), _a_iso(int(m[1]), int(m[2]), int(m[3]))))
    for m in _FECHA_DMY.finditer(texto):
        candidatas.append((m.start(), _a_iso(int(m[3]), int(m[2]), int(m[1]))))
    for m in _FECHA_TEXTO.finditer(texto):
        if m[2] in _MESES:
            candidatas.append((m.start(), _a_iso(int(m[3]), _MESES[m[2]], int(m[1]))))
    return sorted((pos, f) for pos, f in candidatas if f)


def fecha_del_certificado(texto: str) -> str:
    """Fecha de emisión del documento: la rotulada "FECHA DE EMISIÓN" o, si
    no la hay, la más reciente que no sea futura (las demás son fechas de
    constitución, nombramientos o plazos). Vacío si no se reconoce ninguna."""
    normal = normalizar_texto(texto)
    hoy = date.today().isoformat()
    fechas = [(pos, f) for pos, f in _fechas(normal) if f <= hoy]
    for m in _EMISION.finditer(normal):
        rotulada = next((f for pos, f in fechas if m.end() <= pos <= m.end() + 5), "")
        if rotulada:
            return rotulada
    return max((f for _, f in fechas), default="")


def _monto(texto: str) -> float:
    """'1.234,56', '1,234.56', '800.00' o '384732.0000' -> float: el último
    separador, seguido de 2 o 4 cifras, es el decimal."""
    entero, _sep, decimales = re.split(r"([.,])(?=\d+$)", texto)
    return float(re.sub(r"[.,]", "", entero) + "." + decimales)


# ---------------------------------------------------------------------------
# Identificaciones
# ---------------------------------------------------------------------------

def _identificacion(digitos: str) -> str:
    """Cédula o RUC válido dentro de una secuencia de dígitos. El PDF a veces
    pega a la identificación el número de registro que la precede
    ("2315" + "0102960085"), así que en secuencias más largas se prueba el
    final. Vacío si no hay una identificación válida."""
    for largo, valida in ((13, ruc_valido), (10, cedula_valida)):
        if len(digitos) >= largo and valida(digitos[-largo:]):
            return digitos[-largo:]
    return ""


def _rucs_de_la_compania(lineas: list[str]) -> set[str]:
    """RUC que el documento declara como el de la compañía: los RUC válidos
    rotulados "RUC" en la misma línea o hasta dos líneas antes o después (el
    PDF a veces pone la etiqueta y el valor en líneas distintas)."""
    rotuladas = {i for i, linea in enumerate(lineas) if re.search(r"(?<![A-Z])RUC(?![A-Z])", normalizar_texto(linea))}
    rucs = set()
    for i, linea in enumerate(lineas):
        if any(abs(i - j) <= 2 for j in rotuladas):
            rucs |= {d for d in _DIGITOS.findall(linea) if len(d) == 13 and ruc_valido(d)}
    return rucs


# ---------------------------------------------------------------------------
# Filas
# ---------------------------------------------------------------------------

def _separar_pegados(linea: str) -> str:
    """'384732.0000CANDO' -> '384732.0000 CANDO'; 'RL2GERENTE' -> 'RL 2 GERENTE'."""
    return _PEGADOS.sub(" ", linea)


def _seccion(linea: str) -> str:
    """'administradores' o 'accionistas' si la línea es el título de una
    sección de la nómina; vacío si no lo es (o si nombra a ambas, como el
    título general del certificado)."""
    normal = normalizar_texto(linea)
    if _DIGITOS.search(normal):
        return ""
    adm = "ADMINISTRADOR" in normal
    acc = "ACCIONISTA" in normal or "SOCIO" in normal
    return ("administradores" if adm else "accionistas") if adm != acc else ""


def _registros(lineas: list[str], excluir: set[str]) -> list[tuple[str, str, list[str]]]:
    """(sección, identificación, líneas de la fila). Una fila empieza en la
    línea con una identificación válida y sigue en las siguientes (las celdas
    largas del PDF se parten) hasta la próxima fila, un título de sección, una
    línea de prosa o _MAX_LINEAS_FILA líneas."""
    registros: list[tuple[str, str, list[str]]] = []
    seccion, abierta = "", False
    for linea in lineas:
        ids = [i for i in (_identificacion(d) for d in _DIGITOS.findall(linea)) if i and i not in excluir]
        if ids:
            registros.append((seccion, ids[0], [_DIGITOS.sub(" ", linea)]))
            abierta = True
        elif _seccion(linea):
            seccion, abierta = _seccion(linea), False
        elif abierta and len(_separar_pegados(linea).split()) >= _PALABRAS_PROSA:
            abierta = False
        elif abierta and linea.strip():
            registros[-1][2].append(linea)
            abierta = len(registros[-1][2]) <= _MAX_LINEAS_FILA
    return registros


def _tokens(lineas: list[str]) -> list[tuple[int, str, str]]:
    """(línea, palabra original en mayúsculas, palabra normalizada)."""
    return [
        (n, token, normalizar_texto(token))
        for n, linea in enumerate(lineas)
        for token in _NO_LETRAS.sub(" ", linea).upper().split()
    ]


def _cargo(tokens: list[tuple[int, str, str]]) -> tuple[str, set[int]]:
    """Cargo de la fila y las posiciones de sus palabras. Admite cargos
    partidos por el PDF ("PRESIDEN" / "TE"): compara las palabras unidas."""
    normales = [t[2] for t in tokens]
    for cargo in CARGOS:
        compacto = cargo.replace(" ", "")
        for i in range(len(normales)):
            unido = ""
            for j in range(i, min(i + 5, len(normales))):
                unido += normales[j]
                if unido == compacto:
                    return cargo, set(range(i, j + 1))
                if not compacto.startswith(unido):
                    break
    return "", set()


def _frases(tokens: list[tuple[int, str, str]], frases: list[str]) -> tuple[str, set[int]]:
    """Primera frase de la lista presente en la fila y sus posiciones."""
    normales = [t[2] for t in tokens]
    for frase in frases:
        partes = frase.split()
        for i in range(len(normales) - len(partes) + 1):
            if normales[i:i + len(partes)] == partes:
                return frase, set(range(i, i + len(partes)))
    return "", set()


def _nombre(tokens: list[tuple[int, str, str]], cortes: set[int], lineas: list[str]) -> str:
    """Tramo de palabras más largo de la fila (puede cruzar líneas: el PDF
    parte los nombres largos), más las líneas finales que solo traen palabras
    y quedaron separadas del tramo por un número o una fecha."""
    tramos: list[list[int]] = []
    actual: list[int] = []
    for i, (_n, token, normal) in enumerate(tokens):
        if i not in cortes and _PALABRA.match(token) and normal not in _RUIDO and normal not in _PIE:
            actual.append(i)
        else:
            if actual:
                tramos.append(actual)
            actual = []
    if actual:
        tramos.append(actual)
    mejor = max(tramos, key=len, default=[])
    for n in range(1, len(lineas)):
        posiciones = [i for i, t in enumerate(tokens) if t[0] == n]
        solo_nombre = (
            posiciones and len(posiciones) <= 4 and not set(posiciones) & set(mejor)
            and any(posiciones == tramo for tramo in tramos)
            and len(lineas[n].split()) == len(posiciones)
        )
        if solo_nombre and mejor and posiciones[0] > mejor[-1]:
            mejor = mejor + posiciones
    palabras = [tokens[i][1] for i in mejor]
    return " ".join(palabras) if len(palabras) >= 2 else ""


def _fila(identificacion: str, lineas: list[str], seccion: str) -> tuple[str, dict] | None:
    """Clasifica la fila y extrae sus datos. Un cargo la hace de
    administradores; un capital o porcentaje, de accionistas; si trae ambos o
    ninguno, decide la sección del documento. Sin ninguna señal, no es una
    fila de la nómina (None)."""
    lineas = [_separar_pegados(linea) for linea in lineas]
    normal = normalizar_texto(" ".join(lineas))
    tokens = _tokens(lineas)
    cargo, pos_cargo = _cargo(tokens)
    nacionalidad, pos_nacionalidad = _frases(tokens, NACIONALIDADES)
    montos = _MONTO.findall(normal)
    porcentaje = _PORCENTAJE.search(normal)
    if cargo and not (montos or porcentaje):
        tipo = "administradores"
    elif (montos or porcentaje) and not cargo:
        tipo = "accionistas"
    elif seccion:
        tipo = seccion
    else:
        return None
    fila = {"identificacion": identificacion, "tipo_identificacion": inferir_tipo(identificacion)}
    if tipo == "administradores":
        fila |= {"cargo": cargo, "nacionalidad": nacionalidad}
        cortes = pos_cargo | pos_nacionalidad
    else:
        fila |= {
            "capital": _monto(montos[0]) if montos else None,
            "participacion_porcentaje": float(porcentaje[1].replace(",", ".")) if porcentaje else None,
        }
        cortes = pos_nacionalidad | pos_cargo
    fila["nombre"] = _nombre(tokens, cortes, lineas)
    return (tipo, fila) if fila["nombre"] else None


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def _faltantes(nomina: dict[str, list[dict]]) -> list[str]:
    return [
        f"No se reconocieron {tipo} con cédula o RUC válidos: regístrelos manualmente si corresponde."
        for tipo in NOMINAS if not nomina[tipo]
    ]


def verificar_compania(texto: str, ruc_empresa: str) -> None:
    """Confirma que el documento es de la compañía del expediente antes de
    extraer sus datos: debe contener el RUC del expediente. Lanza ValueError
    con el motivo si no lo contiene, declara otro RUC o no tiene texto."""
    if not ruc_empresa:
        raise ValueError(
            "Registre el RUC del expediente antes de adjuntar el certificado: "
            "se usa para verificar que el documento sea de esta compañía"
        )
    if not normalizar_texto(texto):
        raise ValueError(
            "El PDF no tiene texto seleccionable (parece escaneado): no se puede verificar que sea "
            "de esta compañía. Registre la nómina manualmente"
        )
    if any(digitos.endswith(ruc_empresa) for digitos in _DIGITOS.findall(texto)):
        return
    propios = _rucs_de_la_compania(texto.splitlines())
    if propios:
        raise ValueError(
            f"El documento corresponde al RUC {', '.join(sorted(propios))}, no al del expediente "
            f"({ruc_empresa}). Descargue el documento de esta compañía"
        )
    raise ValueError(
        f"El documento no menciona el RUC del expediente ({ruc_empresa}): no se puede verificar que "
        "sea de esta compañía"
    )


def analizar_nomina(texto: str, ruc_empresa: str = "", avisar_faltantes: bool = True) -> dict:
    """Propuesta de administradores y accionistas del documento para que el
    auditor la revise. No verifica la compañía (ver verificar_compania): el
    RUC del expediente y el que declara el documento solo se excluyen de las
    filas.

    Devuelve {"administradores": [...], "accionistas": [...],
    "fecha_certificado": "AAAA-MM-DD" | "", "advertencias": [...]}. Cada
    administrador trae identificacion, tipo_identificacion, nombre, cargo y
    nacionalidad; cada accionista trae identificacion, tipo_identificacion,
    nombre, capital y participacion_porcentaje.
    """
    advertencias = []
    lineas = texto.splitlines()
    normal_doc = normalizar_texto(texto)
    propios = _rucs_de_la_compania(lineas)
    if not normal_doc:
        advertencias.append(
            "El PDF no tiene texto seleccionable (parece escaneado): registre la nómina manualmente."
        )

    nomina: dict[str, list[dict]] = {tipo: [] for tipo in NOMINAS}
    omitidas = []
    for seccion, identificacion, filas in _registros(lineas, propios | {ruc_empresa}):
        resultado = _fila(identificacion, filas, seccion)
        if resultado:
            nomina[resultado[0]].append(resultado[1])
        else:
            omitidas.append(identificacion)
    if omitidas:
        advertencias.append(
            f"Se omitieron {len(omitidas)} identificación(es) que no se reconocen como administrador ni "
            f"accionista: {', '.join(omitidas)}."
        )

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


def analizar_nominas(documentos: list[tuple[str, str]], ruc_empresa: str) -> dict:
    """Propuesta combinada de varios PDF [(nombre, texto)]: Supercias entrega
    los administradores y los accionistas en documentos separados. Misma forma
    que analizar_nomina(); las filas repetidas entre documentos se proponen
    una sola vez y la fecha es la del documento más reciente.

    Primero verifica la compañía de todos los documentos: si alguno no es del
    expediente, lanza ValueError con el nombre del archivo y no extrae nada."""
    for nombre, texto in documentos:
        try:
            verificar_compania(texto, ruc_empresa)
        except ValueError as exc:
            raise ValueError(f"{nombre}: {exc}") from exc
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
