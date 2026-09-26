"""MiFicha v0.1 — autenticación.

Con Supabase configurado: registro con código de 6 dígitos por correo vía
Supabase Auth (sign_in_with_otp + verify_otp). La sesión de la app es una
cookie firmada por el servidor (itsdangerous, SECRET_KEY del entorno) que
guarda el id del usuario de nuestra tabla `users`.
Sin Supabase: fallback local de desarrollo con la misma cookie firmada.
El registro en modo desarrollo omite la verificación de email/teléfono y
lo dice claramente en el log.

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
    """Usuario de la sesión actual (cookie firmada por el servidor)."""
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


# --- Supabase Auth: enlace mágico (funciona sin editar plantillas) -----------

def send_magic_link(email: str, redirect_to: str | None = None) -> None:
    """Envía el enlace de acceso al correo vía Supabase Auth.

    Usa la plantilla "Magic Link" por defecto (sin necesidad de editarla):
    el correo trae un enlace que Supabase redirige a `redirect_to`.
    """
    try:
        _supabase_client().auth.sign_in_with_otp({
            "email": email,
            "options": {"email_redirect_to": redirect_to} if redirect_to else {},
        })
    except AuthError:
        raise
    except Exception as exc:
        raise _friendly_error(exc)
    log.info("Enlace mágico enviado a %s", email)


def complete_magic_link(access_token: str = "", token_hash: str = "") -> dict:
    """Valida el token que Supabase dejó en el enlace mágico.

    Flujo implícito (plantilla por defecto): access_token en el fragmento.
    Flujo PKCE: token_hash en el query. Devuelve {"id", "email"} verificado.
    """
    access_token = (access_token or "").strip()
    token_hash = (token_hash or "").strip()
    if not access_token and not token_hash:
        raise AuthError("codigo_invalido",
                        "Ese enlace no es válido o ya venció. Pide uno nuevo.")
    try:
        client = _supabase_client()
        if token_hash:
            resp = client.auth.verify_otp(
                {"token_hash": token_hash, "type": "email"})
        else:
            resp = client.auth.get_user(access_token)
    except Exception as exc:
        raise _friendly_error(exc)
    user = getattr(resp, "user", None)
    if not user or not getattr(user, "id", None):
        raise AuthError("codigo_invalido",
                        "Ese enlace no es válido o ya venció. Pide uno nuevo.")
    return {"id": user.id, "email": getattr(user, "email", None) or ""}


# --- Supabase Auth: código de 6 dígitos (requiere plantilla/SMTP propio) ----

class AuthError(Exception):
    """Error de autenticación con mensaje listo para mostrar al usuario."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "codigo_invalido" | "limite" | "red" | "config"


def _supabase_client():
    """Cliente de Supabase con la anon key (suficiente para Auth OTP)."""
    if not config.USE_SUPABASE:
        raise AuthError(
            "config",
            "El registro con Supabase Auth aún no está conectado en esta versión.",
        )
    from supabase import create_client  # type: ignore

    return create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)


def _friendly_error(exc: Exception) -> AuthError:
    msg = (str(exc) or "").lower()
    if "rate limit" in msg or "too many" in msg or "429" in msg:
        return AuthError(
            "limite",
            "Pediste varios códigos seguidos. Espera unos minutos y pide uno nuevo.",
        )
    if "expired" in msg or "invalid" in msg or "incorrect" in msg:
        return AuthError(
            "codigo_invalido",
            "Ese código no es válido o ya venció. Revisa tu correo o pide uno nuevo.",
        )
    log.warning("Error de Supabase Auth: %s", exc)
    return AuthError(
        "red",
        "No pudimos verificar tu correo en este momento. Inténtalo de nuevo.",
    )


def send_email_otp(email: str) -> None:
    """Envía el código de 6 dígitos al correo vía Supabase Auth.

    Requiere que la plantilla "Magic Link" del proyecto incluya la variable
    {{ .Token }} (si no, el correo trae un enlace en vez del código).
    """
    try:
        _supabase_client().auth.sign_in_with_otp({"email": email})
    except AuthError:
        raise
    except Exception as exc:
        raise _friendly_error(exc)
    log.info("Código OTP enviado a %s", email)


def verify_email_otp(email: str, token: str) -> dict:
    """Verifica el código; devuelve {"id", "email"} del usuario de Auth."""
    token = "".join((token or "").split())  # tolera "123 456"
    if not token:
        raise AuthError(
            "codigo_invalido",
            "Escribe el código de 6 dígitos que te enviamos por correo.",
        )
    try:
        resp = _supabase_client().auth.verify_otp(
            {"email": email, "token": token, "type": "email"}
        )
    except AuthError:
        raise
    except Exception as exc:
        raise _friendly_error(exc)
    user = getattr(resp, "user", None)
    if not user or not getattr(user, "id", None):
        raise AuthError(
            "codigo_invalido",
            "Ese código no es válido o ya venció. Revisa tu correo o pide uno nuevo.",
        )
    return {"id": user.id, "email": getattr(user, "email", None) or email}


def send_magic_link_compat(email: str) -> None:
    """Compatibilidad: antes enviaba magic link; ahora manda el código OTP."""
    send_email_otp(email)


def send_phone_otp(telefono: str) -> None:
    """Envía OTP por SMS vía Supabase Auth (requiere configuración)."""
    if not config.USE_SUPABASE:
        raise RuntimeError("Supabase Auth no está configurado.")
    from supabase import create_client  # type: ignore

    create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY).auth.sign_in_with_otp(
        {"phone": telefono}
    )
