"""Tests del registro de cuenta: flujo OTP por correo (Supabase) y dev local.

- Modo Supabase: POST /guardar/{id} envía el código y muestra el paso 2;
  POST /guardar/{id}/verificar valida el código y crea la sesión.
- Modo desarrollo (sin Supabase): el registro directo reclama el intake.
"""
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal
from app.routers import accounts, hash_token


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


@pytest.fixture()
def intake_lista():
    """Intake listo con token de invitado válido."""
    iid = "t-" + uuid.uuid4().hex[:12]
    raw_token = "tok-" + uuid.uuid4().hex[:8]
    dal.save_intake({
        "id": iid,
        "guest_token_hash": hash_token(raw_token),
        "status": "ready",
        "explanation": {"summary_simple": "Prueba"},
    })
    yield {"id": iid, "token": raw_token}
    _clean([iid])


# ---------------------------------------------------------------- desarrollo

def test_dev_register_reclama_y_abre_ficha(client, intake_lista):
    iid = intake_lista["id"]
    r = client.get(f"/guardar/{iid}?t={intake_lista['token']}")
    assert r.status_code == 200
    assert "Enviarme el enlace" in r.text

    r = client.post(f"/guardar/{iid}",
                    data={"email": "dev-test@example.com",
                          "telefono": "+593991234567",
                          "t": intake_lista["token"]},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    assert auth.SESSION_COOKIE in r.cookies

    # La cookie de sesión abre la ficha (get_current_user funciona).
    r = client.get("/ficha", cookies={auth.SESSION_COOKIE:
                                      r.cookies[auth.SESSION_COOKIE]})
    assert r.status_code == 200

    _clean([iid])


def test_dev_register_email_invalido(client, intake_lista):
    iid = intake_lista["id"]
    r = client.post(f"/guardar/{iid}",
                    data={"email": "no-es-correo", "telefono": "+59399111",
                          "t": intake_lista["token"]})
    assert r.status_code == 400
    assert "no parece válido" in r.text


# ------------------------------------------------------------------ Supabase

class FakeDAL:
    def __init__(self, intake):
        self.intake = intake
        self.users = {}
        self.claimed = {}

    def get_intake(self, intake_id):
        return self.intake if self.intake["id"] == intake_id else None

    def create_user(self, email, telefono=None):
        uid = "u-" + uuid.uuid4().hex[:12]
        self.users[uid] = {"id": uid, "email": email, "telefono": telefono}
        return self.users[uid]

    def get_user_by_email(self, email):
        for u in self.users.values():
            if u["email"] == (email or "").strip().lower():
                return u
        return None

    def claim_intake(self, intake_id, user_id):
        self.claimed[intake_id] = user_id
        self.intake["owner_user_id"] = user_id

    def save_document(self, doc):
        self.doc = doc
        return doc


@pytest.fixture()
def sb(monkeypatch):
    """Modo Supabase con DAL y Auth falsos (sin red)."""
    intake = {"id": "sb-" + uuid.uuid4().hex[:8],
              "guest_token_hash": hash_token("tok-sb"),
              "status": "ready",
              "explanation": {"summary_simple": "Prueba"},
              "original_key": None}
    fake = FakeDAL(intake)
    monkeypatch.setattr("app.config.USE_SUPABASE", True)
    monkeypatch.setattr(accounts, "dal", fake)
    enviados = {}

    def _fake_send(email, redirect_to=None):
        enviados["email"] = email
        enviados["redirect_to"] = redirect_to

    monkeypatch.setattr(auth, "send_magic_link", _fake_send)
    fake.enviados = enviados
    return fake


def test_sb_guardar_envia_enlace_y_muestra_paso(client, sb):
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}",
                    data={"email": "  Test@Example.com ",
                          "telefono": "+593992223333", "t": "tok-sb"})
    assert r.status_code == 200
    assert "Revisa tu correo" in r.text
    assert "test@example.com" in r.text  # normalizado a minúsculas
    assert "enlace seguro" in r.text
    assert sb.enviados["email"] == "test@example.com"
    assert sb.enviados["redirect_to"].endswith("/cuenta/activar")
    assert "test@example.com" in accounts._PENDIENTE


