"""Regresión: la subida con sesión iniciada debe guardarse en la ficha.

El bug (2026-09-27): un usuario con sesión que subía un examen nunca veía
el documento en su ficha — el intake nacía sin dueño y ningún camino lo
reclamaba (la tarjeta de cuenta se ocultó para sesiones en el commit de
sign in/out). Estos tests cubren:

- POST /subir con sesión asigna owner_user_id al intake.
- POST /subir anónimo no asigna dueño (comportamiento anterior intacto).
- _run_pipeline guarda el documento cuando el intake tiene dueño.
- _run_pipeline no crea documento para intakes anónimos.
- GET /resultado con token reclama y guarda un intake listo sin dueño
  cuando hay sesión (red de seguridad, p. ej. subido antes de entrar).
"""

import os
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, storage
from app.db import dal
from app.routers import hash_token, new_guest_token
from app.routers import ingest as ingest_router
from app.storage import quarantine_path


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents", "shares"):
        for i in ids:
            try:
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


def _pdf_valido():
    import fitz
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), "Hemoglobina 14.2 g/dL")
    return doc.tobytes()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


@pytest.fixture()
def usuario():
    email = f"s-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    yield uid
    _clean([uid])


def _intake_id_de_redirect(respuesta):
    # /procesando/{iid}?t=...
    loc = respuesta.headers["location"]
    return loc.split("/procesando/")[1].split("?")[0]


def test_subir_con_sesion_asigna_dueno(client, usuario, monkeypatch):
    monkeypatch.setattr(ingest_router, "_run_pipeline", lambda iid, lang="es": None)
    r = client.post(
        "/subir",
        files={"archivo": ("examen.pdf", _pdf_valido(), "application/pdf")},
        cookies=_cookies(usuario),
        follow_redirects=False,
    )
    assert r.status_code in (303, 307), r.status_code
    iid = _intake_id_de_redirect(r)
    try:
        intake = dal.get_intake(iid)
        assert intake is not None
        assert intake.get("owner_user_id") == usuario
    finally:
        _clean([iid])


def test_subir_anonimo_no_asigna_dueno(client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_run_pipeline", lambda iid, lang="es": None)
    r = client.post(
        "/subir",
        files={"archivo": ("examen.pdf", _pdf_valido(), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code in (303, 307), r.status_code
    iid = _intake_id_de_redirect(r)
    try:
        intake = dal.get_intake(iid)
        assert intake is not None
        assert intake.get("owner_user_id") is None
    finally:
        _clean([iid])


def test_pipeline_guarda_documento_cuando_hay_dueno(usuario, monkeypatch):
    iid = "p-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "processing",
                     "owner_user_id": usuario})
    with open(quarantine_path(iid), "wb") as f:
        f.write(_pdf_valido())
    monkeypatch.setattr(ingest_router, "process_intake",
                        lambda _iid, _pdf, _hint, lang="es": _exp())
    try:
        ingest_router._run_pipeline(iid)
        doc = dal.get_document(iid)
        assert doc is not None
        assert doc.get("user_id") == usuario
        assert dal.get_intake(iid)["status"] == "ready"
    finally:
        _clean([iid])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass


def test_pipeline_no_crea_documento_sin_dueno(monkeypatch):
    iid = "p-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "processing"})
    with open(quarantine_path(iid), "wb") as f:
        f.write(_pdf_valido())
    monkeypatch.setattr(ingest_router, "process_intake",
                        lambda _iid, _pdf, _hint, lang="es": _exp())
    try:
        ingest_router._run_pipeline(iid)
        assert dal.get_document(iid) is None
        assert dal.get_intake(iid)["status"] == "ready"
    finally:
        _clean([iid])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass


def test_resultado_reclama_intake_sin_dueno_con_token(client, usuario):
    iid = "g-" + uuid.uuid4().hex[:12]
    token = new_guest_token()
    key = storage.store_original(iid, b"%PDF-1.4 rescate")
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "original_key": key,
                     "guest_token_hash": hash_token(token)})
    try:
        r = client.get(f"/resultado/{iid}?t={token}",
                       cookies=_cookies(usuario))
        assert r.status_code == 200, r.status_code
        intake = dal.get_intake(iid)
        assert intake.get("owner_user_id") == usuario
        doc = dal.get_document(iid)
        assert doc is not None
        assert doc.get("user_id") == usuario
        # El documento ya es visible en la ficha del usuario.
        r2 = client.get("/ficha", cookies=_cookies(usuario))
        assert r2.status_code == 200
        assert iid in r2.text
    finally:
        storage.delete_original(key)
        _clean([iid])
