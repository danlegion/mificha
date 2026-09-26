"""MiFicha v0.1 — cuenta: guardar el resultado en la ficha (vault)."""

import logging
import re
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import can_view_intake, guest_token_valid, templates

log = logging.getLogger("mificha.accounts")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PENDING_TTL = 15 * 60  # el enlace mágico expira en Supabase; el contexto, aquí

# Registro pendiente del enlace mágico, por correo verificado:
# email -> {telefono, intake_id, guest_token, expira}. En memoria (prototipo):
# si el servidor reinicia, el usuario pide otro enlace.
_PENDIENTE: dict[str, dict] = {}


def _pendiente_guardar(email: str, telefono: str, intake_id: str,
                       guest_token: str | None) -> None:
    _PENDIENTE[email] = {
        "telefono": telefono,
        "intake_id": intake_id,
        "guest_token": guest_token,
        "expira": time.time() + _PENDING_TTL,
    }


def _pendiente_tomar(email: str) -> dict | None:
    datos = _PENDIENTE.pop(email, None)
    if not datos or datos["expira"] < time.time():
        return None
    return datos


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


def _reclamar(intake_id: str, email: str, telefono: str | None) -> dict:
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
    return nuevo_usuario


def _con_sesion(resp, user_id: str):
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session(user_id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 90,
    )
    return resp


def _finalizar_registro(intake_id: str, email: str, telefono: str | None):
    """Versión con redirect (modo desarrollo)."""
    nuevo_usuario = _reclamar(intake_id, email, telefono)
    return _con_sesion(RedirectResponse("/ficha", status_code=303),
                       nuevo_usuario["id"])


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
        # Paso 1: se envía el enlace mágico al correo (plantilla por defecto,
        # sin necesidad de editarla). Al abrirlo, Supabase redirige a
        # /cuenta/activar, que completa el registro.
        _pendiente_guardar(email, telefono or None, intake_id, raw)
        try:
            auth.send_magic_link(
                email, redirect_to=config.BASE_URL + "/cuenta/activar")
        except auth.AuthError as e:
            _PENDIENTE.pop(email, None)
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
            _ctx(request, intake_id, raw, step="link",
                 email=email, telefono=telefono),
        )

    if auth.DEV_AUTH:
        log.warning(
            "MODO DESARROLLO: verificación de email/teléfono omitida "
            "(registro de %s)", email)
    return _finalizar_registro(intake_id, email, telefono or None)


@router.get("/cuenta/activar")
def activar_pagina(request: Request):
    """Página que captura el token del enlace mágico (llega en el fragmento
    de la URL) y lo envía al POST de esta misma ruta."""
    error = request.query_params.get("error_description") or \
        request.query_params.get("error")
    return templates.TemplateResponse(
        request, "auth_callback.html",
        {"request": request, "error": error,
         "demo_mode": DEMO_MODE, "dev_auth": auth.DEV_AUTH,
         "user": auth.get_current_user(request)})


@router.post("/cuenta/activar")
async def activar(request: Request):
    """Completa el registro con el access_token del enlace mágico."""
    try:
        datos = await request.json()
    except Exception:
        datos = {}
    access_token = (datos or {}).get("access_token", "")
    token_hash = (datos or {}).get("token_hash", "")
    try:
        sb_user = auth.complete_magic_link(access_token=access_token,
                                           token_hash=token_hash)
    except auth.AuthError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    email = (sb_user.get("email") or "").strip().lower()
    pendiente = _pendiente_tomar(email) if email else None
    if not pendiente:
        return JSONResponse(
            {"ok": False,
             "message": "Este enlace ya se usó o venció. Vuelve a tu resultado "
                        "y pide un enlace nuevo."},
            status_code=400)
    intake = dal.get_intake(pendiente["intake_id"])
    if not intake:
        return JSONResponse(
            {"ok": False,
             "message": "Tu resultado ya no está disponible. Sube tu examen "
                        "de nuevo."},
            status_code=400)
    nuevo_usuario = _reclamar(pendiente["intake_id"], email,
                              pendiente.get("telefono"))
    return _con_sesion(
        JSONResponse({"ok": True, "redirect": "/ficha"}),
        nuevo_usuario["id"])


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
    _pendiente_guardar(email, telefono or None, intake_id, raw)
    try:
        auth.send_magic_link(
            email, redirect_to=config.BASE_URL + "/cuenta/activar")
    except auth.AuthError as e:
        _PENDIENTE.pop(email, None)
        return templates.TemplateResponse(
            request,
            "register.html",
            _ctx(request, intake_id, raw, error=str(e), step="link",
                 email=email, telefono=telefono),
            status_code=400 if e.kind != "red" else 502,
        )
    return templates.TemplateResponse(
        request,
        "register.html",
        _ctx(request, intake_id, raw, step="link",
             email=email, telefono=telefono,
             notice=f"Te enviamos un enlace nuevo a {email}."),
    )
