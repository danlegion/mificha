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


def _ctx(request: Request, intake_id, guest_token, error=None, step="form",
         email="", telefono="", notice=None) -> dict:
    return {
        "request": request,
        "intake_id": intake_id,
        "guest_token": guest_token,
        "error": error,
        "step": step,
        "email": email,
        "telefono": telefono,
        "notice": notice,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
        "user": auth.get_current_user(request),
    }


def _finalizar_registro(intake_id: str, email: str, telefono: str | None):
    """Crea/ubica al usuario, reclama el intake y guarda el documento."""
    nuevo_usuario = dal.create_user(email=email, telefono=telefono)
    dal.claim_intake(intake_id, nuevo_usuario["id"])
    intake = dal.get_intake(intake_id) or {}
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

    email = (email or "").strip().lower()
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
        # Paso 1: se envía el código de 6 dígitos al correo; el paso 2
        # (/verificar) lo valida y completa el registro.
        try:
            auth.send_email_otp(email)
        except auth.AuthError as e:
            return templates.TemplateResponse(
                request,
                "register.html",
                _ctx(request, intake_id, raw, error=str(e),
                     email=email, telefono=telefono),
                status_code=400 if e.kind != "red" else 502,
            )
        return templates.TemplateResponse(
            request,
            "register.html",
            _ctx(request, intake_id, raw, step="code",
                 email=email, telefono=telefono),
        )

    if auth.DEV_AUTH:
        log.warning(
            "MODO DESARROLLO: verificación de email/teléfono omitida "
            "(registro de %s)", email)
    return _finalizar_registro(intake_id, email, telefono or None)


@router.post("/guardar/{intake_id}/verificar")
def verificar(
    intake_id: str,
    request: Request,
    email: str = Form(...),
    telefono: str = Form(default=""),
    codigo: str = Form(...),
    t: str = Form(default=""),
):
    token = (t or "").strip()
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None

    email = (email or "").strip().lower()
    telefono = (telefono or "").strip()
    try:
        sb_user = auth.verify_email_otp(email, codigo)
    except auth.AuthError as e:
        return templates.TemplateResponse(
            request,
            "register.html",
            _ctx(request, intake_id, raw, error=str(e), step="code",
                 email=email, telefono=telefono),
            status_code=400,
        )
    return _finalizar_registro(
        intake_id, sb_user.get("email") or email, telefono or None)


@router.post("/guardar/{intake_id}/reenviar")
def reenviar(
    intake_id: str,
    request: Request,
    email: str = Form(...),
    telefono: str = Form(default=""),
    t: str = Form(default=""),
):
    token = (t or "").strip()
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, token, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    raw = token if guest_token_valid(intake, token) else None

    email = (email or "").strip().lower()
    telefono = (telefono or "").strip()
    try:
        auth.send_email_otp(email)
    except auth.AuthError as e:
        return templates.TemplateResponse(
            request,
            "register.html",
            _ctx(request, intake_id, raw, error=str(e), step="code",
                 email=email, telefono=telefono),
            status_code=400 if e.kind != "red" else 502,
        )
    return templates.TemplateResponse(
        request,
        "register.html",
        _ctx(request, intake_id, raw, step="code",
             email=email, telefono=telefono,
             notice=f"Te enviamos un código nuevo a {email}."),
    )
