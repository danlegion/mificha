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
    with TestClient(app) as c:
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
    assert html.index("Explica otro examen") < html.index("<h2>Tus exámenes</h2>")
    # La lista trae fecha, tipo, urgencia y fuera de rango.
    assert "Resultado de laboratorio" in html
    assert "Agenda una cita" in html
    assert "2 valores fuera de rango" in html
    assert f"/ficha/{usuario_con_docs['reciente']}" in html
    # El documento viejo (>6 meses) no aparece en el home.
    assert f"/ficha/{usuario_con_docs['viejo']}" not in html


def test_home_usuario_sin_docs(client):
    email = f"h0-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    try:
        r = client.get("/", cookies=_cookies(uid))
        assert "Explica tu primer examen" in r.text
        assert "<h2>Tus exámenes</h2>" not in r.text
    finally:
        _clean([uid])


def test_home_anonimo_igual_que_antes(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Entiende tu examen en minutos" in r.text
    assert "Explica tu primer examen" in r.text
    assert "Hola de nuevo" not in r.text
