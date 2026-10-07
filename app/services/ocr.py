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
    """OCR local con el binario `tesseract` (paquetes spa+eng).

    Detecta la orientación con OSD (--psm 0) y endereza la imagen antes
    de leerla: los escaneos de teléfono suelen venir rotados 90°.
    """

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

    def _detectar_rotacion(self, image_bytes: bytes) -> int:
        """Grados horarios para enderezar la imagen (0/90/180/270)."""
        try:
            proc = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "0",
                 "-l", self.langs],
                input=image_bytes,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - sin OSD se sigue sin rotar
            log.warning("OSD de tesseract falló: %s", exc)
            return 0
        salida = (proc.stdout or b"").decode("utf-8", "replace")
        for linea in salida.splitlines():
            if "Rotate:" in linea:
                try:
                    return int(linea.split("Rotate:")[1].strip().split()[0]) % 360
                except (ValueError, IndexError):
                    return 0
        return 0

    @staticmethod
    def _rotar_horario(image_bytes: bytes, grados: int) -> bytes:
        """Rota la imagen N grados en sentido horario (Pillow)."""
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        # Pillow rota en sentido antihorario: negativo = horario.
        img = img.rotate(-grados, expand=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _ocr(self, image_bytes: bytes) -> str:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout",
             "-l", self.langs, "--psm", str(self.psm)],
            input=image_bytes,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            detalle = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            raise RuntimeError(f"tesseract falló (código {proc.returncode}): {detalle}")
        return (proc.stdout or b"").decode("utf-8", "replace").strip()

    def extract_text(self, image_bytes: bytes) -> str:
        if not self.is_available():
            raise RuntimeError(
                "tesseract no está instalado en este entorno"
            )
        rotacion = self._detectar_rotacion(image_bytes)
        if rotacion:
            log.info("OSD detectó rotación de %d°: enderezando imagen", rotacion)
            try:
                image_bytes = self._rotar_horario(image_bytes, rotacion)
            except Exception as exc:  # noqa: BLE001 - se intenta sin rotar
                log.warning("No se pudo rotar la imagen: %s", exc)
        try:
            return self._ocr(image_bytes)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"tesseract tardó más de {self.timeout}s") from exc


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
