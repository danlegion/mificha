"""MiFicha v0.1 — orquestación: extract → (ocr) → llm → validar → urgencia.

Contrato:
    process_intake(intake_id: str, pdf_bytes: bytes,
                   doc_type_hint: str | None) -> dict

Devuelve el Explanation dict validado. Si no se extrajo nada útil, lanza
UnprocessableDocument con un mensaje en español para el usuario.
"""

import logging
import re

from app import schemas
from app.services import pdf_extract
from app.services.llm import get_llm_provider
from app.services.ocr import get_ocr_provider
from app.services.urgency import decide_urgency

log = logging.getLogger("mificha.pipeline")


class UnprocessableDocument(Exception):
    """El documento no se pudo procesar (mensaje en español para el usuario)."""


# Palabras que en un informe sugieren una situación de emergencia.
_EMERGENCIA = [
    "emergencia", "urgente", "urgencia", "código azul", "código rojo",
    "infarto", "derrame cerebral", "hemorragia", "shock séptico", "sepsis",
    "paro card", "disección aórtica", "hallazgo crítico",
    "requiere atención inmediata",
]

_NIVEL_RANK = {
    "no_urgente": 0,
    "agendar_cita": 1,
    "hablar_hoy_con_profesional": 2,
    "buscar_ayuda_ya": 3,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").lower())


def _derivar_senales(exp: dict, extracted: dict) -> dict:
    """Construye las señales de urgencia desde la explicación validada."""
    texto = extracted.get("full_text") or ""
    abnormal = []
    critico = False
    for b in exp.get("biomarkers", []):
        flag = str(b.get("flag") or "").lower()
        if flag in ("alto", "alta", "bajo", "baja", "high", "low"):
            if "crít" in flag or "crit" in flag:
                sev = "critica"
            elif any(k in flag for k in ["muy", "severo", "severa", "grave"]):
                sev = "grave"
            else:
                sev = "leve"
            abnormal.append(
                {"name": b.get("name", "valor"), "flag": flag, "severity": sev}
            )
        if re.search(r"cr[ií]tico|p[áa]nico", flag):
            critico = True
        if re.search(r"[!*]", str(b.get("value") or "")):
            critico = True

    emergencia = any(k in texto.lower() for k in _EMERGENCIA)

    # Dato crítico incierto: páginas escaneadas sin OCR en un posible lab.
    escaneadas = extracted.get("scanned_pages") or []
    tipo = exp.get("document_type")
    incierto_critico = bool(escaneadas) and tipo in (
        "lab_result", "unknown", None,
    )

    prioridad = None
    if tipo == "exam_order":
        m = re.search(
            r"(?i)\b(prioritari[oa]|urgente|preferente|rutina)\b[^.\n]{0,60}",
            texto,
        )
        if m:
            prioridad = m.group(0).strip()  # se reproduce, no se inventa

    return {
        "abnormal": abnormal,
        "has_critical_flag": critico,
        "has_uncertain_critical": incierto_critico,
        "narrative_emergency_language": emergencia,
        "explicit_priority": prioridad,
        "doc_type": tipo,
    }


def _aplicar_reglas_duras(exp: dict, extracted: dict) -> dict:
    """Post-validación: rangos solo del documento + fusión de urgencia."""
    texto_norm = _norm(extracted.get("full_text"))
    biomarcadores_ok = []
    for i, b in enumerate(exp.get("biomarkers", [])):
        rango = b.get("ref_range")
        if rango and _norm(rango) not in texto_norm:
            # El rango no está en el documento: no se puede confiar.
            exp.setdefault("uncertain_fields", []).append(
                {
                    "field": f"biomarkers[{i}].ref_range",
                    "reason": (
                        f"El rango de referencia de «{b.get('name')}» no se "
                        "encontró en el documento, así que no se incluye."
                    ),
                }
            )
            continue
        biomarcadores_ok.append(b)
    exp["biomarkers"] = biomarcadores_ok

    # Urgencia: la regla conservadora nunca baja lo que el LLM marcó.
    regla = decide_urgency(_derivar_senales(exp, extracted))
    nivel_llm = (exp.get("urgency") or {}).get("level", "no_urgente")
    if _NIVEL_RANK.get(regla["level"], 0) >= _NIVEL_RANK.get(nivel_llm, 0):
        exp["urgency"] = regla
    return exp


def process_intake(
    intake_id: str, pdf_bytes: bytes, doc_type_hint: str | None
) -> dict:
    log.info("Procesando intake %s (hint=%s)", intake_id, doc_type_hint)

    extracted = pdf_extract.extract_document(pdf_bytes)
    texto = (extracted.get("full_text") or "").strip()
    if extracted.get("num_pages", 0) == 0 or not texto:
        if extracted.get("scanned_pages"):
            raise UnprocessableDocument(
                "Detectamos que tu documento es una foto o un escaneo, y aún "
                "no podemos leer ese formato: estamos trabajando en agregarlo. "
                "Por ahora, sube el PDF original del laboratorio para obtener "
                "tu explicación."
            )
        raise UnprocessableDocument(
            "No pudimos leer ningún texto de tu documento. "
            "Revisa que el archivo sea un PDF válido o intenta con otro archivo."
        )

    # OCR: las páginas escaneadas se marcan como inciertas, nunca se ignoran.
    # En v0.1 no hay proveedor OCR configurado (NullOCRProvider): el LLM
    # recibe la nota y esas páginas terminan en uncertain_fields.
    if extracted.get("scanned_pages"):
        proveedor = get_ocr_provider()
        if proveedor.name == "null":
            log.info(
                "Páginas escaneadas sin OCR (%s): se marcan como inciertas",
                extracted["scanned_pages"],
            )
            extracted["ocr_unavailable"] = (
                "OCR vendor not configured — set OCR_PROVIDER"
            )
        else:
            # Proveedor real: aquí se renderizaría cada página escaneada a
            # imagen y se llamaría a proveedor.extract_text(imagen_bytes).
            try:
                for n in extracted["scanned_pages"]:
                    raise NotImplementedError(
                        f"OCR page render no implementado para {proveedor.name}"
                    )
            except Exception as exc:  # noqa: BLE001 - el OCR no tumba el flujo
                log.warning("OCR falló en páginas escaneadas: %s", exc)
                extracted["ocr_unavailable"] = str(exc)

    extracted["doc_type_hint"] = doc_type_hint
    proveedor_llm = get_llm_provider()
    crudo = proveedor_llm.explain(extracted)
    crudo["demo_mode"] = bool(proveedor_llm.is_demo)

    try:
        exp = schemas.validate_explanation(crudo)
    except Exception as exc:
        log.warning("La explicación del LLM no validó: %s", exc)
        raise UnprocessableDocument(
            "No pudimos entender tu documento con claridad. "
            "Intenta con otro archivo o revisa que el PDF se lea bien."
        ) from exc

    exp = _aplicar_reglas_duras(exp, extracted)

    # Páginas escaneadas: se informa al usuario en todos los motores.
    # (DemoExplainer ya las agrega; aquí se cubren Gemini/Anthropic.)
    for n in extracted.get("scanned_pages", []):
        campo = f"pages[{n}].text"
        ya = any(f.get("field") == campo for f in exp.get("uncertain_fields", []))
        if not ya:
            exp.setdefault("uncertain_fields", []).append(
                {
                    "field": campo,
                    "reason": (
                        f"La página {n} parece ser una foto o un escaneo y no "
                        "pudimos leer su texto. Estamos trabajando en poder "
                        "leer este tipo de documentos; por ahora su contenido "
                        "no se incluyó en esta explicación."
                    ),
                }
            )

    log.info(
        "Intake %s listo: tipo=%s urgencia=%s",
        intake_id, exp.get("document_type"), exp.get("urgency", {}).get("level"),
    )
    return exp
