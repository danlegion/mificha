"""MiFicha v0.1 — autenticación.

Con Supabase configurado: interfaz lista para Supabase Auth (magic link +
phone OTP) — el flujo real se conecta cuando haya frontend de login.
Sin Supabase: fallback local de desarrollo con cookie de sesión firmada
(itsdangerous, SECRET_KEY del entorno). El registro en modo desarrollo omite
la verificación de email/teléfono y lo dice claramente en el log.

NUNCA presentar el modo desarrollo como autenticación real: las plantillas
reciben `dev_auth: True` y muestran el banner de demostración.
"""

import logging

from itsdangerous import BadSignature, URLSafeSerializer

from app import config
from app.db import dal

log = logging.getLogger("mificha.auth")

SESSION_COOKIE = "mificha_session"
DEV_AUTH = not config.USE_SUPABASE  # True => fallback local de desarrollo


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(config.SECRET_KEY, salt="mificha-session")


def create_session(user_id: str) -> str:
    """Valor firmado para la cookie de sesión (modo desarrollo)."""
    return _serializer().dumps({"uid": user_id})


def get_session_user_id(cookie_value: str | None) -> str | None:
    if not cookie_value:
        return None
    try:
        data = _serializer().loads(cookie_value)
    except BadSignature:
        return None
    return data.get("uid")


def get_current_user(request) -> dict | None:
    """Usuario de la sesión actual (cookie firmada o Supabase Auth)."""
    if config.USE_SUPABASE:
        # Supabase Auth: el token llegaría en Authorization o cookie `sb-*`.
        # Interfaz lista; el intercambio del token se implementa con el login.
        return None
    uid = get_session_user_id(request.cookies.get(SESSION_COOKIE))
    if not uid:
        return None
    return dal.get_user_by_id(uid)


def register_dev(email: str, telefono: str) -> dict:
    """Registro en modo desarrollo: crea/busca usuario SIN verificación."""
    log.warning(
        "MODO DESARROLLO: verificación de email/teléfono omitida "
        "(registro de %s)",
        email,
    )
    return dal.create_user(email=email, telefono=telefono)


# --- Supabase Auth (interfaz lista para cuando se conecte el login) --------

def send_magic_link(email: str) -> None:
    """Envía magic link por email vía Supabase Auth (requiere configuración)."""
    if not config.USE_SUPABASE:
        raise RuntimeError("Supabase Auth no está configurado.")
    from supabase import create_client  # type: ignore

    create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY).auth.sign_in_with_otp(
        {"email": email}
    )


def send_phone_otp(telefono: str) -> None:
    """Envía OTP por SMS vía Supabase Auth (requiere configuración)."""
    if not config.USE_SUPABASE:
        raise RuntimeError("Supabase Auth no está configurado.")
    from supabase import create_client  # type: ignore

    create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY).auth.sign_in_with_otp(
        {"phone": telefono}
    )
