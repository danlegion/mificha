"""Single-password gate: private self-hosted instances lock behind one
credential, and only when it is configured.

Gate on: APP_USERNAME + APP_PASSWORD set, no Supabase. Everything except
/acceso, statics, language switch, logout, and /s/ share links requires
the single-user session. Gate off: the public demo works as before, with
uploads requiring the magic-link session (no guest flow anywhere).
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, ratelimit


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "en"},
                    follow_redirects=False) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean_user(email):
    con = sqlite3.connect(_db_path())
    try:
        con.execute("DELETE FROM users WHERE email=?", (email,))
        con.commit()
    except sqlite3.OperationalError:
        pass
    con.close()


@pytest.fixture()
def gate(monkeypatch):
    monkeypatch.setattr(config, "APP_USERNAME", "owner")
    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret-pass")
    monkeypatch.setattr(config, "USE_SUPABASE", False)
    ratelimit.reset()
    yield
    _clean_user("owner@mificha.local")


def test_gate_off_by_default(client, monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    monkeypatch.setattr(config, "USE_SUPABASE", False)
    assert not auth.single_gate_enabled()
    r = client.get("/")
    assert r.status_code == 200


def test_gate_on_requires_login(client, gate):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/acceso"
    r = client.get("/ficha")
    assert r.status_code == 303
    # Upload without a session is gated too.
    r = client.post("/subir", files={"archivo": ("x.pdf", b"%PDF-1.4")})
    assert r.status_code == 303
    assert r.headers["location"] == "/acceso"


def test_gate_exempts_login_and_share_paths(client, gate):
    r = client.get("/acceso")
    assert r.status_code == 200
    assert "private" in r.text.lower() or "privada" in r.text.lower()
    r = client.get("/s/does-not-exist")
    assert r.status_code in (404, 303)  # not a gate redirect to /acceso
    assert r.headers.get("location") != "/acceso"


def test_gate_wrong_then_right_password(client, gate):
    r = client.post("/acceso", data={"usuario": "owner", "clave": "wrong"})
    assert r.status_code == 401
    r = client.post("/acceso", data={"usuario": "owner", "clave": "s3cret-pass"})
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    cookie = r.headers["set-cookie"]
    assert auth.SESSION_COOKIE in cookie
    # The session cookie now opens the app.
    session = cookie.split(";")[0].split("=", 1)[1]
    r2 = client.get("/", cookies={auth.SESSION_COOKIE: session})
    assert r2.status_code == 200
    # And the single session is the vault owner.
    r3 = client.get("/ficha", cookies={auth.SESSION_COOKIE: session})
    assert r3.status_code == 200


def test_gate_wrong_username_rejected(client, gate):
    r = client.post("/acceso", data={"usuario": "someone-else",
                                     "clave": "s3cret-pass"})
    assert r.status_code == 401


def test_gate_ignores_supabase_mode(client, monkeypatch):
    # With Supabase, magic-link auth owns login; the gate stays off even
    # if the env vars happen to be set.
    monkeypatch.setattr(config, "APP_USERNAME", "owner")
    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret-pass")
    monkeypatch.setattr(config, "USE_SUPABASE", True)
    assert not auth.single_gate_enabled()
