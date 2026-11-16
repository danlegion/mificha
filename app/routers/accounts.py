"""MiFicha v0.1 — cuenta: entrar con enlace mágico y acceso único.

No hay flujo de invitado: subir exige sesión, y cada cuenta se crea al
entrar por primera vez con el enlace mágico (registro e inicio de sesión
son el mismo paso). En modo desarrollo (sin Supabase) la sesión es
directa: si el correo no tiene cuenta, se crea una.
"""

import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import auth, config, ratelimit
from app.db import dal
from app.i18n import t as _t
from app.routers import page_lang, templates

log = logging.getLogger("mificha.accounts")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _con_sesion(resp, user_id: str):
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session(user_id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 90,
        secure=config.BASE_URL.startswith("https://"),
    )
    return resp


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
    """Completa el inicio de sesión con el access_token del enlace mágico.

    Si el correo no tiene cuenta todavía, se crea una: entrar por primera
    vez es registrarse.
    """
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
    usuario = dal.get_user_by_email(email) if email else None
    if not usuario:
        usuario = dal.create_user(email=email, telefono=None)
        log.info("Cuenta creada al activar enlace mágico (%s)", email)
    return _con_sesion(
        JSONResponse({"ok": True, "redirect": "/ficha"}),
        usuario["id"])


# --- Iniciar sesión -----------------------------------------------------

def _ctx_entrar(request: Request, error=None, step="form", email="",
                notice=None) -> dict:
    return {
        "request": request,
        "lang": page_lang(request),
        "error": error,
        "step": step,
        "email": email,
        "notice": notice,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
        "user": auth.get_current_user(request),
    }


@router.get("/entrar")
def entrar_form(request: Request):
    if auth.single_gate_enabled():
        # Con puerta única, el login por correo no existe: va a /acceso.
        return RedirectResponse("/acceso", status_code=303)
    if auth.get_current_user(request):
        return RedirectResponse("/ficha", status_code=303)
    return templates.TemplateResponse(request, "signin.html",
                                      _ctx_entrar(request))


@router.post("/entrar")
def entrar(request: Request, email: str = Form(...)):
    if auth.single_gate_enabled():
        return RedirectResponse("/acceso", status_code=303)
    email = (email or "").strip().lower()
    if not _RE_EMAIL.match(email):
        return templates.TemplateResponse(
            request, "signin.html",
            _ctx_entrar(request, _t("auth.invalid_email"),
                        email=email),
            status_code=400)
    if not config.USE_SUPABASE:
        # Modo desarrollo: sin Supabase, sesión directa. Si el correo no
        # tiene cuenta, se crea una (entrar por primera vez es registrarse).
        usuario = dal.get_user_by_email(email)
        if not usuario:
            usuario = dal.create_user(email=email, telefono=None)
            log.warning("MODO DESARROLLO: cuenta creada al entrar (%s)",
                        email)
        else:
            log.warning("MODO DESARROLLO: inicio de sesión sin "
                        "verificación (%s)", email)
        return _con_sesion(RedirectResponse("/ficha", status_code=303),
                           usuario["id"])
    try:
        auth.send_magic_link(
            email, redirect_to=config.BASE_URL + "/cuenta/activar")
    except auth.AuthError as e:
        return templates.TemplateResponse(
            request, "signin.html",
            _ctx_entrar(request, str(e), email=email),
            status_code=400 if e.kind != "red" else 502)
    return templates.TemplateResponse(
        request, "signin.html", _ctx_entrar(request, step="link", email=email))


@router.get("/salir")
def salir(request: Request):
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, samesite="lax")
    return resp


# --- Acceso único (auto-hospedaje) ---------------------------------------
# Solo existe cuando la puerta está activa (APP_PASSWORD + sin Supabase).
# Sin puerta, /acceso no hace nada útil: redirige a la página principal.
def _ctx_acceso(request: Request, error=None, usuario=""):
    return {
        "request": request, "lang": page_lang(request),
        "error": error, "usuario": usuario,
        "demo_mode": DEMO_MODE, "dev_auth": auth.DEV_AUTH,
        "user": auth.get_current_user(request),
    }


@router.get("/acceso")
def acceso_form(request: Request):
    if auth.is_single_session(request):
        return RedirectResponse("/ficha", status_code=303)
    if not auth.single_gate_enabled():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login_single.html",
                                      _ctx_acceso(request))


@router.post("/acceso")
def acceso(request: Request, usuario: str = Form(default=""),
           clave: str = Form(default="")):
    if auth.is_single_session(request):
        return RedirectResponse("/ficha", status_code=303)
    if not auth.single_gate_enabled():
        return RedirectResponse("/", status_code=303)
    ip = request.client.host if request.client else "?"
    if not ratelimit.check(f"acceso:{ip}", 10, window_s=900):
        return templates.TemplateResponse(
            request, "login_single.html",
            _ctx_acceso(request, error=_t("single.rate_limited"),
                        usuario=usuario),
            status_code=429)
    if not auth.verify_single_credentials(usuario, clave):
        log.warning("Acceso único fallido desde %s", ip)
        return templates.TemplateResponse(
            request, "login_single.html",
            _ctx_acceso(request, error=_t("single.error"), usuario=usuario),
            status_code=401)
    user = auth.get_or_create_single_user()
    return _con_sesion(RedirectResponse("/ficha", status_code=303),
                       user["id"])
