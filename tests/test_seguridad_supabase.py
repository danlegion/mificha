"""Seguridad Supabase: RLS activado + service_role solo en el servidor.

Invariantes que estos tests protegen:
- El acceso a datos (tablas vía db.py y Storage vía storage.py) usa la
  service key, que salta RLS. Sin ella, la anon key ya no puede leer las
  tablas (RLS activado sin políticas = denegar por defecto).
- Supabase Auth (magic links) SIGUE usando la anon key: GoTrue la exige y
  no toca las tablas.
"""

import inspect

import app.auth as auth_mod
import app.config as config
import app.db as db_mod
import app.storage as storage_mod


def test_data_key_prefiere_service_key(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_SERVICE_KEY", "srv-123")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "anon-456")
    assert config.supabase_data_key() == "srv-123"


def test_data_key_degrada_a_anon_con_error(monkeypatch, caplog):
    monkeypatch.setattr(config, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "anon-456")
    with caplog.at_level("ERROR", logger="mificha.config"):
        assert config.supabase_data_key() == "anon-456"
    assert "SUPABASE_SERVICE_KEY no configurada" in caplog.text


def test_dal_y_storage_usan_data_key():
    # Ni db.py ni storage.py deben cablear la anon key directo: pasan por
    # supabase_data_key() para que RLS no los bloquee.
    assert "supabase_data_key()" in inspect.getsource(db_mod._supabase_dal)
    for fn in ("store_original", "get_original_path", "delete_original"):
        assert "supabase_data_key()" in inspect.getsource(
            getattr(storage_mod, fn)
        ), fn
    assert "SUPABASE_ANON_KEY" not in inspect.getsource(db_mod._supabase_dal)


def test_auth_sigue_con_anon_key():
    # GoTrue (magic links) exige la anon key y no accede a las tablas.
    src = inspect.getsource(auth_mod._supabase_client)
    assert "SUPABASE_ANON_KEY" in src
    assert "supabase_data_key" not in src
