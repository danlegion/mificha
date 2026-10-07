"""MiFicha — proveedores OCR.

Cadena: Tesseract (local, gratuito, privado) como primario y Google
Document AI como fallback administrado cuando el primario no extrae texto
útil — el rol que el diseño del sistema v1.5 asigna al OCR fallback.
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
        """Grados horarios para enderezar la imagen (0/90/180/270).

        Usa el modelo OSD de tesseract (`-l osd`; con spa+eng el OSD falla).
        Solo se aplica con confianza razonable: un falso positivo rotaría
        un documento que ya estaba bien.
        """
        try:
            proc = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "0", "-l", "osd"],
                input=image_bytes,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - sin OSD se sigue sin rotar
            log.warning("OSD de tesseract falló: %s", exc)
            return 0
        if proc.returncode != 0:
            log.warning("OSD de tesseract no disponible en este entorno")
            return 0
        salida = (proc.stdout or b"").decode("utf-8", "replace")
        rotar, confianza = 0, 0.0
        for linea in salida.splitlines():
            if "Rotate:" in linea:
                try:
                    rotar = int(linea.split("Rotate:")[1].strip().split()[0]) % 360
                except (ValueError, IndexError):
                    pass
            if "Orientation confidence:" in linea:
                try:
                    confianza = float(linea.split(":")[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
        if rotar and confianza >= 1.0:
            log.info("OSD: rotación de %d° (confianza %.1f)", rotar, confianza)
            return rotar
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


def _docai_libs():
    """Import perezoso de las libs de Google (no frenan el arranque)."""
    from google.cloud import documentai
    from google.oauth2 import service_account

    return documentai, service_account


class GoogleDocAIProvider(OCRProvider):
    """Fallback administrado: Google Document AI (processor Document OCR).

    Solo se invoca cuando el OCR primario no extrajo texto útil.
    Requiere (variables de entorno):
      DOCAI_PROCESSOR = projects/{p}/locations/{loc}/processors/{id}
      DOCAI_CREDENTIALS_JSON = service account JSON con el rol
        "Document AI API User".
    Precios: primeras 1.000 páginas/mes gratis; luego USD 1.50 por 1.000.
    """

    name = "documentai"

    def __init__(
        self,
        processor: str | None = None,
        credentials_json: str | None = None,
        timeout: int | None = None,
    ):
        self.processor = (processor or os.getenv("DOCAI_PROCESSOR", "")).strip()
        self.credentials_json = (
            credentials_json or os.getenv("DOCAI_CREDENTIALS_JSON", "")
        ).strip()
        self.timeout = timeout or int(os.getenv("DOCAI_TIMEOUT", "120"))
        if not self.processor or not self.credentials_json:
            raise ValueError(
                "DOCAI_PROCESSOR y DOCAI_CREDENTIALS_JSON son requeridos"
            )
        self._client = None

    @staticmethod
    def is_configured() -> bool:
        return bool(
            os.getenv("DOCAI_PROCESSOR", "").strip()
            and os.getenv("DOCAI_CREDENTIALS_JSON", "").strip()
        )

    def _cliente(self):
        if self._client is None:
            import json

            documentai, service_account = _docai_libs()
            info = json.loads(self.credentials_json)
            creds = service_account.Credentials.from_service_account_info(info)
            # projects/{p}/locations/{loc}/processors/{id} -> endpoint regional
            location = self.processor.split("/")[3]
            self._client = documentai.DocumentProcessorServiceClient(
                credentials=creds,
                client_options={
                    "api_endpoint": f"{location}-documentai.googleapis.com"
                },
            )
        return self._client

    def extract_text(self, image_bytes: bytes) -> str:
        documentai, _ = _docai_libs()
        client = self._cliente()
        raw = documentai.RawDocument(content=image_bytes, mime_type="image/png")
        req = documentai.ProcessRequest(name=self.processor, raw_document=raw)
        result = client.process_document(request=req, timeout=self.timeout)
        return (result.document.text or "").strip()


def get_ocr_provider() -> OCRProvider:
    """Fábrica del OCR primario: lee OCR_PROVIDER del entorno (default "null")."""
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


def get_ocr_fallback_provider() -> OCRProvider | None:
    """Fallback administrado (Google Document AI). None si no está configurado.

    Nunca tumba el flujo: un fallback mal configurado solo se registra.
    """
    if not GoogleDocAIProvider.is_configured():
        return None
    try:
        return GoogleDocAIProvider()
    except Exception as exc:  # noqa: BLE001 - el fallback es opcional
        log.warning("Fallback OCR no disponible: %s", exc)
        return None
