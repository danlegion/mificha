"""MiFicha v0.1 — configuración leída del entorno.

El archivo .env lo mantiene otro agente (.env.example); aquí solo se lee.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- Seguridad / sesiones -------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-cambiar-en-produccion")

# --- Acceso único (solo auto-hospedaje, sin Supabase) ---------------------
# Cuando APP_PASSWORD está puesto, la app exige iniciar sesión con este
# único usuario/contraseña antes de cualquier página (salvo /acceso,
# estáticos y enlaces de compartir /s/). Sin APP_PASSWORD no hay puerta:
# el demo público y el flujo de invitado siguen como antes. La contraseña
# vive solo en .env (nunca en git ni en el código) y se compara en tiempo
# constante. No cifra datos en reposo: es un cerrojo de acceso, no
# cifrado, y va encima de Tailscale, no en su lugar.
APP_USERNAME = os.getenv("APP_USERNAME", "")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# --- LLM ------------------------------------------------------------------
# Proveedor de explicación: "gemini" | "anthropic" | "openai" | "demo" |
# "auto" (default: el primero con llave, en ese orden). Un proveedor
# explícito sin su llave cae al siguiente con llave, con un warning.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Model for Claude. The old LLM_MODEL name still works as a fallback.
# (Sonnet 4.5 retired Nov 30, 2026; the default is now Sonnet 5.5.)
ANTHROPIC_MODEL = os.getenv(
    "ANTHROPIC_MODEL",
    os.getenv("LLM_MODEL", "claude-sonnet-5-5"),
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# --- OCR ------------------------------------------------------------------
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "null")  # "null" = sin proveedor

# --- Supabase (opcional) ---------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
# Clave service_role: SOLO en el servidor (Render). Salta RLS y se usa para
# el acceso a datos (tablas y Storage). La anon key queda reservada para
# Supabase Auth (magic links), que la exige.
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def supabase_data_key() -> str:
    """Clave para el acceso a datos desde el servidor.

    Con RLS activado en las tablas, la anon key ya no puede leerlas; por eso
    el backend usa service_role (que salta RLS). Si falta, se degrada a la
    anon key con un error en el log (la app fallará de forma visible, no
    silenciosa).
    """
    if SUPABASE_SERVICE_KEY:
        return SUPABASE_SERVICE_KEY
    import logging

    logging.getLogger("mificha.config").error(
        "SUPABASE_SERVICE_KEY no configurada: el acceso a datos usará "
        "la anon key y fallará con RLS activado."
    )
    return SUPABASE_ANON_KEY

# --- Límites de ingesta ----------------------------------------------------
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "15"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "30"))

# --- Compartir --------------------------------------------------------------
SHARE_TTL_DAYS = int(os.getenv("SHARE_TTL_DAYS", "7"))
# Tope de subidas por IP y por hora (anti-abuso: cada subida quema IA).
UPLOAD_RATE_LIMIT_PER_HOUR = int(os.getenv("UPLOAD_RATE_LIMIT_PER_HOUR", "10"))

# --- URLs ------------------------------------------------------------------
BASE_URL = os.getenv("BASE_URL", "http://localhost:18473").rstrip("/")
