"""MiFicha v0.1 — ingesta: POST /subir."""

import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app import auth, config
from app.db import dal
from app.i18n import t as _t
from app.routers import (hash_token, new_guest_token, page_lang, templates)
from app.services.pipeline import UnprocessableDocument, process_intake
from app.storage import quarantine_path, store_original

log = logging.getLogger("mificha.ingest")

router = APIRouter()


def _validar_pdf(pdf_bytes: bytes) -> str | None:
    """Devuelve mensaje de error en el idioma actual o None si el PDF es válido."""
    if not pdf_bytes.startswith(b"%PDF"):
        return _t("ingest.not_pdf")
    mb = len(pdf_bytes) / (1024 * 1024)
    if mb > config.MAX_PDF_MB:
        return _t("ingest.too_big", mb=f"{mb:.1f}", max_mb=config.MAX_PDF_MB)
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n = doc.page_count
        doc.close()
    except Exception:
        return _t("ingest.cant_open")
    if n == 0:
        return _t("ingest.no_pages")
    if n > config.MAX_PDF_PAGES:
        return _t("ingest.too_many_pages", n=n, max_p=config.MAX_PDF_PAGES)
    return None


def _run_pipeline(intake_id: str, lang: str = "es"):
    """Tarea en segundo plano: procesa el PDF en cuarentena."""
    intake = dal.get_intake(intake_id)
    if not intake:
        log.error("Intake %s no existe; no se procesa.", intake_id)
        return
    if lang not in ("es", "en"):
        lang = "es"
    qpath = quarantine_path(intake_id)
    try:
        with open(qpath, "rb") as f:
            pdf_bytes = f.read()
    except OSError as exc:
        log.error("No se pudo leer cuarentena de %s: %s", intake_id, exc)
        dal.set_intake_ready(intake_id, status="failed",
                             detail=_t("ingest.unreadable", lang=lang))
        return
    # El original se sube a Storage aquí, en segundo plano: ya no retrasa
    # la redirección a la página de "procesando".
    try:
        dal.set_intake_original_key(
            intake_id, store_original(intake_id, pdf_bytes))
    except Exception:  # noqa: BLE001 - el original es opcional
        log.exception("No se pudo guardar el original de %s; sigue sin él.",
                      intake_id)
    try:
        exp = process_intake(intake_id, pdf_bytes,
                             intake.get("doc_type_hint"), lang=lang)
    except UnprocessableDocument as exc:
        log.info("Intake %s rechazado: %s", intake_id, exc)
        dal.set_intake_ready(intake_id, status="rejected", detail=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - no tumbar el worker
        log.exception("Intake %s falló: %s", intake_id, exc)
        dal.set_intake_ready(
            intake_id, status="failed",
            detail=_t("ingest.processing_error", lang=lang),
        )
        return
    dal.set_intake_ready(intake_id, explanation=exp, status="ready")
    # Si el intake tiene dueño (subida con sesión iniciada), el documento
    # queda guardado en su ficha automáticamente. Se relee el intake porque
    # el original_key se guardó en segundo plano después de crearlo.
    intake = dal.get_intake(intake_id) or {}
    owner_id = intake.get("owner_user_id")
    if owner_id:
        dal.save_document(
            {
                "id": intake_id,
                "user_id": owner_id,
                "intake_id": intake_id,
                "original_key": intake.get("original_key"),
                "explanation": exp,
            }
        )
        log.info("Intake %s guardado en la ficha del usuario %s",
                 intake_id, owner_id)
    try:
        os.remove(qpath)
    except OSError:
        pass


@router.post("/subir")
async def subir(
    request: Request,
    background: BackgroundTasks,
    archivo: UploadFile = File(...),
    tipo_doc: str = Form(default=""),
):
    contenido = await archivo.read()
    error = _validar_pdf(contenido)
    if error:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "lang": page_lang(request),
             "message": error, "detail": None,
             "dev_auth": False, "demo_mode": False},
            status_code=400,
        )

    intake_id = uuid.uuid4().hex
    guest_token = new_guest_token()
    with open(quarantine_path(intake_id), "wb") as f:
        f.write(contenido)

    # Si hay sesión iniciada, el intake nace con dueño: al terminar el
    # procesamiento el documento se guarda solo en su ficha.
    # (El PDF original se sube a Storage en segundo plano, sin bloquear
    # la redirección a la página de "procesando".)
    user = auth.get_current_user(request)
    nuevo_intake = {
        "id": intake_id,
        "guest_token_hash": hash_token(guest_token),
        "status": "processing",
        "doc_type_hint": (tipo_doc or "").strip() or None,
    }
    if user:
        nuevo_intake["owner_user_id"] = user["id"]
    dal.save_intake(nuevo_intake)
    # Log sin contenido clínico: solo tamaños y estado.
    log.info("Nuevo intake %s (%d bytes, hint=%s)",
             intake_id, len(contenido), tipo_doc or "-")
    background.add_task(_run_pipeline, intake_id, page_lang(request))
    return RedirectResponse(
        f"/procesando/{intake_id}?t={guest_token}", status_code=303
    )
