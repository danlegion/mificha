"""MiFicha v0.1 — orquestación: extract → (ocr) → llm → validar → urgencia.

Contrato:
    process_intake(intake_id: str, pdf_bytes: bytes,
                   doc_type_hint: str | None) -> dict

Devuelve el Explanation dict validado. Si no se extrajo nada útil, lanza
UnprocessableDocument con un mensaje en español para el usuario.
"""

import logging
import os
import re
from datetime import datetime

from app import schemas
from app.i18n import get_current_lang, set_current_lang, t as _t
from app.services import pdf_extract
from app.services.llm import get_llm_provider
from app.services.ocr import get_ocr_provider
from app.services.urgency import decide_urgency

log = logging.getLogger("mificha.pipeline")


class UnprocessableDocument(Exception):
    """El documento no se pudo procesar (mensaje en español para el usuario)."""


# OCR: límites para no saturar la instancia gratuita de Render.
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "12"))
OCR_DPI = int(os.getenv("OCR_DPI", "200"))
OCR_MIN_CHARS = 20  # igual que pdf_extract.MIN_CHARS_FOR_TEXT_PAGE


def _intentar_ocr(extracted: dict, pdf_bytes: bytes, lang: str) -> None:
    """Pasa OCR por las páginas escaneadas e inyecta el texto en `extracted`.

    Las páginas cuyo OCR devuelve texto útil salen de `scanned_pages` y entran
    en `ocr_pages`. Las que fallan quedan en `scanned_pages`. Nunca tumba el
    flujo: los fallos se registran y la página sigue marcada como incierta.
    """
    try:
        proveedor = get_ocr_provider()
    except Exception as exc:  # noqa: BLE001 - OCR mal configurado no tumba el flujo
        log.warning("OCR no disponible: %s", exc)
        extracted["ocr_unavailable"] = str(exc)
        return
    if proveedor.name == "null":
        log.info(
            "Páginas escaneadas sin OCR (%s): se marcan como inciertas",
            extracted["scanned_pages"],
        )
        extracted["ocr_unavailable"] = "OCR vendor not configured — set OCR_PROVIDER"
        return

    import fitz

    pendientes = list(extracted.get("scanned_pages") or [])[:OCR_MAX_PAGES]
    if len(extracted.get("scanned_pages") or []) > OCR_MAX_PAGES:
        log.warning(
            "OCR limitado a %d de %d páginas escaneadas",
            OCR_MAX_PAGES, len(extracted["scanned_pages"]),
        )
    ocr_pages = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for numero in pendientes:
            try:
                pagina = doc[numero - 1]
                pix = pagina.get_pixmap(dpi=OCR_DPI)
                imagen = pix.tobytes("png")
                texto = proveedor.extract_text(imagen).strip()
            except Exception as exc:  # noqa: BLE001 - una página no tumba el flujo
                log.warning("OCR falló en la página %d: %s", numero, exc)
                continue
            if len(texto) < OCR_MIN_CHARS:
                log.info("OCR sin texto útil en la página %d", numero)
                continue
            for p in extracted["pages"]:
                if p.get("page_number") == numero:
                    p["text"] = texto
                    p["has_text"] = True
                    p["is_scanned"] = False
                    p["ocr_text"] = True
                    break
            ocr_pages.append(numero)
            log.info("OCR leyó %d caracteres en la página %d", len(texto), numero)
    finally:
        doc.close()

    if ocr_pages:
        leidas = set(ocr_pages)
        extracted["scanned_pages"] = [
            n for n in extracted["scanned_pages"] if n not in leidas
        ]
        extracted["ocr_pages"] = ocr_pages
        textos = [p.get("text", "") for p in extracted["pages"] if p.get("text")]
        extracted["full_text"] = "\n\n".join(textos)


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
                    "reason": _t("pipe.range_uncertain", name=b.get("name")),
                }
            )
            continue
        biomarcadores_ok.append(b)
    # Sin duplicados del mismo análisis («CREATININA» vs «Creatinina»).
    exp["biomarkers"] = schemas.dedupe_biomarkers(biomarcadores_ok)

    # Prioridad clínica del LLM: normaliza a alta/media/baja ("media" si el
    # LLM no la devolvió) y recorta el motivo a una frase corta.
    for b in exp["biomarkers"]:
        pc = str(b.get("prioridad_clinica") or "").strip().lower()
        b["prioridad_clinica"] = pc if pc in ("alta", "media", "baja") else "media"
        motivo = str(b.get("motivo_prioridad") or "").strip()
        b["motivo_prioridad"] = motivo[:180] or None

    # Laboratorio: tal como aparece en el documento, o null.
    lab = str(exp.get("laboratorio") or "").strip()
    exp["laboratorio"] = lab[:120] or None

    # Fecha del documento: solo AAAA-MM-DD válido; nunca inventada.
    fd = str(exp.get("fecha_documento") or "").strip()
    try:
        datetime.strptime(fd, "%Y-%m-%d")
        exp["fecha_documento"] = fd
    except ValueError:
        exp["fecha_documento"] = None

    # Resumen por secciones: textos cortos, sin inventar.
    for campo in ("resumen_que_es", "resumen_rapido", "resumen_hallazgos"):
        s = str(exp.get(campo) or "").strip()
        exp[campo] = s[:600] or None

    # Urgencia: la regla conservadora nunca baja lo que el LLM marcó.
    regla = decide_urgency(_derivar_senales(exp, extracted))
    nivel_llm = (exp.get("urgency") or {}).get("level", "no_urgente")
    if _NIVEL_RANK.get(regla["level"], 0) >= _NIVEL_RANK.get(nivel_llm, 0):
        exp["urgency"] = regla
    return exp


