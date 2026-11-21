"""MiFicha v0.1 — almacenamiento de PDFs originales.

Con Supabase: bucket "originales". Sin Supabase: ./data/originales/ en disco.

    store_original(intake_id, pdf_bytes) -> str   # devuelve la key
    get_original_path(key) -> str | None         # ruta local descargable
"""

import logging
import os

from app import config

log = logging.getLogger("mificha.storage")

BUCKET = "originales"


def _local_dir() -> str:
    d = os.path.join(config.DATA_DIR, "originales")
    os.makedirs(d, exist_ok=True)
    return d


def _quarantine_dir() -> str:
    d = os.path.join(config.DATA_DIR, "quarantine")
    os.makedirs(d, exist_ok=True)
    return d


def quarantine_path(intake_id: str) -> str:
    return os.path.join(_quarantine_dir(), f"{intake_id}.pdf")


def store_original(intake_id: str, pdf_bytes: bytes) -> str:
    """Guarda el PDF original y devuelve su key."""
    key = f"{intake_id}.pdf"
    if config.USE_SUPABASE:
        try:
            from supabase import create_client  # type: ignore

            sb = create_client(config.SUPABASE_URL, config.supabase_data_key())
            sb.storage.from_(BUCKET).upload(
                key, pdf_bytes, {"content-type": "application/pdf", "upsert": "true"}
            )
            log.info("Original %s subido a Supabase bucket %s", key, BUCKET)
            return key
        except Exception as exc:  # noqa: BLE001 - degradar a disco local
            log.error(
                "Supabase Storage falló (%s): PDF guardado solo en disco "
                "EFÍMERO de Render (se pierde en cada despliegue).",
                exc,
            )
    path = os.path.join(_local_dir(), key)
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return key


def get_original_path(key: str) -> str | None:
    """Ruta local del original (descargándolo de Supabase si hace falta)."""
    if not key:
        return None
    if config.USE_SUPABASE:
        try:
            from supabase import create_client  # type: ignore

            sb = create_client(config.SUPABASE_URL, config.supabase_data_key())
            data = sb.storage.from_(BUCKET).download(key)
            path = _resolve_key(key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return path
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo descargar %s de Supabase: %s", key, exc)
    path = _resolve_key(key)
    return path if os.path.exists(path) else None


def _resolve_key(key: str) -> str:
    """Local path for an original_key.

    File-first keys look like ``members/<member>/documents/<id>/original.pdf``
    and live under DATA_DIR; legacy keys are bare filenames under
    DATA_DIR/originales/.
    """
    if key.startswith("members/"):
        return os.path.join(config.DATA_DIR, key)
    return os.path.join(_local_dir(), key)


def original_exists(key: str) -> bool:
    """¿Sigue disponible el PDF original? (sin descargarlo).

    Los documentos guardados antes de la mejora de almacenamiento (sep 2026)
    perdieron su original; para ellos la tarjeta muestra un aviso en vez de
    botones muertos.
    """
    if not key:
        return False
    if config.USE_SUPABASE:
        try:
            from supabase import create_client  # type: ignore

            sb = create_client(config.SUPABASE_URL, config.supabase_data_key())
            files = sb.storage.from_(BUCKET).list(options={"search": key})
            if any((f or {}).get("name") == key for f in (files or [])):
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo verificar %s en Supabase: %s", key, exc)
    return os.path.exists(_resolve_key(key))


def delete_original(key: str) -> None:
    """Borra el PDF original (Supabase Storage y/o disco local)."""
    if not key:
        return
    if config.USE_SUPABASE:
        try:
            from supabase import create_client  # type: ignore

            sb = create_client(config.SUPABASE_URL, config.supabase_data_key())
            sb.storage.from_(BUCKET).remove([key])
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo borrar %s de Supabase: %s", key, exc)
    path = _resolve_key(key)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
