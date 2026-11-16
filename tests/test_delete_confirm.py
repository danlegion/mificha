"""Borrar un examen pide confirmación con un diálogo propio (no el confirm
nativo del navegador), en la página de resultado y en la ficha."""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, config, storage
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
    for table in ("intakes", "users", "documents", "shares"):
        try:
            for i in ids:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def _exp():
    return {
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen simple",
        "biomarkers": [],
        "urgency": {"level": "no_urgente", "label": "Sin urgencia",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }


@pytest.fixture()
def intake_dueno():
    email = f"del-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "g-" + uuid.uuid4().hex[:12]
    key = storage.store_original(iid, b"%PDF-1.4 prueba")
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "original_key": key, "owner_user_id": uid})
    data = {"uid": uid, "iid": iid, "key": key}
    yield data
    _clean([uid, iid])


@pytest.fixture()
def doc_guardado():
    email = f"del-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "d-" + uuid.uuid4().hex[:12]
    key = storage.store_original(iid, b"%PDF-1.4 prueba")
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "original_key": key, "owner_user_id": uid})
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "original_key": key, "explanation": _exp()})
    data = {"uid": uid, "iid": iid, "key": key}
    yield data
    _clean([uid, iid])


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def test_resultado_usa_dialogo_no_confirm_nativo(client, intake_dueno):
    r = client.get(f"/resultado/{intake_dueno['iid']}",
                   cookies=_cookies(intake_dueno["uid"]))
    assert r.status_code == 200
    assert 'data-confirm-dialog="delete-dialog"' in r.text
    assert '<dialog class="confirm-dialog" id="delete-dialog"' in r.text
    assert "data-confirm-submit" in r.text
    assert "Cancelar" in r.text
    assert "onsubmit=\"return confirm" not in r.text


def test_ficha_detalle_usa_dialogo_no_confirm_nativo(client, doc_guardado):
    r = client.get(f"/ficha/{doc_guardado['iid']}",
                   cookies=_cookies(doc_guardado["uid"]))
    assert r.status_code == 200
    assert 'data-confirm-dialog="delete-dialog"' in r.text
    assert '<dialog class="confirm-dialog" id="delete-dialog"' in r.text
    assert "onsubmit=\"return confirm" not in r.text
