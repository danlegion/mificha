"""Tests de app.services.urgency.decide_urgency.

Contrato:
    decide_urgency(signals: dict) -> dict
    {"level": "no_urgente" | "agendar_cita" | "hablar_hoy_con_profesional"
              | "buscar_ayuda_ya",
     "label": str, "action": str, "timeframe": str, "reason": str}

Esquema de `signals` asumido: ver conftest.py (make_signals).
"""

import pytest

from app.services.urgency import decide_urgency

from conftest import (
    NIVELES_VALIDOS,
    assert_urgencia_valida,
    make_abnormal,
    make_signals,
)


def test_bandera_critica_buscar_ayuda_ya():
    signals = make_signals(
        abnormal=[make_abnormal(name="Potasio", flag="alta", severity="critica")],
        has_critical_flag=True,
    )
    r = assert_urgencia_valida(decide_urgency(signals))
    assert r["level"] == "buscar_ayuda_ya"


def test_un_valor_levemente_anormal_agendar_cita():
    signals = make_signals(
        abnormal=[make_abnormal(name="Colesterol total", flag="alta", severity="leve")]
    )
    r = assert_urgencia_valida(decide_urgency(signals))
    assert r["level"] == "agendar_cita"


def test_todo_normal_no_urgente():
    r = assert_urgencia_valida(decide_urgency(make_signals()))
    assert r["level"] == "no_urgente"


def test_dato_critico_incierto_nunca_es_no_urgente():
    """Conservador: si hay un dato crítico incierto y nada confirmado,
    el nivel debe ser agendar_cita o mayor, nunca no_urgente."""
    signals = make_signals(has_uncertain_critical=True)
    r = assert_urgencia_valida(decide_urgency(signals))
    assert r["level"] in NIVELES_VALIDOS - {"no_urgente"}


def test_prioridad_explicita_se_reproduce_literal_y_no_se_inventa():
    token = "PRIORITARIO-XYZ-123"
    signals = make_signals(
        abnormal=[make_abnormal(severity="leve")],
        explicit_priority=token,
    )
    r = assert_urgencia_valida(decide_urgency(signals))
    assert token in r["reason"], "la prioridad de la orden debe citarse literal en reason"

    control = make_signals(abnormal=[make_abnormal(severity="leve")])
    r_control = assert_urgencia_valida(decide_urgency(control))
    assert token not in r_control["reason"], "no se debe inventar una prioridad"


@pytest.mark.parametrize(
    "signals, nivel_esperado",
    [
        (make_signals(), "no_urgente"),
        (make_signals(abnormal=[make_abnormal(severity="leve")]), "agendar_cita"),
        # Mapeo asumido: una anomalía moderada -> hablar hoy con un profesional.
        (make_signals(abnormal=[make_abnormal(name="Glucosa", flag="alta",
                                              severity="moderada")]),
         "hablar_hoy_con_profesional"),
        (make_signals(abnormal=[make_abnormal(severity="critica")],
                      has_critical_flag=True),
         "buscar_ayuda_ya"),
    ],
)
def test_todos_los_niveles_devuelven_textos_en_espanol(signals, nivel_esperado):
    r = assert_urgencia_valida(decide_urgency(signals))
    assert r["level"] == nivel_esperado
    # label/action/timeframe son textos para la UI, distintos del código de nivel
    for campo in ("label", "action", "timeframe"):
        assert r[campo] != r["level"]
