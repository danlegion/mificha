"""Site navigation: one hierarchy, and a way back to where you were.

Structure: Home (/) -> Vault (/ficha, with Exams | Vaccines tabs)
-> one document (/ficha/<id>). The header marks the current section,
and the two deep pages (vault detail, fresh result) carry breadcrumbs
whose "Vault" link keeps the member filter, so going back returns
to the filtered list you came from.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal


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
                con.execute(f"DELETE FROM {table} WHERE owner_user_id=?",
                            (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


@pytest.fixture
def owner():
    email = f"nav-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    yield uid
    _clean([uid])


def _exp():
    return {
        "document_type": "lab_results",
        "type_label": "Lab results",
        "nombre_examen": "Complete blood count",
        "summary_simple": "Everything looks normal.",
        "biomarkers": [],
        "vaccines": [],
        "ordered_tests": [],
        "narrative_findings": [],
        "uncertain_fields": [],
    }


def test_header_nav_marks_current_section(client, owner):
    r = client.get("/", cookies=_cookies(owner))
    assert r.status_code == 200
    assert '<a href="/" aria-current="page">Home</a>' in r.text
    assert '<a href="/ficha">Vault</a>' in r.text

    r = client.get("/ficha", cookies=_cookies(owner))
    assert r.status_code == 200
    assert '<a href="/ficha" aria-current="page">Vault</a>' in r.text
    assert '<a href="/">Home</a>' in r.text


def test_header_nav_signed_out_has_no_file_links(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<a href="/ficha"' not in r.text
    assert ">Log in<" in r.text


def test_vault_detail_breadcrumb_keeps_member_filter(client, owner):
    member = dal.create_member(owner, "Ana")
    did = "n-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": did, "user_id": owner, "intake_id": did,
                       "explanation": _exp(), "member_id": member["id"]})
    try:
        r = client.get(f"/ficha/{did}", cookies=_cookies(owner))
        assert r.status_code == 200
        assert 'class="crumbs"' in r.text
        assert (f'href="/ficha?miembro={member["id"]}"' in r.text), r.text
        assert "Complete blood count" in r.text
    finally:
        _clean([did, member["id"]])


def test_result_breadcrumb_keeps_member_filter(client, owner):
    member = dal.create_member(owner, "Ana")
    iid = "n-" + uuid.uuid4().hex[:12]
    dal.save_intake({"id": iid, "status": "ready", "explanation": _exp(),
                     "owner_user_id": owner, "member_id": member["id"]})
    try:
        r = client.get(f"/resultado/{iid}", cookies=_cookies(owner))
        assert r.status_code == 200
        assert 'class="crumbs"' in r.text
        assert (f'href="/ficha?miembro={member["id"]}"' in r.text), r.text
        assert "Complete blood count" in r.text
    finally:
        _clean([iid, member["id"]])
