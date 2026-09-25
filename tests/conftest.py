"""Fábricas y documentación de contratos para la suite de tests de MiFicha v0.1.

Los tests se escriben contra los contratos que implementa el backend; si un
test falla por un desajuste de contrato (no por un bug), hay que alinear el
contrato, no relajar el test.

Contratos asumidos
------------------
1. app.services.pdf_extract.extract_document(pdf_bytes: bytes) -> dict
   {
     "pages": [{"page_number": int, "text": str,
                "has_text": bool, "is_scanned": bool}],
     "full_text": str,
     "scanned_pages": [int],
     "num_pages": int,
   }
   Regla: página con <20 caracteres extraíbles -> is_scanned True.

2. app.services.urgency.decide_urgency(signals: dict) -> dict
   {
     "level": "no_urgente" | "agendar_cita"
              | "hablar_hoy_con_profesional" | "buscar_ayuda_ya",
     "label": str, "action": str, "timeframe": str, "reason": str,
   }
   Esquema de `signals` asumido (el backend puede extenderlo, pero debe
   respetar estas claves):
     - "abnormal": lista de dicts {"name": str, "flag": "alta" | "baja",
       "severity": "leve" | "moderada" | "grave" | "critica"}.
     - "has_critical_flag": bool. Un biomarcador marcó bandera crítica.
     - "has_uncertain_critical": bool. Dato crítico incierto (OCR o parsing
       dudoso) sin valor confirmado -> nunca "no_urgente" (conservador).
     - "explicit_priority": str | None. Prioridad escrita en la orden médica;
       si existe se cita literal en "reason" y nunca se inventa.

3. app.schemas: modelos pydantic de la Explanation (ver test_schemas.py).
   Niveles de urgencia válidos: los cuatro del punto 2.
"""

from copy import deepcopy


# ---------------------------------------------------------------------------
# Señales para decide_urgency
# ---------------------------------------------------------------------------

def make_signals(**overrides):
    """Devuelve un dict de señales válido para decide_urgency."""
    signals = {
        "abnormal": [],
        "has_critical_flag": False,
        "has_uncertain_critical": False,
        "explicit_priority": None,
    }
    signals.update(overrides)
    return signals


def make_abnormal(name="Colesterol total", flag="alta", severity="leve"):
    """Un biomarcador fuera de rango."""
    return {"name": name, "flag": flag, "severity": severity}


NIVELES_VALIDOS = {
    "no_urgente",
    "agendar_cita",
    "hablar_hoy_con_profesional",
    "buscar_ayuda_ya",
}


def assert_urgencia_valida(resultado):
    """Chequea forma y nivel del dict que devuelve decide_urgency."""
    assert isinstance(resultado, dict)
    assert resultado["level"] in NIVELES_VALIDOS, (
        f"nivel inesperado: {resultado['level']!r}"
    )
    for campo in ("label", "action", "timeframe", "reason"):
        valor = resultado[campo]
        assert isinstance(valor, str) and valor.strip(), (
            f"campo {campo!r} debe ser texto no vacío"
        )
    return resultado


# ---------------------------------------------------------------------------
# Explanation mínima válida
# ---------------------------------------------------------------------------

def make_urgency(level="no_urgente"):
    return {
        "level": level,
        "label": "No urgente",
        "action": "Sigue tu rutina normal y comenta estos resultados en tu próxima cita.",
        "timeframe": "En tu próxima cita de control",
        "reason": "Todos los valores están dentro de sus rangos de referencia.",
    }


def make_biomarker(**overrides):
    biomarker = {
        "name": "Glucosa",
        "value": "95",
        "unit": "mg/dL",
        "ref_range": "70 - 100",
        "flag": "normal",
        "plain_explanation": "La glucosa es el azúcar en tu sangre; tu valor está dentro de lo esperado.",
        "possible_health_effect": "Un valor normal no sugiere ningún efecto sobre tu salud.",
    }
    biomarker.update(overrides)
    return biomarker


def make_explanation(**overrides):
    """Explanation mínima válida según el contrato de app.schemas."""
    explanation = {
        "document_type": "lab_result",
        "type_label": "Resultado de laboratorio",
        "summary_simple": (
            "Tus exámenes de sangre salieron dentro de lo esperado. "
            "La glucosa, que es el azúcar en la sangre, está en un valor normal."
        ),
        "biomarkers": [make_biomarker()],
        "narrative_findings": [
            {
                "finding": "Sin hallazgos relevantes descritos en el informe.",
                "plain_explanation": "El laboratorio no reportó observaciones adicionales.",
            }
        ],
        "ordered_tests": [
            {
                "name": "Perfil lipídico",
                "what_it_is": "Un examen de sangre que mide las grasas en tu sangre.",
                "usual_objectives": [
                    "Revisar el riesgo de problemas del corazón a futuro."
                ],
                "preparation": "Ayuno de 8 a 12 horas antes del examen.",
                "question_for_doctor": "¿Necesito repetir este examen y cada cuánto?",
            }
        ],
        "urgency": make_urgency(),
        "recommendations": [
            "Mantén tus controles médicos habituales."
        ],
        "questions_for_doctor": [
            "¿Estos resultados cambian algo de mi tratamiento actual?"
        ],
        "uncertain_fields": [
            {
                "field": "biomarkers[0].ref_range",
                "reason": "El rango se leyó del documento pero la impresión estaba borrosa.",
            }
        ],
        "disclaimer": (
            "Esta explicación es solo informativa y no es un diagnóstico. "
            "Consulta siempre a un profesional de la salud."
        ),
        "demo_mode": True,
    }
    # deepcopy para que los overrides anidados no contaminen otras pruebas
    explanation = deepcopy(explanation)
    explanation.update(overrides)
    return explanation
