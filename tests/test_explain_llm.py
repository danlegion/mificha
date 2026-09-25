"""Placeholder: la explicación vía LLM no se prueba en esta suite.

No hay llamadas de red en los tests. La explicación con LLM
(app/services/explain.py) se valida manual con ANTHROPIC_API_KEY.
"""

import pytest


@pytest.mark.skip(reason="requiere ANTHROPIC_API_KEY; sin llamadas de red en la suite")
def test_explicacion_llm_genera_explanation_valida():
    pass
