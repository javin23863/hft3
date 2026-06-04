from __future__ import annotations

import json

from data_layer.llm import openai_compatible_client as llm_client


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode("utf-8")


def test_openai_compatible_client_sends_gpt55_xhigh_json_request(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["authorization"] = req.get_header("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response()

    monkeypatch.setenv("HFT3_LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client.request, "urlopen", fake_urlopen)

    result = llm_client.generate(
        "system",
        "user",
        model="gpt-5.5",
        base_url="https://api.openai.com/v1",
        format_json=True,
        num_predict=123,
    )

    assert result.text == '{"ok": true}'
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "gpt-5.5"
    assert captured["payload"]["reasoning_effort"] == "xhigh"
    assert captured["payload"]["max_completion_tokens"] == 123
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert "temperature" not in captured["payload"]
    assert "top_p" not in captured["payload"]


def test_openai_compatible_client_optional_sampling_controls(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response()

    monkeypatch.setenv("HFT3_LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client.request, "urlopen", fake_urlopen)

    result = llm_client.generate(
        "system",
        "user",
        model="gpt-5.5",
        temperature=0.7,
        top_p=0.95,
    )

    assert result.text == '{"ok": true}'
    assert captured["payload"]["temperature"] == 0.7
    assert captured["payload"]["top_p"] == 0.95


def test_openai_compatible_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("HFT3_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = llm_client.generate("system", "user")

    assert result.text is None
    assert "API_KEY" in str(result.error)
    assert llm_client.llm_available() is False
