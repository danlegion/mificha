"""El resumen de la explicación se divide en subtítulos legibles."""

from app.routers import render_explanation, templates


def _exp_render(**kw):
    base = {
        "document_type": "lab_result",
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen largo",
        "biomarkers": [],
        "urgency": {"level": "no_urgente", "label": "l", "action": "a",
                    "timeframe": "t", "reason": "r"},
    }
    base.update(kw)
    exp = render_explanation(base)
    tpl = templates.get_template("_explanation.html")
    return tpl.render({"exp": exp})


def test_subtitulos_presentes():
    html = _exp_render(
        resumen_que_es="Qué es.",
        resumen_rapido="Rápido.",
        resumen_hallazgos="Hallazgos.",
    )
    assert "Qué es este examen" in html
    assert "Resumen rápido" in html
    assert "Hallazgos principales" in html
    assert "Qué es." in html
    # Sin el bloque viejo de texto corrido.
    assert '<h1 class="summary">' not in html


def test_respaldo_sin_secciones():
    html = _exp_render()
    assert '<h1 class="summary">resumen largo</h1>' in html
    assert "Qué es este examen" not in html


def test_sin_duplicar_hallazgos_narrativos():
    html = _exp_render(
        resumen_hallazgos="Hallazgos.",
        narrative_findings=[{"finding": "F1", "plain_explanation": "p1"}],
    )
    # La lista rápida de encabezados no se duplica con la prosa.
    assert html.count("Hallazgos principales") == 1
    # Pero el detalle en "Entender más" sí muestra el hallazgo.
    assert "F1" in html
