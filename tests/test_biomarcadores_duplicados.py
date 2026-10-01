"""Sin valores repetidos: el mismo análisis no aparece dos veces.

El defecto (2026-10-01): en un resultado real, «Creatinina» salía dos veces,
una como «CREATININA» y otra como «Creatinina» — el LLM repitió el análisis
con distinto uso de mayúsculas y nada lo deduplicaba. Ahora se deduplica al
procesar (documentos nuevos) y al mostrar (documentos ya guardados).
"""

from app import schemas
from app.routers import render_explanation
from app.services.pipeline import _aplicar_reglas_duras


def _b(nombre, **kw):
    base = {
        "name": nombre,
        "value": "1.1",
        "unit": None,
        "ref_range": None,
        "flag": "normal",
        "plain_explanation": "Mide cómo trabajan los riñones.",
    }
    base.update(kw)
    return base


def test_dedup_por_mayusculas_conserva_el_mas_completo():
    lista = [
        _b("CREATININA", value="", plain_explanation=""),
        _b("Creatinina", unit="mg/dL", ref_range="0.5 - 1.2"),
    ]
    salida = schemas.dedupe_biomarkers(lista)
    assert len(salida) == 1
    unico = salida[0]
    assert unico["value"] == "1.1"          # rellenado desde el duplicado
    assert unico["unit"] == "mg/dL"
    assert unico["ref_range"] == "0.5 - 1.2"


def test_dedup_sin_acentos_y_espacios():
    lista = [_b("Ácido  úrico"), _b("acido urico", unit="mg/dL")]
    salida = schemas.dedupe_biomarkers(lista)
    assert len(salida) == 1
    assert salida[0]["unit"] == "mg/dL"


def test_no_funde_analisis_distintos_ni_cambia_el_orden():
    lista = [
        _b("Hemoglobina"),
        _b("hemoglobina glicosilada"),
        _b("HEMOGLOBINA"),
        _b("Glucosa"),
    ]
    salida = schemas.dedupe_biomarkers(lista)
    assert [b["name"] for b in salida] == [
        "Hemoglobina", "hemoglobina glicosilada", "Glucosa",
    ]


def test_pipeline_deduplica_antes_de_guardar():
    exp = {
        "document_type": "lab_result",
        "biomarkers": [
            _b("CREATININA", plain_explanation=""),
            _b("Creatinina", unit="mg/dL", ref_range="0.5 - 1.2"),
        ],
        "urgency": {"level": "no_urgente"},
    }
    extracted = {"full_text": "Creatinina 1.1 mg/dL rango 0.5 - 1.2"}
    salida = _aplicar_reglas_duras(exp, extracted)
    assert len(salida["biomarkers"]) == 1
    assert salida["biomarkers"][0]["unit"] == "mg/dL"


def test_render_deduplica_documentos_ya_guardados():
    exp = {
        "urgency": {"level": "agendar_cita"},
        "biomarkers": [
            _b("CREATININA", flag="alto", ref_range="0.5 - 1.2"),
            _b("Creatinina", flag="alto", ref_range="0.5 - 1.2",
               unit="mg/dL"),
            _b("Glucosa", flag="normal", ref_range="70 - 99"),
        ],
    }
    vista = render_explanation(exp)
    nombres = [b["name"].lower() for b in vista["biomarkers"]]
    assert nombres.count("creatinina") == 1
    assert len(vista["biomarkers"]) == 2
    # "Valores que necesitan atención" tampoco repite el análisis.
    atencion = [b["name"].lower() for b in vista["concerns_top"]]
    assert atencion.count("creatinina") == 1
