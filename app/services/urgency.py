"""MiFicha v0.1 — decisión conservadora del nivel de urgencia.

Contrato (esquema de `signals` según tests/conftest.py):
    decide_urgency(signals: dict) -> dict
    {"level": "no_urgente" | "agendar_cita"
              | "hablar_hoy_con_profesional" | "buscar_ayuda_ya",
     "label": str, "action": str, "timeframe": str, "reason": str}

    Los textos salen en el idioma actual (navegador o cambio manual).
    signals = {
      "abnormal": [{"name": str, "flag": "alta"|"baja",
                    "severity": "leve"|"moderada"|"grave"|"critica"}],
      "has_critical_flag": bool,
      "has_uncertain_critical": bool,
      "explicit_priority": str | None,
    }

Reglas conservadoras:
- bandera crítica o severidad "critica" (o lenguaje de emergencia narrativo)
  -> buscar_ayuda_ya
- "moderada"/"grave" -> hablar_hoy_con_profesional
- un valor "leve" -> agendar_cita
- dato crítico incierto sin nada confirmado -> nunca no_urgente
  (mínimo agendar_cita, "por precaución")
- la prioridad explícita de una orden se reproduce literal en `reason`,
  nunca se inventa.
"""

_SEVERIDAD_CRITICA = {"critica", "crítica", "critical"}
_SEVERIDAD_ALTA = {"grave", "moderada", "severa", "severe", "moderate"}

from app.i18n import t as _t


def _norm(s) -> str:
    return str(s or "").strip().lower()


def decide_urgency(signals: dict) -> dict:
    signals = signals or {}

    # Esquema tests/conftest.py, con alias tolerados del boceto original.
    abnormal = signals.get("abnormal") or signals.get("abnormal_values") or []
    has_critical_flag = bool(
        signals.get("has_critical_flag") or signals.get("critical_flags")
    )
    has_uncertain_critical = bool(signals.get("has_uncertain_critical"))
    narrative_emergency = bool(signals.get("narrative_emergency_language"))
    explicit_priority = signals.get("explicit_priority")

    severities = [
        _norm(a.get("severity")) for a in abnormal if isinstance(a, dict)
    ]
    nombres = [
        str(a.get("name") or "un valor").strip()
        for a in abnormal
        if isinstance(a, dict)
    ]

    priority_note = ""
    if explicit_priority:
        # Se reproduce literal; nunca se inventa.
        priority_note = _t("u.priority_note", p=explicit_priority)

    es_critico = (
        has_critical_flag
        or narrative_emergency
        or any(s in _SEVERIDAD_CRITICA for s in severities)
    )
    es_serio = any(s in _SEVERIDAD_ALTA for s in severities)

    if es_critico:
        detalle = (
            f" ({', '.join(nombres)})" if nombres else ""
        )
        return {
            "level": "buscar_ayuda_ya",
            "label": _t("u.now_label"),
            "action": _t("u.now_action"),
            "timeframe": _t("u.now_time"),
            "reason": (
                _t("u.now_reason", detalle=detalle) + priority_note
            ),
        }

    if es_serio:
        detalle = f" ({', '.join(nombres)})" if nombres else ""
        return {
            "level": "hablar_hoy_con_profesional",
            "label": _t("u.today_label"),
            "action": _t("u.today_action"),
            "timeframe": _t("u.today_time"),
            "reason": (
                _t("u.today_reason", detalle=detalle) + priority_note
            ),
        }

    if abnormal:
        detalle = f" ({', '.join(nombres)})" if nombres else ""
        return {
            "level": "agendar_cita",
            "label": _t("u.appt_label"),
            "action": _t("u.appt_action"),
            "timeframe": _t("u.appt_time"),
            "reason": (
                _t("u.appt_reason", detalle=detalle) + priority_note
            ),
        }

    if has_uncertain_critical:
        return {
            "level": "agendar_cita",
            "label": _t("u.appt_label"),
            "action": _t("u.appt_uncertain_action"),
            "timeframe": _t("u.appt_time"),
            "reason": (
                _t("u.appt_uncertain_reason") + priority_note
            ),
        }

    return {
        "level": "no_urgente",
        "label": _t("u.none_label"),
        "action": _t("u.none_action"),
        "timeframe": _t("u.none_time"),
        "reason": (
            _t("u.none_reason") + priority_note
        ),
    }
