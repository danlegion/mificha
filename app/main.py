"""MiFicha v0.1 — app FastAPI (monolito modular)."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import auth, config
from app.db import dal
from app.routers import normalize_flag_key, templates, urgency_css_key
from app.routers import accounts, ingest, results, share, vault
from app.routers.vault import _fecha_corta

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
    total_docs = 0
    if user:
        limite = datetime.now(timezone.utc) - timedelta(days=180)
        all_docs = dal.list_documents(user["id"])
        total_docs = len(all_docs)
        for d in all_docs:
            exp = d.get("explanation") or {}
            creado_raw = d.get("created_at") or ""
            try:
                creado = datetime.fromisoformat(creado_raw)
            except ValueError:
                creado = None
            if creado is not None:
                if creado.tzinfo is None:
                    creado = creado.replace(tzinfo=timezone.utc)
                if creado < limite:
                    continue
            urg = exp.get("urgency") or {}
            fuera = sum(
                1 for b in exp.get("biomarkers", [])
                if normalize_flag_key(b.get("flag")) in ("alto", "bajo")
            )
            recent_docs.append(
                {
                    "id": d["id"],
                    "type_label": exp.get("type_label", "Documento"),
                    "fecha": _fecha_corta(creado_raw),
                    "urgency_label": urg.get("label", ""),
                    "urgency_key": urgency_css_key(urg.get("level", "no_urgente")),
                    "fuera_de_rango": fuera,
                }
            )
            if len(recent_docs) >= 10:
                break
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "user": user,
            "recent_docs": recent_docs,
            "total_docs": total_docs,
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
