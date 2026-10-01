"""Tarjetas de /ficha: fecha compacta, título corto del examen y urgencia.

- La fecha de cada tarjeta es la del examen (fecha_documento del LLM),
  no la de subida; con fallback a la de subida si no hay fecha.
  Formato compacto: "25 sep 2026".
- Las tarjetas se ordenan por fecha del examen, más reciente primero.
- El título es corto: prefiere nombre_examen del LLM; sin ese campo,
  trunca resumen_que_es a ~60 caracteres; sin ambos, cae a type_label.
  La tarjeta no muestra el resumen del resultado.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth
from app.db import dal


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents"):
        for i in ids:
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _exp(fecha_doc=None, resumen_rapido=None, summary_simple="resumen viejo",
         tipo="Resultado de laboratorio", urgencia="Agenda una cita",
         que_es=None, nombre_examen=None):
    exp = {
        "type_label": tipo,
        "summary_simple": summary_simple,
        "biomarkers": [],
        "urgency": {"level": "agendar_cita", "label": urgencia,
                    "action": "a", "timeframe": "t", "reason": "r"},
    }
    if fecha_doc:
        exp["fecha_documento"] = fecha_doc
    if resumen_rapido:
        exp["resumen_rapido"] = resumen_rapido
    if que_es:
        exp["resumen_que_es"] = que_es
    if nombre_examen:
        exp["nombre_examen"] = nombre_examen
    return exp


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


@pytest.fixture()
def usuario_docs():
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    viejo = "f-" + uuid.uuid4().hex[:12]
    nuevo = "f-" + uuid.uuid4().hex[:12]
    # El "nuevo" por fecha de examen se subió primero (al revés que created_at).
    dal.save_document({"id": viejo, "user_id": uid, "intake_id": viejo,
                       "explanation": _exp(fecha_doc="2026-03-10",
                                           tipo="Examen viejo")})
    dal.save_document({"id": nuevo, "user_id": uid, "intake_id": nuevo,
                       "explanation": _exp(fecha_doc="2026-09-25",
                                           resumen_rapido="Todo salió bien",
                                           tipo="Examen nuevo")})
    con = sqlite3.connect(_db_path())
    ahora = datetime.now(timezone.utc)
    con.execute("UPDATE documents SET created_at=? WHERE id=?",
                ((ahora - timedelta(days=1)).isoformat(), nuevo))
    con.execute("UPDATE documents SET created_at=? WHERE id=?",
                (ahora.isoformat(), viejo))
    con.commit()
    con.close()
    yield {"uid": uid, "ids": [uid, viejo, nuevo]}
    _clean([uid, viejo, nuevo])


def test_tarjetas_muestran_fecha_del_examen(client, usuario_docs):
    r = client.get("/ficha", cookies=_cookies(usuario_docs["uid"]))
    assert r.status_code == 200
    # Fecha del documento, no de subida (formato compacto).
    assert "25 sep 2026" in r.text
    assert "10 mar 2026" in r.text


def test_tarjetas_ordenadas_por_fecha_del_examen(client, usuario_docs):
    r = client.get("/ficha", cookies=_cookies(usuario_docs["uid"]))
    pos_nuevo = r.text.index("Examen nuevo")
    pos_viejo = r.text.index("Examen viejo")
    assert pos_nuevo < pos_viejo


def test_tarjeta_muestra_que_es_no_resumen(client, usuario_docs):
    r = client.get("/ficha", cookies=_cookies(usuario_docs["uid"]))
    assert "Examen nuevo" in r.text            # tipo como respaldo
    assert "Agenda una cita" in r.text          # urgencia
    assert "Todo salió bien" not in r.text     # resumen_rapido ya no se muestra
    assert "resumen viejo" not in r.text       # summary_simple tampoco


def test_tarjeta_prefiere_nombre_examen(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "explanation": _exp(
                           nombre_examen="Examen de sangre completo",
                           que_es="Hemograma: examen de sangre que revisa tus glóbulos rojos, blancos y plaquetas para ver cómo está tu sangre en general",
                           tipo="Resultado de laboratorio")})
    try:
        r = client.get("/ficha", cookies=_cookies(uid))
        assert r.status_code == 200
        assert "Examen de sangre completo" in r.text
        assert "revisa tus glóbulos rojos" not in r.text
    finally:
        _clean([uid, iid])


def test_tarjeta_trunca_que_es_largo(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "f-" + uuid.uuid4().hex[:12]
    largo = ("Hemograma: examen de sangre que revisa tus glóbulos rojos, "
             "blancos y plaquetas para ver cómo está tu sangre en general")
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "explanation": _exp(que_es=largo,
                                           tipo="Resultado de laboratorio")})
    try:
        r = client.get("/ficha", cookies=_cookies(uid))
        assert r.status_code == 200
        assert largo not in r.text  # no va entero
        assert "Hemograma: examen de sangre que revisa" in r.text
    finally:
        _clean([uid, iid])


def test_tarjeta_que_es_sin_prefijo_este_es(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "explanation": _exp(
                           que_es="Este es un examen de sangre que mide tus defensas",
                           tipo="Resultado de laboratorio")})
    try:
        r = client.get("/ficha", cookies=_cookies(uid))
        assert r.status_code == 200
        assert "Examen de sangre que mide tus defensas" in r.text
        assert "Este es un examen" not in r.text
    finally:
        _clean([uid, iid])


def test_tarjeta_sin_que_es_cae_a_tipo(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "explanation": _exp(tipo="Resultado de laboratorio")})
    try:
        r = client.get("/ficha", cookies=_cookies(uid))
        assert r.status_code == 200
        assert "Resultado de laboratorio" in r.text
    finally:
        _clean([uid, iid])


def test_tarjeta_sin_fecha_usa_subida(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    iid = "f-" + uuid.uuid4().hex[:12]
    dal.save_document({"id": iid, "user_id": uid, "intake_id": iid,
                       "explanation": _exp(tipo="Sin fecha")})
    try:
        r = client.get("/ficha", cookies=_cookies(uid))
        assert r.status_code == 200
        # Sin fecha_documento: muestra la fecha de subida (hoy), compacta.
        hoy = datetime.now(timezone.utc)
        meses = ["ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic"]
        assert f"{hoy.day} {meses[hoy.month - 1]} {hoy.year}" in r.text
    finally:
        _clean([uid, iid])


def _exp_bio(fecha_doc, valor):
    exp = _exp(fecha_doc=fecha_doc, tipo="Lab")
    exp["biomarkers"] = [{"name": "Hemoglobina", "value": valor,
                          "unit": "g/dL", "flag": "normal",
                          "plain_explanation": "x"}]
    return exp


def test_historial_usa_fecha_del_examen_y_ordena(client):
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    viejo = "f-" + uuid.uuid4().hex[:12]
    nuevo = "f-" + uuid.uuid4().hex[:12]
    # Subidos al revés: el de examen más reciente se subió primero.
    dal.save_document({"id": viejo, "user_id": uid, "intake_id": viejo,
                       "explanation": _exp_bio("2026-03-10", "13.1")})
    dal.save_document({"id": nuevo, "user_id": uid, "intake_id": nuevo,
                       "explanation": _exp_bio("2026-09-25", "14.2")})
    con = sqlite3.connect(_db_path())
    ahora = datetime.now(timezone.utc)
    con.execute("UPDATE documents SET created_at=? WHERE id=?",
                ((ahora - timedelta(days=1)).isoformat(), nuevo))
    con.execute("UPDATE documents SET created_at=? WHERE id=?",
                (ahora.isoformat(), viejo))
    con.commit()
    con.close()
    try:
        r = client.get(f"/ficha/{nuevo}", cookies=_cookies(uid))
        assert r.status_code == 200
        pos_sep = r.text.index("25 sep 2026")
        pos_mar = r.text.index("10 mar 2026")
        # Etiquetas con fecha del examen y orden: examen reciente primero.
        assert pos_sep < pos_mar
        assert "14.2" in r.text and "13.1" in r.text
    finally:
        _clean([uid, viejo, nuevo])


def test_tarjeta_estructura_legible(client, usuario_docs):
    r = client.get("/ficha", cookies=_cookies(usuario_docs["uid"]))
    assert r.status_code == 200
    assert "doc-card-top" in r.text       # fila fecha + urgencia
    assert '<h3 class="doc-about">' in r.text  # qué es el examen, como título
    assert "doc-summary" not in r.text     # sin resumen del resultado
    assert "Ver detalle" in r.text


def test_tarjeta_css_uniforme_y_sin_desborde():
    """Reglas CSS que mantienen las tarjetas parejas y sin desbordes."""
    import pathlib
    css = pathlib.Path("static/app.css").read_text()
    bloque = css.split(".doc-about {", 1)[1].split("}", 1)[0]
    assert "-webkit-line-clamp: 2" in bloque   # título a 2 líneas
    assert "min-height:" in bloque             # reserva el espacio
    chip = css.split(".doc-card-top .urgency-chip {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in chip   # el chip nunca se sale
