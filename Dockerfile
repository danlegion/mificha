# MiFicha en Render — imagen Docker con Tesseract OCR (spa+eng).
FROM python:3.12-slim

# Tesseract + paquetes de idioma español e inglés para el OCR de escaneos,
# más el modelo OSD (detección de orientación para enderezar fotos rotadas).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Render inyecta $PORT; fuera de Render, 8000 por defecto.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
