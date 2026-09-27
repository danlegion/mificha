"""MiFicha v0.1 — capa de datos mínima (DAL).

Si SUPABASE_URL y SUPABASE_ANON_KEY están configuradas (y `supabase` está
instalado), se usa Supabase (tablas: intakes, users, documents, shares).
Si no, SQLite local vía SQLAlchemy en ./data/mificha.db con las mismas
tablas lógicas.

Ambos caminos implementan la misma DAL con dicts simples serializables.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from app import config

log = logging.getLogger("mificha.db")

TABLES = ("intakes", "users", "documents", "shares")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# SQLite (SQLAlchemy)
# ---------------------------------------------------------------------------

def _sqlite_dal():
    from sqlalchemy import (Boolean, Column, DateTime, String, Text, create_engine,
                            desc)
    from sqlalchemy.orm import declarative_base, sessionmaker

    os.makedirs(config.DATA_DIR, exist_ok=True)
    db_path = os.path.join(config.DATA_DIR, "mificha.db")
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base = declarative_base()
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    class Intake(Base):
        __tablename__ = "intakes"
        id = Column(String, primary_key=True)
        guest_token_hash = Column(String, nullable=True)
        status = Column(String, default="processing")
        detail = Column(String, nullable=True)
        doc_type_hint = Column(String, nullable=True)
        original_key = Column(String, nullable=True)
        explanation_json = Column(Text, nullable=True)
        corrections_json = Column(Text, default="[]")
        owner_user_id = Column(String, nullable=True)
        created_at = Column(DateTime, default=_now)
        updated_at = Column(DateTime, default=_now, onupdate=_now)

    class User(Base):
        __tablename__ = "users"
        id = Column(String, primary_key=True)
        email = Column(String, unique=True, nullable=False)
        telefono = Column(String, nullable=True)
        created_at = Column(DateTime, default=_now)

    class Document(Base):
        __tablename__ = "documents"
        id = Column(String, primary_key=True)
        user_id = Column(String, nullable=False)
        intake_id = Column(String, nullable=False)
        original_key = Column(String, nullable=True)
        explanation_json = Column(Text, nullable=True)
        created_at = Column(DateTime, default=_now)

    class Share(Base):
        __tablename__ = "shares"
        id = Column(String, primary_key=True)
        intake_id = Column(String, nullable=False)
        token = Column(String, unique=True, nullable=False)
        snapshot_json = Column(Text, nullable=False)
        expires_at = Column(DateTime, nullable=False)
        revoked = Column(Boolean, default=False)
        created_at = Column(DateTime, default=_now)

    Base.metadata.create_all(engine)

    def _intake_d(i: Intake) -> dict:
        return {
            "id": i.id,
            "guest_token_hash": i.guest_token_hash,
            "status": i.status,
            "detail": i.detail,
            "doc_type_hint": i.doc_type_hint,
            "original_key": i.original_key,
            "explanation": json.loads(i.explanation_json) if i.explanation_json else None,
            "corrections": json.loads(i.corrections_json or "[]"),
            "owner_user_id": i.owner_user_id,
            "created_at": _iso(i.created_at),
            "updated_at": _iso(i.updated_at),
        }

    def _user_d(u: User) -> dict:
        return {"id": u.id, "email": u.email, "telefono": u.telefono,
                "created_at": _iso(u.created_at)}

    def _doc_d(d: Document) -> dict:
        return {
            "id": d.id, "user_id": d.user_id, "intake_id": d.intake_id,
            "original_key": d.original_key,
            "explanation": json.loads(d.explanation_json) if d.explanation_json else None,
            "created_at": _iso(d.created_at),
        }

    def _share_d(s: Share) -> dict:
        return {
            "id": s.id, "intake_id": s.intake_id, "token": s.token,
            "snapshot": json.loads(s.snapshot_json),
            "expires_at": _iso(s.expires_at), "revoked": bool(s.revoked),
            "created_at": _iso(s.created_at),
        }

    class SQLiteDAL:
        # -- intakes ----------------------------------------------------
        def save_intake(self, intake: dict) -> dict:
            with Session() as s:
                obj = s.get(Intake, intake["id"])
                if obj is None:
                    obj = Intake(id=intake["id"])
                    s.add(obj)
                obj.guest_token_hash = intake.get("guest_token_hash")
                obj.status = intake.get("status", "processing")
                obj.detail = intake.get("detail")
                obj.doc_type_hint = intake.get("doc_type_hint")
                obj.original_key = intake.get("original_key")
                if intake.get("explanation") is not None:
                    obj.explanation_json = json.dumps(intake["explanation"], ensure_ascii=False)
                if intake.get("corrections") is not None:
                    obj.corrections_json = json.dumps(intake["corrections"], ensure_ascii=False)
                if "owner_user_id" in intake:
                    obj.owner_user_id = intake["owner_user_id"]
                s.commit()
                return _intake_d(obj)

        def get_intake(self, intake_id: str):
            with Session() as s:
                obj = s.get(Intake, intake_id)
                return _intake_d(obj) if obj else None

        def set_intake_ready(self, intake_id, explanation=None, status="ready", detail=None):
            with Session() as s:
                obj = s.get(Intake, intake_id)
                if not obj:
                    return None
                obj.status = status
                obj.detail = detail
                if explanation is not None:
                    obj.explanation_json = json.dumps(explanation, ensure_ascii=False)
                s.commit()
                return _intake_d(obj)

        def update_intake_corrections(self, intake_id, corrections):
            with Session() as s:
                obj = s.get(Intake, intake_id)
                if not obj:
                    return None
                obj.corrections_json = json.dumps(corrections or [], ensure_ascii=False)
                if obj.explanation_json and corrections:
                    exp = json.loads(obj.explanation_json)
                    # Regenera la nota: las correcciones alimentan los campos inciertos
                    for c in corrections:
                        for uf in exp.get("uncertain_fields", []):
                            if uf.get("key") == c.get("key") or uf.get("field") == c.get("field"):
                                uf["guessed_value"] = c.get("corrected_value", "")
                    exp["correction_note"] = (
                        "Corregiste algunos datos el "
                        f"{_now().strftime('%d/%m/%Y')}; esta explicación los tiene en cuenta."
                    )
                    obj.explanation_json = json.dumps(exp, ensure_ascii=False)
                s.commit()
                return _intake_d(obj)

        def claim_intake(self, intake_id, user_id):
            with Session() as s:
                obj = s.get(Intake, intake_id)
                if not obj:
                    return None
                obj.owner_user_id = user_id
                obj.guest_token_hash = None  # el token de invitado queda revocado
                s.commit()
                return _intake_d(obj)

        # -- users ------------------------------------------------------
        def create_user(self, email, telefono=None):
            email = (email or "").strip().lower()
            with Session() as s:
                obj = s.query(User).filter_by(email=email).first()
                if obj:
                    if telefono:
                        obj.telefono = telefono
                        s.commit()
                    return _user_d(obj)
                obj = User(id=uuid.uuid4().hex, email=email, telefono=telefono)
                s.add(obj)
                s.commit()
                return _user_d(obj)

        def get_user_by_id(self, user_id):
            with Session() as s:
                obj = s.get(User, user_id)
                return _user_d(obj) if obj else None

        def get_user_by_email(self, email):
            email = (email or "").strip().lower()
            with Session() as s:
                obj = s.query(User).filter_by(email=email).first()
                return _user_d(obj) if obj else None

        # -- documents --------------------------------------------------
        def save_document(self, doc: dict) -> dict:
            with Session() as s:
                obj = s.get(Document, doc["id"])
                if obj is None:
                    obj = Document(id=doc["id"])
                    s.add(obj)
                obj.user_id = doc["user_id"]
                obj.intake_id = doc.get("intake_id", doc["id"])
                obj.original_key = doc.get("original_key")
                if doc.get("explanation") is not None:
                    obj.explanation_json = json.dumps(doc["explanation"], ensure_ascii=False)
                s.commit()
                return _doc_d(obj)

        def get_document(self, doc_id):
            with Session() as s:
                obj = s.get(Document, doc_id)
                return _doc_d(obj) if obj else None

        def list_documents(self, user_id):
            with Session() as s:
                objs = (
                    s.query(Document)
                    .filter_by(user_id=user_id)
                    .order_by(desc(Document.created_at))
                    .all()
                )
                return [_doc_d(o) for o in objs]

        # -- shares ------------------------------------------------------
        def create_share(self, share: dict) -> dict:
            with Session() as s:
                obj = Share(
                    id=uuid.uuid4().hex,
                    intake_id=share["intake_id"],
                    token=share["token"],
                    snapshot_json=json.dumps(share["snapshot"], ensure_ascii=False),
                    expires_at=share["expires_at"],
                    revoked=False,
                )
                s.add(obj)
                s.commit()
                return _share_d(obj)

        def get_share(self, token):
            with Session() as s:
                obj = s.query(Share).filter_by(token=token).first()
                return _share_d(obj) if obj else None

        def get_active_share_for_intake(self, intake_id):
            with Session() as s:
                objs = (
                    s.query(Share)
                    .filter_by(intake_id=intake_id, revoked=False)
                    .order_by(desc(Share.created_at))
                    .all()
                )
                now = _now()
                for o in objs:
                    exp = o.expires_at
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp > now:
                        return _share_d(o)
                return None

        def revoke_shares_for_intake(self, intake_id) -> int:
            with Session() as s:
                objs = s.query(Share).filter_by(intake_id=intake_id, revoked=False).all()
                for o in objs:
                    o.revoked = True
                s.commit()
                return len(objs)

    return SQLiteDAL()


# ---------------------------------------------------------------------------
# Supabase (stub funcional)
# ---------------------------------------------------------------------------

def _supabase_dal():
    from supabase import create_client  # type: ignore

    client = create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)
    log.info("DAL: usando Supabase")

    def _one(resp):
        rows = resp.data or []
        return rows[0] if rows else None

    # Normalizadores: misma forma de dict que el DAL SQLite.
    def _n_intake(r):
        if not r:
            return None
        return {
            "id": r["id"],
            "guest_token_hash": r.get("guest_token_hash"),
            "status": r.get("status", "processing"),
            "detail": r.get("detail"),
            "doc_type_hint": r.get("doc_type_hint"),
            "original_key": r.get("original_key"),
            "explanation": json.loads(r["explanation_json"]) if r.get("explanation_json") else None,
            "corrections": json.loads(r.get("corrections_json") or "[]"),
            "owner_user_id": r.get("owner_user_id"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        }

    def _n_user(r):
        if not r:
            return None
        return {"id": r["id"], "email": r.get("email"),
                "telefono": r.get("telefono"), "created_at": r.get("created_at")}

    def _n_doc(r):
        if not r:
            return None
        return {
            "id": r["id"], "user_id": r.get("user_id"),
            "intake_id": r.get("intake_id"), "original_key": r.get("original_key"),
            "explanation": json.loads(r["explanation_json"]) if r.get("explanation_json") else None,
            "created_at": r.get("created_at"),
        }

    def _n_share(r):
        if not r:
            return None
        return {
            "id": r["id"], "intake_id": r.get("intake_id"), "token": r.get("token"),
            "snapshot": json.loads(r["snapshot_json"]) if r.get("snapshot_json") else {},
            "expires_at": r.get("expires_at"), "revoked": bool(r.get("revoked")),
            "created_at": r.get("created_at"),
        }

    class SupabaseDAL:
        # -- intakes ----------------------------------------------------
        def save_intake(self, intake: dict) -> dict:
            row = {
                "id": intake["id"],
                "guest_token_hash": intake.get("guest_token_hash"),
                "status": intake.get("status", "processing"),
                "detail": intake.get("detail"),
                "doc_type_hint": intake.get("doc_type_hint"),
                "original_key": intake.get("original_key"),
                "explanation_json": json.dumps(intake["explanation"], ensure_ascii=False)
                if intake.get("explanation") is not None else None,
                "corrections_json": json.dumps(intake.get("corrections") or []),
                "owner_user_id": intake.get("owner_user_id"),
                "updated_at": _now().isoformat(),
            }
            return _n_intake(_one(client.table("intakes").upsert(row).execute()))

        def get_intake(self, intake_id: str):
            return _n_intake(_one(client.table("intakes").select("*").eq("id", intake_id).execute()))

        def set_intake_ready(self, intake_id, explanation=None, status="ready", detail=None):
            row = {"status": status, "detail": detail, "updated_at": _now().isoformat()}
            if explanation is not None:
                row["explanation_json"] = json.dumps(explanation, ensure_ascii=False)
            _one(client.table("intakes").update(row).eq("id", intake_id).execute())
            return self.get_intake(intake_id)

        def update_intake_corrections(self, intake_id, corrections):
            row = {"corrections_json": json.dumps(corrections or []),
                   "updated_at": _now().isoformat()}
            _one(client.table("intakes").update(row).eq("id", intake_id).execute())
            return self.get_intake(intake_id)

        def claim_intake(self, intake_id, user_id):
            row = {"owner_user_id": user_id, "guest_token_hash": None,
                   "updated_at": _now().isoformat()}
            _one(client.table("intakes").update(row).eq("id", intake_id).execute())
            return self.get_intake(intake_id)

        # -- users ------------------------------------------------------
        def create_user(self, email, telefono=None):
            email = (email or "").strip().lower()
            row = _n_user(_one(client.table("users").select("*").eq("email", email).execute()))
            if row:
                if telefono:
                    _one(client.table("users").update({"telefono": telefono}).eq("id", row["id"]).execute())
                    row["telefono"] = telefono
                return row
            return _n_user(_one(client.table("users").insert(
                {"id": uuid.uuid4().hex, "email": email, "telefono": telefono}
            ).execute()))

        def get_user_by_id(self, user_id):
            return _n_user(_one(client.table("users").select("*").eq("id", user_id).execute()))

        def get_user_by_email(self, email):
            email = (email or "").strip().lower()
            return _n_user(_one(client.table("users").select("*").eq("email", email).execute()))

        # -- documents --------------------------------------------------
        def save_document(self, doc: dict) -> dict:
            row = {
                "id": doc["id"], "user_id": doc["user_id"],
                "intake_id": doc.get("intake_id", doc["id"]),
                "original_key": doc.get("original_key"),
                "explanation_json": json.dumps(doc["explanation"], ensure_ascii=False)
                if doc.get("explanation") is not None else None,
            }
            return _n_doc(_one(client.table("documents").upsert(row).execute()))

        def get_document(self, doc_id):
            return _n_doc(_one(client.table("documents").select("*").eq("id", doc_id).execute()))

        def list_documents(self, user_id):
            resp = (client.table("documents").select("*").eq("user_id", user_id)
                    .order("created_at", desc=True).execute())
            return [_n_doc(r) for r in (resp.data or [])]

        # -- shares ------------------------------------------------------
        def create_share(self, share: dict) -> dict:
            row = {
                "id": uuid.uuid4().hex, "intake_id": share["intake_id"],
                "token": share["token"],
                "snapshot_json": json.dumps(share["snapshot"], ensure_ascii=False),
                "expires_at": share["expires_at"].isoformat()
                if hasattr(share["expires_at"], "isoformat") else share["expires_at"],
                "revoked": False,
            }
            return _n_share(_one(client.table("shares").insert(row).execute()))

        def get_share(self, token):
            return _n_share(_one(client.table("shares").select("*").eq("token", token).execute()))

        def get_active_share_for_intake(self, intake_id):
            resp = (client.table("shares").select("*").eq("intake_id", intake_id)
                    .eq("revoked", False).order("created_at", desc=True).execute())
            now = _now()
            for r in resp.data or []:
                exp = r.get("expires_at")
                dt = datetime.fromisoformat(exp) if isinstance(exp, str) else exp
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > now:
                    return _n_share(r)
            return None

        def revoke_shares_for_intake(self, intake_id) -> int:
            resp = (client.table("shares").update({"revoked": True})
                    .eq("intake_id", intake_id).eq("revoked", False).execute())
            return len(resp.data or [])

    return SupabaseDAL()


def _build_dal():
    if config.USE_SUPABASE:
        try:
            return _supabase_dal()
        except ImportError:
            log.warning(
                "SUPABASE_* configurado pero 'supabase' no instalado: "
                "usando SQLite local."
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo conectar a Supabase (%s): usando SQLite.", exc)
    log.info("DAL: usando SQLite local")
    return _sqlite_dal()


dal = _build_dal()
