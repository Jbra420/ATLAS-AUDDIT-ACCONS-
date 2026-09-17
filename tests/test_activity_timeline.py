"""Pruebas de la cronologia de actividad del expediente."""
from __future__ import annotations

import unittest

from services.activity_timeline import build_activity_timeline


class TestActivityTimeline(unittest.TestCase):
    def setUp(self):
        self.audit = {
            "company_name": "Empresa Ejemplo S.A.",
            "auditor_name": "Ana Auditora",
            "period": "2026",
            "created_at": "2026-09-17 14:30:00",
        }
        self.research = {
            "commercial_name": "Empresa Ejemplo",
            "economic_activity": "Servicios",
            "generated_summary": "Resumen preliminar",
            "updated_at": "2026-09-17 16:40:00",
        }
        self.profile = {
            "razon_social": "Empresa Ejemplo S.A.",
            "estado_contribuyente": "ACTIVO",
            "updated_at": "2026-09-17 15:00:00",
        }
        self.location = {"ciudad": "Cuenca", "updated_at": "2026-09-17 15:00:00"}
        self.documents = [
            {
                "nombre": "Estado de resultados",
                "estado": "revisado",
                "revisado_at": "2026-09-17 16:00:00",
            },
            {"nombre": "RUC", "estado": "pendiente", "revisado_at": None},
        ]
        self.snapshot = {"activo_total": 1000, "updated_at": "2026-09-17 15:30:00"}
        self.checks = [
            {
                "fuente": "SRI - Consulta RUC",
                "estado": "consultada",
                "observacion": "Contribuyente activo",
                "consultada_at": "2026-09-17 15:15:00",
            }
        ]
        self.sources = [
            {
                "title": "Consulta SERCOP",
                "source_type": "SERCOP",
                "created_at": "2026-09-17 16:20:00",
            }
        ]

    def _timeline(self):
        return build_activity_timeline(
            self.audit,
            self.research,
            self.profile,
            self.location,
            self.documents,
            self.snapshot,
            self.checks,
            self.sources,
        )

    def test_builds_events_in_reverse_chronological_order(self):
        timeline = self._timeline()
        timestamps = [event["timestamp"] for event in timeline["events"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        self.assertEqual(timeline["events"][0]["category"], "resumen")

    def test_includes_only_reviewed_documents(self):
        timeline = self._timeline()
        document_events = [event for event in timeline["events"] if event["category"] == "documentos"]
        self.assertEqual(len(document_events), 1)
        self.assertIn("Estado de resultados", document_events[0]["title"])

    def test_counts_source_activity(self):
        timeline = self._timeline()
        self.assertEqual(timeline["counts"]["fuentes"], 2)
        self.assertGreaterEqual(timeline["total"], 7)

    def test_empty_research_does_not_create_false_event(self):
        timeline = build_activity_timeline(
            self.audit,
            {"updated_at": "2026-09-17 16:40:00"},
            None,
            None,
            [],
            None,
            [],
            [],
        )
        self.assertEqual(timeline["total"], 1)
        self.assertEqual(timeline["events"][0]["category"], "asignacion")


if __name__ == "__main__":
    unittest.main()
