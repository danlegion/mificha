"""MiFicha v0.1 — ficha (vault): lista, detalle, historial y PDF original."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import auth, config, file_store, schemas
from app.db import dal
from app.i18n import MESES_CORTO, get_current_lang, t as _t
from app.routers import (key_values_with_position, member_color,
                         nombre_examen_corto, normalize_flag_key, page_lang,
                         render_explanation, share_ctx, templates,
                         urgency_css_key)
from app.services.vaccines import collect_vaccines, group_vaccines_by_member
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
    """Fecha compacta para tarjetas: '25 sep 2026' / '25 Sep 2026'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    meses = MESES_CORTO.get(get_current_lang(), MESES_CORTO["es"])
    return f"{dt.day} {meses[dt.month - 1]} {dt.year}"


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
    return {"alto": _t("flag.alto"), "bajo": _t("flag.bajo"),
            "rango": _t("flag.rango"),
            "uncertain": _t("flag.uncertain")}.get(
                key, str(flag or "").capitalize() or _t("flag.dash"))


def _require_user(request: Request) -> dict:
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Inicia sesión")
    return user


def _base_ctx(request: Request, user) -> dict:
    return {
        "request": request,
        "lang": page_lang(request),
        "user": user,
        "demo_mode": DEMO_MODE,
        "dev_auth": auth.DEV_AUTH,
    }


@router.get("/ficha")
def ficha(request: Request, miembro: str = ""):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    members = dal.list_members(user["id"])
    by_id = {m["id"]: m["name"] for m in members}
    # Filter by member when requested; an unknown id shows everything
    # rather than an empty vault, so a stale link never hides files.
    selected = miembro.strip() if miembro.strip() in by_id else ""
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
    color_by_member = {}
    for i, m in enumerate(members):
        bg, fg = member_color(i)
        color_by_member[m["id"]] = {"bg": bg, "fg": fg, "dot": fg}
    for d in crudos:
        exp = d.get("explanation") or {}
        urg = exp.get("urgency") or {}
        fecha_iso, _fecha_dt = _fecha_efectiva(exp, d.get("created_at") or "")
        bios = exp.get("biomarkers", [])
        fuera = sum(
            1 for b in bios
            if normalize_flag_key(b.get("flag")) in ("alto", "bajo")
        )
        por_confirmar = bool(exp.get("uncertain_fields")) or any(
            normalize_flag_key(b.get("flag")) == "uncertain" for b in bios
        )
        key_metric = None
        if bios:
            b0 = bios[0]
            key_metric = {
                "name": b0.get("name") or "Valor",
                "value": f"{b0.get('value', '')} {(b0.get('unit') or '').strip()}".strip(),
            }
        # Título corto: qué examen es (no el resultado ni el tipo genérico).
        docs.append(
            {
                "id": d["id"],
                "date_label": _fecha_corta(fecha_iso),
                "exam_about": nombre_examen_corto(exp),
                "laboratorio": exp.get("laboratorio") or "",
                "summary": exp.get("summary_simple") or "",
                "doc_type": exp.get("document_type") or "",
                "urgency_label": urg.get("label", ""),
                "urgency_key": urgency_css_key(urg.get("level", "no_urgente")),
                "fuera": fuera,
                "por_confirmar": por_confirmar,
                "key_metric": key_metric,
                "member_id": d.get("member_id"),
                "member_name": by_id.get(d.get("member_id") or ""),
                "member_color": color_by_member.get(d.get("member_id") or "", {}),
            }
        )
    # Group by member, members first in creation order, unassigned last.
    groups = []
    for m in members:
        group_docs = [d for d in docs if d["member_id"] == m["id"]]
        if not selected or selected == m["id"]:
            groups.append({"member_id": m["id"], "member_name": m["name"],
                           "docs": group_docs})
    unassigned = [d for d in docs if not d.get("member_id")]
    if not selected:
        groups.append({"member_id": "", "member_name": "",
                       "docs": unassigned})
    exams_total = len(docs)
    if selected:
        docs = [d for d in docs if d["member_id"] == selected]
    return templates.TemplateResponse(
        request,
        "vault.html",
        {**_base_ctx(request, user), "docs": docs, "groups": groups,
         "members": members, "selected_member": selected,
         "exams_count": exams_total,
         "vaccines_count": len(collect_vaccines(crudos))})


@router.get("/vacunas")
def vacunas(request: Request, miembro: str = ""):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    members = dal.list_members(user["id"])
    by_id = {m["id"]: m["name"] for m in members}
    selected = miembro.strip() if miembro.strip() in by_id else ""
    all_docs = dal.list_documents(user["id"])
    groups = group_vaccines_by_member(all_docs, members)
    for i, g in enumerate(groups):
        bg, fg = member_color(i)
        g["color_bg"] = bg
        g["color_fg"] = fg
    if selected:
        groups = [g for g in groups if g["member_id"] == selected]
    return templates.TemplateResponse(
        request,
        "vaccines.html",
        {**_base_ctx(request, user), "groups": groups,
         "members": members, "selected_member": selected,
         "exams_count": len(all_docs),
         "vaccines_count": len(collect_vaccines(all_docs))})


