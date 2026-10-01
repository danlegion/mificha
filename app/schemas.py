"""MiFicha v0.1 — modelos pydantic del Explanation dict.

El dict de explicación es el contrato entre el pipeline de IA y las rutas web.
Todo lo que devuelva `LLMProvider.explain()` se valida aquí antes de guardarse.
"""

import re
import unicodedata
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

UrgencyLevel = Literal[
    "no_urgente",
    "agendar_cita",
    "hablar_hoy_con_profesional",
    "buscar_ayuda_ya",
]

# Niveles válidos (para chequeos rápidos sin pydantic)
NIVELES_URGENTES_VALIDOS = (
    "no_urgente",
    "agendar_cita",
    "hablar_hoy_con_profesional",
    "buscar_ayuda_ya",
)

DocumentType = Literal[
    "lab_result",
    "narrative_report",
    "exam_order",
    "doctor_note",
    "unknown",
]


class Biomarker(BaseModel):
    name: str
    value: str
    unit: Optional[str] = None
    ref_range: Optional[str] = None
    flag: Optional[str] = None  # p. ej. "alto", "bajo", "normal", "por confirmar"
    prioridad_clinica: Optional[str] = None  # "alta" | "media" | "baja" (la pone el LLM)
    motivo_prioridad: Optional[str] = None  # frase corta en lenguaje simple
    plain_explanation: str
    possible_health_effect: Optional[str] = None


class NarrativeFinding(BaseModel):
    finding: str
    plain_explanation: str


class OrderedTest(BaseModel):
    name: str
    what_it_is: str
    usual_objectives: List[str] = Field(default_factory=list)
    preparation: Optional[str] = None
    question_for_doctor: str


class Urgency(BaseModel):
    level: UrgencyLevel
    label: str
    action: str
    timeframe: str
    reason: str


class UncertainField(BaseModel):
    field: str
    reason: str
    # Campos opcionales que el backend rellena para el formulario de corrección
    key: Optional[str] = None
    label: Optional[str] = None
    guessed_value: Optional[str] = ""
    hint: Optional[str] = None


class Explanation(BaseModel):
    document_type: str  # str libre; DocumentType cubre los conocidos
    type_label: str
    nombre_examen: Optional[str] = None  # nombre corto en palabras cotidianas ("Examen de sangre")
    laboratorio: Optional[str] = None  # nombre del laboratorio/clínica según el documento
    fecha_documento: Optional[str] = None  # AAAA-MM-DD en que el doctor/lab completó el documento
    summary_simple: str
    # Resumen por secciones (lo nuevo); summary_simple queda como respaldo.
    resumen_que_es: Optional[str] = None
    resumen_rapido: Optional[str] = None
    resumen_hallazgos: Optional[str] = None
    biomarkers: List[Biomarker] = Field(default_factory=list)
    narrative_findings: List[NarrativeFinding] = Field(default_factory=list)
    ordered_tests: List[OrderedTest] = Field(default_factory=list)
    urgency: Urgency
    recommendations: List[str] = Field(default_factory=list)
    questions_for_doctor: List[str] = Field(default_factory=list)
    uncertain_fields: List[UncertainField] = Field(default_factory=list)
    disclaimer: str
    demo_mode: bool = False
    # Nota regenerada tras correcciones del usuario (no la muestra la plantilla v0.1)
    correction_note: Optional[str] = None


def validate_explanation(data: dict) -> dict:
    """Valida un dict contra el esquema Explanation y devuelve el dict limpio."""
    return Explanation.model_validate(data).model_dump()


def _clave_biomarcador(name) -> str:
    """Clave de comparación: sin mayúsculas, acentos ni signos («CREATININA»,
    «Creatinina» y «creatinina » dan la misma clave)."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9%]+", " ", s.lower())
    return " ".join(s.split())


def dedupe_biomarkers(biomarkers) -> list:
    """Quita duplicados del mismo análisis (el LLM a veces lo repite con otro
    uso de mayúsculas, p. ej. una vez desde un encabezado y otra desde la
    tabla). Conserva el registro más completo y rellena sus huecos con el
    otro; el orden de aparición se mantiene."""
    pares = []   # [clave, dict] en orden de aparición
    indice = {}  # clave -> posición en pares
    for b in biomarkers or []:
        clave = _clave_biomarcador(b.get("name"))
        if not clave:
            pares.append([None, b])  # sin nombre usable: no se toca
            continue
        if clave not in indice:
            indice[clave] = len(pares)
            pares.append([clave, b])
            continue

        def _puntaje(x):
            return (
                bool(x.get("ref_range")),
                str(x.get("flag") or "").strip().lower()
                not in ("", "por confirmar"),
                len(str(x.get("plain_explanation") or "")),
            )

        actual = pares[indice[clave]][1]
        ganador, perdedor = (
            (actual, b) if _puntaje(actual) >= _puntaje(b) else (b, actual)
        )
        for campo in (
            "value", "unit", "ref_range", "flag", "plain_explanation",
            "possible_health_effect", "prioridad_clinica", "motivo_prioridad",
        ):
            if not ganador.get(campo) and perdedor.get(campo):
                ganador[campo] = perdedor[campo]
        pares[indice[clave]][1] = ganador
    return [b for _, b in pares]