def _process_intake(
    intake_id: str, pdf_bytes: bytes, doc_type_hint: str | None, lang: str
) -> dict:
    log.info("Procesando intake %s (hint=%s, lang=%s)", intake_id, doc_type_hint, lang)
    if lang not in ("es", "en"):
        lang = "en"

    extracted = pdf_extract.extract_document(pdf_bytes)

    # OCR primero: las páginas escaneadas se intentan leer antes de decidir
    # que el documento no tiene texto útil.
    if extracted.get("scanned_pages"):
        _intentar_ocr(extracted, pdf_bytes, lang)

    texto = (extracted.get("full_text") or "").strip()
    if extracted.get("num_pages", 0) == 0 or not texto:
        if extracted.get("scanned_pages"):
            raise UnprocessableDocument(_t("pipe.scanned_unprocessable", lang=lang))
        raise UnprocessableDocument(_t("pipe.unreadable", lang=lang))

    extracted["doc_type_hint"] = doc_type_hint
    extracted["lang"] = lang
    proveedor_llm = get_llm_provider()
    crudo = proveedor_llm.explain(extracted)
    crudo["demo_mode"] = bool(proveedor_llm.is_demo)
    crudo["lang"] = lang

    try:
        exp = schemas.validate_explanation(crudo)
    except Exception as exc:
        log.warning("La explicación del LLM no validó: %s", exc)
        raise UnprocessableDocument(
            _t("pipe.llm_invalid", lang=lang)
        ) from exc

    exp = _aplicar_reglas_duras(exp, extracted)

    # Páginas escaneadas y leídas por OCR: se informa al usuario en todos
    # los motores. (DemoExplainer ya las agrega; aquí se cubren
    # Gemini/Anthropic.)
    for n in extracted.get("scanned_pages", []):
        campo = f"pages[{n}].text"
        ya = any(f.get("field") == campo for f in exp.get("uncertain_fields", []))
        if not ya:
            exp.setdefault("uncertain_fields", []).append(
                {
                    "field": campo,
                    "reason": _t("demo.scanned", n=n),
                }
            )
    for n in extracted.get("ocr_pages", []):
        campo = f"pages[{n}].text"
        ya = any(f.get("field") == campo for f in exp.get("uncertain_fields", []))
        if not ya:
            exp.setdefault("uncertain_fields", []).append(
                {
                    "field": campo,
                    "reason": _t("demo.ocr_page", n=n),
                }
            )

    log.info(
        "Intake %s listo: tipo=%s urgencia=%s",
        intake_id, exp.get("document_type"), exp.get("urgency", {}).get("level"),
    )
    return exp


def process_intake(
    intake_id: str, pdf_bytes: bytes, doc_type_hint: str | None,
    lang: str = "es"
) -> dict:
    """Punto de entrada: fija el idioma para esta ejecución (reglas duras y
    urgencia leen el idioma actual) y lo restaura al terminar, incluso en
    hilos de segundo plano reutilizados."""
    if lang not in ("es", "en"):
        lang = "en"
    from app.i18n import current_lang
    token = set_current_lang(lang)
    try:
        return _process_intake(intake_id, pdf_bytes, doc_type_hint, lang)
    finally:
        current_lang.reset(token)
