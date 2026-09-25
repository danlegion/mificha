"""Tests de app.services.pdf_extract.extract_document.

Contrato:
    extract_document(pdf_bytes: bytes) -> dict
    {"pages": [{"page_number": int, "text": str,
                "has_text": bool, "is_scanned": bool}],
     "full_text": str, "scanned_pages": [int], "num_pages": int}
    Regla: página con <20 caracteres extraíbles -> is_scanned True.

Los PDFs se generan en memoria con PyMuPDF (sin archivos temporales).
"""

import pymupdf

from app.services.pdf_extract import extract_document


def _pdf_bytes(construir):
    doc = pymupdf.open()
    try:
        construir(doc)
        return doc.tobytes()
    finally:
        doc.close()


def _pagina_laboratorio(doc):
    """Página digital con una tabla de laboratorio (texto real)."""
    page = doc.new_page()
    lineas = [
        "LABORATORIO CLINICO SANTA MARIA",
        "Paciente: Juan Perez    Fecha: 2026-09-20",
        "Biomarcador        Valor   Unidad   Rango de referencia",
        "Glucosa            95      mg/dL    70 - 100",
        "Colesterol total   210     mg/dL    menos de 200",
        "Hemoglobina        14.2    g/dL     13.5 - 17.5",
    ]
    y = 72
    for linea in lineas:
        page.insert_text((72, y), linea, fontsize=11)
        y += 18


def _pagina_solo_dibujo(doc):
    """Página sin texto (rectángulo dibujado): simula escaneado sin OCR."""
    page = doc.new_page()
    rect = pymupdf.Rect(72, 72, 540, 700)
    page.draw_rect(rect, color=(0.2, 0.2, 0.2), fill=(0.93, 0.93, 0.93), width=2)


def test_pdf_digital_extrae_tabla_de_laboratorio():
    pdf = _pdf_bytes(_pagina_laboratorio)
    r = extract_document(pdf)

    assert r["num_pages"] == 1
    assert len(r["pages"]) == 1
    pagina = r["pages"][0]
    assert pagina["has_text"] is True
    assert pagina["is_scanned"] is False
    assert "Glucosa" in pagina["text"]
    assert "Colesterol total" in r["full_text"]
    assert r["scanned_pages"] == []


def test_pdf_sin_texto_se_marca_escaneado():
    pdf = _pdf_bytes(_pagina_solo_dibujo)
    r = extract_document(pdf)

    assert r["num_pages"] == 1
    pagina = r["pages"][0]
    assert pagina["has_text"] is False
    assert pagina["is_scanned"] is True
    # scanned_pages debe coincidir con las páginas marcadas (sin asumir base 0/1)
    assert r["scanned_pages"] == [pagina["page_number"]]


def test_pdf_multipagina_mixto():
    def construir(doc):
        _pagina_laboratorio(doc)
        _pagina_solo_dibujo(doc)
        _pagina_laboratorio(doc)

    r = extract_document(_pdf_bytes(construir))

    assert r["num_pages"] == 3
    estados = [(p["has_text"], p["is_scanned"]) for p in r["pages"]]
    assert estados == [(True, False), (False, True), (True, False)]
    esperadas = [p["page_number"] for p in r["pages"] if p["is_scanned"]]
    assert r["scanned_pages"] == esperadas
    assert len(esperadas) == 1
    assert "Glucosa" in r["full_text"]


def test_regla_20_caracteres():
    """<20 caracteres extraíbles -> is_scanned True; >=20 -> False."""
    def construir(doc):
        p1 = doc.new_page()
        p1.insert_text((72, 72), "x" * 19, fontsize=11)   # 19 caracteres
        p2 = doc.new_page()
        p2.insert_text((72, 72), "y" * 25, fontsize=11)   # 25 caracteres

    r = extract_document(_pdf_bytes(construir))

    assert r["num_pages"] == 2
    assert r["pages"][0]["is_scanned"] is True
    assert r["pages"][1]["is_scanned"] is False
