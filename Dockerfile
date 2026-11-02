# MiFicha on Render — Docker image with Tesseract OCR (spa+eng).
FROM python:3.12-slim

# Tesseract + Spanish and English language packages for scan OCR,
# plus the OSD model (orientation detection to straighten rotated photos).
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

# Render injects $PORT; outside Render, 18473 by default.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-18473}"]
