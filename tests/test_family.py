"""Página /family: tarjetas de un vistazo primero, luego la lista reciente.

- La nav lleva a /family (no a una sección del home).
- Las tarjetas son las mismas del home: tocar una lleva a la bóveda
  filtrada por ese familiar.
- Debajo va la lista de documentos subidos y explicados recientemente
  (últimos 6 meses): fecha, tipo, urgencia, fuera de rango, laboratorio,
  por confirmar, compartido y comparación con el examen anterior.
- Solo el home no tiene la lista; /family sí.
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
    with TestClient(app, headers={"Accept-Language": "es"}) as c:
        yield c


def _db_path():
    return config.DATA_DIR + "/mificha.db"


def _clean(ids):
    con = sqlite3.connect(_db_path())
    for table in ("intakes", "users", "documents", "members"):
        for i in ids:
            con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def _exp_campos(**kw):
    base = {
        "type_label": "Resultado de laboratorio",
        "summary_simple": "resumen",
        "biomarkers": [
            {"name": "Colesterol", "value": "210", "unit": "mg/dL",
             "ref_range": "0 - 200", "flag": "alto", "plain_explanation": "x"},
            {"name": "Glucosa", "value": "90", "unit": "mg/dL",
             "ref_range": "70 - 99", "flag": "normal", "plain_explanation": "x"},
        ],
        "urgency": {"level": "no_urgente", "label": "Sin urgencia",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }
    base.update(kw)
    return base


def _save_doc(did, uid, exp, member_id=None, days_ago=None):
    dal.save_intake({"id": did, "status": "ready", "explanation": exp,
                     "owner_user_id": uid})
    dal.save_document({"id": did, "user_id": uid, "intake_id": did,
                       "explanation": exp, "member_id": member_id})
    if days_ago is not None:
        con = sqlite3.connect(_db_path())
        ts = (datetime.now(timezone.utc)
              - timedelta(days=days_ago)).isoformat()
        con.execute("UPDATE documents SET created_at=? WHERE id=?", (ts, did))
        con.execute("UPDATE intakes SET created_at=? WHERE id=?", (ts, did))
        con.commit()
        con.close()


@pytest.fixture()
def usuario_family():
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    member = dal.create_member(uid, "Ana")
    # Previo (hace 60 días): colesterol peor, glucosa fuera de rango.
    previo = "f-" + uuid.uuid4().hex[:12]
    exp_prev = _exp_campos()
    exp_prev["biomarkers"][0].update({"value": "260"})
    exp_prev["biomarkers"][1].update({"value": "120", "flag": "alto"})
    _save_doc(previo, uid, exp_prev, member_id=member["id"], days_ago=60)
    # Actual: con laboratorio, por confirmar y enlace compartido.
    actual = "f-" + uuid.uuid4().hex[:12]
    exp_act = _exp_campos(laboratorio="Lab Clínico Norte",
                          uncertain_fields=[{"field": "x", "reason": "y"}])
    _save_doc(actual, uid, exp_act, member_id=member["id"])
    dal.create_share({
        "intake_id": actual, "token": "tok-" + uuid.uuid4().hex[:8],
        "snapshot": {},
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    })
    # Viejo (hace 200 días): fuera de la ventana de 6 meses.
    viejo = "f-" + uuid.uuid4().hex[:12]
    _save_doc(viejo, uid, _exp_campos(), days_ago=200)
    # Luis: un documento limpio, sin nada por revisar.
    luis = dal.create_member(uid, "Luis")
    limpio = "f-" + uuid.uuid4().hex[:12]
    exp_limpio = _exp_campos()
    exp_limpio["biomarkers"][0].update({"value": "150", "flag": "normal"})
    _save_doc(limpio, uid, exp_limpio, member_id=luis["id"])
    # Elena: sin documentos, sin insignia.
    elena = dal.create_member(uid, "Elena")
    yield {"uid": uid, "member": member, "actual": actual,
           "previo": previo, "viejo": viejo,
           "luis": luis, "limpio": limpio, "elena": elena}
    _clean([uid, actual, previo, viejo, limpio,
            member["id"], luis["id"], elena["id"]])


def test_family_anonimo_redirige(client):
    r = client.get("/family", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_family_tarjetas_muestran_quien_tiene_por_revisar(
        client, usuario_family):
    # Insignia como en el diseño aprobado: arriba a la derecha de la
    # tarjeta, junto a las iniciales. Ana tiene 2 documentos por
    # revisar (previo + actual, colesterol alto); Luis, con todo al
    # día, lleva la insignia verde; Elena, sin documentos, ninguna.
    # La tarjeta dice de quién es el número del hero, en /family y
    # en el home.
    for path in ("/family", "/"):
        r = client.get(path, cookies=_cookies(usuario_family["uid"]))
        assert r.status_code == 200
        assert "2 por revisar" in r.text
        assert r.text.count("Al día") == 1  # solo Luis
        # Solo la tarjeta de Ana lleva la insignia ámbar.
        assert r.text.count("por revisar") == (
            2 if path == "/" else 1)  # en el home, + la etiqueta del hero
        # La insignia va en la fila superior, junto a las iniciales.
        assert 'class="family-top"' in r.text
        assert "family-review" in r.text
        assert "family-review--ok" in r.text


def test_family_tarjetas_primero_y_lista_reciente(client, usuario_family):
    r = client.get("/family", cookies=_cookies(usuario_family["uid"]))
    assert r.status_code == 200
    html = r.text
    # Las tarjetas van primero: continuidad con el home.
    assert "Tu familia de un vistazo" in html
    assert "Tus exámenes" in html
    assert html.index("Tu familia de un vistazo") < html.index("Tus exámenes")
    # La tarjeta del familiar enlaza a su bóveda filtrada.
    assert "Ana" in html
    assert f"/ficha?miembro={usuario_family['member']['id']}" in html
    # La lista trae el reciente y deja fuera el viejo.
    assert f"/ficha/{usuario_family['actual']}" in html
    assert f"/ficha/{usuario_family['viejo']}" not in html
    assert "más de 6 meses" not in html


def test_family_muestra_laboratorio_por_confirmar_y_compartido(
        client, usuario_family):
    r = client.get("/family", cookies=_cookies(usuario_family["uid"]))
    html = r.text
    assert "Lab Clínico Norte" in html
    assert "por confirmar" in html
    assert "Compartido con tu doctor" in html


def test_family_compara_con_examen_anterior(client, usuario_family):
    r = client.get("/family", cookies=_cookies(usuario_family["uid"]))
    html = r.text
    # Colesterol: 260 -> 210 (sigue alto pero más cerca del rango) = mejoró.
    # Glucosa: 120 alto -> 90 en rango = mejoró.
    assert "2 mejoraron" in html
    assert "Desde tu examen de" in html


def test_family_solo_docs_viejos_muestra_nota(client):
    email = f"fv-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    did = "fv-" + uuid.uuid4().hex[:12]
    try:
        _save_doc(did, uid, _exp_campos(), days_ago=200)
        r = client.get("/family", cookies=_cookies(uid))
        assert r.status_code == 200
        assert "más de 6 meses" in r.text
        assert "<h2>Tus exámenes</h2>" not in r.text
        assert f"/ficha/{did}" not in r.text
    finally:
        _clean([uid, did])


def test_comparar_empeora_y_sin_datos():
    import app.main as m
    bios_prev = [{"name": "X", "value": "90", "ref_range": "70 - 99"}]
    bios_act = [{"name": "X", "value": "150", "ref_range": "70 - 99"}]
    mej, emp, _ = m._comparar_con_anterior(
        bios_act, [{"explanation": {"biomarkers": bios_prev},
                    "created_at": "2026-01-01T00:00:00+00:00"}])
    assert (mej, emp) == (0, 1)
    # Sin biomarcadores comparables: no hay resumen.
    mej, emp, fecha = m._comparar_con_anterior(
        [{"name": "Y", "value": "no numérico", "ref_range": "1 - 5"}], [])
    assert (mej, emp, fecha) == (0, 0, "")
