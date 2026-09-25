"""MiFicha v0.1 — proveedores OCR (stub).

El OCR real requiere un proveedor externo (p. ej. un servicio de visión).
Mientras no haya uno configurado, `NullOCRProvider` deja claro que falta
configuración y el pipeline marca las páginas escaneadas como inciertas en
lugar de saltárselas en silencio.
"""

import os


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


def get_ocr_provider() -> OCRProvider:
    """Fábrica: lee OCR_PROVIDER del entorno (default "null")."""
    provider = os.getenv("OCR_PROVIDER", "null").strip().lower()
    if provider in ("", "null", "none", "off"):
        return NullOCRProvider()
    # Futuros proveedores (p. ej. "vision", "aws-textract") se conectan aquí.
    raise ValueError(f"Proveedor OCR desconocido: {provider!r}")
