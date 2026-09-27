"""Borrar un examen y descargar el original.

- POST /ficha/{doc_id}/eliminar borra documento, intake, archivo original
  y revoca los enlaces compartidos; redirige a /ficha.
- Solo el dueño puede borrar (otro usuario recibe 404).
- GET /ficha/{doc_id}/original?descargar=1 responde con attachment.
"""

import os
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app import storage
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


@pytest.fixture()
def examen():
    """Usuario dueño + documento con original en disco."""
    email = f"t-{uuid.uuid4().hex[:8]}@x.test"
    user = dal.create_user(email)
    uid = user["id"]
    otro = dal.create_user(f"o-{uuid.uuid4().hex[:8]}@x.test")["id"]
    iid = "t-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "ready",
                     "explanation": {"summary_simple": "x"}})
    key = storage.store_original(iid, b"%PDF-1.4 prueba")
    dal.save_document({
        "id": iid, "user_id": uid, "intake_id": iid,
        "original_key": key, "explanation": {"summary_simple": "x"},
    })
    data = {"uid": uid, "otro": otro, "doc_id": iid, "key": key,
            "emails": [email]}
    yield data
    _clean([uid, otro, iid])


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


# ------------------------------------------------------------------ eliminar


def test_eliminar_borra_todo_y_redirige(client, examen):
    r = client.post(f"/ficha/{examen['doc_id']}/eliminar",
                    cookies=_cookies(examen["uid"]),
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    assert dal.get_document(examen["doc_id"]) is None
    assert dal.get_intake(examen["doc_id"]) is None
    assert not os.path.exists(
        os.path.join(config.DATA_DIR, "originales", examen["key"]))


def test_eliminar_otro_usuario_da_404(client, examen):
    r = client.post(f"/ficha/{examen['doc_id']}/eliminar",
                    cookies=_cookies(examen["otro"]),
                    follow_redirects=False)
    assert r.status_code == 404
    # El documento sigue intacto.
    assert dal.get_document(examen["doc_id"]) is not None


def test_eliminar_sin_sesion_redirige(client, examen):
    r = client.post(f"/ficha/{examen['doc_id']}/eliminar",
                    follow_redirects=False)
    assert r.status_code == 303
    assert dal.get_document(examen["doc_id"]) is not None


# ----------------------------------------------------------------- original


def test_original_inline_por_defecto(client, examen):
    r = client.get(f"/ficha/{examen['doc_id']}/original",
                   cookies=_cookies(examen["uid"]))
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-type"] == "application/pdf"


def test_original_descargar_responde_attachment(client, examen):
    r = client.get(f"/ficha/{examen['doc_id']}/original?descargar=1",
                   cookies=_cookies(examen["uid"]))
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


def test_dal_delete_document_e_intake():
    iid = "t-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "ready"})
    dal.save_document({"id": iid, "user_id": "u-x", "intake_id": iid,
                       "explanation": {}})
    assert dal.delete_document(iid) is True
    assert dal.delete_intake(iid) is True
    assert dal.get_document(iid) is None
    assert dal.get_intake(iid) is None
    assert dal.delete_document(iid) is False
    _clean([iid])


def test_delete_original_borra_archivo():
    key = storage.store_original("t-borrar-test", b"%PDF-1.4 x")
    path = os.path.join(config.DATA_DIR, "originales", key)
    assert os.path.exists(path)
    storage.delete_original(key)
    assert not os.path.exists(path)
    # Idempotente: no falla si ya no existe.
    storage.delete_original(key)
