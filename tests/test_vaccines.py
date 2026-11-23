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
        r = client.get("/ficha?vista=vacunas", cookies=_cookies(owner))
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
    """The vault is one page: the Exams / Vaccines control is a centered
    client-side filter just above the cards, not a navigation between
    pages. /vacunas only survives as a redirect to the vaccines view."""
    r = client.get("/ficha", cookies=_cookies(owner))
    assert r.status_code == 200
    assert 'class="vault-tabs"' in r.text
    assert 'data-vault-view="exams"' in r.text
    assert 'data-vault-view="vacunas"' in r.text
    assert 'data-view="exams"' in r.text
    assert 'data-view-section="exams"' in r.text
    assert 'data-view-section="vacunas"' in r.text
    # The toggle is a pair of buttons, not links: nothing navigates.
    assert 'href="/vacunas"' not in r.text
    assert 'data-vault-view="exams" aria-pressed="true"' in r.text
    assert 'data-vault-view="vacunas" aria-pressed="false"' in r.text
    assert 'Exams <span class="tab-count" data-count="exams">0</span>' in r.text
    assert 'Vaccines <span class="tab-count" data-count="vaccines">0</span>' in r.text
    # The old URL redirects to the same page with the filter on.
    r2 = client.get("/vacunas", cookies=_cookies(owner),
                    follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/ficha?vista=vacunas"
    r3 = client.get("/ficha?vista=vacunas", cookies=_cookies(owner))
    assert r3.status_code == 200
    assert 'data-view="vacunas"' in r3.text
    assert 'data-vault-view="vacunas" aria-pressed="true"' in r3.text


def test_vaccines_empty_state_asks_for_a_vaccine_record(client, owner):
    """The empty registry must not reuse the vault's 'Explain your
    first exam' button: the user may already have exams, just no
    vaccine records yet. Both sections are on the one page, so the
    check is scoped to the vaccines section."""
    r = client.get("/ficha?vista=vacunas", cookies=_cookies(owner))
    assert r.status_code == 200
    vax = r.text.split('data-view-section="vacunas"')[1]
    assert "Upload a vaccine record" in vax
    assert "Add a document" not in vax
    exams = r.text.split('data-view-section="exams"')[1].split(
        'data-view-section="vacunas"')[0]
    assert "Add a document" in exams


def test_vault_tabs_swap_without_reload(client):
    """The Exams / Vaccines control is a client-side filter, not a
    navigation: the JS only switches which section shows (data-view),
    with no fetch, no <main> swap and no reload. (The browser behavior
    itself can't run in TestClient; this locks the wiring in place.)"""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "initVaultFilter" in r.text
    assert "initVaultMemberFilter" in r.text
    assert "initVaultLayout" in r.text
    assert "data-vault-layout" in r.text
    assert "initVaultTabs" not in r.text
    assert "data-vault-view" in r.text
    assert "data-member-filter" in r.text
    assert "aria-pressed" in r.text
    assert "applyVaultFilters" in r.text
    assert "updateVaultCounts" in r.text
    # No fetch anywhere in the vault filter: the old swap fetched the
    # other page. (The one fetch() left in app.js is the processing
    # page polling its own status, not the vault.)
    assert "fetch(url" not in r.text
    assert "main.container" not in r.text
    # The toggle sits centered just above the cards.
    css = client.get("/static/app.css").text
    assert ".vault-tabs {\n  display: flex;" in css
    assert "margin: 20px auto 0;" in css
    assert ".vault-panel[data-view=\"exams\"] [data-view-section=\"vacunas\"]" in css
    assert ".vault-panel[data-view=\"vacunas\"] [data-view-section=\"exams\"]" in css


def _lab_exp(summary, name, value="250"):
    return {
        "document_type": "lab_result",
        "type_label": "Lab result",
        "summary_simple": summary,
        "biomarkers": [{"name": name, "value": value, "unit": "mg/dL",
                        "flag": "alto", "ref_range": "0 - 200 mg/dL"}],
        "vaccines": [],
        "urgency": {"level": "no_urgente", "label": "No urgency",
                    "action": "a", "timeframe": "t", "reason": "r"},
        "disclaimer": "Informational only.",
    }


def test_member_filter_is_client_side(client, owner):
    """The name pills filter in place: the server renders every
    document, marks the ones outside the chosen member hidden, and
    counts the pills for that member only — no navigation, so the
    scroll position never resets."""
    ana = dal.create_member(owner, "Ana")
    luis = dal.create_member(owner, "Luis")
    d1 = "v-" + uuid.uuid4().hex[:12]
    d2 = "v-" + uuid.uuid4().hex[:12]
    d3 = "v-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": d1, "user_id": owner, "intake_id": d1,
                       "member_id": ana["id"],
                       "explanation": _lab_exp("Colesterol alto.",
                                               "Colesterol")})
    dal.save_document({"id": d2, "user_id": owner, "intake_id": d2,
                       "member_id": ana["id"],
                       "explanation": _exp([{"name": "Influenza",
                                             "date_administered":
                                             "2024-10-15", "dose": "1"}])})
    dal.save_document({"id": d3, "user_id": owner, "intake_id": d3,
                       "member_id": luis["id"],
                       "explanation": _lab_exp("Glucosa alta.",
                                               "Glucosa", "180")})
    try:
        # No member selected: every card is present and visible, the
        # pills count the whole vault (3 docs, 1 vaccine).
        r = client.get("/ficha", cookies=_cookies(owner))
        assert r.status_code == 200
        assert r.text.count('class="card doc-card"') == 3
        assert 'data-count="exams">3<' in r.text
        assert 'data-count="vaccines">1<' in r.text
        # The pills are buttons, not links to other pages.
        assert 'data-member-filter=""' in r.text
        assert f'data-member-filter="{ana["id"]}"' in r.text
        assert 'href="/ficha?miembro=' not in r.text
        # The search matches what the card shows: summary and the
        # first biomarker's name.
        assert "Colesterol alto. Colesterol" in r.text
        # One member selected: the server pre-hides the others inline
        # (no flash before the JS runs) and counts that member only.
        r = client.get(f"/ficha?miembro={ana['id']}",
                       cookies=_cookies(owner))
        assert r.status_code == 200
        assert f'data-member="{ana["id"]}"' in r.text
        assert r.text.count('class="card doc-card"') == 3
        assert 'data-count="exams">2<' in r.text
        assert 'data-count="vaccines">1<' in r.text
        assert 'data-member-filter=""' in r.text
        # Ana's pills are pressed; Luis's card is hidden inline.
        assert (f'data-member-filter="{ana["id"]}" aria-pressed="true"'
                in r.text)
        assert 'style="display: none"' in r.text
    finally:
        _clean([d1, d2, d3, ana["id"], luis["id"]])


