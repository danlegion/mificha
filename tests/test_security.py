"""Seguridad: rate limit de subidas (anti-abuso del crédito de IA)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import ratelimit
from app.routers import ingest as ingest_router


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c
    ratelimit.reset()


def _pdf_valido() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000056 00000 n \n0000000111 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )


def _clean(ids):
    con = sqlite3.connect(config.DATA_DIR + "/mificha.db")
    for table in ("intakes", "users", "documents", "shares"):
        for i in ids:
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _subir(client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_run_pipeline",
                        lambda iid, lang="es": None)
    return client.post(
        "/subir",
        files={"archivo": ("examen.pdf", _pdf_valido(), "application/pdf")},
        follow_redirects=False,
    )


def test_rate_limit_frena_tras_el_tope(client, monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(config, "UPLOAD_RATE_LIMIT_PER_HOUR", 2)
    iids = []
    r1 = r2 = None
    try:
        r1 = _subir(client, monkeypatch)
        r2 = _subir(client, monkeypatch)
        assert r1.status_code in (303, 307)
        assert r2.status_code in (303, 307)
        r3 = _subir(client, monkeypatch)
        assert r3.status_code == 429
        assert "demasiados documentos" in r3.text
    finally:
        for r in (r1, r2):
            if r is None:
                continue
            loc = r.headers.get("location", "")
            if "/procesando/" in loc:
                iids.append(loc.split("/procesando/")[1].split("?")[0])
        _clean(iids)


def test_rate_limit_por_defecto_es_10_por_hora():
    assert config.UPLOAD_RATE_LIMIT_PER_HOUR == 10
