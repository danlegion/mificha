"""MiFicha v0.1 — ingesta: POST /subir."""

import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app import auth, config
from app import ratelimit
from app.db import dal
from app.i18n import t as _t
from app.routers import page_lang, templates
from app.services.pipeline import UnprocessableDocument, process_intake
from app.storage import quarantine_path, store_original

log = logging.getLogger("mificha.ingest")

router = APIRouter()


def _client_ip(request: Request) -> str:
    """IP real del cliente (Render pone el proxy delante)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "?"


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
        lang = "en"
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
                "member_id": intake.get("member_id"),
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
    miembro: str = Form(default=""),
    nuevo_miembro: str = Form(default=""),
):
    ip = _client_ip(request)
    if not ratelimit.check(f"subir:{ip}", config.UPLOAD_RATE_LIMIT_PER_HOUR):
        log.warning("Rate limit de subida excedido para %s", ip)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "lang": page_lang(request),
             "message": _t("ingest.rate_limited"), "detail": None,
             "dev_auth": False, "demo_mode": False},
            status_code=429,
        )
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

    # Subir exige sesión: no hay subidas anónimas ni enlaces de invitado.
    # Sin sesión, a /entrar (con puerta única, /entrar redirige a /acceso).
    user = auth.get_current_user(request)
    if not user:
        return RedirectResponse("/entrar", status_code=303)

    # La subida puede asignarse a un miembro de la familia: elegir uno
    # existente o crear uno inline. Un id que no pertenece a esta cuenta
    # se rechaza, nunca se reasigna en silencio.
    member_id = None
    if (nuevo_miembro or "").strip():
        member_id = dal.create_member(
            user["id"], nuevo_miembro.strip())["id"]
    elif (miembro or "").strip():
        member = dal.get_member(miembro.strip())
        if not member or member.get("owner_user_id") != user["id"]:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"request": request, "lang": page_lang(request),
                 "message": _t("member.invalid"), "detail": None,
                 "dev_auth": False, "demo_mode": False},
                status_code=400,
            )
        member_id = member["id"]

    intake_id = uuid.uuid4().hex
    with open(quarantine_path(intake_id), "wb") as f:
        f.write(contenido)

    # El intake nace con dueño: al terminar el procesamiento el documento
    # se guarda solo en su ficha. (El PDF original se sube a Storage en
    # segundo plano, sin bloquear la redirección a "procesando".)
    nuevo_intake = {
        "id": intake_id,
        "status": "processing",
        "doc_type_hint": (tipo_doc or "").strip() or None,
        "owner_user_id": user["id"],
    }
    if member_id:
        nuevo_intake["member_id"] = member_id
    dal.save_intake(nuevo_intake)
    # Log sin contenido clínico: solo tamaños y estado.
    log.info("Nuevo intake %s (%d bytes, hint=%s)",
             intake_id, len(contenido), tipo_doc or "-")
    background.add_task(_run_pipeline, intake_id, page_lang(request))
    return RedirectResponse(f"/procesando/{intake_id}", status_code=303)
