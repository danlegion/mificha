"""MiFicha — file-first storage (self-hosting only, no Supabase).

The files are the source of truth; the SQLite database is only an index
that can be rebuilt from them (see rebuild_index). Layout under
config.DATA_DIR:

    members/
      <member_id>/
        member.json          # {id, owner_user_id, owner_email, name, created_at}
        biomarkers.csv       # per-person biomarker history, rebuilt from
                             # this member's explanation.json files
        documents/
          <doc_id>/
            meta.json        # {id, user_id, member_id, intake_id,
                             #  original_key, created_at}
            original.pdf     # the uploaded PDF, untouched
            explanation.json # the plain-language explanation
      unassigned/
        documents/<doc_id>/...   # uploads with no member yet

Deleting the app or the container never destroys content: everything a
person needs lives in this folder, in plain files. Back it up as one
folder (see README, "Where your data lives").

Every function here is a no-op when Supabase is configured: the hosted
deployment keeps its bucket + Postgres storage unchanged.
"""

import csv
import json
import logging
import os
import re
import shutil
import sqlite3
import sys

from app import config

log = logging.getLogger("mificha.file_store")

UNASSIGNED = "unassigned"

BIOMARKER_CSV_COLUMNS = [
    "date", "biomarker", "value", "unit", "flag", "ref_range", "document_id",
]


def enabled() -> bool:
    """File-first storage applies only to self-hosted (local SQLite) mode."""
    return not config.USE_SUPABASE


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _member_key(member_id) -> str:
    return (member_id or "").strip() or UNASSIGNED


def member_dir(member_id) -> str:
    return os.path.join(config.DATA_DIR, "members", _member_key(member_id))


def document_dir(member_id, doc_id: str) -> str:
    return os.path.join(member_dir(member_id), "documents", doc_id)


def original_key(member_id, doc_id: str) -> str:
    """The original_key stored in the DB for a file-first document."""
    return f"members/{_member_key(member_id)}/documents/{doc_id}/original.pdf"


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def save_member_file(member: dict, owner_email: str | None = None) -> str | None:
    """Write members/<id>/member.json. No-op when disabled."""
    if not enabled() or not member:
        return None
    path = os.path.join(member_dir(member["id"]), "member.json")
    _write_json(path, {
        "id": member["id"],
        "owner_user_id": member.get("owner_user_id"),
        "owner_email": owner_email,
        "name": member.get("name"),
        "created_at": member.get("created_at"),
    })
    return path


# ---------------------------------------------------------------------------
# Documents: original + explanation
# ---------------------------------------------------------------------------

