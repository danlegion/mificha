"""Pruebas del OCR: proveedor Tesseract e integración en el pipeline."""

import shutil
import subprocess

import pytest

from app.services import pipeline
from app.services.ocr import (
    NullOCRProvider,
    TesseractOCRProvider,
    get_ocr_provider,
)
from app.services.pipeline import process_intake


# ---------- Fábrica ----------

def test_fabrica_default_es_null(monkeypatch):
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    assert isinstance(get_ocr_provider(), NullOCRProvider)


def test_fabrica_desconocido_falla(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "vision")
    with pytest.raises(ValueError):
        get_ocr_provider()


def test_fabrica_tesseract_sin_binario_falla_claro(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    with pytest.raises(RuntimeError) as exc:
        get_ocr_provider()
    assert "tesseract" in str(exc.value)


# ---------- TesseractOCRProvider (sin binario: subprocess simulado) ----------

def _simular_tesseract(monkeypatch, texto=b"GLUCOSA 95", codigo=0):
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tesseract")

    def falso_run(cmd, **kw):
        assert cmd[:3] == ["tesseract", "stdin", "stdout"]
        assert "-l" in cmd and "--psm" in cmd
        assert kw["input"]  # recibe los bytes de la imagen
        return subprocess.CompletedProcess(cmd, codigo, stdout=texto, stderr=b"")

    monkeypatch.setattr(subprocess, "run", falso_run)


def test_tesseract_devuelve_texto_limpio(monkeypatch):
    _simular_tesseract(monkeypatch, texto=b"  GLUCOSA 95 mg/dL \n")
    p = TesseractOCRProvider()
    assert p.extract_text(b"<png>") == "GLUCOSA 95 mg/dL"


def test_tesseract_falla_con_codigo(monkeypatch):
    _simular_tesseract(monkeypatch, codigo=1)
    p = TesseractOCRProvider()
    with pytest.raises(RuntimeError):
        p.extract_text(b"<png>")


def test_tesseract_sin_binario_falla_claro(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    with pytest.raises(RuntimeError) as exc:
        TesseractOCRProvider().extract_text(b"<png>")
    assert "no está instalado" in str(exc.value)


def test_tesseract_timeout(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tesseract")

    def falso_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 90)

    monkeypatch.setattr(subprocess, "run", falso_run)
    with pytest.raises(RuntimeError) as exc:
        TesseractOCRProvider(timeout=90).extract_text(b"<png>")
    assert "90s" in str(exc.value)


# ---------- Integración en el pipeline (proveedor falso) ----------

class _ProveedorFalso:
    name = "tesseract"

    def __init__(self, texto):
        self._texto = texto
        self.llamadas = 0

    def extract_text(self, image_bytes: bytes) -> str:
        assert image_bytes  # el pipeline renderiza la página a imagen
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


def test_ocr_inyecta_texto_y_genera_explicacion(monkeypatch):
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)
    falso = _ProveedorFalso("GLUCOSA  95  mg/dL  70 - 100")
    monkeypatch.setattr(pipeline, "get_ocr_provider", lambda: falso)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exp = process_intake("abc", _pdf_minimo(), None, lang="es")

    assert falso.llamadas == 1
    nombres = [b["name"] for b in exp["biomarkers"]]
    assert any("lucosa" in n.lower() for n in nombres), nombres
    razones = [f.get("reason", "") for f in exp.get("uncertain_fields", [])]
    assert any("OCR" in r for r in razones), razones


def test_ocr_sin_texto_util_sigue_rechazando(monkeypatch):
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)
    falso = _ProveedorFalso("   ")  # OCR no leyó nada útil
    monkeypatch.setattr(pipeline, "get_ocr_provider", lambda: falso)
    with pytest.raises(pipeline.UnprocessableDocument):
        process_intake("abc", _pdf_minimo(), None, lang="es")


def test_ocr_mal_configurado_no_tumba_el_flujo(monkeypatch):
    monkeypatch.setattr(pipeline.pdf_extract, "extract_document",
                        _extracted_escaneado)

    def roto():
        raise RuntimeError("OCR_PROVIDER=tesseract pero no hay binario")

    monkeypatch.setattr(pipeline, "get_ocr_provider", roto)
    # Sin texto y sin OCR: se rechaza con el mensaje de escaneo, no con un 500.
    with pytest.raises(pipeline.UnprocessableDocument) as exc:
        process_intake("abc", _pdf_minimo(), None, lang="es")
    assert "escaneo" in str(exc.value).lower()


# ---------- OCR real (solo si el binario está instalado) ----------

tesseract_hay = shutil.which("tesseract") is not None


@pytest.mark.skipif(not tesseract_hay, reason="tesseract no instalado")
def test_ocr_real_lee_imagen():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=200)
    page.insert_text((72, 100), "GLUCOSA 95 mg/dL", fontsize=28)
    pix = page.get_pixmap(dpi=200)
    texto = TesseractOCRProvider(langs="spa+eng").extract_text(pix.tobytes("png"))
    assert "GLUCOSA" in texto.upper()
    assert "95" in texto
