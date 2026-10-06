"""MiFicha v0.1 — resultado: procesando, estado, explicación y correcciones."""

import logging
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import (can_view_intake, guest_token_valid, page_lang,
                         render_explanation, share_ctx, templates)
from app.storage import delete_original, get_original_path

log = logging.getLogger("mificha.results")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)


def _base_ctx(request: Request, user=None, guest_token=None) -> dict:
    return {
        "request": request,
        "lang": page_lang(request),
        "user": user,
        "guest_token": guest_token,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
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
    # Red de seguridad: con sesión iniciada, si el intake está listo pero aún
    # no tiene dueño (p. ej. se subió antes de entrar) y se abre con el token
    # de invitado, se reclama y se guarda en la ficha.
    if user and not intake.get("owner_user_id") and raw:
        dal.claim_intake(intake_id, user["id"])
        dal.save_document(
            {
                "id": intake_id,
                "user_id": user["id"],
                "intake_id": intake_id,
                "original_key": intake.get("original_key"),
                "explanation": intake.get("explanation"),
            }
        )
        log.info("Intake %s reclamado al verlo por usuario %s",
                 intake_id, user["id"])
        intake = dal.get_intake(intake_id) or intake
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            **_base_ctx(request, user, raw),
            "exp": render_explanation(intake["explanation"]),
            "intake_id": intake_id,
            "is_saved": bool(intake.get("owner_user_id")),
            "share": share_ctx(share_row),
            "demo_mode": bool(intake["explanation"].get("demo_mode", DEMO_MODE)),
        },
    )


@router.get("/resultado/{intake_id}/original")
def resultado_original(intake_id: str, request: Request, descargar: bool = False):
    intake, _, _ = _resolver_acceso(intake_id, request)
    path = get_original_path(intake.get("original_key") or "")
    if not path:
        raise HTTPException(status_code=404, detail="Original no disponible")
    modo = "attachment" if descargar else "inline"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{modo}; filename="documento.pdf"'},
    )


@router.post("/resultado/{intake_id}/eliminar")
def resultado_eliminar(intake_id: str, request: Request):
    intake, user, _ = _resolver_acceso(intake_id, request)
    guardado = bool(intake.get("owner_user_id"))
    delete_original(intake.get("original_key") or "")
    dal.revoke_shares_for_intake(intake_id)
    if dal.get_document(intake_id):
        dal.delete_document(intake_id)
    dal.delete_intake(intake_id)
    log.info("Intake %s eliminado (guardado=%s)", intake_id, guardado)
    destino = "/ficha" if (user and guardado) else "/"
    return RedirectResponse(destino, status_code=303)


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