@router.post("/ficha/miembros")
def crear_miembro(request: Request, nombre: str = Form(default="")):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    if (nombre or "").strip():
        member = dal.create_member(user["id"], nombre.strip())
        file_store.save_member_file(member, owner_email=user.get("email"))
    return RedirectResponse("/ficha", status_code=303)


@router.post("/ficha/{doc_id}/miembro")
def cambiar_miembro(doc_id: str, request: Request,
                    miembro: str = Form(default="")):
    try:
        user = _require_user(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    doc = dal.get_document(doc_id)
    if not doc or doc.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="No encontrado")
    member_id = (miembro or "").strip() or None
    if member_id:
        member = dal.get_member(member_id)
        if not member or member.get("owner_user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="No encontrado")
    old_member_id = doc.get("member_id")
    dal.save_document({**doc, "member_id": member_id})
    # La carpeta del documento se mueve con él: los archivos siguen a la
    # persona, y el CSV de biomarcadores de ambas se reconstruye.
    file_store.move_document(doc_id, old_member_id, member_id)
    return RedirectResponse(f"/ficha/{doc_id}", status_code=303)


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
    members = dal.list_members(user["id"])
    by_id = {m["id"]: m["name"] for m in members}
    return templates.TemplateResponse(
        request,
        "vault_detail.html",
        {
            **_base_ctx(request, user),
            "exp": exp,
            "exam_name": nombre_examen_corto(doc.get("explanation") or {}),
            "key_values": key_values_with_position(doc.get("explanation") or {}),
            "doc": {
                "id": doc["id"],
                "original_url": f"/ficha/{doc['id']}/original",
                "download_url": f"/ficha/{doc['id']}/original?descargar=1",
                "original_available": original_exists(doc.get("original_key") or ""),
                "member_id": doc.get("member_id"),
                "member_name": by_id.get(doc.get("member_id") or ""),
                "date_label": _fecha_corta(
                    _fecha_efectiva(doc.get("explanation") or {},
                                    doc.get("created_at") or "")[0]),
                "laboratorio": (doc.get("explanation") or {}).get("laboratorio") or "",
            },
            "members": members,
            "history": _historial(user["id"], doc_id,
                                  member_id=doc.get("member_id")),
            "intake_id": intake_id,
            "share": share_ctx(share_row),
        },
    )


def _mejor_nombre_display(actual: str, nuevo: str) -> str:
    """Nombre a mostrar para un análisis: prefiere el que no sea todo
    mayúsculas ni todo minúsculas («Creatinina» sobre «CREATININA»); en
    empate, se mantiene el primero para no mover la fila de sitio."""
    def _plano(n: str) -> bool:
        return n.isupper() or n.islower()

    if _plano(actual) and not _plano(nuevo):
        return nuevo
    return actual


def _agrupar_por_analisis(items):
    """Agrupa [(nombre, entrada)] por análisis, ignorando mayúsculas, acentos
    y espacios («CREATININA» y «Creatinina» van en la misma fila del
    historial). Devuelve [(nombre_display, [entradas])] en orden de
    aparición."""
    grupos = {}
    for nombre, entrada in items:
        clave = schemas.clave_biomarcador(nombre) or nombre
        if clave not in grupos:
            grupos[clave] = {"nombre": nombre, "entradas": []}
        else:
            g = grupos[clave]
            g["nombre"] = _mejor_nombre_display(g["nombre"], nombre)
        grupos[clave]["entradas"].append(entrada)
    # Formato uniforme también aquí («CREATININA» -> «Creatinina»).
    return [
        (schemas.nombre_display(g["nombre"]), g["entradas"])
        for g in grupos.values()
    ]


def _historial(user_id: str, doc_actual_id: str,
               member_id: str | None = None) -> list:
    """[{biomarker_name, entries:[{date_label,value,unit,flag_label,is_current}]}]

    NOTA: es una lista (no un dict), porque la plantilla itera
    `{% for h in history %}` y usa `h.entries`.
    """
    docs = dal.list_documents(user_id)
    # History stays within one person: a member's values never mix with
    # another member's, and unassigned docs only compare with unassigned.
    if member_id:
        docs = [d for d in docs if d.get("member_id") == member_id]
    else:
        docs = [d for d in docs if not d.get("member_id")]
    items = []
    for d in docs:
        exp = d.get("explanation") or {}
        fecha_iso, fecha_dt = _fecha_efectiva(exp, d.get("created_at") or "")
        fecha = _fecha_corta(fecha_iso)
        for b in exp.get("biomarkers", []):
            items.append(
                (b.get("name") or "Valor", {
                    "date_label": fecha,
                    "value": b.get("value", ""),
                    "unit": b.get("unit") or "",
                    "flag_label": _flag_label(b.get("flag")),
                    "is_current": d["id"] == doc_actual_id,
                    "_dt": fecha_dt,
                })
            )
    # Orden de cada historial: por fecha del examen, más reciente primero.
    resultado = []
    for nombre, entradas in _agrupar_por_analisis(items):
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
    file_store.delete_document_files(doc.get("member_id"), doc_id)
    delete_original(doc.get("original_key") or "")
    dal.revoke_shares_for_intake(intake_id)
    dal.delete_document(doc_id)
    dal.delete_intake(intake_id)
    return RedirectResponse("/ficha", status_code=303)
