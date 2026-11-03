"""Family members: profiles under one account, not separate logins.

- An account holder creates members and assigns uploads to one.
- The vault groups documents by member, unassigned last.
- A member id belonging to another account is never accepted on upload
  or reassignment.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal
from app.routers import ingest as ingest_router


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, headers={"Accept-Language": "en"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents", "shares", "members"):
        for i in ids:
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
            try:
                con.execute(
                    f"DELETE FROM {table} WHERE owner_user_id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def _exp():
    return {
        "type_label": "Lab result",
        "summary_simple": "All good.",
        "biomarkers": [],
        "urgency": {"level": "no_urgente", "label": "No urgency",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }


def _pdf():
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hemoglobina 14.2 g/dL")
    return doc.tobytes()


@pytest.fixture()
def owner():
    email = f"m-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    yield uid
    _clean([uid])


def test_create_list_get_member(owner):
    member = dal.create_member(owner, "  Heather  ")
    assert member["name"] == "Heather"
    assert member["owner_user_id"] == owner
    assert dal.get_member(member["id"])["name"] == "Heather"
    names = [m["name"] for m in dal.list_members(owner)]
    assert "Heather" in names
    _clean([member["id"]])


def test_create_member_rejects_blank_name(owner):
    with pytest.raises(ValueError):
        dal.create_member(owner, "   ")


def test_documents_filter_by_member(owner):
    m1 = dal.create_member(owner, "Heather")
    m2 = dal.create_member(owner, "Oliver")
    d1 = "m-" + uuid.uuid4().hex[:12]
    d2 = "m-" + uuid.uuid4().hex[:12]
    d3 = "m-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": d1, "user_id": owner, "intake_id": d1,
                       "explanation": _exp(), "member_id": m1["id"]})
    dal.save_document({"id": d2, "user_id": owner, "intake_id": d2,
                       "explanation": _exp(), "member_id": m2["id"]})
    dal.save_document({"id": d3, "user_id": owner, "intake_id": d3,
                       "explanation": _exp()})
    try:
        assert [d["id"] for d in dal.list_documents(owner, m1["id"])] == [d1]
        assert len(dal.list_documents(owner)) == 3
        doc = dal.get_document(d1)
        assert doc["member_id"] == m1["id"]
    finally:
        _clean([d1, d2, d3, m1["id"], m2["id"]])


def test_create_member_via_vault(client, owner):
    r = client.post("/ficha/miembros", data={"nombre": "Eleanor"},
                    cookies=_cookies(owner), follow_redirects=False)
    assert r.status_code in (303, 307), r.status_code
    try:
        assert "Eleanor" in [m["name"] for m in dal.list_members(owner)]
        r2 = client.get("/ficha", cookies=_cookies(owner))
        assert r2.status_code == 200
        assert "Eleanor" in r2.text
    finally:
        for m in dal.list_members(owner):
            _clean([m["id"]])


def test_vault_groups_by_member_and_filters(client, owner):
    m1 = dal.create_member(owner, "Heather")
    m2 = dal.create_member(owner, "Oliver")
    d1 = "m-" + uuid.uuid4().hex[:12]
    d2 = "m-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": d1, "user_id": owner, "intake_id": d1,
                       "explanation": _exp(), "member_id": m1["id"]})
    dal.save_document({"id": d2, "user_id": owner, "intake_id": d2,
                       "explanation": _exp(), "member_id": m2["id"]})
    try:
        r = client.get("/ficha", cookies=_cookies(owner))
        assert r.status_code == 200
        assert "Heather" in r.text and "Oliver" in r.text
        assert d1 in r.text and d2 in r.text
        r = client.get(f"/ficha?miembro={m1['id']}",
                       cookies=_cookies(owner))
        assert r.status_code == 200
        assert d1 in r.text
        assert d2 not in r.text
    finally:
        _clean([d1, d2, m1["id"], m2["id"]])


def test_upload_with_new_member_assigns_intake(client, owner, monkeypatch):
    monkeypatch.setattr(ingest_router, "_run_pipeline",
                        lambda iid, lang="es": None)
    r = client.post(
        "/subir",
        files={"archivo": ("examen.pdf", _pdf(), "application/pdf")},
        data={"nuevo_miembro": "Heather"},
        cookies=_cookies(owner),
        follow_redirects=False,
    )
    assert r.status_code in (303, 307), r.status_code
    iid = r.headers["location"].split("/procesando/")[1].split("?")[0]
    try:
        intake = dal.get_intake(iid)
        assert intake is not None
        assert intake.get("owner_user_id") == owner
        member_id = intake.get("member_id")
        assert member_id
        member = dal.get_member(member_id)
        assert member["name"] == "Heather"
        assert member["owner_user_id"] == owner
    finally:
        _clean([iid])
        for m in dal.list_members(owner):
            _clean([m["id"]])


def test_upload_with_foreign_member_is_rejected(client, owner):
    other = dal.create_user(f"o-{uuid.uuid4().hex[:8]}@x.test")["id"]
    foreign = dal.create_member(other, "Stranger")
    try:
        r = client.post(
            "/subir",
            files={"archivo": ("examen.pdf", _pdf(), "application/pdf")},
            data={"miembro": foreign["id"]},
            cookies=_cookies(owner),
            follow_redirects=False,
        )
        assert r.status_code == 400, r.status_code
    finally:
        _clean([foreign["id"], other])


def test_reassign_document_to_member(client, owner):
    member = dal.create_member(owner, "Heather")
    did = "m-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": did, "user_id": owner, "intake_id": did,
                       "explanation": _exp()})
    try:
        r = client.post(f"/ficha/{did}/miembro",
                        data={"miembro": member["id"]},
                        cookies=_cookies(owner), follow_redirects=False)
        assert r.status_code in (303, 307), r.status_code
        assert dal.get_document(did)["member_id"] == member["id"]
        r2 = client.get(f"/ficha/{did}", cookies=_cookies(owner))
        assert r2.status_code == 200
        assert "Heather" in r2.text
    finally:
        _clean([did, member["id"]])


def test_pipeline_saves_document_with_member(owner, monkeypatch):
    from app.routers import ingest as ingest_router
    from app.storage import quarantine_path
    import os
    member = dal.create_member(owner, "Heather")
    iid = "m-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "processing",
                     "owner_user_id": owner, "member_id": member["id"]})
    with open(quarantine_path(iid), "wb") as f:
        f.write(_pdf())
    monkeypatch.setattr(ingest_router, "process_intake",
                        lambda _iid, _pdf_bytes, _hint, lang="es": _exp())
    try:
        ingest_router._run_pipeline(iid)
        doc = dal.get_document(iid)
        assert doc is not None
        assert doc["member_id"] == member["id"]
    finally:
        _clean([iid, member["id"]])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass
