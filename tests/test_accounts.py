"""Tests de cuenta: entrar con enlace mágico (Supabase) y dev local.

No hay flujo de invitado: subir exige sesión, y entrar por primera vez
con el enlace mágico crea la cuenta (registro e inicio de sesión son el
mismo paso). En modo desarrollo (sin Supabase) la sesión es directa.
"""
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal
from app.routers import accounts


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table, key in (("intakes", "id"), ("users", "id"),
                       ("documents", "id")):
        for i in ids:
            con.execute(f"DELETE FROM {table} WHERE {key}=?", (i,))
    con.commit()
    con.close()


# ---------------------------------------------------------------- desarrollo

def test_dev_entrar_crea_cuenta_si_no_existe(client):
    email = f"nuevo-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/entrar", data={"email": email},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    assert auth.SESSION_COOKIE in r.cookies
    try:
        assert dal.get_user_by_email(email) is not None
        r = client.get("/ficha", cookies={auth.SESSION_COOKIE:
                                          r.cookies[auth.SESSION_COOKIE]})
        assert r.status_code == 200
    finally:
        user = dal.get_user_by_email(email)
        if user:
            _clean([user["id"]])


def test_dev_entrar_email_invalido(client):
    r = client.post("/entrar", data={"email": "no-es-correo"})
    assert r.status_code == 400
    assert "no parece válido" in r.text


# ------------------------------------------------------------------ Supabase

class FakeDAL:
    def __init__(self):
        self.users = {}

    def get_user_by_email(self, email):
        for u in self.users.values():
            if u["email"] == (email or "").strip().lower():
                return u
        return None

    def create_user(self, email, telefono=None):
        uid = "u-" + uuid.uuid4().hex[:12]
        self.users[uid] = {"id": uid, "email": email, "telefono": telefono}
        return self.users[uid]


@pytest.fixture()
def sb(monkeypatch):
    """Modo Supabase con DAL y Auth falsos (sin red)."""
    fake = FakeDAL()
    monkeypatch.setattr("app.config.USE_SUPABASE", True)
    monkeypatch.setattr(accounts, "dal", fake)
    enviados = {}

    def _fake_send(email, redirect_to=None):
        enviados["email"] = email
        enviados["redirect_to"] = redirect_to

    monkeypatch.setattr(auth, "send_magic_link", _fake_send)
    fake.enviados = enviados
    return fake


def test_sb_entrar_envia_enlace(client, sb):
    r = client.post("/entrar", data={"email": "  Login@Example.com "})
    assert r.status_code == 200
    assert "Revisa tu correo" in r.text
    assert "login@example.com" in r.text
    assert sb.enviados["email"] == "login@example.com"
    assert sb.enviados["redirect_to"].endswith("/cuenta/activar")


def test_sb_activar_enlace_ok_crea_sesion(client, sb, monkeypatch):
    monkeypatch.setattr(
        auth, "complete_magic_link",
        lambda access_token="", token_hash="": {"id": "sb-auth-1",
                                                "email": "activa@example.com"})
    r = client.post("/cuenta/activar", json={"access_token": "tok-bueno"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["redirect"] == "/ficha"
    assert auth.SESSION_COOKIE in r.cookies
    # Primera vez: el enlace crea la cuenta.
    assert sb.get_user_by_email("activa@example.com") is not None


def test_sb_activar_enlace_malo_muestra_error(client, sb, monkeypatch):
    def _falla(access_token="", token_hash=""):
        raise auth.AuthError("codigo_invalido", "Ese enlace no es válido.")
    monkeypatch.setattr(auth, "complete_magic_link", _falla)
    r = client.post("/cuenta/activar", json={"access_token": "tok-malo"})
    assert r.status_code == 400
    assert "no es válido" in r.json()["message"]


def test_sb_activar_pagina_carga(client):
    r = client.get("/cuenta/activar")
    assert r.status_code == 200
    assert "Verificando tu cuenta" in r.text
    assert "/cuenta/activar" in r.text  # el JS apunta al POST


# ------------------------------------------------- unidad: complete_magic_link

def _fake_supabase_user(monkeypatch, user=None, exc=None):
    class FakeAuth:
        def get_user(self, jwt):
            if exc:
                raise exc
            return type("R", (), {"user": user})()

        def verify_otp(self, params):
            if exc:
                raise exc
            return type("R", (), {"user": user})()

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth, "_supabase_client", lambda: FakeClient())
    monkeypatch.setattr("app.config.USE_SUPABASE", True)


def test_complete_magic_link_access_token(monkeypatch):
    user = type("U", (), {"id": "u1", "email": "a@b.c"})()
    _fake_supabase_user(monkeypatch, user=user)
    out = auth.complete_magic_link(access_token="jwt-bueno")
    assert out == {"id": "u1", "email": "a@b.c"}


def test_complete_magic_link_token_hash(monkeypatch):
    user = type("U", (), {"id": "u2", "email": "c@d.e"})()
    _fake_supabase_user(monkeypatch, user=user)
    out = auth.complete_magic_link(token_hash="hash-bueno")
    assert out["id"] == "u2"


def test_complete_magic_link_mapea_error_amable(monkeypatch):
    _fake_supabase_user(monkeypatch,
                        exc=Exception("Token has expired or is invalid"))
    with pytest.raises(auth.AuthError) as e:
        auth.complete_magic_link(access_token="jwt-malo")
    assert e.value.kind == "codigo_invalido"
    assert "vencido" in str(e.value) or "válido" in str(e.value)


# ------------------------------------------------- inicio / cierre de sesión

def test_entrar_con_sesion_redirige_a_ficha(client):
    user = dal.create_user(email="ya-dentro@example.com", telefono=None)
    try:
        client.cookies.set(auth.SESSION_COOKIE,
                           auth.create_session(user["id"]))
        r = client.get("/entrar", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/ficha"
    finally:
        client.cookies.clear()
        _clean([user["id"]])


def test_sb_activar_sin_pendiente_inicia_sesion(client, sb, monkeypatch):
    sb.users["u-existe"] = {"id": "u-existe", "email": "existe@example.com",
                            "telefono": None}
    monkeypatch.setattr(
        auth, "complete_magic_link",
        lambda access_token="", token_hash="": {"id": "sb-x",
                                                "email": "existe@example.com"})
    r = client.post("/cuenta/activar", json={"access_token": "tok-ok"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "redirect": "/ficha"}
    assert auth.SESSION_COOKIE in r.cookies


def test_salir_borra_cookie_y_redirige(client):
    client.cookies.set(auth.SESSION_COOKIE, "valor-falso")
    try:
        r = client.get("/salir", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert auth.SESSION_COOKIE in r.headers.get("set-cookie", "")
    finally:
        client.cookies.clear()


def test_resultado_anonimo_es_403_y_sin_tarjeta_guardar(client):
    """Sin sesión no hay resultado que ver: 403, y nunca la tarjeta
    'Crear mi cuenta gratis' (el flujo de invitado ya no existe)."""
    iid = "r-" + uuid.uuid4().hex[:12]
    dal.save_intake({
        "id": iid,
        "status": "ready",
        "explanation": {"summary_simple": "Prueba"},
    })
    try:
        client.cookies.clear()  # el cliente de módulo puede traer sesión
        r = client.get(f"/resultado/{iid}")
        assert r.status_code == 403
        assert "Crear mi cuenta gratis" not in r.text
    finally:
        _clean([iid])
