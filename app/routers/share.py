"""MiFicha v0.1 — compartir: crear/revocar enlaces y vista pública /s/{token}."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import (can_view_intake, guest_token_valid, templates,
                         urgency_css_key)
from app.services import share as share_svc

log = logging.getLogger("mificha.share")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)


def _resolver_acceso(intake_id: str, request: Request, token: str | None = None):
    token = token if token is not None else request.query_params.get("t")
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None
    return intake, user, raw


@router.post("/compartir/{intake_id}")
async def crear_share(
    intake_id: str,
    request: Request,
    incluir_resumen: str = Form(default=""),
    incluir_urgencia: str = Form(default=""),
    t: str = Form(default=""),
):
    intake, _, raw = _resolver_acceso(intake_id, request, t or None)
    if intake["status"] != "ready" or not intake.get("explanation"):
        raise HTTPException(status_code=409, detail="El documento aún no está listo")

    con_resumen = incluir_resumen in ("on", "1", "true")
    con_urgencia = incluir_urgencia in ("on", "1", "true")
    if not con_resumen and not con_urgencia:
        con_resumen = True  # por defecto, al menos el resumen

    # Un solo enlace activo por intake: revoca el anterior al crear uno nuevo.
    dal.revoke_shares_for_intake(intake_id)
    token = share_svc.new_token()
    dal.create_share(
        {
            "intake_id": intake_id,
            "token": token,
            "snapshot": share_svc.build_snapshot(
                intake["explanation"], con_resumen, con_urgencia
            ),
            "expires_at": share_svc.expiry_datetime(),
        }
    )
    log.info("Share creado para intake %s (expira en %d días)",
             intake_id, config.SHARE_TTL_DAYS)

    destino = f"/resultado/{intake_id}"
    if raw:
        destino += f"?t={raw}"
    return RedirectResponse(destino, status_code=303)


@router.post("/compartir/{intake_id}/revocar")
async def revocar_share(intake_id: str, request: Request, t: str = Form(default="")):
    _intake, _, raw = _resolver_acceso(intake_id, request, t or None)
    n = dal.revoke_shares_for_intake(intake_id)
    log.info("Shares revocados para intake %s: %d", intake_id, n)
    destino = f"/resultado/{intake_id}"
    if raw:
        destino += f"?t={raw}"
    return RedirectResponse(destino, status_code=303)


@router.get("/s/{token}")
def ver_share(token: str, request: Request):
    row = dal.get_share(token)
    if not row or row.get("revoked"):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "message": "Este enlace ya no está disponible.",
             "detail": "Pudo haber expirado o haber sido revocado.",
             "demo_mode": False, "dev_auth": auth.DEV_AUTH},
            status_code=404,
        )
    try:
        expira = datetime.fromisoformat(row["expires_at"])
    except (ValueError, TypeError):
        expira = None
    if expira is not None:
        if expira.tzinfo is None:
            expira = expira.replace(tzinfo=timezone.utc)
        if expira <= datetime.now(timezone.utc):
            return templates.TemplateResponse(
                request,
                "error.html",
                {"request": request, "message": "Este enlace expiró.",
                 "detail": None, "demo_mode": False,
                 "dev_auth": auth.DEV_AUTH},
                status_code=410,
            )

    snap = row.get("snapshot") or {}
    con_urgencia = bool(snap.get("incluir_urgencia"))
    # Solo resumen y urgencia: sin PDF, sin otros documentos, sin identidad.
    shared = {
        "summary_simple": snap.get("summary_simple")
        or "El resumen no fue incluido en este enlace.",
        "urgency_level": urgency_css_key(snap.get("urgency_level", "no_urgente"))
        if con_urgencia else "",
        "urgency_label": snap.get("urgency_label", ""),
        "action": snap.get("urgency_action", ""),
        "expires_label": share_svc.format_expires_label(expira) if expira else "",
    }
    return templates.TemplateResponse(
        request,
        "shared.html",
        {"request": request, "shared": shared, "cta_url": "/",
         "demo_mode": False, "dev_auth": auth.DEV_AUTH},
    )
