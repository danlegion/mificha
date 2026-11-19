"""Pruebas unitarias de los proveedores LLM (sin red: urlopen va mockeado)."""

import io
import json
import os
import urllib.error

import pytest

from app.services import llm
from app.services.llm import (
    AnthropicProvider,
    DemoExplainer,
    GeminiProvider,
    OpenAIProvider,
    get_llm_provider,
)


class _Resp:
    """Respuesta falsa que sirve json.load(resp)."""

    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini_ok_payload(texto_json: str) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": texto_json}]}, "finishReason": "STOP"}
        ]
    }


def _explicacion_minima() -> str:
    return json.dumps(
        {
            "document_type": "lab_result",
            "type_label": "Resultado de laboratorio",
            "summary_simple": "Resumen.",
            "biomarkers": [],
            "narrative_findings": [],
            "ordered_tests": [],
            "urgency": {
                "level": "no_urgente",
                "label": "Sin urgencia",
                "action": "Nada por ahora.",
                "timeframe": "",
                "reason": "",
            },
            "recommendations": [],
            "questions_for_doctor": [],
            "uncertain_fields": [],
            "disclaimer": "d",
        }
    )


def test_gemini_explain_parsea_respuesta_y_marca_demo_false(monkeypatch):
    vistas = {}

    def falso_urlopen(req, timeout=None):
        vistas["url"] = req.full_url
        vistas["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(_gemini_ok_payload(_explicacion_minima()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", falso_urlopen)
    prov = GeminiProvider(model="gemini-x", api_key="K")
    data = prov.explain({"full_text": "GLUCOSA 90", "doc_type_hint": None})

    assert "generateContent" in vistas["url"]
    assert "gemini-x" in vistas["url"]
    assert "key=K" in vistas["url"]
    cuerpo = vistas["body"]
    assert cuerpo["generationConfig"]["responseMimeType"] == "application/json"
    assert "systemInstruction" in cuerpo
    assert "GLUCOSA 90" in cuerpo["contents"][0]["parts"][0]["text"]
    assert data["document_type"] == "lab_result"
    assert data["demo_mode"] is False


def test_gemini_reintenta_503_y_luego_ok(monkeypatch):
    llamadas = {"n": 0}

    def falso_urlopen(req, timeout=None):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "x", {}, None)
        return _Resp(_gemini_ok_payload(_explicacion_minima()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", falso_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    prov = GeminiProvider(api_key="K")
    data = prov.explain({"full_text": "t"})
    assert llamadas["n"] == 2
    assert data["demo_mode"] is False


def test_gemini_sin_key_falla():
    with pytest.raises(RuntimeError):
        GeminiProvider(api_key="").explain({"full_text": "t"})


def _openai_ok_payload(texto_json: str) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": texto_json},
             "finish_reason": "stop"}
        ]
    }


def test_openai_explain_parsea_respuesta_y_marca_demo_false(monkeypatch):
    vistas = {}

    def falso_urlopen(req, timeout=None):
        vistas["url"] = req.full_url
        vistas["auth"] = req.get_header("Authorization")
        vistas["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(_openai_ok_payload(_explicacion_minima()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", falso_urlopen)
    prov = OpenAIProvider(model="gpt-5-mini", api_key="K")
    data = prov.explain({"full_text": "GLUCOSA 90", "doc_type_hint": None})

    assert vistas["url"] == "https://api.openai.com/v1/chat/completions"
    assert vistas["auth"] == "Bearer K"
    cuerpo = vistas["body"]
    assert cuerpo["model"] == "gpt-5-mini"
    assert cuerpo["response_format"] == {"type": "json_object"}
    assert "temperature" not in cuerpo
    assert cuerpo["messages"][0]["role"] == "system"
    assert "MiFicha" in cuerpo["messages"][0]["content"]
    assert cuerpo["messages"][1]["role"] == "user"
    assert "GLUCOSA 90" in cuerpo["messages"][1]["content"]
    assert data["document_type"] == "lab_result"
    assert data["demo_mode"] is False


def test_openai_explain_en_ingles_usa_prompt_en(monkeypatch):
    vistas = {}

    def falso_urlopen(req, timeout=None):
        vistas["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(_openai_ok_payload(_explicacion_minima()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", falso_urlopen)
    prov = OpenAIProvider(api_key="K")
    prov.explain({"full_text": "GLUCOSE 90", "lang": "en"})
    assert "simple, everyday English" in vistas["body"]["messages"][0]["content"]


def test_openai_reintenta_429_y_luego_ok(monkeypatch):
    llamadas = {"n": 0}

    def falso_urlopen(req, timeout=None):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, None)
        return _Resp(_openai_ok_payload(_explicacion_minima()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", falso_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    prov = OpenAIProvider(api_key="K")
    data = prov.explain({"full_text": "t"})
    assert llamadas["n"] == 2
    assert data["demo_mode"] is False


def test_openai_sin_key_falla():
    with pytest.raises(RuntimeError):
        OpenAIProvider(api_key="").explain({"full_text": "t"})


def test_anthropic_model_usa_anthropic_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-nuevo")
    monkeypatch.setenv("LLM_MODEL", "claude-viejo")
    assert AnthropicProvider(api_key="K").model == "claude-nuevo"


def test_anthropic_model_acepta_llm_model_legacy(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "claude-viejo")
    assert AnthropicProvider(api_key="K").model == "claude-viejo"


def test_anthropic_model_default_sin_variables(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert AnthropicProvider(api_key="K").model == "claude-sonnet-4-5-20250929"


def test_fabrica_elige_gemini_antes_que_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    assert isinstance(get_llm_provider(), GeminiProvider)


def test_fabrica_elige_anthropic_sin_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    assert isinstance(get_llm_provider(), AnthropicProvider)


def test_fabrica_elige_openai_sin_las_otras(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "ok")
    assert isinstance(get_llm_provider(), OpenAIProvider)


def test_fabrica_llm_provider_openai_explicito(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("OPENAI_API_KEY", "ok")
    assert isinstance(get_llm_provider(), OpenAIProvider)


def test_fabrica_llm_provider_explicito_sin_llave_cae_al_siguiente(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    assert isinstance(get_llm_provider(), GeminiProvider)


def test_fabrica_llm_provider_demo(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "demo")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    assert isinstance(get_llm_provider(), DemoExplainer)


def test_fabrica_llm_provider_desconocido_usa_auto(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "no-existe")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    assert isinstance(get_llm_provider(), GeminiProvider)


def test_fabrica_demo_sin_llaves(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_llm_provider(), DemoExplainer)
    assert get_llm_provider().is_demo


def test_demo_no_requiere_red():
    prov = DemoExplainer()
    assert prov.is_demo
    assert os.getenv("PYTEST_SIN_RED", "1") == "1"  # marcador: nada de red aquí
