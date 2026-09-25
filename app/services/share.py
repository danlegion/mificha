"""MiFicha v0.1 — enlaces compartidos (snapshot mínimo y revocable).

El snapshot solo guarda los campos permitidos al crear el enlace:
resumen simple y/o urgencia. Nunca el PDF, nunca otros documentos, nunca la
identidad del dueño (el enlace dice "un familiar").
"""

import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

from app import config

_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def new_token() -> str:
    return secrets.token_urlsafe(24)


def build_snapshot(
    exp: dict, incluir_resumen: bool, incluir_urgencia: bool
) -> dict:
    """Snapshot con SOLO los campos permitidos."""
    snap = {
        "incluir_resumen": bool(incluir_resumen),
        "incluir_urgencia": bool(incluir_urgencia),
    }
    if incluir_resumen:
        snap["summary_simple"] = exp.get("summary_simple", "")
    if incluir_urgencia:
        urg = exp.get("urgency") or {}
        snap["urgency_level"] = urg.get("level", "no_urgente")
        snap["urgency_label"] = urg.get("label", "")
        snap["urgency_action"] = urg.get("action", "")
    return snap


def expiry_datetime() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=config.SHARE_TTL_DAYS)


def format_expires_label(dt: datetime) -> str:
    """'el 2 de octubre de 2026'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"el {dt.day} de {_MESES[dt.month - 1]} de {dt.year}"


def share_url(token: str) -> str:
    return f"{config.BASE_URL}/s/{token}"


def whatsapp_text(token: str, incluir_urgencia: bool) -> str:
    url = share_url(token)
    if incluir_urgencia:
        msg = f"Te comparto el resumen de mi examen médico: {url}"
    else:
        msg = f"Te comparto información de mi examen médico: {url}"
    return urllib.parse.quote(msg)
