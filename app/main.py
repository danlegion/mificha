"""MiFicha v0.1 — app FastAPI (monolito modular)."""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, config
from app.db import dal
from app.i18n import (LANG_COOKIE, SUPPORTED, get_lang, set_current_lang,
                      t as _t)
from app.routers import (initials, member_color, needs_review,
                         nombre_examen_corto, page_lang, templates)
from app.routers import accounts, ingest, results, share, vault
from app.routers.vault import _fecha_corta, _fecha_efectiva

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger("mificha")

STATIC_DIR = os.path.join(config.BASE_DIR, "static")

app = FastAPI(title="MiFicha", version="0.1")

# Puerta de acceso único (auto-hospedaje): cuando APP_PASSWORD está puesto
# y no hay Supabase, toda la app exige la sesión del usuario único, salvo
# la página de acceso, los estáticos, el cambio de idioma, salir, y los
# enlaces de compartir (/s/) — esos son de solo lectura, con token propio,
# 7 días y revocables, y el titular los creó a propósito. Sin puerta no
# hace nada: el demo público y el flujo de invitado siguen intactos.
# Se registra primero para que corra después de idioma/log: la redirección
# a /acceso sale con idioma, log y cabeceras correctas.
_GATE_EXEMPT_PREFIXES = ("/static", "/acceso", "/s/", "/idioma")
_GATE_EXEMPT_EXACT = {"/manifest.json", "/sw.js", "/salir", "/favicon.ico"}


@app.middleware("http")
async def acceso_unico(request: Request, call_next):
    if auth.single_gate_enabled():
        path = request.url.path
        exempt = path in _GATE_EXEMPT_EXACT or any(
            path.startswith(p) for p in _GATE_EXEMPT_PREFIXES)
        if not exempt and not auth.is_single_session(request):
            return RedirectResponse("/acceso", status_code=303)
    return await call_next(request)


# Log de requests SIN contenido clínico: método, ruta (sin query), estado.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    inicio = time.time()
    resp = await call_next(request)
    ms = int((time.time() - inicio) * 1000)
    log.info("%s %s -> %s (%d ms)",
             request.method, request.url.path, resp.status_code, ms)
    return resp


# Idioma: navegador (Accept-Language) o cambio manual ES/EN. Queda fijado
# para todo lo que se renderice en esta petición.
@app.middleware("http")
async def idioma(request: Request, call_next):
    lang = get_lang(request)
    request.state.lang = lang
    token = set_current_lang(lang)
    try:
        return await call_next(request)
    finally:
        from app.i18n import current_lang
        current_lang.reset(token)


# El HTML es personal y cambia con cada acción (borrar, subir, guardar):
# nunca debe servirse desde la caché del navegador.
@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    resp = await call_next(request)
    if "text/html" in resp.headers.get("content-type", ""):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# Seguridad: los tokens de share viajan en la URL (/s/...).
# Esta cabecera impide que el navegador los envíe a terceros en Referer.
@app.middleware("http")
async def referrer_policy(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp




@app.get("/")
def index(request: Request):
    user = auth.get_current_user(request)
    last_explained = None
    total_docs = 0
    family = []
    # Documentos por revisar: requieren revisión (incierto o fuera de
    # rango, ver needs_review) y aún no se marcaron como revisados.
    # Cuenta toda la bóveda, no solo los últimos 180 días.
    to_review = 0
    if user:
        limite = datetime.now(timezone.utc) - timedelta(days=180)
        all_docs = dal.list_documents(user["id"])
        total_docs = len(all_docs)
        to_review = sum(
            1 for d in all_docs
            if needs_review(d.get("explanation") or {})
            and not d.get("reviewed_at")
        )
        # Orden: por fecha del documento (doctor/laboratorio), no de subida.
        all_docs = sorted(
            all_docs,
            key=lambda d: _fecha_efectiva(
                d.get("explanation") or {}, d.get("created_at") or ""
            )[1] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        # Último explicado (hero): el documento más reciente de los
        # últimos 180 días. La lista de recientes ya no se muestra en
        # el home: las tarjetas de la familia son la lista.
        for d in all_docs:
            exp = d.get("explanation") or {}
            fecha_iso, fecha_dt = _fecha_efectiva(
                exp, d.get("created_at") or "")
            if fecha_dt is not None and fecha_dt < limite:
                continue
            last_explained = {
                "exam_about": nombre_examen_corto(exp),
                "fecha": _fecha_corta(fecha_iso),
            }
            break
        # Familia de un vistazo: un dato real por familiar (cuántos
        # documentos y el último examen), sin estados inventados.
        members = dal.list_members(user["id"])
        for i, m in enumerate(members):
            mdocs = [d for d in all_docs if d.get("member_id") == m["id"]]
            last = ""
            if mdocs:
                exp0 = mdocs[0].get("explanation") or {}
                iso0, _ = _fecha_efectiva(exp0, mdocs[0].get("created_at") or "")
                last = _fecha_corta(iso0)
            bg, fg = member_color(i)
            family.append({
                "id": m["id"],
                "name": m["name"],
                "initials": initials(m["name"]),
                "color_bg": bg,
                "color_fg": fg,
                "docs_count": len(mdocs),
                "last_exam": last,
            })
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "lang": page_lang(request),
            "user": user,
            "total_docs": total_docs,
            "family": family,
            "to_review": to_review,
            "last_explained": last_explained,
            "members": dal.list_members(user["id"]) if user else [],
            "demo_mode": not bool(config.ANTHROPIC_API_KEY),
            "dev_auth": auth.DEV_AUTH,
        },
    )


@app.get("/manifest.json")
def manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.json"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(ingest.router)
app.include_router(results.router)
app.include_router(accounts.router)
app.include_router(vault.router)
app.include_router(share.router)


@app.get("/idioma/{lang}")
def cambiar_idioma(lang: str, request: Request):
    """Cambio manual ES/EN: guarda la elección en cookie y vuelve atrás."""
    lang = lang if lang in SUPPORTED else "en"
    destino = request.headers.get("referer", "/") or "/"
    if not destino.startswith("/"):  # solo redirecciones internas
        destino = "/"
    resp = RedirectResponse(destino, status_code=303)
    resp.set_cookie(LANG_COOKIE, lang, max_age=365 * 24 * 3600,
                    samesite="lax", path="/",
                    secure=config.BASE_URL.startswith("https://"))
    return resp


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "lang": page_lang(request),
         "message": _t("error.not_found"),
         "detail": None, "demo_mode": False, "dev_auth": auth.DEV_AUTH},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "lang": page_lang(request),
         "message": _t("error.forbidden"),
         "detail": None, "demo_mode": False, "dev_auth": auth.DEV_AUTH},
        status_code=403,
    )
