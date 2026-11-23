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
                         nombre_examen_corto, normalize_flag_key, page_lang,
                         templates, urgency_css_key)
from app.routers import accounts, ingest, results, share, vault
from app.routers.vault import _fecha_corta, _fecha_efectiva
from app.services.llm import _parse_rango

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




def _num_valor(s) -> float | None:
    """Primer número parseable del texto de un valor, o None."""
    if s is None:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _distancia_fuera(valor: float | None, ref_range) -> float | None:
    """Qué tan lejos está el valor de su rango (0.0 = en rango)."""
    lo, hi = _parse_rango(ref_range)
    if valor is None or (lo is None and hi is None):
        return None
    if lo is not None and valor < lo:
        return lo - valor
    if hi is not None and valor > hi:
        return valor - hi
    return 0.0


def _comparar_con_anterior(
    bios_actual: list, docs_previos: list
) -> tuple[int, int, str]:
    """Compara valores con el examen anterior comparable.

    Devuelve (mejoraron, empeoraron, fecha_label_del_previo).
    """
    actual = {}
    for b in bios_actual:
        nombre = str(b.get("name") or "").strip().lower()
        d = _distancia_fuera(_num_valor(b.get("value")), b.get("ref_range"))
        if nombre and d is not None:
            actual[nombre] = d
    if not actual:
        return 0, 0, ""
    for previo in docs_previos:
        pexp = previo.get("explanation") or {}
        prev = {}
        for b in pexp.get("biomarkers", []):
            nombre = str(b.get("name") or "").strip().lower()
            d = _distancia_fuera(_num_valor(b.get("value")), b.get("ref_range"))
            if nombre and d is not None and nombre in actual:
                prev[nombre] = d
        if not prev:
            continue
        mejoraron = empeoraron = 0
        for nombre, d_prev in prev.items():
            d_curr = actual[nombre]
            if d_prev > 0 and d_curr == 0:
                mejoraron += 1
            elif d_prev == 0 and d_curr > 0:
                empeoraron += 1
            elif d_prev > 0 and d_curr > 0:
                if d_curr < d_prev * 0.9:
                    mejoraron += 1
                elif d_curr > d_prev * 1.1:
                    empeoraron += 1
        iso_prev, _dt_prev = _fecha_efectiva(pexp, previo.get("created_at") or "")
        return mejoraron, empeoraron, _fecha_corta(iso_prev)
    return 0, 0, ""


def _resumen_cambio(mejoraron: int, empeoraron: int, fecha_prev: str) -> str:
    partes = []
    if mejoraron:
        partes.append(_t("main.improved_one") if mejoraron == 1
                      else _t("main.improved_many", n=mejoraron))
    if empeoraron:
        partes.append(_t("main.worsened_one") if empeoraron == 1
                      else _t("main.worsened_many", n=empeoraron))
    if not partes:
        return ""
    return _t("main.change_from", fecha=fecha_prev) + " · ".join(partes)


def _sort_by_doc_date(all_docs: list) -> list:
    """Orden: por fecha del documento (doctor/laboratorio), no de subida."""
    return sorted(
        all_docs,
        key=lambda d: _fecha_efectiva(
            d.get("explanation") or {}, d.get("created_at") or ""
        )[1] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _family_cards(members: list, all_docs: list) -> list:
    """Un dato real por familiar (cuántos documentos y el último examen),
    sin estados inventados. all_docs debe venir ya ordenado por fecha
    del documento, descendente."""
    family = []
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
    return family


def _recent_docs(all_docs: list, limite) -> list:
    """Los 10 documentos más recientes dentro del límite (180 días),
    con laboratorio, urgencia, fuera de rango y comparación con el
    examen anterior. Es la lista de /family."""
    recent = []
    for i, d in enumerate(all_docs):
        exp = d.get("explanation") or {}
        creado_raw = d.get("created_at") or ""
        fecha_iso, fecha_dt = _fecha_efectiva(exp, creado_raw)
        if fecha_dt is not None and fecha_dt < limite:
            continue
        urg = exp.get("urgency") or {}
        bios = exp.get("biomarkers", [])
        fuera = sum(
            1 for b in bios
            if normalize_flag_key(b.get("flag")) in ("alto", "bajo")
        )
        por_confirmar = bool(exp.get("uncertain_fields")) or any(
            normalize_flag_key(b.get("flag")) == "uncertain" for b in bios
        )
        compartido = bool(
            dal.get_active_share_for_intake(d.get("intake_id") or "")
        )
        mej, emp, fecha_prev = _comparar_con_anterior(bios, all_docs[i + 1:])
        recent.append(
            {
                "id": d["id"],
                "exam_about": nombre_examen_corto(exp),
                "laboratorio": exp.get("laboratorio"),
                "fecha": _fecha_corta(fecha_iso),
                "urgency_label": urg.get("label", ""),
                "urgency_key": urgency_css_key(urg.get("level", "no_urgente")),
                "fuera_de_rango": fuera,
                "por_confirmar": por_confirmar,
                "compartido": compartido,
                "cambio_resumen": _resumen_cambio(mej, emp, fecha_prev),
            }
        )
        if len(recent) >= 10:
            break
    return recent


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
        all_docs = _sort_by_doc_date(all_docs)
        # Último explicado (hero): el documento más reciente de los
        # últimos 180 días. La lista de recientes vive en /family.
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
        family = _family_cards(dal.list_members(user["id"]), all_docs)
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


@app.get("/family")
def family_page(request: Request):
    """Página de la familia: empieza con las tarjetas de un vistazo
    (las mismas del home, para dar continuidad al tocar una) y sigue
    con la lista de documentos subidos y explicados recientemente."""
    user = auth.get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    limite = datetime.now(timezone.utc) - timedelta(days=180)
    all_docs = _sort_by_doc_date(dal.list_documents(user["id"]))
    return templates.TemplateResponse(
        request,
        "family.html",
        {
            "request": request,
            "lang": page_lang(request),
            "user": user,
            "family": _family_cards(dal.list_members(user["id"]), all_docs),
            "recent_docs": _recent_docs(all_docs, limite),
            "total_docs": len(all_docs),
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
