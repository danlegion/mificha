"""MiFicha v0.1 — resultado: procesando, estado, explicación y correcciones."""

import logging
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import (can_view_intake, guest_token_valid,
                         render_explanation, templates, urgency_css_key)
from app.services import share as share_svc

log = logging.getLogger("mificha.results")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)


def _base_ctx(request: Request, user=None, guest_token=None) -> dict:
    return {
        "request": request,
        "user": user,
        "guest_token": guest_token,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
    }


def _share_ctx(share_row: dict | None) -> dict | None:
    """Contexto `share` para result.html (las 4 claves + extras)."""
    if not share_row:
        return None
    snap = share_row.get("snapshot") or {}
    token = share_row["token"]
    incluir_urgencia = bool(snap.get("incluir_urgencia"))
    return {
        "url": share_svc.share_url(token),
        "text_encoded": share_svc.whatsapp_text(token, incluir_urgencia),
        "include_urgency": incluir_urgencia,
        "urgency_level": urgency_css_key(snap.get("urgency_level", "no_urgente"))
        if incluir_urgencia else "",
        "summary_simple": snap.get("summary_simple", ""),
        "urgency_label": snap.get("urgency_label", ""),
        "action": snap.get("urgency_action", ""),
        "expires_label": share_svc.format_expires_label(
            datetime.fromisoformat(share_row["expires_at"])
        ),
    }


def _resolver_acceso(intake_id: str, request: Request):
    """Devuelve (intake, user, guest_token_raw) o lanza 404/403."""
    token = request.query_params.get("t")
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None
    return intake, user, raw


@router.get("/procesando/{intake_id}")
def procesando(intake_id: str, request: Request):
    intake, user, raw = _resolver_acceso(intake_id, request)
    if intake["status"] == "ready":
        destino = f"/resultado/{intake_id}"
        if raw:
            destino += f"?t={raw}"
        return RedirectResponse(destino, status_code=303)
    return templates.TemplateResponse(
        request,
        "processing.html",
        {**_base_ctx(request, user, raw), "intake_id": intake_id},
    )


@router.get("/api/v1/estado/{intake_id}")
def estado(intake_id: str, request: Request):
    intake, _, _ = _resolver_acceso(intake_id, request)
    detalle = intake.get("detail")
    return JSONResponse(
        {"status": intake["status"], "detail": detalle, "message": detalle}
    )


@router.get("/resultado/{intake_id}")
def resultado(intake_id: str, request: Request):
    intake, user, raw = _resolver_acceso(intake_id, request)
    if intake["status"] == "processing":
        destino = f"/procesando/{intake_id}"
        if raw:
            destino += f"?t={raw}"
        return RedirectResponse(destino, status_code=303)
    if intake["status"] in ("rejected", "failed") or not intake.get("explanation"):
        return templates.TemplateResponse(
            request,
            "error.html",
            {**_base_ctx(request, user, raw),
             "message": intake.get("detail") or "No pudimos procesar tu documento.",
             "detail": None},
            status_code=422,
        )
    share_row = dal.get_active_share_for_intake(intake_id)
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            **_base_ctx(request, user, raw),
            "exp": render_explanation(intake["explanation"]),
            "intake_id": intake_id,
            "is_saved": bool(intake.get("owner_user_id")),
            "share": _share_ctx(share_row),
            "demo_mode": bool(intake["explanation"].get("demo_mode", DEMO_MODE)),
        },
    )


@router.post("/resultado/{intake_id}/corregir")
async def corregir(intake_id: str, request: Request):
    form = await request.form()
    token = form.get("t") or request.query_params.get("t")
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None
    exp = intake.get("explanation") or {}
    render = render_explanation(exp)

    nuevas = []
    for uf in render.get("uncertain_fields", []):
        clave = uf.get("key")
        valor = (form.get(clave) or "").strip() if clave else ""
        if valor:
            nuevas.append(
                {
                    "field": uf.get("field"),
                    "key": clave,
                    "corrected_value": valor,
                    "at": datetime.now().isoformat(),
                }
            )
    if nuevas:
        previas = intake.get("corrections") or []
        dal.update_intake_corrections(intake_id, previas + nuevas)
        log.info("Intake %s: %d correcciones guardadas", intake_id, len(nuevas))

    destino = f"/resultado/{intake_id}"
    if raw:
        destino += f"?t={raw}"
    return RedirectResponse(destino, status_code=303)
