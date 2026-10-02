"""MiFicha v0.1 — routers: helpers compartidos.

Adaptación de contextos a lo que consumen las plantillas (ver notas del
frontend builder):
- urgency.level de la plantilla es la CLAVE DE COLOR (verde/amarilla/naranja/
  roja); el dict canónico de Explanation guarda el nivel semántico
  (no_urgente/…/buscar_ayuda_ya) y aquí se mapea solo para render.
- Cada biomarcador lleva `flag_key` normalizado (alto/bajo/rango/uncertain)
  para la clase CSS `flag-{{ flag_key }}`.
- history (vault_detail) es una LISTA de {biomarker_name, entries:[...]}
  porque la plantilla itera `{% for h in history %}` y usa `h.entries`
  (no `.values`: Jinja2 lo resolvería al método del dict).
- uncertain_fields lleva key/label/guessed_value/hint para el formulario
  de corrección de result.html.
- narrative_findings expone además heading/simple_explanation (alias de la
  plantilla); ordered_tests une usual_objectives en un string.
"""

import copy
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime

from fastapi.templating import Jinja2Templates

from app import config, schemas
from app.services import llm as _llm
from app.services import share as share_svc

TEMPLATES_DIR = os.path.join(config.BASE_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Nivel semántico -> clave de color CSS (ver static/app.css)
URGENCY_CSS = {
    "no_urgente": "verde",
    "agendar_cita": "amarilla",
    "hablar_hoy_con_profesional": "naranja",
    "buscar_ayuda_ya": "roja",
}

_FLAG_MAP = {
    "alto": "alto", "alta": "alto", "high": "alto",
    "elevado": "alto", "elevada": "alto",
    "bajo": "bajo", "baja": "bajo", "low": "bajo",
    "disminuido": "bajo", "disminuida": "bajo",
    "normal": "rango", "rango": "rango", "en rango": "rango", "ok": "rango",
    "por confirmar": "uncertain", "uncertain": "uncertain",
    "incierto": "uncertain", "incierta": "uncertain",
}

# Prioridad en "Valores que necesitan atención":
# 1) marcas críticas del laboratorio, 2) prioridad clínica del LLM,
# 3) desviación del rango como desempate.
_SEVERITY_RANK = {"critica": 0, "grave": 1, "moderada": 2, "leve": 3}
_CLIN_RANK = {"alta": 0, "media": 1, "baja": 2}
_MAX_CONCERNS = 5


def _severity_for(b: dict) -> str:
    """Severidad de un biomarcador para ordenar 'Valores que necesitan atención'.

    Usa la que trae el dict (ruta demo) o la deriva de valor + rango cuando
    falta (ruta Gemini: el LLM no la devuelve)."""
    sev = str(b.get("severity") or "").strip().lower()
    if sev in _SEVERITY_RANK:
        return sev
    valor_txt = str(b.get("value") or "")
    if re.search(r"\d\s*[!*]", valor_txt):
        return "critica"
    valor = _llm._num(re.sub(r"[!*†#]+$", "", valor_txt.strip()))
    lo, hi = _llm._parse_rango(str(b.get("ref_range") or ""))
    return _llm._severidad_por_desviacion(valor, lo, hi)


def normalize_flag_key(flag) -> str:
    return _FLAG_MAP.get(str(flag or "").strip().lower(), "")


def urgency_css_key(semantic_level: str) -> str:
    return URGENCY_CSS.get(semantic_level, "amarilla")


def nombre_examen_corto(exp: dict) -> str:
    """Título corto para tarjetas: qué examen es, en pocas palabras.

    1. `nombre_examen` del LLM (documentos nuevos).
    2. `resumen_que_es` truncado a ~60 caracteres, sin el "Este es un…"
       inicial (documentos sin el campo nuevo).
    3. `type_label` genérico (documentos viejos).
    """
    nombre = str((exp or {}).get("nombre_examen") or "").strip()
    if nombre:
        return nombre
    que_es = " ".join(str((exp or {}).get("resumen_que_es") or "").split())
    if que_es:
        bajo = que_es.lower()
        for pref in (
            "este es un ", "este es una ", "esta es un ", "esta es una ",
            "este documento es un ", "este documento es una ",
            "es un ", "es una ",
            "se trata de un ", "se trata de una ",
        ):
            if bajo.startswith(pref):
                que_es = que_es[len(pref):].lstrip()
                break
        if que_es:
            que_es = que_es[0].upper() + que_es[1:]
        if len(que_es) <= 60:
            return que_es
        corte = que_es[:60].rsplit(" ", 1)[0] or que_es[:60]
        return corte + "…"
    return str((exp or {}).get("type_label") or "Documento").strip() or "Documento"


def _humanize_field(field: str) -> str:
    f = field or ""
    m = re.match(r"pages\[(\d+)\]\.text", f)
    if m:
        return f"Texto de la página {m.group(1)}"
    m = re.match(r"biomarkers\[(.+?)\]\.ref_range", f)
    if m:
        return f"Rango de referencia de {m.group(1)}"
    if f == "biomarkers":
        return "Valores del laboratorio"
    if f == "ordered_tests":
        return "Exámenes pedidos"
    if f == "narrative_findings":
        return "Hallazgos del informe"
    return f.replace("_", " ").replace("[", " ").replace("]", "").strip().capitalize()


def render_explanation(exp: dict) -> dict:
    """Copia del Explanation dict adaptada a las plantillas Jinja2."""
    e = copy.deepcopy(exp or {})

    # Documentos ya guardados pueden traer el mismo análisis repetido con
    # otro uso de mayúsculas («CREATININA» vs «Creatinina»): se muestra una
    # sola vez, con los datos más completos.
    e["biomarkers"] = schemas.dedupe_biomarkers(e.get("biomarkers"))

    urg = e.get("urgency") or {}
    nivel = urg.get("level", "no_urgente")
    urg["level"] = urgency_css_key(nivel)
    urg["semantic_level"] = nivel
    e["urgency"] = urg

    for b in e.get("biomarkers", []):
        # Formato uniforme: nunca todo en mayúsculas y siempre igual.
        b["name"] = schemas.nombre_display(b.get("name"))
        b["flag_key"] = normalize_flag_key(b.get("flag"))
        b["flag_uncertain"] = b["flag_key"] == "uncertain"

    # "Valores que necesitan atención": los más urgentes primero y como
    # máximo _MAX_CONCERNS en la lista; si hay más, la plantilla avisa
    # cuántos otros también requieren atención.
    concerns = [b for b in e.get("biomarkers", [])
                if b.get("flag_key") in ("alto", "bajo")]

    def _orden(b):
        sev = _severity_for(b)
        clin = str(b.get("prioridad_clinica") or "media").strip().lower()
        return (
            0 if sev == "critica" else 1,          # marcas del laboratorio
            _CLIN_RANK.get(clin, 1),                # prioridad clínica (LLM)
            _SEVERITY_RANK.get(sev, 3),             # desviación del rango
        )

    concerns.sort(key=_orden)
    e["concerns_top"] = concerns[:_MAX_CONCERNS]
    extra = max(0, len(concerns) - _MAX_CONCERNS)
    e["concerns_extra"] = extra
    e["concerns_extra_note"] = (
        f"Además hay {extra} valor{'es' if extra > 1 else ''} más fuera de "
        f"rango que también requiere{'n' if extra > 1 else ''} tu atención: "
        f"está{'n' if extra > 1 else ''} en «Valores de tu examen», más abajo."
        if extra else None
    )

    for f in e.get("narrative_findings", []):
        f.setdefault("heading", f.get("finding", ""))
        f.setdefault("simple_explanation", f.get("plain_explanation", ""))

    for t in e.get("ordered_tests", []):
        uo = t.get("usual_objectives")
        if isinstance(uo, list):
            t["usual_objectives"] = "; ".join(uo)

    for i, uf in enumerate(e.get("uncertain_fields", []), start=1):
        uf["key"] = uf.get("key") or f"campo_{i}"
        uf["label"] = uf.get("label") or _humanize_field(uf.get("field", ""))
        uf.setdefault("guessed_value", "")
        uf["hint"] = uf.get("hint") or uf.get("reason", "")

    return e


# --- tokens de invitado -----------------------------------------------------

def new_guest_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def guest_token_valid(intake: dict | None, token: str | None) -> bool:
    if not intake or not token:
        return False
    esperado = intake.get("guest_token_hash")
    if not esperado:
        return False
    return hmac.compare_digest(esperado, hash_token(token))


def can_view_intake(intake: dict | None, token: str | None, user: dict | None) -> bool:
    """Acceso a un intake: token de invitado válido o dueño en sesión."""
    if not intake:
        return False
    if guest_token_valid(intake, token):
        return True
    if user and intake.get("owner_user_id") == user["id"]:
        return True
    return False


def share_ctx(share_row: dict | None) -> dict | None:
    """Contexto `share` para las plantillas (las 4 claves + extras)."""
    if not share_row:
        return None
    snap = share_row.get("snapshot") or {}
    token = share_row.get("token", "")
    incluir_urgencia = bool(snap.get("incluir_urgencia"))
    return {
        "url": share_svc.share_url(token),
        "text_encoded": share_svc.whatsapp_text(token, incluir_urgencia),
        "include_urgency": incluir_urgencia,
        "urgency_level": urgency_css_key(snap.get("urgency_level", "no_urgente"))
        if incluir_urgencia else "",
        "summary_simple": snap.get("summary_simple", ""),
        "urgency_label": snap.get("urgency_label", ""),
        "action": snap.get("urgency_action", ""),
        "expires_label": share_svc.format_expires_label(
            datetime.fromisoformat(share_row["expires_at"])
        ),
    }
