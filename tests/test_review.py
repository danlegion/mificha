"""Estado de revisión (paso 2 del rediseño, Nov 21, 2026).

Criterio elegido: un documento requiere revisión cuando tiene
valores por confirmar (inciertos) o fuera de rango. La revisión se marca
dentro del propio documento (detalle o resultado), nunca con un botón en
la tarjeta de familia. El héroe cuenta los documentos que requieren
revisión y aún no se marcaron; el estado sobrevive a un re-guardado y
a una reconstrucción del índice desde los archivos.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.config as config
from app import auth, file_store
from app.db import dal
from app.routers import needs_review


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
            try:
                con.execute(f"DELETE FROM {table} WHERE id=?", (i,))
            except sqlite3.OperationalError:
                pass
    con.commit()
    con.close()


def _cookies(uid):
    return {auth.SESSION_COOKIE: auth.create_session(uid)}


def _exp(biomarkers=None, uncertain_fields=None, nombre="Hemograma"):
    exp = {
        "type_label": "Resultado de laboratorio",
        "nombre_examen": nombre,
        "summary_simple": "Resumen.",
        "biomarkers": biomarkers if biomarkers is not None else [],
        "urgency": {"level": "no_urgente", "label": "Sin urgencia",
                    "action": "a", "timeframe": "t", "reason": "r"},
    }
    if uncertain_fields:
        exp["uncertain_fields"] = uncertain_fields
    return exp


def _alto():
    return [{"name": "Colesterol", "value": "240", "unit": "mg/dL",
             "flag": "alto", "ref_range": "0 - 200 mg/dL"}]


def _limpio():
    return [{"name": "Glucosa", "value": "88", "unit": "mg/dL",
             "flag": "normal", "ref_range": "70 - 100 mg/dL"}]


def test_needs_review_criterio():
    assert needs_review(_exp(biomarkers=_alto()))
    assert needs_review(_exp(biomarkers=_limpio(),
                             uncertain_fields=[{"field": "x"}]))
    assert needs_review(_exp(biomarkers=[
        {"name": "X", "value": "1", "unit": "", "flag": "uncertain",
         "ref_range": ""}]))
    assert not needs_review(_exp(biomarkers=_limpio()))
    assert not needs_review({})
    # "bajo" también es fuera de rango y cuenta.
    assert needs_review(_exp(biomarkers=[
        {"name": "X", "value": "1", "unit": "", "flag": "bajo",
         "ref_range": ""}]))


@pytest.fixture()
def owner_docs():
    email = f"f-{uuid.uuid4().hex[:8]}@x.test"
    uid = dal.create_user(email)["id"]
    flagged = "f-" + uuid.uuid4().hex[:12]
    clean = "f-" + uuid.uuid4().hex[:12]
    exp_flagged = _exp(biomarkers=_alto(), nombre="Colesterol alto")
    dal.save_document({"id": flagged, "user_id": uid, "intake_id": flagged,
                       "explanation": exp_flagged})
    # El resultado (/resultado/{id}) se sirve desde el intake.
    dal.save_intake({"id": flagged, "status": "ready", "owner_user_id": uid,
                     "explanation": exp_flagged})
    dal.save_document({"id": clean, "user_id": uid, "intake_id": clean,
                       "explanation": _exp(biomarkers=_limpio(),
                                           nombre="Glucosa normal")})
    yield {"uid": uid, "flagged": flagged, "clean": clean}
    _clean([uid, flagged, clean])


def test_hero_cuenta_solo_los_pendientes(client, owner_docs):
    r = client.get("/", cookies=_cookies(owner_docs["uid"]))
    assert r.status_code == 200
    assert '<div class="stat-num stat-num--warn">1</div>' in r.text
    assert "Documentos por revisar" in r.text
    # La tarjeta del documento con valores fuera de rango lleva la etiqueta.
    r2 = client.get("/ficha", cookies=_cookies(owner_docs["uid"]))
    assert "Por revisar" in r2.text
    # Una por presentación: la tarjeta y la fila de la lista muestran el
    # mismo documento, así que la etiqueta aparece dos veces en la página.
    cards_html = r2.text.partition('<div class="table-wrap doc-list">')[0]
    assert cards_html.count("Por revisar") == 1
    assert r2.text.count("Por revisar") == 2


def test_marcar_revisado_baja_el_heroe_y_muestra_revisado(client, owner_docs):
    uid = owner_docs["uid"]
    fid = owner_docs["flagged"]
    r = client.post(f"/ficha/{fid}/revisar", data={"volver": f"/ficha/{fid}"},
                    cookies=_cookies(uid), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/ficha/{fid}"
    doc = dal.get_document(fid)
    assert doc["reviewed_at"]
    # Héroe a cero y "Todo al día".
    r2 = client.get("/", cookies=_cookies(uid))
    assert '<div class="stat-num stat-num--ok">0</div>' in r2.text
    assert "Todo al día" in r2.text
    # La etiqueta desaparece de la tarjeta y el detalle muestra Revisado.
    r3 = client.get("/ficha", cookies=_cookies(uid))
    assert "Por revisar" not in r3.text
    r4 = client.get(f"/ficha/{fid}", cookies=_cookies(uid))
    assert "Revisado" in r4.text
    assert "Marcar como revisado" not in r4.text


def test_marcar_revisado_solo_el_dueno_y_solo_si_requiere(client, owner_docs):
    uid = owner_docs["uid"]
    # Documento limpio: no hay nada que marcar, no cambia nada.
    r = client.post(f"/ficha/{owner_docs['clean']}/revisar",
                    data={"volver": "/ficha"}, cookies=_cookies(uid),
                    follow_redirects=False)
    assert r.status_code == 303
    assert dal.get_document(owner_docs["clean"])["reviewed_at"] is None
    # Otro usuario: 404, y el documento sigue sin revisar.
    otro = dal.create_user(f"f-{uuid.uuid4().hex[:8]}@x.test")["id"]
    try:
        r2 = client.post(f"/ficha/{owner_docs['flagged']}/revisar",
                         data={"volver": "/ficha"}, cookies=_cookies(otro),
                         follow_redirects=False)
        assert r2.status_code == 404
        assert dal.get_document(owner_docs["flagged"])["reviewed_at"] is None
    finally:
        _clean([otro])


def test_revisar_desde_la_pagina_de_resultado(client, owner_docs):
    # El botón vive también en /resultado (mismo documento, mismo id):
    # la página muestra la acción y volver lleva al resultado.
    uid = owner_docs["uid"]
    fid = owner_docs["flagged"]
    r = client.get(f"/resultado/{fid}", cookies=_cookies(uid))
    assert r.status_code == 200
    assert "Marcar como revisado" in r.text
    r2 = client.post(f"/ficha/{fid}/revisar",
                     data={"volver": f"/resultado/{fid}"},
                     cookies=_cookies(uid), follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == f"/resultado/{fid}"


def test_re_guardar_conserva_la_revision(client, owner_docs):
    uid = owner_docs["uid"]
    fid = owner_docs["flagged"]
    dal.mark_reviewed(fid)
    # Una corrección re-guarda el documento con la explicación nueva.
    doc = dal.get_document(fid)
    exp = doc["explanation"]
    exp["summary_simple"] = "Resumen corregido."
    dal.save_document({**doc, "explanation": exp})
    assert dal.get_document(fid)["reviewed_at"]


def test_revision_sobrevive_a_la_reconstruccion(client, owner_docs):
    # file-first: reviewed_at vive en meta.json y el rebuild lo restaura.
    uid = owner_docs["uid"]
    fid = owner_docs["flagged"]
    dal.mark_reviewed(fid)
    doc = dal.get_document(fid)
    try:
        assert file_store.save_explanation(
            uid, doc.get("member_id"), fid, doc["explanation"])
        assert file_store.set_reviewed_at(
            doc.get("member_id"), fid, doc["reviewed_at"])
        con = sqlite3.connect(_db_path())
        con.execute("DELETE FROM documents WHERE id=?", (fid,))
        con.commit()
        con.close()
        file_store.rebuild_index()
        assert dal.get_document(fid)["reviewed_at"]
    finally:
        file_store.delete_document_files(doc.get("member_id"), fid)
