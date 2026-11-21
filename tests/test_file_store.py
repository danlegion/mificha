"""File-first storage: the files are the source of truth.

- _run_pipeline writes original.pdf + explanation.json + meta.json under
  members/<member_id>/documents/<doc_id>/, and the member's biomarkers.csv.
- Reassigning a document moves its folder and rebuilds both CSVs.
- Deleting a document removes its folder.
- migrate_existing moves legacy flat originales/<id>.pdf files into the
  per-person layout and writes the JSON/CSV files from the DB rows.
- rebuild_index restores the SQLite rows from the files alone.
- With Supabase configured, every file_store function is a no-op.
"""

import json
import os
import shutil
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, file_store
from app.db import dal
from app.routers import ingest as ingest_router
from app.storage import quarantine_path
from tests.conftest import make_explanation


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
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
    con.commit()
    con.close()


def _clean_member_dirs(member_ids):
    for mid in member_ids:
        shutil.rmtree(file_store.member_dir(mid), ignore_errors=True)


def _pdf_valido():
    import fitz
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), "Glucosa 95 mg/dL")
    return doc.tobytes()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


@pytest.fixture()
def usuario():
    email = f"fs-{uuid.uuid4().hex[:8]}@x.test"
    user = dal.create_user(email)
    yield user
    _clean([user["id"]])


def _run_pipeline_with_doc(usuario_id, member_id, monkeypatch):
    """Run the real pipeline (mocked LLM) and return the intake id."""
    iid = "fs-" + uuid.uuid4().hex[:12]
    intake = {"id": iid, "status": "processing", "owner_user_id": usuario_id}
    if member_id:
        intake["member_id"] = member_id
    dal.save_intake(intake)
    with open(quarantine_path(iid), "wb") as f:
        f.write(_pdf_valido())
    exp = make_explanation()
    exp["fecha_documento"] = "2026-11-18"
    monkeypatch.setattr(ingest_router, "process_intake",
                        lambda _iid, _pdf, _hint, lang="es": exp)
    ingest_router._run_pipeline(iid)
    return iid, exp


def test_pipeline_writes_per_person_files(usuario, monkeypatch):
    member = dal.create_member(usuario["id"], "Ana")
    iid, exp = _run_pipeline_with_doc(usuario["id"], member["id"], monkeypatch)
    try:
        ddir = file_store.document_dir(member["id"], iid)
        assert os.path.exists(os.path.join(ddir, "original.pdf"))
        saved = file_store.read_explanation(member["id"], iid)
        assert saved is not None
        assert saved["biomarkers"][0]["name"] == "Glucosa"
        meta = json.load(open(os.path.join(ddir, "meta.json")))
        assert meta["user_id"] == usuario["id"]
        assert meta["member_id"] == member["id"]
        # The DB row points at the per-person path, not the flat dir.
        doc = dal.get_document(iid)
        assert doc is not None
        assert doc["original_key"] == (
            f"members/{member['id']}/documents/{iid}/original.pdf")
        # The member's biomarkers.csv has the row from this document.
        csv_path = os.path.join(file_store.member_dir(member["id"]),
                                "biomarkers.csv")
        assert os.path.exists(csv_path)
        content = open(csv_path, encoding="utf-8").read()
        assert "Glucosa" in content and "2026-11-18" in content
    finally:
        _clean([iid, usuario["id"], member["id"]])
        _clean_member_dirs([member["id"]])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass


def test_pipeline_without_member_uses_unassigned(usuario, monkeypatch):
    iid, _exp = _run_pipeline_with_doc(usuario["id"], None, monkeypatch)
    try:
        ddir = file_store.document_dir(None, iid)
        assert os.path.exists(os.path.join(ddir, "original.pdf"))
        assert os.path.exists(os.path.join(ddir, "explanation.json"))
    finally:
        _clean([iid, usuario["id"]])
        _clean_member_dirs([file_store.UNASSIGNED])
        try:
            os.remove(quarantine_path(iid))
        except OSError:
            pass


def test_reassign_moves_folder(client, usuario):
    member_a = dal.create_member(usuario["id"], "Ana")
    member_b = dal.create_member(usuario["id"], "Luis")
    iid = "fs-" + uuid.uuid4().hex[:12]
    dal.save_document({
        "id": iid, "user_id": usuario["id"], "intake_id": iid,
        "original_key": None,
        "explanation": make_explanation(), "member_id": member_a["id"],
    })
    file_store.save_member_file(member_a, owner_email=usuario["email"])
    file_store.save_member_file(member_b, owner_email=usuario["email"])
    file_store.save_original(member_a["id"], iid, _pdf_valido())
    file_store.save_explanation(usuario["id"], member_a["id"], iid,
                                make_explanation())
    try:
        r = client.post(f"/ficha/{iid}/miembro",
                        data={"miembro": member_b["id"]},
                        cookies=_cookies(usuario["id"]),
                        follow_redirects=False)
        assert r.status_code in (303, 307), r.status_code
        assert not os.path.exists(
            file_store.document_dir(member_a["id"], iid))
        assert os.path.exists(os.path.join(
            file_store.document_dir(member_b["id"], iid), "original.pdf"))
        assert dal.get_document(iid)["member_id"] == member_b["id"]
        # Ana's CSV no longer has the biomarker; Luis's does.
        csv_a = open(os.path.join(file_store.member_dir(member_a["id"]),
                                  "biomarkers.csv"), encoding="utf-8").read()
        csv_b = open(os.path.join(file_store.member_dir(member_b["id"]),
                                  "biomarkers.csv"), encoding="utf-8").read()
        assert "Glucosa" not in csv_a
        assert "Glucosa" in csv_b
    finally:
        _clean([iid, usuario["id"], member_a["id"], member_b["id"]])
        _clean_member_dirs([member_a["id"], member_b["id"]])


