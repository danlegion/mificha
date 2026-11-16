"""El endpoint de estado no deja el spinner girando para siempre.

Si un intake lleva demasiado tiempo en "processing" (la tarea en segundo
plano murió sin escribir estado), /api/v1/estado lo reporta como fallo con
un mensaje útil en lugar de "processing" eternamente.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth, config
from app.db import dal
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users"):
        try:
            for i in ids:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


@pytest.fixture()
def dueno():
    uid = dal.create_user(
        email=f"st-{uuid.uuid4().hex[:8]}@x.test")["id"]
    yield uid
    _clean([uid])


def _intake_procesando(age_minutes, uid):
    iid = "stalled-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "processing",
                     "owner_user_id": uid})
    vieja = (datetime.now(timezone.utc)
             - timedelta(minutes=age_minutes)).isoformat()
    con = sqlite3.connect(_db_path())
    con.execute("UPDATE intakes SET updated_at=? WHERE id=?", (vieja, iid))
    con.commit()
    con.close()
    return iid


def test_estado_reciente_sigue_processing(client, dueno):
    iid = _intake_procesando(2, dueno)
    try:
        r = client.get(f"/api/v1/estado/{iid}", cookies=_cookies(dueno))
        assert r.status_code == 200
        assert r.json()["status"] == "processing"
    finally:
        _clean([iid])


def test_estado_estancado_reporta_fallo_con_mensaje(client, dueno):
    iid = _intake_procesando(15, dueno)
    try:
        r = client.get(f"/api/v1/estado/{iid}", cookies=_cookies(dueno))
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "failed"
        assert "de nuevo" in data["message"]
    finally:
        _clean([iid])


def test_procesando_muestra_aviso_lento(client, dueno):
    iid = _intake_procesando(1, dueno)
    try:
        r = client.get(f"/procesando/{iid}", cookies=_cookies(dueno))
        assert r.status_code == 200
        assert "processing-slow" in r.text
    finally:
        _clean([iid])
