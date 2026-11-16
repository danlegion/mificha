"""La subida responde rápido: el redirect a /procesando no espera a Storage.

El bug de UX (2026-09-30): entre aplastar "Explicar mi examen" y ver la
página de proceso pasaba demasiado tiempo, sin ninguna señal visual.
Dos causas en el camino crítico:
1. El servidor subía el PDF a Supabase Storage ANTES de redirigir.
2. El formulario no mostraba progreso de subida.
Ahora: el redirect sale apenas se valida el PDF; el original se sube en
segundo plano; y el formulario muestra el porcentaje real de subida.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal
from app.routers import ingest as ingest_router
from app.storage import quarantine_path


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _pdf_valido() -> bytes:
    # PDF mínimo que PyMuPDF puede abrir (1 página).
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000056 00000 n \n0000000111 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )


def _clean(ids):
    import sqlite3
    con = sqlite3.connect(config.DATA_DIR + "/mificha.db")
    for table in ("intakes", "users", "documents", "shares"):
        for i in ids:
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def test_subir_no_guarda_original_antes_del_redirect(client, monkeypatch):
    """El POST /subir redirige sin haber subido el original a Storage."""
    monkeypatch.setattr(ingest_router, "_run_pipeline", lambda iid, lang="es": None)
    subidas = []
    monkeypatch.setattr(
        ingest_router, "store_original",
        lambda iid, pdf: subidas.append(iid) or "no-deberia-llamarse.pdf",
    )
    uid = dal.create_user(f"s-{uuid.uuid4().hex[:8]}@x.test")["id"]
    r = client.post(
        "/subir",
        files={"archivo": ("examen.pdf", _pdf_valido(), "application/pdf")},
        cookies={auth.SESSION_COOKIE: auth.create_session(uid)},
        follow_redirects=False,
    )
    assert r.status_code in (303, 307), r.status_code
    iid = r.headers["location"].split("/procesando/")[1].split("?")[0]
    try:
        assert subidas == [], "store_original no debe correr antes del redirect"
        assert dal.get_intake(iid).get("original_key") is None
    finally:
        _clean([iid, uid])


def test_set_intake_original_key_no_toca_otros_campos():
    iid = "k-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "processing",
                     "owner_user_id": "u-1", "doc_type_hint": "lab"})
    try:
        dal.set_intake_original_key(iid, "k.pdf")
        intake = dal.get_intake(iid)
        assert intake["original_key"] == "k.pdf"
        assert intake["status"] == "processing"
        assert intake["owner_user_id"] == "u-1"
        assert intake["doc_type_hint"] == "lab"
    finally:
        _clean([iid])


def test_pipeline_sube_original_en_segundo_plano(monkeypatch):
    """_run_pipeline sube el original y lo deja en intake y documento."""
    iid = "p-" + uuid.uuid4().hex[:12]
    uid = dal.create_user(f"s-{uuid.uuid4().hex[:8]}@x.test")["id"]
    dal.save_intake({"id": iid, "status": "processing", "owner_user_id": uid})
    with open(quarantine_path(iid), "wb") as f:
        f.write(_pdf_valido())
    monkeypatch.setattr(
        ingest_router, "store_original", lambda _iid, _pdf: f"{_iid}.pdf")
    monkeypatch.setattr(
        ingest_router, "process_intake",
        lambda _iid, _pdf, _hint, lang="es": {"urgency": {"level": "ok"}})
    try:
        ingest_router._run_pipeline(iid)
        assert dal.get_intake(iid)["original_key"] == f"{iid}.pdf"
        assert dal.get_document(iid)["original_key"] == f"{iid}.pdf"
    finally:
        _clean([iid, uid])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass


def test_formulario_muestra_progreso(client):
    """La tarjeta de subida trae barra de progreso y subida con XHR."""
    r = client.get("/")
    assert r.status_code == 200
    assert "upload-progress" in r.text
    assert "XMLHttpRequest" in r.text
    assert 'data-upload-btn' in r.text
