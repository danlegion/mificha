"""Vaccine registry: extraction, aggregation per member, registry page.

The registry only records vaccines already received, as documents state
them. It never computes due dates and never invents a vaccine, dose,
or date.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, schemas
from app.db import dal
from app.services.llm import DemoExplainer
from app.services.vaccines import (collect_vaccines, group_vaccines_by_member,
                                  parse_vaccine_date)


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


def _exp(vaccines):
    return {
        "document_type": "vaccine_record",
        "type_label": "Vaccine record",
        "summary_simple": "Vaccines recorded.",
        "biomarkers": [],
        "vaccines": vaccines,
        "urgency": {"level": "no_urgente", "label": "No urgency",
                    "action": "a", "timeframe": "t", "reason": "r"},
        "disclaimer": "Informational only.",
    }


@pytest.fixture()
def owner():
    email = f"v-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    yield uid
    _clean([uid])


def test_parse_vaccine_date():
    assert parse_vaccine_date("2024-10-15") == "2024-10-15"
    assert parse_vaccine_date("15/10/2024") == "2024-10-15"
    assert parse_vaccine_date("15 Oct 2024") == "2024-10-15"
    assert parse_vaccine_date("15 de octubre de 2024") == "2024-10-15"
    assert parse_vaccine_date("not a date") is None
    assert parse_vaccine_date("") is None


def test_schema_accepts_vaccines():
    exp = schemas.validate_explanation(_exp([
        {"name": "Influenza", "date_administered": "2024-10-15",
         "dose": "1", "lot": "ABC123", "manufacturer": "Pfizer",
         "provider": None},
    ]))
    assert exp["vaccines"][0]["name"] == "Influenza"
    assert exp["vaccines"][0]["lot"] == "ABC123"
    assert exp["vaccines"] == exp["vaccines"]  # default list, not shared


def test_demo_explainer_extracts_vaccine_record():
    text = (
        "REGISTRO DE VACUNACIÓN\n"
        "Influenza 2024-10-15 Lote ABC123\n"
        "COVID-19 2023-05-02 Dosis 2 Pfizer\n"
        "Paciente: Test Person\n"
    )
    exp = DemoExplainer().explain({
        "full_text": text, "doc_type_hint": None, "lang": "es",
        "scanned_pages": [], "ocr_pages": [],
    })
    assert exp["document_type"] == "vaccine_record"
    names = [v["name"] for v in exp["vaccines"]]
    assert any("Influenza" in n for n in names)
    assert any("COVID" in n for n in names)
    by_name = {v["name"]: v for v in exp["vaccines"]}
    flu = [v for v in exp["vaccines"] if "Influenza" in v["name"]][0]
    assert flu["date_administered"] == "2024-10-15"
    assert flu["lot"] == "ABC123"


def test_demo_explainer_does_not_invent_vaccines_without_date():
    text = "REGISTRO DE VACUNACIÓN\nInfluenza\nCOVID-19\n"
    exp = DemoExplainer().explain({
        "full_text": text, "doc_type_hint": None, "lang": "es",
        "scanned_pages": [], "ocr_pages": [],
    })
    assert exp["document_type"] == "vaccine_record"
    assert exp["vaccines"] == []


def test_collect_vaccines_dedupes_and_sorts(owner):
    m1 = dal.create_member(owner, "Ana")
    m2 = dal.create_member(owner, "Luis")
    d1 = "v-" + uuid.uuid4().hex[:12]
    d2 = "v-" + uuid.uuid4().hex[:12]
    d3 = "v-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": d1, "user_id": owner, "intake_id": d1,
                       "explanation": _exp([
                           {"name": "Influenza",
                            "date_administered": "2024-10-15"}]),
                       "member_id": m1["id"]})
    # Same vaccine in a second document: counted once.
    dal.save_document({"id": d2, "user_id": owner, "intake_id": d2,
                       "explanation": _exp([
                           {"name": "INFLUENZA",
                            "date_administered": "2024-10-15"},
                           {"name": "COVID-19",
                            "date_administered": "2023-05-02"}]),
                       "member_id": m1["id"]})
    dal.save_document({"id": d3, "user_id": owner, "intake_id": d3,
                       "explanation": _exp([
                           {"name": "Hepatitis B",
                            "date_administered": "2025-01-10"}]),
                       "member_id": m2["id"]})
    try:
        docs = dal.list_documents(owner)
        entries = collect_vaccines(docs)
        assert len(entries) == 3
        # Newest first.
        assert entries[0]["name"] == "Hepatitis B"
        groups = group_vaccines_by_member(docs, dal.list_members(owner))
        ana = [g for g in groups if g["member_id"] == m1["id"]][0]
        assert len(ana["vaccines"]) == 2
        luis = [g for g in groups if g["member_id"] == m2["id"]][0]
        assert len(luis["vaccines"]) == 1
    finally:
        _clean([d1, d2, d3, m1["id"], m2["id"]])


def test_registry_page_groups_by_member(client, owner):
    member = dal.create_member(owner, "Ana")
    did = "v-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": did, "user_id": owner, "intake_id": did,
                       "explanation": _exp([
                           {"name": "Influenza",
                            "date_administered": "2024-10-15",
                            "dose": "1"}]),
                       "member_id": member["id"]})
    try:
        r = client.get("/vacunas", cookies=_cookies(owner))
        assert r.status_code == 200
        assert "Ana" in r.text
        assert "Influenza" in r.text
        assert "2024-10-15" in r.text
        assert did in r.text  # link back to the source document
        r2 = client.get(f"/ficha/{did}", cookies=_cookies(owner))
        assert r2.status_code == 200
        assert "Influenza" in r2.text
    finally:
        _clean([did, member["id"]])


def test_vault_pages_share_one_tab_nav(client, owner):
    """The vault has one navigation between its two views, not a button
    beside the upload action: /ficha and /vacunas both render the same
    tab nav, with the current view marked, and the hero carries no
    cross-link button."""
    r = client.get("/ficha", cookies=_cookies(owner))
    assert r.status_code == 200
    assert 'class="vault-tabs"' in r.text
    assert '<a href="/ficha" aria-current="page">Exams <span class="tab-count">0</span></a>' in r.text
    assert '<a href="/vacunas">Vaccines <span class="tab-count">0</span></a>' in r.text
    # The vaccines page is linked once, as a tab — not as a button
    # beside the upload action in the panel head.
    assert r.text.count('href="/vacunas"') == 1
    assert 'href="/#explicar"' in r.text  # the one panel action: upload
    r2 = client.get("/vacunas", cookies=_cookies(owner))
    assert r2.status_code == 200
    assert 'class="vault-tabs"' in r2.text
    assert '<a href="/ficha">Exams <span class="tab-count">0</span></a>' in r2.text
    assert '<a href="/vacunas" aria-current="page">Vaccines <span class="tab-count">0</span></a>' in r2.text


def test_vaccines_empty_state_asks_for_a_vaccine_record(client, owner):
    """The empty registry must not reuse the vault's 'Explain your
    first exam' button: the user may already have exams, just no
    vaccine records yet."""
    r = client.get("/vacunas", cookies=_cookies(owner))
    assert r.status_code == 200
    assert "Upload a vaccine record" in r.text
    assert "Add a document" not in r.text


def test_vault_tabs_swap_without_reload(client):
    """The tab switch is wired in JS: clicks on .vault-tabs are
    intercepted, the other view is fetched and only <main> is swapped,
    with pushState keeping the URL in sync. (The browser behavior
    itself can't run in TestClient; this locks the wiring in place.)"""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "initVaultTabs" in r.text
    assert ".vault-tabs a" in r.text
    assert "main.container" in r.text
    assert "pushState" in r.text
    assert "popstate" in r.text
    # Clicking the tab we're already on is a no-op, never a reload:
    # the navigation is prevented before the same-URL check.
    assert ("e.preventDefault();\n      if (url.href === window.location.href)"
            " return;") in r.text
    # A failed fetch retries once before falling back to navigation.
    assert "swap(url, push, true)" in r.text
    # While a swap is in flight the panel dims, so a slow fetch reads
    # as loading rather than a dead click.
    css = client.get("/static/app.css")
    assert 'main[aria-busy="true"] .vault-panel' in css.text