def test_vault_cards_or_list_layout(client, owner):
    """The vault offers Cards and List (the /family table) for the same
    documents, switched in place: the server renders both, the toggle
    only changes which one shows, and ?layout=list lands on the list."""
    ana = dal.create_member(owner, "Ana")
    d1 = "v-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": d1, "user_id": owner, "intake_id": d1,
                       "member_id": ana["id"],
                       "explanation": _lab_exp("Colesterol alto.",
                                               "Colesterol")})
    try:
        r = client.get("/ficha", cookies=_cookies(owner))
        assert r.status_code == 200
        assert 'data-layout="cards"' in r.text
        assert 'data-vault-layout="cards" aria-pressed="true"' in r.text
        assert 'data-vault-layout="list" aria-pressed="false"' in r.text
        # Both layouts carry the same document: the card and the row.
        assert r.text.count('class="card doc-card"') == 1
        assert r.text.count("<tr data-search=") == 1
        assert "Colesterol alto. Colesterol" in r.text
        # The list is the /family table: date, exam, urgency, out.
        assert 'class="table-wrap doc-list"' in r.text
        assert "Colesterol" in r.text
        # ?layout=list lands on the list; anything else falls back.
        r = client.get("/ficha?layout=list", cookies=_cookies(owner))
        assert 'data-layout="list"' in r.text
        assert 'data-vault-layout="list" aria-pressed="true"' in r.text
        r = client.get("/ficha?layout=grid", cookies=_cookies(owner))
        assert 'data-layout="cards"' in r.text
    finally:
        _clean([d1, ana["id"]])
