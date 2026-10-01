"""MiFicha v0.1 — ficha (vault): lista, detalle, historial y PDF original."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import (nombre_examen_corto, normalize_flag_key,
                         render_explanation, share_ctx, templates,
                         urgency_css_key)
from app.storage import delete_original, get_original_path, original_exists

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)

_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


_MESES_CORTO = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def _fecha_corta(iso: str | None) -> str:
    """Fecha compacta para tarjetas: '25 sep 2026'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{dt.day} {_MESES_CORTO[dt.month - 1]} {dt.year}"


def _fecha_efectiva(exp: dict, creado_raw: str) -> tuple[str, datetime | None]:
    """Fecha que representa al documento: la del doctor/laboratorio si existe,
    si no la de subida. Devuelve (iso_para_mostrar, datetime_para_ordenar)."""
    fd = str((exp or {}).get("fecha_documento") or "").strip()
    dt = None
    iso = creado_raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fd):
        try:
            dt = datetime.strptime(fd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            iso = fd
        except ValueError:
            dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(creado_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
    return iso, dt


def _flag_label(flag) -> str:
    key = normalize_flag_key(flag)
    return {"alto": "Alto", "bajo": "Bajo", "rango": "Normal",
            "uncertain": "Por confirmar"}.get(key, str(flag or "").capitalize() or "—")


def _require_user(request: Request) -> dict:
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Inicia sesión")
    return user


def _base_ctx(request: Request, user) -> dict:
    return {
        "request": request,
        "user": user,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
    }


@router.get("/ficha")
def ficha(request: Request):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    docs = []
    crudos = dal.list_documents(user["id"])
    # Orden: por fecha del examen (doctor/laboratorio), no de subida.
    crudos = sorted(
        crudos,
        key=lambda d: _fecha_efectiva(
            d.get("explanation") or {}, d.get("created_at") or ""
        )[1] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for d in crudos:
        exp = d.get("explanation") or {}
        urg = exp.get("urgency") or {}
        fecha_iso, _fecha_dt = _fecha_efectiva(exp, d.get("created_at") or "")
        # Título corto: qué examen es (no el resultado ni el tipo genérico).
        docs.append(
            {
                "id": d["id"],
                "date_label": _fecha_corta(fecha_iso),
                "exam_about": nombre_examen_corto(exp),
                "urgency_label": urg.get("label", ""),
                "urgency_key": urgency_css_key(urg.get("level", "no_urgente")),
            }
        )
    return templates.TemplateResponse(
        request,
        "vault.html", {**_base_ctx(request, user), "docs": docs})


@router.get("/ficha/{doc_id}")
def ficha_detalle(doc_id: str, request: Request):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    doc = dal.get_document(doc_id)
    if not doc or doc.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="No encontrado")
    exp = render_explanation(doc.get("explanation") or {})
    intake_id = doc.get("intake_id") or doc["id"]
    share_row = dal.get_active_share_for_intake(intake_id)
    return templates.TemplateResponse(
        request,
        "vault_detail.html",
        {
            **_base_ctx(request, user),
            "exp": exp,
            "doc": {
                "id": doc["id"],
                "original_url": f"/ficha/{doc['id']}/original",
                "download_url": f"/ficha/{doc['id']}/original?descargar=1",
                "original_available": original_exists(doc.get("original_key") or ""),
            },
            "history": _historial(user["id"], doc_id),
            "intake_id": intake_id,
            "share": share_ctx(share_row),
        },
    )


def _historial(user_id: str, doc_actual_id: str) -> list:
    """[{biomarker_name, entries:[{date_label,value,unit,flag_label,is_current}]}]

    NOTA: es una lista (no un dict), porque la plantilla itera
    `{% for h in history %}` y usa `h.entries`.
    """
    docs = dal.list_documents(user_id)
    por_nombre: dict[str, list] = {}
    for d in docs:
        exp = d.get("explanation") or {}
        fecha_iso, fecha_dt = _fecha_efectiva(exp, d.get("created_at") or "")
        fecha = _fecha_corta(fecha_iso)
        for b in exp.get("biomarkers", []):
            nombre = b.get("name") or "Valor"
            por_nombre.setdefault(nombre, []).append(
                {
                    "date_label": fecha,
                    "value": b.get("value", ""),
                    "unit": b.get("unit") or "",
                    "flag_label": _flag_label(b.get("flag")),
                    "is_current": d["id"] == doc_actual_id,
                    "_dt": fecha_dt,
                }
            )
    # Orden de cada historial: por fecha del examen, más reciente primero.
    resultado = []
    for nombre, entradas in por_nombre.items():
        entradas.sort(
            key=lambda e: e["_dt"]
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for e in entradas:
            e.pop("_dt", None)
        resultado.append({"biomarker_name": nombre, "entries": entradas})
    return resultado


@router.get("/ficha/{doc_id}/original")
def ficha_original(doc_id: str, request: Request, descargar: bool = False):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    doc = dal.get_document(doc_id)
    if not doc or doc.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="No encontrado")
    path = get_original_path(doc.get("original_key") or "")
    if not path:
        raise HTTPException(status_code=404, detail="Original no disponible")
    modo = "attachment" if descargar else "inline"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{modo}; filename="documento.pdf"'},
    )


@router.post("/ficha/{doc_id}/eliminar")
def ficha_eliminar(doc_id: str, request: Request):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    doc = dal.get_document(doc_id)
    if not doc or doc.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="No encontrado")
    intake_id = doc.get("intake_id") or doc_id
    delete_original(doc.get("original_key") or "")
    dal.revoke_shares_for_intake(intake_id)
    dal.delete_document(doc_id)
    dal.delete_intake(intake_id)
    return RedirectResponse("/ficha", status_code=303)
