"""Pruebas del pipeline: rechazo de escaneos y aviso de OCR en desarrollo."""

import pytest

from app.services import pipeline
from app.services.pipeline import UnprocessableDocument, process_intake


def _extracted_scanned(monkeypatch):
    def falso(_pdf_bytes):
        return {
            "pages": [{"page_number": 1, "text": "", "has_text": False,
                       "is_scanned": True}],
            "full_text": "",
            "scanned_pages": [1],
            "num_pages": 1,
        }
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document", falso)


def _extracted_vacio(monkeypatch):
    def falso(_pdf_bytes):
        return {"pages": [], "full_text": "", "scanned_pages": [],
                "num_pages": 0}
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document", falso)


def test_rechazo_escaneo_menciona_deteccion_y_desarrollo(monkeypatch):
    _extracted_scanned(monkeypatch)
    with pytest.raises(UnprocessableDocument) as exc:
        process_intake("abc", b"%PDF-falso", None)
    msg = str(exc.value).lower()
    assert "foto o un escaneo" in msg
    assert "trabajando en agregarlo" in msg


def test_rechazo_sin_paginas_no_habla_de_escaneo(monkeypatch):
    _extracted_vacio(monkeypatch)
    with pytest.raises(UnprocessableDocument) as exc:
        process_intake("abc", b"%PDF-falso", None)
    assert "escaneo" not in str(exc.value).lower()


def test_paginas_escaneadas_parciales_avisan_en_desarrollo(monkeypatch):
    from app.services import llm as llm_mod

    def falso_extract(_pdf_bytes):
        return {
            "pages": [
                {"page_number": 1, "text": "Hemoglobina 14.0 g/dL (13.5 - 17.5)",
                 "has_text": True, "is_scanned": False},
                {"page_number": 2, "text": "", "has_text": False,
                 "is_scanned": True},
            ],
            "full_text": "Hemoglobina 14.0 g/dL (13.5 - 17.5)",
            "scanned_pages": [2],
            "num_pages": 2,
        }
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document", falso_extract)
    # Forzar el DemoExplainer (sin API keys) para no tocar la red.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exp = process_intake("abc", b"%PDF-falso", None)
    razones = [f.get("reason", "") for f in exp.get("uncertain_fields", [])]
    assert any("página 2" in r and "trabajando" in r.lower() for r in razones), razones
