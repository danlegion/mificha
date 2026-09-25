"""MiFicha v0.1 — cuenta: guardar el resultado en la ficha (vault)."""

import logging
import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import can_view_intake, guest_token_valid, templates

log = logging.getLogger("mificha.accounts")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ctx(request: Request, intake_id, guest_token, error=None) -> dict:
    return {
        "request": request,
        "intake_id": intake_id,
        "guest_token": guest_token,
        "error": error,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
        "user": auth.get_current_user(request),
    }


@router.get("/guardar/{intake_id}")
def guardar_form(intake_id: str, request: Request):
    token = request.query_params.get("t")
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    if intake["status"] != "ready" or not intake.get("explanation"):
        return RedirectResponse(f"/procesando/{intake_id}?t={token or ''}",
                                status_code=303)
    if user and intake.get("owner_user_id") == user["id"]:
        return RedirectResponse("/ficha", status_code=303)
    raw = token if guest_token_valid(intake, token) else None
    return templates.TemplateResponse(
        request,
        "register.html", _ctx(request, intake_id, raw))


@router.post("/guardar/{intake_id}")
def guardar(
    intake_id: str,
    request: Request,
    email: str = Form(...),
    telefono: str = Form(...),
    t: str = Form(default=""),
):
    token = t or request.query_params.get("t")
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None

    email = (email or "").strip()
    telefono = (telefono or "").strip()
    error = None
    if not _RE_EMAIL.match(email):
        error = "Revisa tu correo electrónico, no parece válido."
    elif len(telefono) < 6:
        error = "Revisa tu teléfono, parece incompleto."
    if error:
        return templates.TemplateResponse(
            request,
            "register.html", _ctx(request, intake_id, raw, error),
            status_code=400,
        )

    if config.USE_SUPABASE:
        # El registro real va por Supabase Auth (magic link / OTP).
        return templates.TemplateResponse(
            request,
            "register.html",
            _ctx(request, intake_id, raw,
                 "El registro con Supabase Auth aún no está conectado en esta versión."),
            status_code=501,
        )

    nuevo_usuario = auth.register_dev(email, telefono)
    dal.claim_intake(intake_id, nuevo_usuario["id"])
    dal.save_document(
        {
            "id": intake_id,
            "user_id": nuevo_usuario["id"],
            "intake_id": intake_id,
            "original_key": intake.get("original_key"),
            "explanation": intake.get("explanation"),
        }
    )
    log.info("Intake %s reclamado por usuario %s", intake_id, nuevo_usuario["id"])

    resp = RedirectResponse("/ficha", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session(nuevo_usuario["id"]),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 90,
    )
    return resp
