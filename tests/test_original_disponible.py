"""Documento original ausente: sin 500 y con aviso en la tarjeta.

- GET /ficha/{doc_id}/original con clave vacía responde 404 (antes: 500,
  porque get_original_path("") devolvía el propio directorio y FileResponse
  tronaba al intentar servirlo).
- La tarjeta "Documento original" del detalle muestra un aviso cuando el
  original ya no existe (documentos guardados antes de la mejora de
  almacenamiento, sep 2026) en vez de botones muertos; con original
  disponible muestra Ver/Descargar.
"""

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


def _clean(ids):
    con = sqlite3.connect(config.DATA_DIR + "/mificha.db")
    for table in ("intakes", "users", "documents"):
        for i in ids:
            con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
    con.commit()
    con.close()


@pytest.fixture()
def docs():
    email = f"o-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    sin = "o-" + uuid.uuid4().hex[:12]   # sin original
    con = "o-" + uuid.uuid4().hex[:12]   # con original en disco
    for iid in (sin, con):
        dal.save_intake({"id": iid, "status": "ready",
                         "explanation": {"summary_simple": "x"}})
    dal.save_document({"id": sin, "user_id": uid, "intake_id": sin,
                       "original_key": "", "explanation": {"summary_simple": "x"}})
    key = storage.store_original(con, b"%PDF-1.4 prueba")
    dal.save_document({"id": con, "user_id": uid, "intake_id": con,
                       "original_key": key, "explanation": {"summary_simple": "x"}})
    yield {"uid": uid, "sin": sin, "con": con, "key": key}
    storage.delete_original(key)
    _clean([uid, sin, con])


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def test_get_original_path_clave_vacia_es_none():
    assert storage.get_original_path("") is None


def test_original_exists_clave_vacia_es_false():
    assert storage.original_exists("") is False


def test_original_sin_clave_responde_404_no_500(client, docs):
    r = client.get(f"/ficha/{docs['sin']}/original",
                   cookies=_cookies(docs["uid"]))
    assert r.status_code == 404


def test_detalle_sin_original_muestra_aviso(client, docs):
    r = client.get(f"/ficha/{docs['sin']}", cookies=_cookies(docs["uid"]))
    assert r.status_code == 200
    assert "ya no est\u00e1 disponible" in r.text
    assert "Ver documento original" not in r.text


def test_detalle_con_original_muestra_botones(client, docs):
    r = client.get(f"/ficha/{docs['con']}", cookies=_cookies(docs["uid"]))
    assert r.status_code == 200
    assert "Ver documento original" in r.text
    assert "ya no est\u00e1 disponible" not in r.text
