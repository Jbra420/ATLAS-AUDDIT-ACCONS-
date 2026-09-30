"""
services/pdf_simple.py — Generador mínimo de PDF con la biblioteca estándar.

Alcanza para cartas y certificados: párrafos (justificados, centrados o
alineados), viñetas, una tabla sencilla, un logo JPEG (suelto o como
cabecera que se repite en cada página), bloques de firma y salto de página
automático. Usa las fuentes estándar Helvetica y
Helvetica-Bold con codificación WinAnsi (tildes y eñes del español), así que
no incrusta fuentes ni depende de librerías externas.

No es un maquetador general: si Auddit entrega una plantilla con diseño
propio, esa plantilla reemplaza a este módulo.
"""
from __future__ import annotations

import unicodedata
import zlib

A4 = (595.28, 841.89)
CARTA = (612.0, 792.0)  # Letter: el tamaño de la carta de encargo de Auddit

# Anchos AFM (milésimas de em) de Helvetica y Helvetica-Bold, caracteres 32 a 126.
_ANCHOS = {
    "Helvetica": (
        278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
    ),
    "Helvetica-Bold": (
        278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
        975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
        333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
        611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
    ),
}
_FUENTES = {"Helvetica": "F1", "Helvetica-Bold": "F2"}


def _ancho_caracter(ch: str, fuente: str) -> int:
    code = ord(ch)
    if 32 <= code <= 126:
        return _ANCHOS[fuente][code - 32]
    if ch == "•":
        return 350
    base = unicodedata.normalize("NFD", ch)[0]  # á -> a, Ñ -> N
    if base != ch and 32 <= ord(base) <= 126:
        return _ANCHOS[fuente][ord(base) - 32]
    return 556


def ancho_texto(texto: str, fuente: str, tamano: float) -> float:
    return sum(_ancho_caracter(ch, fuente) for ch in texto) * tamano / 1000


def _literal(texto: str) -> bytes:
    """Cadena PDF en WinAnsi con paréntesis y barras escapados."""
    datos = texto.encode("cp1252", errors="replace")
    return b"(" + datos.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _dimensiones_jpeg(datos: bytes) -> tuple[int, int, int]:
    """(ancho, alto, componentes) leídos del marcador SOF del JPEG."""
    if not datos.startswith(b"\xff\xd8"):
        raise ValueError("El logo debe ser un JPEG")
    i = 2
    while i + 9 < len(datos):
        if datos[i] != 0xFF:
            i += 1
            continue
        marcador = datos[i + 1]
        largo = int.from_bytes(datos[i + 2:i + 4], "big")
        if marcador in (0xC0, 0xC1, 0xC2):
            alto = int.from_bytes(datos[i + 5:i + 7], "big")
            ancho = int.from_bytes(datos[i + 7:i + 9], "big")
            return ancho, alto, datos[i + 9]
        i += 2 + largo
    raise ValueError("No se pudo leer el tamaño del JPEG")


