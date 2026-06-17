from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "tools" / "ai_coding_delegate.py"
SPEC = importlib.util.spec_from_file_location("ai_coding_delegate", MODULE_PATH)
ai_coding_delegate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = ai_coding_delegate
SPEC.loader.exec_module(ai_coding_delegate)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "diff --git a/x b/x"}}]}).encode("utf-8")


def _clear_delegate_env(monkeypatch):
    for name in ai_coding_delegate.KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_missing_delegate_key_fails_closed_without_network(monkeypatch):
    _clear_delegate_env(monkeypatch)

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("network should not be called without a key")

    monkeypatch.setattr(ai_coding_delegate.request, "urlopen", fail_urlopen)

    result = ai_coding_delegate.generate_delegate("draft this", load_files=False)

    assert result.text is None
    assert "missing coding delegate API key" in str(result.error)


def test_delegate_key_precedence_is_coding_specific(monkeypatch):
    _clear_delegate_env(monkeypatch)
    monkeypatch.setenv("NVAPI_KEY", "nvidia-key")
    monkeypatch.setenv("HFT3_CODE_DELEGATE_API_KEY", "delegate-key")

    name, value = ai_coding_delegate.resolve_api_key(load_files=False)

    assert name == "HFT3_CODE_DELEGATE_API_KEY"
    assert value == "delegate-key"


def test_delegate_request_matches_nvidia_chat_shape(monkeypatch):
    captured = {}
    _clear_delegate_env(monkeypatch)
    monkeypatch.setenv("HFT3_CODE_DELEGATE_API_KEY", "secret-token")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["authorization"] = req.get_header("Authorization")
        captured["accept"] = req.get_header("Accept")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(ai_coding_delegate.request, "urlopen", fake_urlopen)

    result = ai_coding_delegate.generate_delegate(
        "write the smallest patch",
        contexts=[("file.py", "def old():\n    pass\n")],
        mode="patch",
        max_tokens=123,
        temperature=0.1,
        top_p=0.9,
        timeout_s=7,
        load_files=False,
    )

    assert result.text == "diff --git a/x b/x"
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["timeout"] == 7
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["accept"] == "application/json"
    assert captured["payload"]["model"] == "minimaxai/minimax-m3"
    assert captured["payload"]["max_tokens"] == 123
    assert captured["payload"]["temperature"] == 0.1
    assert captured["payload"]["top_p"] == 0.9
    assert captured["payload"]["stream"] is False
    assert "max_completion_tokens" not in captured["payload"]
    assert "reasoning_effort" not in captured["payload"]
    assert "secret-token" not in json.dumps(captured["payload"])


def test_error_redaction_removes_secret():
    assert ai_coding_delegate._redact("bad secret-token here", "secret-token") == "bad <redacted> here"


def test_default_context_cap_uses_large_context_window():
    assert ai_coding_delegate.DEFAULT_MAX_CONTEXT_CHARS == 450_000


def test_context_file_rejects_secret_like_path(tmp_path):
    secret_path = tmp_path / ".env"
    secret_path.write_text("HFT3_CODE_DELEGATE_API_KEY=not-real", encoding="utf-8")

    try:
        ai_coding_delegate.read_context_file(secret_path)
    except ValueError as exc:
        assert "secret-like context filename" in str(exc)
    else:
        raise AssertionError("secret-like context path should be rejected")


def test_context_file_rejects_secret_like_content(tmp_path):
    source_path = tmp_path / "source.py"
    fake_token = "nvapi-" + ("a" * 32)
    source_path.write_text(f"TOKEN={fake_token}", encoding="utf-8")

    try:
        ai_coding_delegate.read_context_file(source_path)
    except ValueError as exc:
        assert "secret-like content patterns" in str(exc)
        assert fake_token not in str(exc)
    else:
        raise AssertionError("secret-like context content should be rejected")


def test_prompt_file_uses_secret_guard(tmp_path):
    prompt_path = tmp_path / "keys.env"
    prompt_path.write_text("NVAPI_KEY=not-real", encoding="utf-8")

    try:
        ai_coding_delegate.read_prompt_file(prompt_path)
    except ValueError as exc:
        assert "secret-like context filename" in str(exc)
    else:
        raise AssertionError("secret-like prompt path should be rejected")


def test_prompt_text_rejects_secret_like_content():
    fake_token = "nvapi-" + ("b" * 32)

    class Args:
        prompt = f"use this {fake_token}"
        prompt_file = None

    try:
        ai_coding_delegate._read_prompt(Args())
    except ValueError as exc:
        assert "secret-like content patterns" in str(exc)
        assert fake_token not in str(exc)
    else:
        raise AssertionError("secret-like prompt text should be rejected")


def test_output_path_must_stay_in_runtime_delegate(tmp_path):
    safe = ai_coding_delegate.repo_root() / "runtime" / "ai_delegate" / "delegate.patch.txt"
    assert ai_coding_delegate.validate_output_path(safe) == safe.resolve()

    unsafe = ai_coding_delegate.repo_root() / "docs" / "ai" / "delegate.patch.txt"
    try:
        ai_coding_delegate.validate_output_path(unsafe)
    except ValueError as exc:
        assert "write delegate drafts under" in str(exc)
    else:
        raise AssertionError("tracked output path should be rejected")


def test_cli_rejects_bad_output_before_network(monkeypatch, capsys):
    _clear_delegate_env(monkeypatch)

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("network should not be called for invalid output path")

    monkeypatch.setattr(ai_coding_delegate.request, "urlopen", fail_urlopen)

    rc = ai_coding_delegate.main(["--prompt", "draft only", "--output", "docs/ai/delegate.patch.txt"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "write delegate drafts under" in captured.err
