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

from app import config
from app.db import dal
from app.main import app
from app.routers import hash_token, new_guest_token


@pytest.fixture()
def client():
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    try:
        for i in ids:
            con.execute("DELETE FROM intakes WHERE id=?", (i,))
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


def _intake_procesando(age_minutes):
    iid = "stalled-" + uuid.uuid4().hex[:12]
    token = new_guest_token()
    dal.save_intake({"id": iid, "status": "processing",
                     "guest_token_hash": hash_token(token)})
    vieja = (datetime.now(timezone.utc)
             - timedelta(minutes=age_minutes)).isoformat()
    con = sqlite3.connect(_db_path())
    con.execute("UPDATE intakes SET updated_at=? WHERE id=?", (vieja, iid))
    con.commit()
    con.close()
    return iid, token


def test_estado_reciente_sigue_processing(client):
    iid, token = _intake_procesando(2)
    try:
        r = client.get(f"/api/v1/estado/{iid}?t={token}")
        assert r.status_code == 200
        assert r.json()["status"] == "processing"
    finally:
        _clean([iid])


def test_estado_estancado_reporta_fallo_con_mensaje(client):
    iid, token = _intake_procesando(15)
    try:
        r = client.get(f"/api/v1/estado/{iid}?t={token}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "failed"
        assert "de nuevo" in data["message"]
    finally:
        _clean([iid])


def test_procesando_muestra_aviso_lento(client):
    iid, token = _intake_procesando(1)
    try:
        r = client.get(f"/procesando/{iid}?t={token}")
        assert r.status_code == 200
        assert "processing-slow" in r.text
    finally:
        _clean([iid])
