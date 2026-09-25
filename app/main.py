"""MiFicha v0.1 — app FastAPI (monolito modular)."""

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import auth, config
from app.db import dal
from app.routers import templates
from app.routers import accounts, ingest, results, share, vault

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger("mificha")

STATIC_DIR = os.path.join(config.BASE_DIR, "static")

app = FastAPI(title="MiFicha", version="0.1")

# Log de requests SIN contenido clínico: método, ruta (sin query), estado.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    inicio = time.time()
    resp = await call_next(request)
    ms = int((time.time() - inicio) * 1000)
    log.info("%s %s -> %s (%d ms)",
             request.method, request.url.path, resp.status_code, ms)
    return resp


@app.get("/")
def index(request: Request):
    user = auth.get_current_user(request)
    recent_docs = []
    if user:
        for d in dal.list_documents(user["id"])[:5]:
            exp = d.get("explanation") or {}
            creado = (d.get("created_at") or "")[:10]
            recent_docs.append(
                {
                    "id": d["id"],
                    "type_label": exp.get("type_label", "Documento"),
                    "fecha": creado,
                    "idea_principal": (exp.get("summary_simple") or "")[:160],
                }
            )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "user": user,
            "recent_docs": recent_docs,
            "guest_token": None,
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


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "message": "No encontramos esa página.",
         "detail": None, "demo_mode": False, "dev_auth": auth.DEV_AUTH},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "message": "No tienes acceso a esto.",
         "detail": None, "demo_mode": False, "dev_auth": auth.DEV_AUTH},
        status_code=403,
    )