def save_original(member_id, doc_id: str, pdf_bytes: bytes) -> str | None:
    """Save the uploaded PDF under its member and return its original_key."""
    if not enabled():
        return None
    path = os.path.join(document_dir(member_id, doc_id), "original.pdf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return original_key(member_id, doc_id)


def save_explanation(user_id: str, member_id, doc_id: str,
                     explanation: dict, created_at: str | None = None) -> str | None:
    """Write meta.json + explanation.json for a document, then refresh the
    member's biomarkers.csv. No-op when disabled, and skipped for
    ownerless intakes: only saved documents become files, so the rebuild
    never resurrects an intake that was never anyone's document."""
    if not enabled() or not user_id:
        return None
    ddir = document_dir(member_id, doc_id)
    _write_json(os.path.join(ddir, "meta.json"), {
        "id": doc_id,
        "user_id": user_id,
        "member_id": member_id,
        "intake_id": doc_id,
        "original_key": original_key(member_id, doc_id),
        "created_at": created_at,
    })
    _write_json(os.path.join(ddir, "explanation.json"), explanation or {})
    rebuild_biomarkers_csv(member_id)
    return os.path.join(ddir, "explanation.json")


def read_explanation(member_id, doc_id: str):
    """Read a document's explanation.json, or None."""
    if not enabled():
        return None
    return _read_json(os.path.join(
        document_dir(member_id, doc_id), "explanation.json"))


def write_explanation(member_id, doc_id: str, explanation: dict) -> str | None:
    """Rewrite a document's explanation.json (e.g. after corrections) and
    refresh the member's biomarkers.csv."""
    if not enabled():
        return None
    path = os.path.join(document_dir(member_id, doc_id), "explanation.json")
    if not os.path.exists(path):
        return None
    _write_json(path, explanation or {})
    rebuild_biomarkers_csv(member_id)
    return path


# ---------------------------------------------------------------------------
# Biomarkers CSV (per person)
# ---------------------------------------------------------------------------

def _effective_date(explanation: dict, created_at: str | None) -> str:
    fd = str((explanation or {}).get("fecha_documento") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fd):
        return fd
    if created_at:
        return str(created_at)[:10]
    return ""


def rebuild_biomarkers_csv(member_id) -> str | None:
    """Rebuild members/<id>/biomarkers.csv from that member's
    explanation.json files, newest first. No-op when disabled."""
    if not enabled():
        return None
    mdir = member_dir(member_id)
    docs_dir = os.path.join(mdir, "documents")
    rows = []
    if os.path.isdir(docs_dir):
        for doc_id in sorted(os.listdir(docs_dir)):
            ddir = os.path.join(docs_dir, doc_id)
            exp = _read_json(os.path.join(ddir, "explanation.json"))
            if not exp:
                continue
            meta = _read_json(os.path.join(ddir, "meta.json")) or {}
            date = _effective_date(exp, meta.get("created_at"))
            for b in exp.get("biomarkers") or []:
                rows.append({
                    "date": date,
                    "biomarker": b.get("name") or "",
                    "value": b.get("value", ""),
                    "unit": b.get("unit") or "",
                    "flag": b.get("flag") or "",
                    "ref_range": b.get("ref_range") or "",
                    "document_id": doc_id,
                })
    rows.sort(key=lambda r: (r["date"], r["document_id"]), reverse=True)
    path = os.path.join(mdir, "biomarkers.csv")
    os.makedirs(mdir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BIOMARKER_CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Move / delete (reassign, delete)
# ---------------------------------------------------------------------------

def move_document(doc_id: str, old_member_id, new_member_id) -> bool:
    """Move a document folder to its new member and refresh both members'
    biomarkers.csv. Returns False when disabled or nothing to move."""
    if not enabled():
        return False
    src = document_dir(old_member_id, doc_id)
    if not os.path.isdir(src):
        return False
    dst = document_dir(new_member_id, doc_id)
    if os.path.abspath(src) == os.path.abspath(dst):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    meta_path = os.path.join(dst, "meta.json")
    meta = _read_json(meta_path) or {}
    meta["member_id"] = new_member_id
    meta["original_key"] = original_key(new_member_id, doc_id)
    _write_json(meta_path, meta)
    rebuild_biomarkers_csv(old_member_id)
    rebuild_biomarkers_csv(new_member_id)
    return True


def delete_document_files(member_id, doc_id: str) -> bool:
    """Remove a document's folder and refresh the member's biomarkers.csv."""
    if not enabled():
        return False
    ddir = document_dir(member_id, doc_id)
    if not os.path.isdir(ddir):
        return False
    shutil.rmtree(ddir)
    rebuild_biomarkers_csv(member_id)
    return True


# ---------------------------------------------------------------------------
# Migration: legacy flat layout -> per-person layout
# ---------------------------------------------------------------------------

def migrate_existing(dal) -> dict:
    """Move existing local data into the per-person layout.

    Reads every document from the DAL, moves its original from
    originales/<key> into its member's folder, and writes meta.json,
    explanation.json, member.json and biomarkers.csv from the DB rows.
    Idempotent: documents already migrated are skipped. Returns counts.
    Only runs in local mode.
    """
    counts = {"members": 0, "documents": 0, "moved_originals": 0, "skipped": 0}
    if not enabled():
        return counts
    legacy_dir = os.path.join(config.DATA_DIR, "originales")

    # Read the index directly: iterating by owner would miss documents
    # whose user row is gone.
    con = sqlite3.connect(os.path.join(config.DATA_DIR, "mificha.db"))
    try:
        emails = {uid: email for uid, email in con.execute(
            "SELECT id, email FROM users").fetchall()}
        member_rows = con.execute(
            "SELECT id, owner_user_id, name, created_at FROM members"
        ).fetchall()
        doc_rows = con.execute(
            "SELECT id, user_id, intake_id, original_key, explanation_json,"
            " member_id, created_at FROM documents").fetchall()
    finally:
        con.close()

    # member.json for every member in the DB, even ones with no documents.
    seen_members = set()
    for mid, owner_id, name, created_at in member_rows:
        if mid not in seen_members:
            save_member_file(
                {"id": mid, "owner_user_id": owner_id, "name": name,
                 "created_at": created_at},
                owner_email=emails.get(owner_id))
            seen_members.add(mid)
            counts["members"] += 1

    con = sqlite3.connect(os.path.join(config.DATA_DIR, "mificha.db"))
    try:
        for doc_id, user_id, intake_id, key, exp_json, member_id, created_at in doc_rows:
            ddir = document_dir(member_id, doc_id)
            new_key = original_key(member_id, doc_id)
            if os.path.exists(os.path.join(ddir, "explanation.json")):
                counts["skipped"] += 1
            else:
                new_path = os.path.join(ddir, "original.pdf")
                if key and not key.startswith("members/"):
                    legacy_path = os.path.join(legacy_dir, key)
                    if os.path.exists(legacy_path):
                        os.makedirs(ddir, exist_ok=True)
                        os.replace(legacy_path, new_path)
                        counts["moved_originals"] += 1
                _write_json(os.path.join(ddir, "meta.json"), {
                    "id": doc_id,
                    "user_id": user_id,
                    "member_id": member_id,
                    "intake_id": intake_id or doc_id,
                    "original_key": new_key,
                    "created_at": created_at,
                })
                try:
                    explanation = json.loads(exp_json) if exp_json else {}
                except ValueError:
                    explanation = {}
                _write_json(os.path.join(ddir, "explanation.json"), explanation)
                counts["documents"] += 1
            # The index must point at the new location, or the vault's
            # "download original" would look for a file that moved.
            if key != new_key:
                con.execute("UPDATE documents SET original_key=? WHERE id=?",
                            (new_key, doc_id))
                con.execute("UPDATE intakes SET original_key=? WHERE id=?",
                            (new_key, doc_id))
        con.commit()
    finally:
        con.close()
    for member_id in seen_members:
        rebuild_biomarkers_csv(member_id)
    rebuild_biomarkers_csv(None)  # unassigned
    return counts


# ---------------------------------------------------------------------------
# Rebuild the SQLite index from the files
# ---------------------------------------------------------------------------

def rebuild_index() -> dict:
    """Rebuild mificha.db (members, documents, intakes) from the files.

    Proves the database is only an index: delete it, run this, and the
    vault comes back. Shares are NOT rebuilt — they are short-lived
    (7-day) links whose snapshots live only in the DB. Users are
    recreated from member.json owner emails. Only runs in local mode.
    """
    counts = {"members": 0, "documents": 0, "intakes": 0}
    if not enabled():
        return counts
    from app.db import dal  # noqa: F401  (import ensures tables exist)

    db_path = os.path.join(config.DATA_DIR, "mificha.db")
    con = sqlite3.connect(db_path)
    try:
        members_root = os.path.join(config.DATA_DIR, "members")
        if not os.path.isdir(members_root):
            return counts
        for member_key in sorted(os.listdir(members_root)):
            mdir = os.path.join(members_root, member_key)
            if not os.path.isdir(mdir):
                continue
            member = _read_json(os.path.join(mdir, "member.json"))
            docs_dir = os.path.join(mdir, "documents")
            doc_ids = (sorted(os.listdir(docs_dir))
                       if os.path.isdir(docs_dir) else [])
            member_id = None
            if member:
                member_id = member["id"]
                owner_id = member.get("owner_user_id")
                if owner_id and member.get("owner_email"):
                    con.execute(
                        "INSERT OR IGNORE INTO users (id, email) VALUES (?, ?)",
                        (owner_id, member["owner_email"]),
                    )
                con.execute(
                    "INSERT OR REPLACE INTO members "
                    "(id, owner_user_id, name, created_at) VALUES (?, ?, ?, ?)",
                    (member_id, member.get("owner_user_id"),
                     member.get("name"), member.get("created_at")),
                )
                counts["members"] += 1
            for doc_id in doc_ids:
                ddir = os.path.join(docs_dir, doc_id)
                meta = _read_json(os.path.join(ddir, "meta.json"))
                if meta is None:
                    # Incomplete folder (original only, never a saved
                    # document): nothing to index.
                    continue
                exp = _read_json(os.path.join(ddir, "explanation.json")) or {}
                owner_id = meta.get("user_id")
                mid = meta.get("member_id") or (
                    None if member_key == UNASSIGNED else member_key)
                if member is None and mid and mid != UNASSIGNED:
                    # Folder exists but member.json is gone: keep the link.
                    member_id = mid
                exp_json = json.dumps(exp, ensure_ascii=False)
                created = meta.get("created_at")
                con.execute(
                    "INSERT OR REPLACE INTO documents "
                    "(id, user_id, intake_id, original_key, explanation_json,"
                    " member_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, owner_id, meta.get("intake_id") or doc_id,
                     meta.get("original_key")
                     or original_key(mid, doc_id),
                     exp_json, mid, created),
                )
                con.execute(
                    "INSERT OR REPLACE INTO intakes "
                    "(id, status, original_key, explanation_json,"
                    " owner_user_id, member_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, "ready",
                     meta.get("original_key") or original_key(mid, doc_id),
                     exp_json, owner_id, mid, created, created),
                )
                counts["documents"] += 1
                counts["intakes"] += 1
        con.commit()
    finally:
        con.close()
    return counts


def _main(argv) -> int:
    if len(argv) != 2 or argv[1] not in ("migrate", "rebuild"):
        print("usage: python -m app.file_store [migrate|rebuild]")
        return 2
    if not enabled():
        print("file-first storage is disabled (Supabase is configured).")
        return 0
    if argv[1] == "migrate":
        from app.db import dal
        print(migrate_existing(dal))
    else:
        print(rebuild_index())
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
