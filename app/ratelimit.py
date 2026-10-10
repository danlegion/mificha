"""Límite de peticiones por clave (ventana deslizante, en memoria).

Pensado para frenar abuso del endpoint de subida (cada subida quema
crédito de IA). En el plan gratuito hay una sola instancia, así que el
estado en memoria es suficiente; se pierde al redesplegar, lo cual es
aceptable para un límite anti-abuso.
"""

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def check(key: str, limit: int, window_s: int = 3600) -> bool:
    """True si la petición pasa (y la cuenta); False si excede el límite."""
    if limit <= 0:
        return True
    now = time.time()
    with _lock:
        hits = _hits[key]
        cutoff = now - window_s
        i = 0
        while i < len(hits) and hits[i] < cutoff:
            i += 1
        del hits[:i]
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def reset() -> None:
    """Limpia todo el estado (tests)."""
    with _lock:
        _hits.clear()
