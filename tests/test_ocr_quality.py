"""Garbled-OCR gate: clean text is saved, garbled text goes to fallback.

The gate is character-level only: it catches symbol soup and broken
tokens, not plausible-but-wrong substitutions. It is conservative by
design — numbers, units, abbreviations, and dates are all clean.
"""

import pytest

from app.services import pipeline
from app.services.ocr_quality import garble_ratio, is_garbled
from app.services.pipeline import process_intake

GARBLED = ("H3m0g10b1na 13,5 9/dL @#% |l| qzxwv bnmklp tprsnm "
            "vfrd $%& _-_ 0l1O ||| xyzq")
GARBLED2 = ("G|uc0sa 9S mg/dL 7O - 1OO $%& |l| |l| xqzv bnmklp "
            "qwrty zxcvbnm")


def test_clean_lab_and_narrative_not_garbled():
    assert not is_garbled(
        "HEMOGLOBINA 13.5 g/dL 12.0 - 16.0 HDL 45 LDL 120 TSH 2.1")
    assert not is_garbled(
        "El paciente presenta dolor de cabeza desde hace tres días. "
        "Se solicita control en dos semanas.")
    assert not is_garbled(
        "The patient reports fatigue and headache for three days. "
        "Follow up in two weeks as directed.")
    assert not is_garbled(
        "Influenza 2024-10-15 Lote ABC123 COVID-19 2023-05-02 Dosis 2")


def test_garbled_symbol_soup_flagged():
    assert is_garbled(GARBLED)
    assert garble_ratio(GARBLED) >= 0.5
    assert is_garbled(GARBLED2)


def test_short_text_never_flagged():
    # Too little evidence to judge: short headers and lab lines pass.
    assert not is_garbled("GLUCOSA 95")
    assert not is_garbled("H3m0g10b1na 13,5")


def test_abbreviations_and_units_are_clean():
    assert not is_garbled(
        "HDL 45 mg/dL LDL 120 mg/dL TSH 2.1 mIU/L CBC WBC 5.4 RBC 4.8")


# ---------- Pipeline integration ----------

class _ProveedorFalso:
    name = "tesseract"

    def __init__(self, texto):
        self._texto = texto
        self.llamadas = 0

    def extract_text(self, image_bytes: bytes) -> str:
        self.llamadas += 1
        return self._texto


def _extracted_escaneado(_pdf_bytes):
    return {
        "pages": [{"page_number": 1, "text": "", "has_text": False,
                   "is_scanned": True}],
        "full_text": "",
        "scanned_pages": [1],
        "num_pages": 1,
    }


def _pdf_minimo() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def test_garbled_primary_uses_fallback(monkeypatch):
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)
    primario = _ProveedorFalso(GARBLED)
    fb = _ProveedorFalso("GLUCOSA  95  mg/dL  70 - 100")
    fb.name = "documentai"
    monkeypatch.setattr(pipeline, "get_ocr_provider", lambda: primario)
    monkeypatch.setattr(pipeline, "get_ocr_fallback_provider", lambda: fb)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exp = process_intake("abc", _pdf_minimo(), None, lang="es")
    assert primario.llamadas == 1
    assert fb.llamadas == 1
    assert any("lucosa" in b["name"].lower() for b in exp["biomarkers"])


def test_garbled_primary_without_fallback_not_saved(monkeypatch):
    """No fallback configured: garbled OCR is not saved as document text."""
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)
    monkeypatch.setattr(pipeline, "get_ocr_provider",
                        lambda: _ProveedorFalso(GARBLED))
    monkeypatch.delenv("DOCAI_PROCESSOR", raising=False)
    monkeypatch.delenv("DOCAI_CREDENTIALS_JSON", raising=False)
    with pytest.raises(pipeline.UnprocessableDocument) as exc:
        process_intake("abc", _pdf_minimo(), None, lang="es")
    assert "escaneo" in str(exc.value).lower()


def test_garbled_fallback_also_not_saved(monkeypatch):
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)
    monkeypatch.setattr(pipeline, "get_ocr_provider",
                        lambda: _ProveedorFalso(GARBLED))
    fb = _ProveedorFalso(GARBLED2)
    fb.name = "documentai"
    monkeypatch.setattr(pipeline, "get_ocr_fallback_provider", lambda: fb)
    with pytest.raises(pipeline.UnprocessableDocument):
        process_intake("abc", _pdf_minimo(), None, lang="es")
    assert fb.llamadas == 1
