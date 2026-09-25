"""MiFicha v0.1 — modelos pydantic del Explanation dict.

El dict de explicación es el contrato entre el pipeline de IA y las rutas web.
Todo lo que devuelva `LLMProvider.explain()` se valida aquí antes de guardarse.
"""

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
    summary_simple: str
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