def test_sb_activar_enlace_ok_crea_sesion(client, sb, monkeypatch):
    monkeypatch.setattr(
        auth, "complete_magic_link",
        lambda access_token="", token_hash="": {"id": "sb-auth-1",
                                                "email": "activa@example.com"})
    accounts._pendiente_guardar("activa@example.com", "+593993334444",
                                sb.intake["id"], "tok-sb")
    r = client.post("/cuenta/activar", json={"access_token": "tok-bueno"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["redirect"] == "/ficha"
    assert auth.SESSION_COOKIE in r.cookies
    assert sb.claimed[sb.intake["id"]]  # intake reclamado
    assert "activa@example.com" not in accounts._PENDIENTE  # un solo uso


def test_sb_activar_enlace_malo_muestra_error(client, sb, monkeypatch):
    def _falla(access_token="", token_hash=""):
        raise auth.AuthError("codigo_invalido", "Ese enlace no es válido.")
    monkeypatch.setattr(auth, "complete_magic_link", _falla)
    r = client.post("/cuenta/activar", json={"access_token": "tok-malo"})
    assert r.status_code == 400
    assert "no es válido" in r.json()["message"]


def test_sb_reenviar_enlace(client, sb):
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}/reenviar",
                    data={"email": "reenvio@example.com",
                          "telefono": "+593992223333", "t": "tok-sb"})
    assert r.status_code == 200
    assert "enlace nuevo" in r.text
    assert sb.enviados["email"] == "reenvio@example.com"


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

def test_sb_entrar_envia_enlace(client, sb):
    r = client.post("/entrar", data={"email": "  Login@Example.com "})
    assert r.status_code == 200
    assert "Revisa tu correo" in r.text
    assert "login@example.com" in r.text
    assert sb.enviados["email"] == "login@example.com"
    assert sb.enviados["redirect_to"].endswith("/cuenta/activar")
    # entrar no crea registro pendiente: es solo inicio de sesión
    assert "login@example.com" not in accounts._PENDIENTE


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


def test_sb_activar_sin_pendiente_ni_cuenta_da_404(client, sb, monkeypatch):
    monkeypatch.setattr(
        auth, "complete_magic_link",
        lambda access_token="", token_hash="": {"id": "sb-y",
                                                "email": "nadie@example.com"})
    r = client.post("/cuenta/activar", json={"access_token": "tok-ok"})
    assert r.status_code == 404
    assert "No encontramos una cuenta" in r.json()["message"]


def test_salir_borra_cookie_y_redirige(client):
    client.cookies.set(auth.SESSION_COOKIE, "valor-falso")
    try:
        r = client.get("/salir", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert auth.SESSION_COOKIE in r.headers.get("set-cookie", "")
    finally:
        client.cookies.clear()


def test_result_oculta_tarjeta_con_sesion():
    """La tarjeta 'Crear mi cuenta gratis' no sale si ya hay sesión."""
    from app.main import app
    iid = "r-" + uuid.uuid4().hex[:12]
    raw_token = "tok-" + uuid.uuid4().hex[:12]
    dal.save_intake({
        "id": iid,
        "guest_token_hash": hash_token(raw_token),
        "status": "ready",
        "explanation": {"summary_simple": "Prueba"},
    })
    user = dal.create_user(email="ficha-test@example.com", telefono=None)
    try:
        with TestClient(app, headers={"Accept-Language": "es"}) as c:
            anon = c.get(f"/resultado/{iid}?t={raw_token}")
            assert anon.status_code == 200
            assert "Crear mi cuenta gratis" in anon.text
            assert ">Entrar</a>" in anon.text
            c.cookies.set(auth.SESSION_COOKIE,
                          auth.create_session(user["id"]))
            logueado = c.get(f"/resultado/{iid}?t={raw_token}")
            assert logueado.status_code == 200
            assert "Crear mi cuenta gratis" not in logueado.text
            assert ">Salir</a>" in logueado.text
    finally:
        _clean([iid, user["id"]])
