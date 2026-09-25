"""MiFicha v0.1 — ficha (vault): lista, detalle, historial y PDF original."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import auth, config
from app.db import dal
from app.routers import normalize_flag_key, render_explanation, templates, urgency_css_key
from app.storage import get_original_path

router = APIRouter()

DEMO_MODE = not bool(config.ANTHROPIC_API_KEY)

_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_corta(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{dt.day} de {_MESES[dt.month - 1]} de {dt.year}"


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
    for d in dal.list_documents(user["id"]):
        exp = d.get("explanation") or {}
        urg = exp.get("urgency") or {}
        docs.append(
            {
                "id": d["id"],
                "date_label": _fecha_corta(d.get("created_at")),
                "type_label": exp.get("type_label", "Documento"),
                "summary_short": (exp.get("summary_simple") or "")[:140],
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
    return templates.TemplateResponse(
        request,
        "vault_detail.html",
        {
            **_base_ctx(request, user),
            "exp": exp,
            "doc": {
                "id": doc["id"],
                "original_url": f"/ficha/{doc['id']}/original",
            },
            "history": _historial(user["id"], doc_id),
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
        fecha = _fecha_corta(d.get("created_at"))
        for b in exp.get("biomarkers", []):
            nombre = b.get("name") or "Valor"
            por_nombre.setdefault(nombre, []).append(
                {
                    "date_label": fecha,
                    "value": b.get("value", ""),
                    "unit": b.get("unit") or "",
                    "flag_label": _flag_label(b.get("flag")),
                    "is_current": d["id"] == doc_actual_id,
                }
            )
    return [
        {"biomarker_name": nombre, "entries": entradas}
        for nombre, entradas in por_nombre.items()
    ]


@router.get("/ficha/{doc_id}/original")
def ficha_original(doc_id: str, request: Request):
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
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="documento.pdf"'},
    )
