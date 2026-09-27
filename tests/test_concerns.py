"""Pruebas de la sección 'Valores que necesitan atención'.

- Los más urgentes van primero (critica > grave > moderada > leve).
- La lista muestra como máximo 5 y avisa cuántos otros requieren atención.
- La severidad se deriva de valor + rango cuando el dict no la trae
  (ruta Gemini: el LLM no devuelve 'severity').
"""

from app.routers import render_explanation
from app.services.llm import DemoExplainer


def _bio(name, flag, severity=None, value="10", unit="mg/dL", ref_range="1 - 5"):
    b = {
        "name": name,
        "value": value,
        "unit": unit,
        "ref_range": ref_range,
        "flag": flag,
        "plain_explanation": "x",
    }
    if severity is not None:
        b["severity"] = severity
    return b


def _exp(biomarkers):
    return render_explanation({
        "document_type": "lab_result",
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen",
        "biomarkers": biomarkers,
        "urgency": {"level": "no_urgente", "label": "l", "action": "a",
                    "timeframe": "t", "reason": "r"},
    })


def test_concerns_ordenados_por_severidad():
    exp = _exp([
        _bio("Leve", "alto", "leve"),
        _bio("Grave", "alto", "grave"),
        _bio("Critica", "alto", "critica"),
        _bio("Moderada", "bajo", "moderada"),
        _bio("Normal", "normal", "leve"),
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top == ["Critica", "Grave", "Moderada", "Leve"]
    assert exp["concerns_extra"] == 0
    assert exp["concerns_extra_note"] is None


def test_concerns_maximo_cinco_y_aviso_de_resto():
    bios = [_bio(f"B{i}", "alto", "leve") for i in range(7)]
    exp = _exp(bios)
    assert len(exp["concerns_top"]) == 5
    assert exp["concerns_extra"] == 2
    assert "2 valores más" in exp["concerns_extra_note"]
    assert "requieren tu atención" in exp["concerns_extra_note"]


def test_concerns_extra_singular():
    bios = [_bio(f"B{i}", "alto", "leve") for i in range(6)]
    exp = _exp(bios)
    assert exp["concerns_extra"] == 1
    assert "1 valor más" in exp["concerns_extra_note"]
    assert "requiere tu atención" in exp["concerns_extra_note"]


def test_concerns_severidad_derivada_valor_rango():
    # Biomarcador estilo Gemini: sin 'severity' en el dict.
    exp = _exp([
        _bio("A", "alto", None, value="10", ref_range="1 - 5"),      # desv 125% -> grave
        _bio("B", "alto", None, value="5.5", ref_range="1 - 5"),     # desv 12%  -> leve
        _bio("C", "alto", None, value="30", ref_range="1 - 5"),      # desv 625% -> grave
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    # A y C (grave) antes que B (leve); orden estable entre iguales.
    assert top == ["A", "C", "B"]


def test_concerns_marca_critica_del_laboratorio_primero():
    exp = _exp([
        _bio("Grave", "alto", "grave", value="20", ref_range="1 - 5"),
        _bio("CriticoLab", "alto", None, value="99 *", ref_range="1 - 5"),
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top[0] == "CriticoLab"


def test_sin_concerns_no_hay_lista_ni_aviso():
    exp = _exp([_bio("Ok", "normal", "leve", value="3", ref_range="1 - 5")])
    assert exp["concerns_top"] == []
    assert exp["concerns_extra"] == 0
    assert exp["concerns_extra_note"] is None


def test_parser_incluye_severity_en_dict():
    demo = DemoExplainer()
    b = demo._parsear_linea_lab("Glucosa  200  mg/dL  70 - 99")
    assert b["dict"]["severity"] == b["severidad"]
    assert b["dict"]["severity"] in ("leve", "moderada", "grave", "critica")
