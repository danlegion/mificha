"""Home de usuario registrado: subir-examen primero, lista de 6 meses.

- Sin tarjeta "Hola de nuevo".
- La tarjeta de explicar examen va primera.
- La lista trae fecha, tipo de documento, urgencia y valores fuera de rango.
- Solo documentos de los últimos 6 meses.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents"):
        for i in ids:
            con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
    con.commit()
    con.close()


def _explicacion(fuera=2):
    bios = [
        {"name": f"B{i}", "value": "10", "flag": "alto",
         "plain_explanation": "x"}
        for i in range(fuera)
    ]
    bios.append({"name": "Ok", "value": "3", "flag": "normal",
                 "plain_explanation": "x"})
    return {
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen",
        "biomarkers": bios,
        "urgency": {"level": "agendar_cita", "label": "Agenda una cita",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }


@pytest.fixture()
def usuario_con_docs():
    email = f"h-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    reciente = "t-" + uuid.uuid4().hex[:12]
    viejo = "t-" + uuid.uuid4().hex[:12]
    for iid in (reciente, viejo):
        dal.save_intake({"id": iid, "status": "ready"})
        dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                           "explanation": _explicacion()})
    # El viejo: hace 200 días.
    con = sqlite3.connect(_db_path())
    con.execute("UPDATE documents SET created_at=? WHERE id=?",
                ((datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
                 viejo))
    con.commit()
    con.close()
    yield {"uid": uid, "reciente": reciente, "viejo": viejo}
    _clean([uid, reciente, viejo])


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def test_home_usuario_subir_primero_y_lista_6_meses(client, usuario_con_docs):
    r = client.get("/", cookies=_cookies(usuario_con_docs["uid"]))
    assert r.status_code == 200
    html = r.text
    assert "Hola de nuevo" not in html
    assert "Tu último examen" not in html
    # La acción más importante va primera.
    assert html.index("Añade un documento") < html.index("<h2>Tus exámenes</h2>")
    # La tabla trae tipo, urgencia, fuera de rango y link a ver todos.
    assert "Resultado de laboratorio" in html
    assert "Agenda una cita" in html
    assert '<td class="num">2</td>' in html
    assert "Ver todos" in html
    assert f"/ficha/{usuario_con_docs['reciente']}" in html
    # El documento viejo (>6 meses) no aparece en el home.
    assert f"/ficha/{usuario_con_docs['viejo']}" not in html


def test_home_usuario_sin_docs(client):
    email = f"h0-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    try:
        r = client.get("/", cookies=_cookies(uid))
        assert "Añade un documento" in r.text
        assert "<h2>Tus exámenes</h2>" not in r.text
    finally:
        _clean([uid])


def test_home_anonimo_igual_que_antes(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Entiende tu examen en minutos" in r.text
    assert "Añade un documento" in r.text
    assert "Hola de nuevo" not in r.text


# ------------------------------------------------- campos nuevos de la lista


def _exp_campos(**kw):
    base = {
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen",
        "biomarkers": [
            {"name": "Colesterol", "value": "210", "unit": "mg/dL",
             "ref_range": "0 - 200", "flag": "alto", "plain_explanation": "x"},
            {"name": "Glucosa", "value": "90", "unit": "mg/dL",
             "ref_range": "70 - 99", "flag": "normal", "plain_explanation": "x"},
        ],
        "urgency": {"level": "no_urgente", "label": "Sin urgencia",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }
    base.update(kw)
    return base


@pytest.fixture()
def usuario_campos():
    from datetime import datetime, timezone
    email = f"c-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    # Previo (hace 60 días): colesterol peor, glucosa fuera de rango.
    previo = "c-" + uuid.uuid4().hex[:12]
    exp_prev = _exp_campos()
    exp_prev["biomarkers"][0].update({"value": "260"})
    exp_prev["biomarkers"][1].update({"value": "120", "flag": "alto"})
    dal.save_intake({"id": previo, "status": "ready", "explanation": exp_prev,
                     "owner_user_id": uid})
    dal.save_document({"id": previo, "user_id": uid, "intake_id": previo,
                       "explanation": exp_prev})
    con = sqlite3.connect(_db_path())
    con.execute(
        "UPDATE documents SET created_at=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(), previo))
    con.execute(
        "UPDATE intakes SET created_at=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(), previo))
    con.commit()
    con.close()
    # Actual: con laboratorio, por confirmar y enlace compartido.
    actual = "c-" + uuid.uuid4().hex[:12]
    exp_act = _exp_campos(laboratorio="Lab Clínico Norte",
                          uncertain_fields=[{"field": "x", "reason": "y"}])
    dal.save_intake({"id": actual, "status": "ready", "explanation": exp_act,
                     "owner_user_id": uid})
    dal.save_document({"id": actual, "user_id": uid, "intake_id": actual,
                       "explanation": exp_act})
    dal.create_share({
        "intake_id": actual, "token": "tok-" + uuid.uuid4().hex[:8],
        "snapshot": {},
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    })
    yield {"uid": uid, "actual": actual, "previo": previo}
    _clean([uid, actual, previo])


def test_home_muestra_laboratorio_por_confirmar_y_compartido(
        client, usuario_campos):
    r = client.get("/", cookies=_cookies(usuario_campos["uid"]))
    assert r.status_code == 200
    html = r.text
    assert "Lab Clínico Norte" in html
    assert "por confirmar" in html
    assert "Compartido con tu doctor" in html


def test_home_compara_con_examen_anterior(client, usuario_campos):
    r = client.get("/", cookies=_cookies(usuario_campos["uid"]))
    html = r.text
    # Colesterol: 260 -> 210 (sigue alto pero más cerca del rango) = mejoró.
    # Glucosa: 120 alto -> 90 en rango = mejoró.
    assert "2 mejoraron" in html
    assert "Desde tu examen de" in html


def test_comparar_empeora_y_sin_datos():
    import app.main as m
    bios_prev = [{"name": "X", "value": "90", "ref_range": "70 - 99"}]
    bios_act = [{"name": "X", "value": "150", "ref_range": "70 - 99"}]
    mej, emp, _ = m._comparar_con_anterior(
        bios_act, [{"explanation": {"biomarkers": bios_prev},
                    "created_at": "2026-01-01T00:00:00+00:00"}])
    assert (mej, emp) == (0, 1)
    # Sin biomarcadores comparables: no hay resumen.
    mej, emp, fecha = m._comparar_con_anterior(
        [{"name": "Y", "value": "no numérico", "ref_range": "1 - 5"}], [])
    assert (mej, emp, fecha) == (0, 0, "")


# ------------------------------------------------- fecha del documento


@pytest.fixture()
def usuario_fechas():
    from datetime import datetime, timezone
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    ids = []

    def _doc(fecha_doc):
        did = "f-" + uuid.uuid4().hex[:12]
        exp = _exp_campos(fecha_documento=fecha_doc)
        dal.save_intake({"id": did, "status": "ready", "explanation": exp,
                         "owner_user_id": uid})
        dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                           "explanation": exp})
        ids.append(did)
        return did

    # Subido hoy pero completado por el laboratorio hace 3 meses.
    con_fecha = _doc("2026-06-27")
    # Subido hoy, sin fecha de documento: usa la de subida.
    sin_fecha = _doc(None)
    yield {"uid": uid, "con_fecha": con_fecha, "sin_fecha": sin_fecha}
    _clean([uid] + ids)


def test_home_muestra_fecha_del_documento_no_de_subida(
        client, usuario_fechas):
    r = client.get("/", cookies=_cookies(usuario_fechas["uid"]))
    html = r.text
    assert "27 jun 2026" in html


def test_home_filtra_por_fecha_del_documento(client):
    from datetime import datetime, timezone
    email = f"f2-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    try:
        # Subido hoy pero con fecha de documento de hace 8 meses: no va
        # en "últimos 6 meses".
        did = "f2-" + uuid.uuid4().hex[:12]
        exp = _exp_campos(fecha_documento="2025-12-01")
        dal.save_intake({"id": did, "status": "ready", "explanation": exp,
                         "owner_user_id": uid})
        dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                           "explanation": exp})
        r = client.get("/", cookies=_cookies(uid))
        assert f"/ficha/{did}" not in r.text
        assert "más de 6 meses" in r.text
    finally:
        _clean([uid, did])
