"""MiFicha v0.1 — extracción de texto de PDFs con PyMuPDF.

Contrato:
    extract_document(pdf_bytes: bytes) -> dict
    {
      "pages": [{"page_number": int, "text": str,
                 "has_text": bool, "is_scanned": bool}],
      "full_text": str,
      "scanned_pages": [int],
      "num_pages": int,
    }

Regla: una página con menos de 20 caracteres extraíbles (tras strip) cuenta
como escaneada (is_scanned=True). page_number es 1-based.
"""

import fitz  # PyMuPDF

MIN_CHARS_FOR_TEXT_PAGE = 20


def extract_document(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        scanned_pages = []
        textos = []
        for i, page in enumerate(doc):
            numero = i + 1  # 1-based
            texto = (page.get_text("text") or "").strip()
            tiene_texto = len(texto) >= MIN_CHARS_FOR_TEXT_PAGE
            es_escaneada = not tiene_texto
            pages.append(
                {
                    "page_number": numero,
                    "text": texto,
                    "has_text": tiene_texto,
                    "is_scanned": es_escaneada,
                }
            )
            if es_escaneada:
                scanned_pages.append(numero)
            if texto:
                textos.append(texto)
        return {
            "pages": pages,
            "full_text": "\n\n".join(textos),
            "scanned_pages": scanned_pages,
            "num_pages": len(pages),
        }
    finally:
        doc.close()
