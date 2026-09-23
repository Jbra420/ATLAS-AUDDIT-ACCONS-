"""
tests/test_certificados.py — Certificados de nómina de Supercias adjuntos.

Cubre el analizador de texto, la extracción real con pypdf sobre un PDF
generado en el test, el parser multipart del servidor, el guardado como
evidencia y el panel de revisión.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.server import parse_multipart
from database import (
    authenticate,
    close_certificate_import,
    connect,
    create_company_audit,
    get_audit_context,
    get_certificate_import,
    init_db,
    save_certificate,
)
from services.certificados import analizar_nomina, extraer_texto, fecha_del_certificado
from views.auditor.radar.certificado import assisted_panel, review_panel

RUC_EMPRESA = "0190444619001"

NOMINA = f"""SUPERINTENDENCIA DE COMPAÑÍAS, VALORES Y SEGUROS
CERTIFICADO DE NÓMINA DE ADMINISTRADORES Y ACCIONISTAS
Fecha de emisión: 15/09/2026   RUC: {RUC_EMPRESA}
ADMINISTRADORES ACTUALES
IDENTIFICACIÓN NOMBRE NACIONALIDAD CARGO FECHA NOMBRAMIENTO PERIODO
0102030405 ORDOÑEZ FAJARDO JULIO ECUADOR GERENTE GENERAL 12/03/2024 5
FERNANDO
0912345678 PÉREZ LÓPEZ ANA MARÍA COLOMBIA PRESIDENTE 12/03/2024 5
NÓMINA DE ACCIONISTAS / SOCIOS
No. IDENTIFICACIÓN NOMBRE NACIONALIDAD TIPO INVERSIÓN CAPITAL
1 0102030405 ORDOÑEZ FAJARDO JULIO FERNANDO ECUADOR NACIONAL 600,00
2 1790012345001 INVERSIONES ANDINAS S.A. ECUADOR NACIONAL 400,00
TOTAL 1.000,00
Página 1 de 1
"""


def _pdf_con_texto(lineas: list[str]) -> bytes:
    """PDF mínimo de una página con una línea de texto por elemento (Helvetica)."""
    def escapar(texto: str) -> str:
        return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    contenido = "BT /F1 10 Tf 40 800 Td 14 TL " + " ".join(f"({escapar(l)}) '" for l in lineas) + " ET"
    stream = contenido.encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objetos, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return pdf


class TestAnalizador(unittest.TestCase):
    def test_un_certificado_llena_ambas_nominas(self):
        resultado = analizar_nomina(NOMINA, RUC_EMPRESA)
        self.assertEqual(resultado["advertencias"], [])
        self.assertEqual(resultado["fecha_certificado"], "2026-09-15")
        self.assertEqual(
            [(f["identificacion"], f["nombre"], f["cargo"], f["nacionalidad"]) for f in resultado["administradores"]],
            [
                ("0102030405", "ORDOÑEZ FAJARDO JULIO FERNANDO", "GERENTE GENERAL", "ECUADOR"),
                ("0912345678", "PÉREZ LÓPEZ ANA MARÍA", "PRESIDENTE", "COLOMBIA"),
            ],
        )
        self.assertEqual(
            [(f["identificacion"], f["tipo_identificacion"], f["capital"], f["participacion_porcentaje"])
             for f in resultado["accionistas"]],
            [("0102030405", "cedula", 600.0, 60.0), ("1790012345001", "ruc", 400.0, 40.0)],
        )
        self.assertEqual(resultado["accionistas"][1]["nombre"], "INVERSIONES ANDINAS S A")

    def test_sin_titulos_clasifica_por_el_contenido_de_la_fila(self):
        texto = "0102030405 TORRES VEGA ANA GERENTE GENERAL\n0912345678 VEGA RUIZ LUIS 500,00"
        resultado = analizar_nomina(texto)
        self.assertEqual([f["nombre"] for f in resultado["administradores"]], ["TORRES VEGA ANA"])
        self.assertEqual([f["nombre"] for f in resultado["accionistas"]], ["VEGA RUIZ LUIS"])

    def test_certificado_de_una_sola_nomina_avisa_la_otra(self):
        texto = "ADMINISTRADORES\n0102030405 TORRES VEGA ANA ECUADOR GERENTE GENERAL"
        resultado = analizar_nomina(texto)
        self.assertEqual(len(resultado["administradores"]), 1)
        self.assertEqual(resultado["accionistas"], [])
        self.assertTrue(any("accionistas" in a for a in resultado["advertencias"]))

    def test_porcentaje_explicito_prevalece(self):
        texto = "ACCIONISTAS\n0102030405 TORRES VEGA ANA 1.500,00 75%\n0912345678 VEGA RUIZ LUIS 500,00 25%"
        accionistas = analizar_nomina(texto)["accionistas"]
        self.assertEqual([f["participacion_porcentaje"] for f in accionistas], [75.0, 25.0])
        self.assertEqual(accionistas[0]["capital"], 1500.0)

    def test_ruc_de_la_empresa_no_es_una_fila(self):
        resultado = analizar_nomina(NOMINA, RUC_EMPRESA)
        ids = [f["identificacion"] for n in ("administradores", "accionistas") for f in resultado[n]]
        self.assertNotIn(RUC_EMPRESA, ids)

    def test_advierte_si_el_certificado_es_de_otra_empresa(self):
        advertencias = analizar_nomina(NOMINA, "1790000000001")["advertencias"]
        self.assertTrue(any("no menciona el RUC" in a for a in advertencias))

    def test_pdf_sin_texto_o_sin_filas(self):
        self.assertIn("escaneado", analizar_nomina("")["advertencias"][0])
        self.assertEqual(len(analizar_nomina("Sin tabla")["advertencias"]), 2)

    def test_fechas(self):
        self.assertEqual(fecha_del_certificado("Emitido el 2026-01-05"), "2026-01-05")
        self.assertEqual(fecha_del_certificado("Quito, 3 de marzo de 2025"), "2025-03-03")
        self.assertEqual(fecha_del_certificado("Vence 01/01/2999"), "", "Una fecha futura no es la de emisión")


class TestExtraerTexto(unittest.TestCase):
    def test_lee_el_texto_de_un_pdf(self):
        pdf = _pdf_con_texto([
            "ADMINISTRADORES", "0102030405 TORRES VEGA ANA ECUADOR GERENTE GENERAL",
            "ACCIONISTAS", "0102030405 TORRES VEGA ANA ECUADOR 800,00",
        ])
        resultado = analizar_nomina(extraer_texto(pdf))
        self.assertEqual(resultado["administradores"][0]["cargo"], "GERENTE GENERAL")
        self.assertEqual(resultado["accionistas"][0]["participacion_porcentaje"], 100.0)

    def test_rechaza_lo_que_no_es_pdf(self):
        with self.assertRaisesRegex(ValueError, "no es un PDF"):
            extraer_texto(b"hola")
        with self.assertRaisesRegex(ValueError, "dañado"):
            extraer_texto(b"%PDF-1.4 basura")


class TestParseMultipart(unittest.TestCase):
    def test_campos_y_archivo(self):
        limite = "----atlas"
        cuerpo = (
            f"--{limite}\r\nContent-Disposition: form-data; name=\"tipo\"\r\n\r\naccionistas\r\n"
            f"--{limite}\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"Nómina.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + b"%PDF-1.4 \x00\xff binario\r\n" + f"--{limite}--\r\n".encode()
        form = parse_multipart(f"multipart/form-data; boundary={limite}", cuerpo)
        self.assertEqual(form["tipo"], ["accionistas"])
        self.assertEqual(form.files["archivo"], ("Nómina.pdf", b"%PDF-1.4 \x00\xff binario"))

    def test_archivo_vacio_se_ignora(self):
        limite = "x"
        cuerpo = (f"--{limite}\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"\"\r\n\r\n\r\n"
                  f"--{limite}--\r\n").encode()
        self.assertEqual(parse_multipart(f"multipart/form-data; boundary={limite}", cuerpo).files, {})


class TestGuardarCertificado(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.db, self.adjuntos = tmp / "test.db", tmp / "adjuntos"
        init_db(self.db)
        self.auditor = authenticate("auditor", "auditor123", self.db)
        admin = authenticate("admin", "admin123", self.db)
        self.audit_id = create_company_audit(
            "COBBLERCOMPANY CIA. LTDA.", RUC_EMPRESA, "Cuenca", "", "2026",
            self.auditor["id"], admin["id"], self.db,
        )
        self.pdf = _pdf_con_texto(["certificado"])
        self.analisis = analizar_nomina(NOMINA, RUC_EMPRESA)

    def _guardar(self, pdf: bytes | None = None) -> int:
        return save_certificate(
            self.audit_id, "nomina.pdf", pdf or self.pdf, self.analisis,
            self.auditor["id"], self.db, self.adjuntos,
        )

    def _evidencias(self) -> list:
        with connect(self.db) as conn:
            return list(conn.execute("SELECT * FROM sources WHERE audit_id = ?", (self.audit_id,)))

    def test_guarda_el_pdf_como_evidencia_y_deja_la_propuesta_pendiente(self):
        import_id = self._guardar()
        propuesta = get_certificate_import(self.audit_id, import_id, self.db)
        self.assertEqual(propuesta["estado"], "pendiente")
        self.assertEqual((len(propuesta["administradores"]), len(propuesta["accionistas"])), (2, 2))
        self.assertEqual(propuesta["fecha_certificado"], "2026-09-15")
        self.assertEqual((self.adjuntos / propuesta["ruta"]).read_bytes(), self.pdf)
        evidencia = self._evidencias()
        self.assertEqual(len(evidencia), 1)
        self.assertIn("administradores y accionistas", evidencia[0]["title"])
        self.assertIn(propuesta["sha256"], evidencia[0]["notes"])
        self.assertEqual(get_audit_context(self.audit_id, self.db)["certificado"]["id"], import_id)

    def test_el_mismo_archivo_no_duplica_la_evidencia_y_reemplaza_la_propuesta(self):
        primero = self._guardar()
        segundo = self._guardar()
        self.assertEqual(len(self._evidencias()), 1)
        self.assertEqual(get_certificate_import(self.audit_id, primero, self.db)["estado"], "descartado")
        self.assertEqual(get_certificate_import(self.audit_id, segundo, self.db)["estado"], "pendiente")

    def test_cerrar_la_propuesta(self):
        import_id = self._guardar()
        close_certificate_import(self.audit_id, import_id, "importado", self.db)
        self.assertIsNone(get_audit_context(self.audit_id, self.db)["certificado"])
        with self.assertRaises(ValueError):
            close_certificate_import(self.audit_id, import_id, "descartado", self.db)

    def test_panel_de_revision(self):
        propuesta = get_certificate_import(self.audit_id, self._guardar(), self.db)
        html = review_panel(self.audit_id, propuesta, "accionistas", "tok")
        self.assertIn('action="/auditor/radar/certificado/importar"', html)
        self.assertIn('formaction="/auditor/radar/certificado/descartar"', html)
        self.assertIn('name="administradores_cargo_0" value="GERENTE GENERAL"', html)
        self.assertIn('name="accionistas_nombre_0" value="ORDOÑEZ FAJARDO JULIO FERNANDO"', html)
        self.assertIn('name="accionistas_participacion_porcentaje_1" value="40.0"', html)
        self.assertEqual(html.count('name="incluir_administradores"'), 2)
        self.assertIn('value="2026-09-15"', html, "La fecha de consulta parte de la del certificado")

    def test_flujo_asistido_ofrece_portal_y_adjunto(self):
        audit = {"ruc": RUC_EMPRESA, "company_name": "COBBLERCOMPANY CIA. LTDA."}
        html = assisted_panel(self.audit_id, audit, [], "admins", "tok")
        self.assertIn("https://www.supercias.gob.ec/portalscvs/index.htm", html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertNotIn('name="tipo"', html, "Un solo adjunto sirve para las dos nóminas")
        self.assertIn("Certificado aún no registrado", html)


if __name__ == "__main__":
    unittest.main()