class PdfDocumento:
    """Documento de una o varias páginas que se escribe de arriba hacia abajo."""

    def __init__(self, margen_x: float = 64, margen_y: float = 56, tamano: tuple[float, float] = A4) -> None:
        self.ancho, self.alto = tamano
        self.mx, self.my = margen_x, margen_y
        self.paginas: list[list[bytes]] = []
        self.imagenes: list[tuple[bytes, int, int, int]] = []
        self._cabecera: bytes | None = None  # operación que dibuja el logo de cada página
        self._inicio_y = self.alto - self.my
        self._nueva_pagina()

    # ── Página y posición ────────────────────────────────────────────────
    @property
    def util(self) -> float:
        return self.ancho - 2 * self.mx

    def _nueva_pagina(self) -> None:
        self.paginas.append([] if self._cabecera is None else [self._cabecera])
        self.y = self._inicio_y

    def cabecera_jpeg(self, datos: bytes, ancho: float, *, margen_superior: float = 14,
                      margen_derecho: float = 56, despues: float = 24) -> None:
        """Logo arriba a la derecha en esta página y en las siguientes; el
        texto empieza debajo. Se llama antes de escribir contenido."""
        w, h, componentes = _dimensiones_jpeg(datos)
        alto = ancho * h / w
        self.imagenes.append((datos, w, h, componentes))
        x, y = self.ancho - margen_derecho - ancho, self.alto - margen_superior - alto
        self._cabecera = f"q {ancho:.2f} 0 0 {alto:.2f} {x:.2f} {y:.2f} cm /Im{len(self.imagenes)} Do Q".encode()
        self._inicio_y = y - despues
        self.paginas[-1].insert(0, self._cabecera)
        self.y = self._inicio_y

    def _asegurar(self, alto: float) -> None:
        if self.y - alto < self.my:
            self._nueva_pagina()

    def espacio(self, puntos: float) -> None:
        self.y -= puntos

    def mantener(self, alto: float) -> None:
        """Pasa a otra página si no quedan `alto` puntos: un título no queda
        solo al pie de la página, separado de su texto."""
        self._asegurar(alto)

    def _texto(self, x: float, y: float, texto: str, fuente: str, tamano: float, tw: float = 0) -> None:
        op = f"BT /{_FUENTES[fuente]} {tamano:.2f} Tf {tw:.3f} Tw {x:.2f} {y:.2f} Td ".encode()
        self.paginas[-1].append(op + _literal(texto) + b" Tj ET")

    def linea(self, x1: float, y1: float, x2: float, y2: float, grosor: float = 0.6) -> None:
        self.paginas[-1].append(f"{grosor:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S".encode())

    # ── Contenido ────────────────────────────────────────────────────────
    def _partir(self, texto: str, fuente: str, tamano: float, ancho: float) -> list[str]:
        return [linea for linea, _fin in self._partir_con_fin(texto, fuente, tamano, ancho)]

    def _partir_con_fin(self, texto: str, fuente: str, tamano: float, ancho: float) -> list[tuple[str, bool]]:
        """Líneas que caben en el ancho; el indicador marca la última de cada
        párrafo (separados por "\n"), que nunca se justifica."""
        lineas: list[tuple[str, bool]] = []
        for parrafo in texto.split("\n"):
            actual = ""
            for palabra in parrafo.split(" "):
                prueba = f"{actual} {palabra}" if actual else palabra
                if actual and ancho_texto(prueba, fuente, tamano) > ancho:
                    lineas.append((actual, False))
                    actual = palabra
                else:
                    actual = prueba
            lineas.append((actual, True))
        return lineas

    def parrafo(
        self, texto: str, *, tamano: float = 10.5, negrita: bool = False, alineacion: str = "justificado",
        sangria: float = 0, despues: float = 8, interlineado: float = 1.35, ancho: float | None = None,
    ) -> None:
        """alineacion: justificado, izquierda, centro o derecha."""
        fuente = "Helvetica-Bold" if negrita else "Helvetica"
        disponible = (ancho or self.util) - sangria
        lineas = self._partir_con_fin(texto, fuente, tamano, disponible)
        alto_linea = tamano * interlineado
        for linea, fin_parrafo in lineas:
            self._asegurar(alto_linea)
            self.y -= alto_linea
            ocupado = ancho_texto(linea, fuente, tamano)
            x, tw = self.mx + sangria, 0.0
            if alineacion == "centro":
                x += (disponible - ocupado) / 2
            elif alineacion == "derecha":
                x += disponible - ocupado
            elif alineacion == "justificado" and not fin_parrafo and linea.count(" "):
                tw = (disponible - ocupado) / linea.count(" ")
            self._texto(x, self.y, linea, fuente, tamano, tw)
        self.y -= despues

    def vineta(self, texto: str, *, tamano: float = 10.5, despues: float = 3, sangria: float = 22) -> None:
        self._asegurar(tamano * 1.4)
        self._texto(self.mx + max(0, sangria - 14), self.y - tamano * 1.35, "•", "Helvetica", tamano)
        self.parrafo(texto, tamano=tamano, sangria=sangria, despues=despues, alineacion="izquierda")

    def tabla(self, encabezados: tuple[str, str], filas: list[tuple[str, str]], *, tamano: float = 10) -> None:
        """Tabla de dos columnas centrada, con líneas entre filas (cronograma)."""
        ancho_col = self.util * 0.38
        x0 = self.mx + (self.util - 2 * ancho_col) / 2
        alto = tamano * 1.35

        def fila(celdas: tuple[str, str], negrita: bool) -> None:
            fuente = "Helvetica-Bold" if negrita else "Helvetica"
            partes = [self._partir(c, fuente, tamano, ancho_col - 12) for c in celdas]
            n = max(len(p) for p in partes)
            self._asegurar(n * alto + 10)
            top = self.y - 5
            for col, lineas in enumerate(partes):
                y = top - (n - len(lineas)) * alto / 2
                for linea in lineas:
                    y -= alto
                    x = x0 + col * ancho_col + (ancho_col - ancho_texto(linea, fuente, tamano)) / 2
                    self._texto(x, y, linea, fuente, tamano)
            self.y = top - n * alto - 5
            self.linea(x0, self.y, x0 + 2 * ancho_col, self.y, 1.0 if negrita else 0.5)

        fila(encabezados, True)
        for celdas in filas:
            fila(celdas, False)
        self.y -= 10

    def imagen_jpeg(self, datos: bytes, ancho: float, alineacion: str = "derecha", despues: float = 12) -> None:
        w, h, componentes = _dimensiones_jpeg(datos)
        alto = ancho * h / w
        self._asegurar(alto)
        self.imagenes.append((datos, w, h, componentes))
        nombre = f"Im{len(self.imagenes)}"
        x = {"izquierda": self.mx, "centro": self.mx + (self.util - ancho) / 2}.get(
            alineacion, self.mx + self.util - ancho)
        self.y -= alto
        self.paginas[-1].append(f"q {ancho:.2f} 0 0 {alto:.2f} {x:.2f} {self.y:.2f} cm /{nombre} Do Q".encode())
        self.y -= despues

    def firmas(self, bloques: list[list[str]], *, tamano: float = 10, espacio_firma: float = 64,
               con_linea: bool = True, interlineado: float = 1.45) -> None:
        """Bloques de firma lado a lado: espacio para firmar (con línea o sin
        ella) y datos en negrita, centrados."""
        ancho_col = self.util / len(bloques)
        alto = tamano * interlineado
        self._asegurar(espacio_firma + alto * max(len(b) for b in bloques))
        self.y -= espacio_firma
        for col, lineas in enumerate(bloques):
            x0 = self.mx + col * ancho_col
            margen = max(24, (ancho_col - 230) / 2)  # la línea de firma no pasa de ~8 cm
            if con_linea:
                self.linea(x0 + margen, self.y, x0 + ancho_col - margen, self.y)
            y = self.y
            for linea in lineas:
                y -= alto
                self._texto(x0 + (ancho_col - ancho_texto(linea, "Helvetica-Bold", tamano)) / 2, y,
                            linea, "Helvetica-Bold", tamano)
        self.y -= alto * max(len(b) for b in bloques) + 8

    # ── Serialización ────────────────────────────────────────────────────
    def bytes(self) -> bytes:
        objetos: list[bytes] = []

        def agregar(contenido: bytes) -> int:
            objetos.append(contenido)
            return len(objetos)

        catalogo = agregar(b"")  # se completa al final
        paginas_id = agregar(b"")
        f1 = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        imagenes = []
        for datos, w, h, componentes in self.imagenes:
            espacio = "/DeviceGray" if componentes == 1 else "/DeviceCMYK" if componentes == 4 else "/DeviceRGB"
            cabecera = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {espacio} "
                        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(datos)} >>\nstream\n").encode()
            imagenes.append(agregar(cabecera + datos + b"\nendstream"))
        xobjects = " ".join(f"/Im{i + 1} {oid} 0 R" for i, oid in enumerate(imagenes))
        recursos = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << {xobjects} >> >>"
        kids = []
        for operaciones in self.paginas:
            flujo = zlib.compress(b"\n".join(operaciones))
            contenido = agregar(f"<< /Length {len(flujo)} /Filter /FlateDecode >>\nstream\n".encode()
                                + flujo + b"\nendstream")
            kids.append(agregar(
                f"<< /Type /Page /Parent {paginas_id} 0 R /MediaBox [0 0 {self.ancho:.2f} {self.alto:.2f}] "
                f"/Resources {recursos} /Contents {contenido} 0 R >>".encode()))
        objetos[catalogo - 1] = f"<< /Type /Catalog /Pages {paginas_id} 0 R >>".encode()
        objetos[paginas_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                                   f"/Count {len(kids)} >>").encode()

        salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        posiciones = []
        for numero, contenido in enumerate(objetos, start=1):
            posiciones.append(len(salida))
            salida += f"{numero} 0 obj\n".encode() + contenido + b"\nendobj\n"
        xref = len(salida)
        salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
        for pos in posiciones:
            salida += f"{pos:010d} 00000 n \n".encode()
        salida += (f"trailer\n<< /Size {len(objetos) + 1} /Root {catalogo} 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(salida)
