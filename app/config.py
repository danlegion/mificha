"""MiFicha v0.1 — configuración leída del entorno.

El archivo .env lo mantiene otro agente (.env.example); aquí solo se lee.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- Seguridad / sesiones -------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-cambiar-en-produccion")

# --- LLM ------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")

# --- OCR ------------------------------------------------------------------
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "null")  # "null" = sin proveedor

# --- Supabase (opcional) ---------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_ANON_KEY)

# --- Límites de ingesta ----------------------------------------------------
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "15"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "30"))

# --- Compartir --------------------------------------------------------------
SHARE_TTL_DAYS = int(os.getenv("SHARE_TTL_DAYS", "7"))

# --- URLs ------------------------------------------------------------------
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
