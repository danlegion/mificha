"""Tarjetas en la página de explicación (/resultado/{id}) y detalle guardado.

- /resultado/{id} muestra: tarjeta de documento original (ver/descargar),
  tarjeta de compartir y tarjeta de eliminar.
- GET /resultado/{id}/original sirve el PDF (inline o attachment).
- POST /resultado/{id}/eliminar borra intake, original y documento
  guardado si existe; solo quien tiene acceso puede borrar.
- /ficha/{id} (detalle guardado) muestra la tarjeta real de compartir
  con el doctor (crear/revocar enlace).
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, storage
from app.db import dal
from app.routers import hash_token, new_guest_token


@pytest.fixture(scope="module")
def client():
    from app.main import app
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
    """Intake reclamado (guardado) con original en disco."""
    email = f"r-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "r-" + uuid.uuid4().hex[:12]
    key = storage.store_original(iid, b"%PDF-1.4 prueba")
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "original_key": key, "owner_user_id": uid})
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "original_key": key, "explanation": _exp()})
    data = {"uid": uid, "iid": iid, "key": key}
    yield data
    _clean([uid, iid])


@pytest.fixture()
def intake_invitado():
    """Intake de invitado (sin dueño) con token de acceso."""
    iid = "g-" + uuid.uuid4().hex[:12]
    token = new_guest_token()
    key = storage.store_original(iid, b"%PDF-1.4 invitado")
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "original_key": key,
                     "guest_token_hash": hash_token(token)})
    data = {"iid": iid, "token": token, "key": key}
    yield data
    _clean([iid])


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def test_resultado_muestra_las_tres_tarjetas(client, intake_dueno):
    r = client.get(f"/resultado/{intake_dueno['iid']}",
                   cookies=_cookies(intake_dueno["uid"]))
    assert r.status_code == 200
    assert "Documento original" in r.text
    assert "Ver documento original" in r.text
    assert "Compartir un resumen" in r.text
    assert "Eliminar este examen" in r.text


def test_resultado_original_inline_y_descarga(client, intake_dueno):
    uid = intake_dueno["uid"]
    iid = intake_dueno["iid"]
    r = client.get(f"/resultado/{iid}/original", cookies=_cookies(uid))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers["content-disposition"]
    r2 = client.get(f"/resultado/{iid}/original?descargar=1",
                    cookies=_cookies(uid))
    assert "attachment" in r2.headers["content-disposition"]


def test_resultado_original_sin_acceso_403(client, intake_dueno):
    r = client.get(f"/resultado/{intake_dueno['iid']}/original")
    assert r.status_code == 403


def test_resultado_eliminar_borra_todo_y_redirige_a_ficha(client, intake_dueno):
    uid = intake_dueno["uid"]
    iid = intake_dueno["iid"]
    key = intake_dueno["key"]
    r = client.post(f"/resultado/{iid}/eliminar", cookies=_cookies(uid),
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    assert dal.get_intake(iid) is None
    assert dal.get_document(iid) is None
    assert storage.get_original_path(key) is None


def test_resultado_eliminar_invitado_redirige_a_home(client, intake_invitado):
    iid = intake_invitado["iid"]
    token = intake_invitado["token"]
    r = client.post(f"/resultado/{iid}/eliminar?t={token}",
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert dal.get_intake(iid) is None


def test_resultado_eliminar_sin_acceso_403(client, intake_invitado):
    r = client.post(f"/resultado/{intake_invitado['iid']}/eliminar",
                    follow_redirects=False)
    assert r.status_code == 403


def test_detalle_guardado_muestra_compartir_con_doctor(client, intake_dueno):
    r = client.get(f"/ficha/{intake_dueno['iid']}",
                   cookies=_cookies(intake_dueno["uid"]))
    assert r.status_code == 200
    assert "Compartir con mi doctor" in r.text
    assert "Crear enlace para compartir" in r.text
    # La tarjeta degradada anterior ya no existe.
    assert "sección de compartir" not in r.text
