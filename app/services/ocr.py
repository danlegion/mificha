"""MiFicha — proveedores OCR.

Tesseract (local, gratuito, privado) es el proveedor real: lee páginas
escaneadas/fotos sin costo por página y sin enviar el documento a terceros.
`NullOCRProvider` queda para cuando no hay OCR configurado.
"""

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


class OCRProvider:
    """Interfaz base de OCR."""

    name = "base"

    def extract_text(self, image_bytes: bytes) -> str:
        raise NotImplementedError


class NullOCRProvider(OCRProvider):
    """Sin proveedor configurado: falla de forma explícita."""

    name = "null"

    def extract_text(self, image_bytes: bytes) -> str:
        raise NotImplementedError(
            "OCR vendor not configured — set OCR_PROVIDER"
        )


class TesseractOCRProvider(OCRProvider):
    """OCR local con el binario `tesseract` (paquetes spa+eng)."""

    name = "tesseract"

    def __init__(
        self,
        langs: str | None = None,
        psm: int | None = None,
        timeout: int | None = None,
    ):
        self.langs = langs or os.getenv("OCR_LANGS", "spa+eng")
        self.psm = psm or int(os.getenv("OCR_PSM", "6"))
        self.timeout = timeout or int(os.getenv("OCR_TIMEOUT", "90"))

    @staticmethod
    def is_available() -> bool:
        return shutil.which("tesseract") is not None

    def extract_text(self, image_bytes: bytes) -> str:
        if not self.is_available():
            raise RuntimeError(
                "tesseract no está instalado en este entorno"
            )
        try:
            proc = subprocess.run(
                ["tesseract", "stdin", "stdout",
                 "-l", self.langs, "--psm", str(self.psm)],
                input=image_bytes,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"tesseract tardó más de {self.timeout}s") from exc
        if proc.returncode != 0:
            detalle = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            raise RuntimeError(f"tesseract falló (código {proc.returncode}): {detalle}")
        return (proc.stdout or b"").decode("utf-8", "replace").strip()


def get_ocr_provider() -> OCRProvider:
    """Fábrica: lee OCR_PROVIDER del entorno (default "null")."""
    provider = os.getenv("OCR_PROVIDER", "null").strip().lower()
    if provider in ("", "null", "none", "off"):
        return NullOCRProvider()
    if provider == "tesseract":
        if not TesseractOCRProvider.is_available():
            raise RuntimeError(
                "OCR_PROVIDER=tesseract pero el binario tesseract no está "
                "instalado en este entorno"
            )
        return TesseractOCRProvider()
    raise ValueError(f"Proveedor OCR desconocido: {provider!r}")
