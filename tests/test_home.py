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


def test_home_sin_lista_reciente(client, usuario_con_docs):
    r = client.get("/", cookies=_cookies(usuario_con_docs["uid"]))
    assert r.status_code == 200
    html = r.text
    # El hero y la subida siguen; la tabla de exámenes recientes ya no.
    assert "Añade un documento" in html
    assert "<h2>Tus exámenes</h2>" not in html
    assert f"/ficha/{usuario_con_docs['reciente']}" not in html
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


# ------------------------------------------------- fecha del documento


def test_home_ultimo_explicado_usa_fecha_del_documento(client):
    from datetime import datetime, timedelta, timezone
    from app.i18n import MESES_CORTO
    email = f"f3-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    try:
        # Subido hoy pero con fecha de documento de hace 90 días: el
        # hero muestra la fecha del documento, no la de subida.
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        fecha_doc = dt.date().isoformat()
        did = "f3-" + uuid.uuid4().hex[:12]
        exp = _exp_campos(fecha_documento=fecha_doc)
        dal.save_intake({"id": did, "status": "ready", "explanation": exp,
                         "owner_user_id": uid})
        dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                           "explanation": exp})
        r = client.get("/", cookies=_cookies(uid))
        esperado = f"{dt.day} {MESES_CORTO['es'][dt.month - 1]} {dt.year}"
        assert esperado in r.text
    finally:
        _clean([uid, did])


def test_home_ultimo_explicado_solo_ultimos_6_meses(client):
    from datetime import datetime, timezone
    email = f"f2-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    try:
        # Subido hoy pero con fecha de documento de hace 8 meses: el
        # hero no muestra línea de último explicado.
        did = "f2-" + uuid.uuid4().hex[:12]
        exp = _exp_campos(fecha_documento="2025-12-01")
        dal.save_intake({"id": did, "status": "ready", "explanation": exp,
                         "owner_user_id": uid})
        dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                           "explanation": exp})
        r = client.get("/", cookies=_cookies(uid))
        assert f"/ficha/{did}" not in r.text
        assert "Último explicado" not in r.text
    finally:
        _clean([uid, did])

