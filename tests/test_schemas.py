"""Tests de los modelos pydantic de app.schemas (contrato Explanation).

Campos de la Explanation:
  document_type, type_label, summary_simple,
  biomarkers[{name, value, unit, ref_range, flag, plain_explanation,
              possible_health_effect}],
  narrative_findings[{finding, plain_explanation}],
  ordered_tests[{name, what_it_is, usual_objectives[], preparation,
                 question_for_doctor}],
  urgency{level, label, action, timeframe, reason},
  recommendations[], questions_for_doctor[],
  uncertain_fields[{field, reason}],
  disclaimer, demo_mode.

Supuestos: todos los campos listados son requeridos; urgency.level es uno de
los cuatro niveles válidos (Literal); biomarkers puede ser lista vacía
(documentos narrativos como un PET sin rangos).
"""

import pytest
from pydantic import ValidationError

from app.schemas import Explanation

from conftest import make_biomarker, make_explanation, make_urgency


def test_explicacion_minima_valida():
    exp = Explanation.model_validate(make_explanation())
    assert exp.document_type == "lab_result"
    assert exp.urgency.level == "no_urgente"
    assert exp.biomarkers[0].name == "Glucosa"
    assert exp.demo_mode is True
    assert exp.disclaimer.strip()


def test_nivel_de_urgencia_invalido_se_rechaza():
    datos = make_explanation(urgency=make_urgency(level="emergencia_total"))
    with pytest.raises(ValidationError):
        Explanation.model_validate(datos)


def test_biomarcador_sin_campo_requerido_se_rechaza():
    biomarker = make_biomarker()
    del biomarker["plain_explanation"]
    datos = make_explanation(biomarkers=[biomarker])
    with pytest.raises(ValidationError):
        Explanation.model_validate(datos)


def test_documento_narrativo_sin_biomarcadores_valida():
    """Un PET u otro informe sin rangos puede no traer biomarcadores."""
    datos = make_explanation(biomarkers=[])
    exp = Explanation.model_validate(datos)
    assert exp.biomarkers == []
    assert exp.narrative_findings, "debe conservar los hallazgos narrativos"
