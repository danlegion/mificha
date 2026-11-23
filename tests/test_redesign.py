"""Rediseño Nov 2026: héroe con cifras reales, familia de un vistazo,
panel de la bóveda con filtros en su propia fila, tarjetas ricas,
filas de vacunas y cabecera oscura del detalle.

Lo que NO se traslada de la vista previa: las banderas inventadas
("Due soon", "1 to review") ni el botón "Mark reviewed" de la tarjeta
de familia — el héroe muestra 0 / "Todo al día" hasta que exista un
sistema de estados de revisión con datos reales.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal
from app.routers import key_values_with_position, range_position


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents", "members"):
        for i in ids:
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def _exp(nombre="Hemograma completo", fecha_doc="2026-11-18"):
    return {
        "type_label": "Resultado de laboratorio",
        "nombre_examen": nombre,
        "summary_simple": "Todo en rango.",
        "fecha_documento": fecha_doc,
        "laboratorio": "Labcorp",
        "document_type": "lab_result",
        "biomarkers": [
            {"name": "Hemoglobina", "value": "14.8", "unit": "g/dL",
             "flag": "normal", "ref_range": "13.5 - 17.5 g/dL"},
            {"name": "Glucosa", "value": "88", "unit": "mg/dL",
             "flag": "normal", "ref_range": "70 - 100 mg/dL"},
        ],
        "urgency": {"level": "no_urgente", "label": "Sin urgencia",
                    "action": "a", "timeframe": "t", "reason": "r"},
        "disclaimer": ("Esta explicación es solo informativa y no es un "
                       "diagnóstico. Consulta siempre a un profesional de "
                       "la salud."),
    }


@pytest.fixture()
def owner_con_datos():
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    member = dal.create_member(uid, "Ana")
    did = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                       "member_id": member["id"],
                       "explanation": _exp()})
    yield {"uid": uid, "member_id": member["id"], "doc_id": did}
    _clean([uid, member["id"], did])


def test_header_pildora_con_familia(client, owner_con_datos):
    r = client.get("/", cookies=_cookies(owner_con_datos["uid"]))
    assert r.status_code == 200
    assert 'class="site-nav"' in r.text
    assert 'href="/#family"' in r.text
    assert '<a href="/ficha">Bóveda</a>' in r.text


def test_hero_muestra_cifras_reales_y_todo_al_dia(client, owner_con_datos):
    r = client.get("/", cookies=_cookies(owner_con_datos["uid"]))
    assert r.status_code == 200
    assert "Cada resultado," in r.text
    assert "explicado con claridad." in r.text
    # Cifras reales: 1 documento, 1 familiar, 0 por revisar.
    assert '<div class="stat-num">1 <em>docs</em></div>' in r.text
    assert '<div class="stat-num">1</div>' in r.text
    assert '<div class="stat-num stat-num--ok">0</div>' in r.text
    assert "Todo al día" in r.text
    assert "Último explicado: Hemograma completo" in r.text


def test_familia_de_un_vistazo_enlaza_a_la_boveda_filtrada(client, owner_con_datos):
    r = client.get("/", cookies=_cookies(owner_con_datos["uid"]))
    assert 'id="family"' in r.text
    assert "Tu familia de un vistazo" in r.text
    assert f'href="/ficha?miembro={owner_con_datos["member_id"]}"' in r.text
    assert "Ana" in r.text
    assert "Último examen" in r.text


def test_panel_boveda_filtros_en_su_propia_fila(client, owner_con_datos):
    r = client.get("/ficha", cookies=_cookies(owner_con_datos["uid"]))
    assert r.status_code == 200
    assert 'class="vault-panel"' in r.text
    assert 'class="vault-filters"' in r.text
    assert "Filtrar" in r.text
    assert 'data-vault-search' in r.text
    assert 'class="member-pill"' in r.text
    assert '<a class="btn btn-primary" href="/#explicar">+ Subir documento</a>' in r.text
    # La tarjeta rica: icono por tipo, quién, resumen, métrica al pie.
    assert 'class="doc-icon"' in r.text
    assert "Ana · Explicado en lenguaje sencillo" in r.text
    assert "Todo en rango." in r.text
    assert "Hemoglobina" in r.text and "14.8 g/dL" in r.text


def test_vacunas_en_filas_con_badge_recibida(client, owner_con_datos):
    # Añadir un registro de vacunas al mismo documento.
    exp = _exp()
    exp["document_type"] = "vaccine_record"
    exp["vaccines"] = [{"name": "Influenza", "date": "2026-10-15",
                        "dose": "1", "lot": "L1",
                        "manufacturer": "Sanofi", "provider": "Clínica"}]
    did2 = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": did2, "user_id": owner_con_datos["uid"],
                       "intake_id": did2,
                       "member_id": owner_con_datos["member_id"],
                       "explanation": exp})
    try:
        r = client.get("/vacunas", cookies=_cookies(owner_con_datos["uid"]))
        assert r.status_code == 200
        assert 'class="vax-row"' in r.text
        assert "Influenza" in r.text
        assert "Recibida" in r.text
        assert 'data-vault-search' in r.text
        assert "Due soon" not in r.text
    finally:
        _clean([did2])


def test_detalle_cabecera_oscura_y_barras_reales(client, owner_con_datos):
    r = client.get(f"/ficha/{owner_con_datos['doc_id']}",
                   cookies=_cookies(owner_con_datos["uid"]))
    assert r.status_code == 200
    assert 'class="detail-hero"' in r.text
    assert "Bóveda</a>" in r.text  # migas: Bóveda › examen (la › es CSS)
    assert "Hemograma completo" in r.text
    assert "Qué significa, en lenguaje sencillo" in r.text
    assert "Valores clave" in r.text
    assert "Hemoglobina" in r.text
    # Barra real: 14.8 dentro de 13.5–17.5 ≈ 32%.
    assert 'class="kv-marker"' in r.text
    # Sin original guardado no hay botón "Ver PDF original" (lo cubre
    # test_original_disponible); la acción real del héroe es compartir.
    assert "Ver PDF original" not in r.text
    assert "Compartir de forma segura" in r.text


def test_detalle_muestra_aviso_no_diagnostico(client, owner_con_datos):
    # El README promete que cada explicación dice que no es un
    # diagnóstico: el aviso debe verse en la página del documento.
    r = client.get(f"/ficha/{owner_con_datos['doc_id']}",
                   cookies=_cookies(owner_con_datos["uid"]))
    assert r.status_code == 200
    assert 'class="disclaimer-box"' in r.text
    assert "no es un diagnóstico" in r.text


def test_range_position_solo_con_rango_numerico():
    assert range_position("14.8", "13.5 - 17.5 g/dL") == pytest.approx(32.5)
    assert range_position("13.5", "13.5 - 17.5") == 0.0
    assert range_position("17.5", "13.5 - 17.5") == 100.0
    assert range_position("20", "13.5 - 17.5") == 100.0  # recorta
    assert range_position("14.8", "negativo") is None
    assert range_position("alto", "13.5 - 17.5") is None
    assert range_position("14.8", "") is None


def test_key_values_con_posicion():
    exp = {"biomarkers": [
        {"name": "Hemoglobina", "value": "14.8", "unit": "g/dL",
         "ref_range": "13.5 - 17.5 g/dL"},
        {"name": "Nota", "value": "positivo", "unit": "",
         "ref_range": "negativo"},
    ]}
    kvs = key_values_with_position(exp)
    assert kvs[0]["value"] == "14.8 g/dL"
    assert kvs[0]["position"] == pytest.approx(32.5)
    assert kvs[1]["position"] is None  # sin barra, solo el valor


def test_js_busqueda_de_la_boveda():
    js = open("static/app.js").read()
    assert "window.MIFicha.initVaultSearch" in js
    assert "data-vault-search" in js
    assert "window.MIFicha.initVaultTabs" in js
