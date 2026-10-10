"""Registro de auditoría: quién accedió a qué documento y cuándo.

Los eventos se guardan con `dal.log_audit(...)` y nunca deben romper la
petición: si el registro falla, se advierte en el log y se sigue.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import Request

from app import auth
from app.db import dal
from app.routers import guest_token_valid

log = logging.getLogger("mificha.audit")


def client_ip(request: Request) -> str:
    """IP real del cliente (Render pone el proxy delante)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "?"


def actor_de(request: Request, intake: dict | None = None) -> str:
    """'user:<id>', 'guest', 'share' o 'anon'."""
    user = auth.get_current_user(request)
    if user:
        return f"user:{user['id']}"
    token = request.query_params.get("t")
    if intake is not None and token and guest_token_valid(intake, token):
        return "guest"
    return "anon"


def log_event(action: str, request: Request, *, intake_id=None, doc_id=None,
              actor: str | None = None, intake: dict | None = None,
              detail: dict | None = None) -> None:
    """Registra un evento de auditoría; nunca lanza excepciones."""
    try:
        dal.log_audit({
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor if actor is not None else actor_de(request, intake),
            "action": action,
            "intake_id": intake_id,
            "doc_id": doc_id,
            "ip": client_ip(request),
            "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
        })
    except Exception as exc:  # noqa: BLE001 - la auditoría no rompe nada
        log.warning("No se pudo registrar evento de auditoría %s: %s",
                    action, exc)