def test_delete_removes_folder(client, usuario):
    member = dal.create_member(usuario["id"], "Ana")
    iid = "fs-" + uuid.uuid4().hex[:12]
    dal.save_document({
        "id": iid, "user_id": usuario["id"], "intake_id": iid,
        "original_key": None,
        "explanation": make_explanation(), "member_id": member["id"],
    })
    file_store.save_original(member["id"], iid, _pdf_valido())
    file_store.save_explanation(usuario["id"], member["id"], iid,
                                make_explanation())
    try:
        r = client.post(f"/ficha/{iid}/eliminar",
                        cookies=_cookies(usuario["id"]),
                        follow_redirects=False)
        assert r.status_code in (303, 307), r.status_code
        assert not os.path.exists(file_store.document_dir(member["id"], iid))
        assert dal.get_document(iid) is None
    finally:
        _clean([iid, usuario["id"], member["id"]])
        _clean_member_dirs([member["id"]])


def test_migrate_legacy_flat_layout(usuario):
    member = dal.create_member(usuario["id"], "Ana")
    iid = "fs-" + uuid.uuid4().hex[:12]
    legacy_key = f"{iid}.pdf"
    legacy_path = os.path.join(config.DATA_DIR, "originales", legacy_key)
    os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
    with open(legacy_path, "wb") as f:
        f.write(_pdf_valido())
    exp = make_explanation()
    exp["fecha_documento"] = "2026-11-10"
    dal.save_document({
        "id": iid, "user_id": usuario["id"], "intake_id": iid,
        "original_key": legacy_key, "explanation": exp,
        "member_id": member["id"],
    })
    try:
        counts = file_store.migrate_existing(dal)
        assert counts["documents"] >= 1
        assert counts["moved_originals"] >= 1
        assert not os.path.exists(legacy_path)
        ddir = file_store.document_dir(member["id"], iid)
        assert os.path.exists(os.path.join(ddir, "original.pdf"))
        assert os.path.exists(os.path.join(ddir, "explanation.json"))
        # The index now points at the new location, so the vault can
        # still find the original.
        assert dal.get_document(iid)["original_key"] == (
            f"members/{member['id']}/documents/{iid}/original.pdf")
        csv_path = os.path.join(file_store.member_dir(member["id"]),
                                "biomarkers.csv")
        assert "Glucosa" in open(csv_path, encoding="utf-8").read()
        # Idempotent: a second run skips the migrated document.
        again = file_store.migrate_existing(dal)
        assert again["documents"] == 0
        assert again["moved_originals"] == 0
    finally:
        _clean([iid, usuario["id"], member["id"]])
        _clean_member_dirs([member["id"]])


def test_rebuild_index_restores_rows(usuario):
    member = dal.create_member(usuario["id"], "Ana")
    iid = "fs-" + uuid.uuid4().hex[:12]
    dal.save_document({
        "id": iid, "user_id": usuario["id"], "intake_id": iid,
        "original_key": None,
        "explanation": make_explanation(), "member_id": member["id"],
    })
    file_store.save_member_file(member, owner_email=usuario["email"])
    file_store.save_original(member["id"], iid, _pdf_valido())
    file_store.save_explanation(usuario["id"], member["id"], iid,
                                make_explanation())
    try:
        # Wipe the index rows, as if the database were lost.
        _clean([iid, member["id"]])
        assert dal.get_document(iid) is None
        counts = file_store.rebuild_index()
        assert counts["documents"] >= 1
        assert counts["members"] >= 1
        doc = dal.get_document(iid)
        assert doc is not None
        assert doc["user_id"] == usuario["id"]
        assert doc["member_id"] == member["id"]
        assert doc["explanation"]["biomarkers"][0]["name"] == "Glucosa"
        assert dal.get_member(member["id"])["name"] == "Ana"
    finally:
        _clean([iid, usuario["id"], member["id"]])
        _clean_member_dirs([member["id"]])


def test_supabase_mode_is_noop(usuario, monkeypatch):
    monkeypatch.setattr(config, "USE_SUPABASE", True)
    assert file_store.enabled() is False
    assert file_store.save_original("m1", "d1", b"%PDF") is None
    assert file_store.save_explanation("u1", "m1", "d1", {}) is None
    assert file_store.migrate_existing(dal) == {
        "members": 0, "documents": 0, "moved_originals": 0, "skipped": 0}
    assert file_store.rebuild_index() == {
        "members": 0, "documents": 0, "intakes": 0}
