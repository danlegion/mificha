"""MiFicha v0.1 — decisión conservadora del nivel de urgencia.

Contrato (esquema de `signals` según tests/conftest.py):
    decide_urgency(signals: dict) -> dict
    {"level": "no_urgente" | "agendar_cita"
              | "hablar_hoy_con_profesional" | "buscar_ayuda_ya",
     "label": str, "action": str, "timeframe": str, "reason": str}

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
        priority_note = f' La orden médica indica prioridad "{explicit_priority}".'

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
            "label": "Busca ayuda ya",
            "action": (
                "Ve a urgencias o llama al número de emergencias de tu país "
                "ahora mismo. No esperes a mañana."
            ),
            "timeframe": "Ahora mismo",
            "reason": (
                "Hay una señal crítica en tu documento"
                + detalle
                + " que necesita atención médica inmediata."
                + priority_note
            ),
        }

    if es_serio:
        detalle = f" ({', '.join(nombres)})" if nombres else ""
        return {
            "level": "hablar_hoy_con_profesional",
            "label": "Habla hoy con un profesional de la salud",
            "action": (
                "Comunícate hoy mismo con tu doctor, tu laboratorio o un "
                "servicio de salud para que revisen estos resultados contigo."
            ),
            "timeframe": "Hoy",
            "reason": (
                "Hay valores fuera de rango"
                + detalle
                + " que conviene revisar con un profesional hoy mismo."
                + priority_note
            ),
        }

    if abnormal:
        detalle = f" ({', '.join(nombres)})" if nombres else ""
        return {
            "level": "agendar_cita",
            "label": "Agenda una cita",
            "action": (
                "Pide una cita con tu doctor para revisar estos resultados "
                "con calma."
            ),
            "timeframe": "En los próximos días o semanas",
            "reason": (
                "Hay valores fuera de su rango de referencia"
                + detalle
                + " que vale la pena comentar con tu doctor."
                + priority_note
            ),
        }

    if has_uncertain_critical:
        return {
            "level": "agendar_cita",
            "label": "Agenda una cita",
            "action": (
                "Pide una cita con tu doctor y lleva el documento original "
                "para que lo revise directamente."
            ),
            "timeframe": "En los próximos días o semanas",
            "reason": (
                "No pudimos leer con certeza una parte del documento, "
                "por precaución conviene que un profesional lo revise."
                + priority_note
            ),
        }

    return {
        "level": "no_urgente",
        "label": "Sin urgencia",
        "action": (
            "Sigue tu rutina normal y comenta estos resultados en tu próxima "
            "cita de control."
        ),
        "timeframe": "En tu próxima cita de control",
        "reason": (
            "Todos los valores están dentro de sus rangos de referencia."
            + priority_note
        ),
    }
