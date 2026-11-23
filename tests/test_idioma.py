"""Idioma ES/EN: detección por navegador, cambio manual y catálogo.

Regla de producto (2026-10-06): el sitio se muestra en inglés cuando el
navegador no es español (Accept-Language), con un cambio manual ES/EN
persistente en cookie. La explicación se genera en el idioma de subida.
"""

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app.i18n import STRINGS, get_lang_from_header, t
from app.services import llm as llm_mod
from app.services.urgency import decide_urgency


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


# --- detección -----------------------------------------------------------

def test_acepta_es_con_variantes():
    assert get_lang_from_header("es-EC,es;q=0.9,en;q=0.8") == "es"
    assert get_lang_from_header("ES") == "es"


def test_sin_cabecera_es_ingles_por_defecto():
    assert get_lang_from_header(None) == "en"
    assert get_lang_from_header("") == "en"


def test_no_es_es_ingles():
    assert get_lang_from_header("en-US,en;q=0.9") == "en"
    assert get_lang_from_header("fr-FR,fr;q=0.8") == "en"
    assert get_lang_from_header("pt-BR") == "en"


def test_cookie_gana_al_navegador(client):
    r = client.get("/", headers={"Accept-Language": "en-US"},
                   cookies={"mificha_lang": "es"})
    assert 'lang="es"' in r.text
    r = client.get("/", headers={"Accept-Language": "es-EC"},
                   cookies={"mificha_lang": "en"})
    assert 'lang="en"' in r.text


# --- cambio manual ---------------------------------------------------------

def test_cambio_idioma_guarda_cookie_y_redirige(client):
    r = client.get("/idioma/en", follow_redirects=False)
    assert r.status_code == 303
    assert "mificha_lang=en" in r.headers["set-cookie"]
    r = client.get("/idioma/xx", follow_redirects=False)
    assert "mificha_lang=en" in r.headers["set-cookie"]


# --- render en ambos idiomas ------------------------------------------------

def test_home_en_con_navegador_ingles(client):
    client.cookies.clear()
    r = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "Understand your exam in minutes" in r.text
    assert "Entiende tu examen" not in r.text


def test_home_en_por_defecto(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "Understand your exam in minutes" in r.text


def test_home_es_con_navegador_espanol(client):
    client.cookies.clear()
    r = client.get("/", headers={"Accept-Language": "es-EC,es;q=0.9"})
    assert r.status_code == 200
    assert 'lang="es"' in r.text
    assert "Entiende tu examen en minutos" in r.text


def test_toggle_visible_en_base(client):
    r = client.get("/", headers={"Accept-Language": "en-US"})
    assert 'href="/idioma/es"' in r.text
    assert 'href="/idioma/en"' in r.text


# --- catálogo ----------------------------------------------------------------

def test_catalogo_completo_en_ambos_idiomas():
    es, en = STRINGS["es"], STRINGS["en"]
    assert set(es) == set(en), "claves faltantes: " + str(
        set(es) ^ set(en))[:200]


def test_fallback_a_en_si_falta_clave():
    assert t("clave_inexistente_xyz") == "clave_inexistente_xyz"
    # una clave real con formato
    assert t("upload.uploading_pct", lang="es", pct=50) == \
        "Subiendo tu documento… 50%"
    assert t("upload.uploading_pct", lang="en", pct=50) == \
        "Uploading your document… 50%"


# --- contenido generado en inglés -------------------------------------------

def test_demo_explainer_en():
    demo = llm_mod.DemoExplainer()
    exp = demo.explain({
        # Columnas separadas por 2+ espacios: nombre, valor, unidad, rango.
        "full_text": "GLUCOSA  90  mg/dL  70 - 100",
        "lang": "en",
    })
    assert exp["type_label"] == "Lab result"
    assert exp["disclaimer"].startswith("This explanation is only")
    assert "sugar" in exp["biomarkers"][0]["plain_explanation"].lower()


def test_urgencia_en():
    i18n.set_current_lang("en")
    try:
        u = decide_urgency({"abnormal": [], "has_critical_flag": False,
                            "has_uncertain_critical": False})
        assert u["label"] == "No urgency"
        u = decide_urgency({"abnormal": [{"name": "X", "flag": "alta",
                                          "severity": "leve"}]})
        assert u["label"] == "Book an appointment"
    finally:
        i18n.set_current_lang("es")


def test_urgencia_es_se_mantiene():
    i18n.set_current_lang("es")
    try:
        u = decide_urgency({"abnormal": [], "has_critical_flag": False,
                            "has_uncertain_critical": False})
        assert u["label"] == "Sin urgencia"
    finally:
        i18n.set_current_lang("en")


def test_prompt_ingles_existe():
    assert "simple, everyday English" in llm_mod.SYSTEM_PROMPT_EN
    assert "NEVER diagnose" in llm_mod.SYSTEM_PROMPT_EN
    assert '"biomarkers"' in llm_mod.SYSTEM_PROMPT_EN
