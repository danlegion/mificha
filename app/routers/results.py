"""MiFicha v0.1 — resultado: procesando, estado, explicación y correcciones."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import auth, config, file_store
from app.db import dal
from app.i18n import t as _t
from app.routers import (can_view_intake, key_values_with_position,
                         needs_review, nombre_examen_corto, page_lang,
                         render_explanation, share_ctx, templates)
from app.storage import delete_original, get_original_path

log = logging.getLogger("mificha.results")

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)

# Si un intake lleva "processing" más que esto, la tarea en segundo plano
# murió sin escribir estado (p. ej. el proceso se reinició): se informa
# como fallo en lugar de dejar el spinner girando para siempre.
STALLED_AFTER_SECONDS = 10 * 60


def _estancado(intake: dict) -> bool:
    """True si lleva demasiado tiempo en "processing" sin actualizarse."""
    raw = intake.get("updated_at") or intake.get("created_at") or ""
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    return delta > STALLED_AFTER_SECONDS


def _base_ctx(request: Request, user=None) -> dict:
    return {
        "request": request,
        "lang": page_lang(request),
        "user": user,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
    }


def _resolver_acceso(intake_id: str, request: Request):
    """Devuelve (intake, user) o lanza 404/403. Solo el dueño en sesión."""
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
    return intake, user


@router.get("/procesando/{intake_id}")
def procesando(intake_id: str, request: Request):
    intake, user = _resolver_acceso(intake_id, request)
    if intake["status"] == "ready":
        return RedirectResponse(f"/resultado/{intake_id}", status_code=303)
    return templates.TemplateResponse(
        request,
        "processing.html",
        {**_base_ctx(request, user), "intake_id": intake_id},
    )


@router.get("/api/v1/estado/{intake_id}")
def estado(intake_id: str, request: Request):
    intake, _ = _resolver_acceso(intake_id, request)
    status = intake["status"]
    detalle = intake.get("detail")
    if status == "processing" and _estancado(intake):
        log.warning("Intake %s estancado en processing; se reporta como fallo",
                    intake_id)
        status = "failed"
        detalle = _t("ingest.stalled_timeout", lang=page_lang(request))
    return JSONResponse(
        {"status": status, "detail": detalle, "message": detalle}
    )


@router.get("/resultado/{intake_id}")
def resultado(intake_id: str, request: Request):
    intake, user = _resolver_acceso(intake_id, request)
    if intake["status"] == "processing":
        return RedirectResponse(f"/procesando/{intake_id}", status_code=303)
    if intake["status"] in ("rejected", "failed") or not intake.get("explanation"):
        return templates.TemplateResponse(
            request,
            "error.html",
            {**_base_ctx(request, user),
             "message": intake.get("detail") or "No pudimos procesar tu documento.",
             "detail": None},
            status_code=422,
        )
    share_row = dal.get_active_share_for_intake(intake_id)
    member_name = ""
    if user and intake.get("member_id"):
        member_name = next(
            (m["name"] for m in dal.list_members(user["id"])
             if m["id"] == intake.get("member_id")), "")
    # Revisión en la propia página del resultado: el documento guardado
    # es el mismo intake (mismo id); sin fila de documento no hay nada
    # que marcar.
    doc_row = dal.get_document(intake_id)
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            **_base_ctx(request, user),
            "exp": render_explanation(intake["explanation"]),
            "intake_id": intake_id,
            "review_id": intake_id if doc_row else "",
            "review_needs": needs_review(intake.get("explanation") or {}),
            "review_reviewed": doc_row.get("reviewed_at") if doc_row else None,
            "exam_name": nombre_examen_corto(intake.get("explanation") or {}),
            "key_values": key_values_with_position(intake.get("explanation") or {}),
            "member_id": intake.get("member_id"),
            "member_name": member_name,
            "laboratorio": (intake.get("explanation") or {}).get("laboratorio") or "",
            "original_available": bool(intake.get("original_key")),
            "is_saved": bool(intake.get("owner_user_id")),
            "share": share_ctx(share_row),
            "demo_mode": bool(intake["explanation"].get("demo_mode", DEMO_MODE)),
        },
    )


@router.get("/resultado/{intake_id}/original")
def resultado_original(intake_id: str, request: Request, descargar: bool = False):
    intake, _ = _resolver_acceso(intake_id, request)
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
    intake, user = _resolver_acceso(intake_id, request)
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
    intake = dal.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No encontrado")
    user = auth.get_current_user(request)
    if not can_view_intake(intake, user):
        raise HTTPException(status_code=403, detail="Sin acceso")
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
        # Si el intake ya es un documento guardado, la corrección también
        # llega al documento y a su explanation.json, para que el archivo,
        # la ficha y el intake digan lo mismo.
        actualizado = dal.get_intake(intake_id) or {}
        doc = dal.get_document(intake_id)
        if doc and actualizado.get("explanation") is not None:
            dal.save_document({**doc, "explanation": actualizado["explanation"]})
            file_store.write_explanation(
                doc.get("member_id"), intake_id, actualizado["explanation"])

    return RedirectResponse(f"/resultado/{intake_id}", status_code=303)
