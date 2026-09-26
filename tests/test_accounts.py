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
    with TestClient(app) as c:
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
    assert "Enviarme el código" in r.text

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
    monkeypatch.setattr(auth, "send_email_otp",
                        lambda email: fake.__dict__.update(sent_to=email))
    return fake


def test_sb_guardar_envia_codigo_y_muestra_paso2(client, sb, monkeypatch):
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}",
                    data={"email": "  Test@Example.com ",
                          "telefono": "+593992223333", "t": "tok-sb"})
    assert r.status_code == 200
    assert "Revisa tu correo" in r.text
    assert "test@example.com" in r.text  # normalizado a minúsculas
    assert sb.__dict__["sent_to"] == "test@example.com"
    assert f'action="/guardar/{iid}/verificar"' in r.text


def test_sb_verificar_codigo_ok_crea_sesion(client, sb, monkeypatch):
    monkeypatch.setattr(auth, "verify_email_otp",
                        lambda email, code: {"id": "sb-auth-1",
                                             "email": email})
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}/verificar",
                    data={"email": "test@example.com",
                          "telefono": "+593992223333",
                          "codigo": "123 456", "t": "tok-sb"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ficha"
    assert auth.SESSION_COOKIE in r.cookies
    assert sb.claimed[iid]  # intake reclamado
    assert sb.doc["user_id"] == sb.claimed[iid]


def test_sb_verificar_codigo_malo_muestra_error(client, sb, monkeypatch):
    def _falla(email, code):
        raise auth.AuthError("codigo_invalido", "Ese código no es válido.")
    monkeypatch.setattr(auth, "verify_email_otp", _falla)
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}/verificar",
                    data={"email": "test@example.com", "telefono": "",
                          "codigo": "000000", "t": "tok-sb"})
    assert r.status_code == 400
    assert "Ese código no es válido" in r.text
    assert "Revisa tu correo" in r.text  # sigue en el paso del código


def test_sb_reenviar_codigo(client, sb):
    iid = sb.intake["id"]
    r = client.post(f"/guardar/{iid}/reenviar",
                    data={"email": "test@example.com",
                          "telefono": "+593992223333", "t": "tok-sb"})
    assert r.status_code == 200
    assert "código nuevo" in r.text


# ------------------------------------------------- unidad: verify_email_otp

def test_verify_otp_tolera_espacios(monkeypatch):
    visto = {}

    class FakeAuth:
        def verify_otp(self, params):
            visto.update(params)
            return type("R", (), {"user": type(
                "U", (), {"id": "x", "email": "a@b.c"})()})()

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth, "_supabase_client", lambda: FakeClient())
    monkeypatch.setattr("app.config.USE_SUPABASE", True)
    out = auth.verify_email_otp("a@b.c", " 123 456 ")
    assert visto["token"] == "123456"
    assert visto["type"] == "email"
    assert out["id"] == "x"


def test_verify_otp_mapea_error_amable(monkeypatch):
    class FakeAuth:
        def verify_otp(self, params):
            raise Exception("Token has expired or is invalid")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth, "_supabase_client", lambda: FakeClient())
    monkeypatch.setattr("app.config.USE_SUPABASE", True)
    with pytest.raises(auth.AuthError) as e:
        auth.verify_email_otp("a@b.c", "000000")
    assert e.value.kind == "codigo_invalido"
    assert "vencido" in str(e.value) or "válido" in str(e.value)
