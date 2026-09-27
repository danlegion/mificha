"""Pruebas de la sección 'Valores que necesitan atención'.

- Los más urgentes van primero (critica > grave > moderada > leve).
- La lista muestra como máximo 5 y avisa cuántos otros requieren atención.
- La severidad se deriva de valor + rango cuando el dict no la trae
  (ruta Gemini: el LLM no devuelve 'severity').
"""

from app.routers import render_explanation
from app.services.llm import DemoExplainer


def _bio(name, flag, severity=None, value="10", unit="mg/dL", ref_range="1 - 5",
         prioridad=None, motivo=None):
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
    if prioridad is not None:
        b["prioridad_clinica"] = prioridad
    if motivo is not None:
        b["motivo_prioridad"] = motivo
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


# ------------------------------------------------- prioridad clínica (LLM)


def test_prioridad_llm_ordena_antes_que_desviacion():
    exp = _exp([
        _bio("Colesterol", "alto", "grave", prioridad="baja"),
        _bio("Potasio", "alto", "leve", prioridad="alta"),
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top == ["Potasio", "Colesterol"]


def test_marca_critica_de_laboratorio_sigue_primera():
    exp = _exp([
        _bio("Potasio", "alto", "leve", prioridad="alta"),
        _bio("CriticoLab", "alto", None, value="99 *", ref_range="1 - 5",
             prioridad="baja"),
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top[0] == "CriticoLab"


def test_desviacion_desempata_misma_prioridad():
    exp = _exp([
        _bio("A", "alto", None, value="5.5", ref_range="1 - 5",
             prioridad="media"),   # leve
        _bio("B", "alto", None, value="30", ref_range="1 - 5",
             prioridad="media"),   # grave
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top == ["B", "A"]


def test_sin_prioridad_se_usa_media_por_defecto():
    exp = _exp([
        _bio("SinPrioridad", "alto", "leve"),
        _bio("Baja", "alto", "leve", prioridad="baja"),
        _bio("Alta", "alto", "leve", prioridad="alta"),
    ])
    top = [b["name"] for b in exp["concerns_top"]]
    assert top == ["Alta", "SinPrioridad", "Baja"]


def test_pipeline_normaliza_prioridad_clinica():
    from app.services.pipeline import _aplicar_reglas_duras
    exp = {
        "biomarkers": [
            {"name": "Potasio", "flag": "alto", "ref_range": "3.5 - 5.0",
             "prioridad_clinica": "ALTA ", "motivo_prioridad": "x" * 200},
            {"name": "Colesterol", "flag": "alto", "ref_range": "0 - 200"},
            {"name": "Glucosa", "flag": "alto", "ref_range": "70 - 99",
             "prioridad_clinica": "urgentisimo", "motivo_prioridad": "  hola  "},
        ],
        "urgency": {"level": "no_urgente"},
    }
    extracted = {"full_text": "Potasio 5.8  3.5 - 5.0\nColesterol 250  0 - 200\nGlucosa 300  70 - 99"}
    out = _aplicar_reglas_duras(exp, extracted)
    bios = {b["name"]: b for b in out["biomarkers"]}
    assert bios["Potasio"]["prioridad_clinica"] == "alta"
    assert len(bios["Potasio"]["motivo_prioridad"]) == 180
    assert bios["Colesterol"]["prioridad_clinica"] == "media"
    assert bios["Glucosa"]["prioridad_clinica"] == "media"
    assert bios["Glucosa"]["motivo_prioridad"] == "hola"
