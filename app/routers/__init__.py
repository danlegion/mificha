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

from fastapi.templating import Jinja2Templates

from app import config
from app.services import llm as _llm

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

# Prioridad en "Valores que necesitan atención": los más urgentes primero.
_SEVERITY_RANK = {"critica": 0, "grave": 1, "moderada": 2, "leve": 3}
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

    urg = e.get("urgency") or {}
    nivel = urg.get("level", "no_urgente")
    urg["level"] = urgency_css_key(nivel)
    urg["semantic_level"] = nivel
    e["urgency"] = urg

    for b in e.get("biomarkers", []):
        b["flag_key"] = normalize_flag_key(b.get("flag"))
        b["flag_uncertain"] = b["flag_key"] == "uncertain"

    # "Valores que necesitan atención": los más urgentes primero y como
    # máximo _MAX_CONCERNS en la lista; si hay más, la plantilla avisa
    # cuántos otros también requieren atención.
    concerns = [b for b in e.get("biomarkers", [])
                if b.get("flag_key") in ("alto", "bajo")]
    concerns.sort(key=lambda b: _SEVERITY_RANK[_severity_for(b)])
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
