"""Repo-local AI coding delegate for draft patches.

This tool is intentionally outside the production/research LLM lanes. It calls
an OpenAI-compatible chat-completions endpoint to draft code or review text, but
it never applies edits. Codex or a human must inspect the output and apply it
through the normal hft3 workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import error, request


KEY_ENV_NAMES = ("HFT3_CODE_DELEGATE_API_KEY", "NVAPI_KEY", "NVIDIA_API_KEY")
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "minimaxai/minimax-m3"
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.95
# MiniMax-M3 advertises a large context window. Keep a bounded default that
# uses it, while leaving room for the task prompt, system prompt, and output.
DEFAULT_MAX_CONTEXT_CHARS = 450_000
SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    "keys.env",
    "id_ed25519",
    "id_ed25519.pub",
}
SECRET_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SECRET_PATH_PARTS = ("credential", "credentials", "secret", "secrets")
SECRET_TEXT_PATTERNS = (
    ("nvidia_api_key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE)),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("authorization_bearer", re.compile(r"\bAuthorization\s*[:=]\s*Bearer\s+[^\s'\"`]+", re.IGNORECASE)),
    (
        "secret_assignment",
        re.compile(r"\b(api[_-]?key|token|password|secret)\s*=\s*['\"]?[^'\"\s<][^'\"\s]{8,}", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class DelegateResult:
    text: str | None
    error: str | None = None
    model: str | None = None
    elapsed_s: float = 0.0


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _chat_completions_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _candidate_env_paths() -> list[Path]:
    paths: list[Path] = []
    for name in ("HFT3_CODE_DELEGATE_ENV_FILE", "HFT3_KEYS_ENV"):
        value = os.environ.get(name, "").strip()
        if value:
            paths.append(Path(value))
    paths.append(Path.home() / "Desktop" / "keys.env")
    paths.append(repo_root() / ".env")

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def _load_plain_env(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def load_key_files() -> list[Path]:
    loaded: list[Path] = []
    for path in _candidate_env_paths():
        if _load_plain_env(path):
            loaded.append(path)
    return loaded


def resolve_api_key(*, explicit: str | None = None, load_files: bool = True) -> tuple[str | None, str | None]:
    if explicit:
        return "explicit", explicit
    if load_files:
        load_key_files()
    for name in KEY_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return name, value
    return None, None


def configured_status() -> dict[str, str]:
    load_key_files()
    return {name: ("set" if os.environ.get(name) else "missing") for name in KEY_ENV_NAMES}


def _redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "<redacted>")
    return text


def _extract_content(body: dict) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


def _system_prompt(mode: str) -> str:
    return "\n".join(
        [
            "You are a code-drafting delegate for hft3.",
            "Codex owns repo state, verification, and final edits; you only draft.",
            "Use only the supplied task and file context. If context is insufficient, return BLOCKED with the missing file or decision.",
            "Respect the hft3 ontology: do not invent pipelines, models, finance methodology, or HFT claims outside supplied context.",
            "Never include secrets, API keys, passwords, or Authorization headers.",
            f"Mode: {mode}.",
            "For patch mode, return a unified diff only, or BLOCKED.",
            "For plan mode, return a concise implementation plan with files and tests.",
            "For review mode, return findings first, with file/line references when provided.",
        ]
    )


def _format_user_message(prompt: str, contexts: Iterable[tuple[str, str]]) -> str:
    parts = ["TASK:", prompt.strip(), ""]
    for label, text in contexts:
        parts.extend([f"CONTEXT: {label}", "```", text, "```", ""])
    return "\n".join(parts).strip()


def _context_path_block_reason(path: Path) -> str | None:
    lowered_parts = [part.lower() for part in path.parts]
    name = path.name.lower()
    if name in SECRET_FILE_NAMES:
        return f"secret-like context filename: {path.name}"
    if name.endswith(SECRET_FILE_SUFFIXES):
        return f"secret-like context file suffix: {path.suffix}"
    if any(part in SECRET_PATH_PARTS for part in lowered_parts):
        return "secret-like context path component"
    return None


def _secret_text_hits(text: str) -> list[str]:
    return [name for name, pattern in SECRET_TEXT_PATTERNS if pattern.search(text)]


def read_context_file(path: Path, *, max_chars: int = DEFAULT_MAX_CONTEXT_CHARS) -> tuple[str, str]:
    block_reason = _context_path_block_reason(path)
    if block_reason:
        raise ValueError(f"refusing context file {path}: {block_reason}")
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = _secret_text_hits(text)
    if hits:
        raise ValueError(f"refusing context file {path}: secret-like content patterns: {', '.join(hits)}")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated by ai_coding_delegate.py]..."
    return str(path), text


def read_prompt_file(path: Path) -> str:
    label, text = read_context_file(path, max_chars=DEFAULT_MAX_CONTEXT_CHARS)
    if text.endswith("\n...[truncated by ai_coding_delegate.py]..."):
        raise ValueError(f"refusing prompt file {label}: prompt files may not be truncated")
    return text


def _runtime_delegate_root() -> Path:
    return repo_root() / "runtime" / "ai_delegate"


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def validate_output_path(path: Path) -> Path:
    resolved = path.resolve()
    output_root = _runtime_delegate_root().resolve()
    if not _is_relative_to(resolved, output_root):
        raise ValueError(f"refusing output path {path}: write delegate drafts under {output_root}")
    return resolved


def generate_delegate(
    prompt: str,
    *,
    contexts: Iterable[tuple[str, str]] = (),
    mode: str = "patch",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    load_files: bool = True,
) -> DelegateResult:
    _key_name, key = resolve_api_key(explicit=api_key, load_files=load_files)
    selected_model = model or os.environ.get("HFT3_CODE_DELEGATE_MODEL") or DEFAULT_MODEL
    selected_base_url = base_url or os.environ.get("HFT3_CODE_DELEGATE_BASE_URL") or DEFAULT_BASE_URL
    if not key:
        names = ", ".join(KEY_ENV_NAMES)
        return DelegateResult(
            None,
            error=f"missing coding delegate API key; set one of: {names}",
            model=selected_model,
            elapsed_s=0.0,
        )

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": _system_prompt(mode)},
            {"role": "user", "content": _format_user_message(prompt, contexts)},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _chat_completions_url(selected_base_url),
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    t0 = time.time()
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = _extract_content(body)
        if not text:
            return DelegateResult(None, error="empty delegate response", model=selected_model, elapsed_s=time.time() - t0)
        return DelegateResult(text, model=selected_model, elapsed_s=time.time() - t0)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        return DelegateResult(
            None,
            error=f"HTTP {exc.code}: {_redact(detail, key)}",
            model=selected_model,
            elapsed_s=time.time() - t0,
        )
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return DelegateResult(None, error=_redact(str(exc), key), model=selected_model, elapsed_s=time.time() - t0)


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        hits = _secret_text_hits(args.prompt)
        if hits:
            raise ValueError(f"refusing prompt text: secret-like content patterns: {', '.join(hits)}")
        return args.prompt
    if args.prompt_file:
        return read_prompt_file(Path(args.prompt_file))
    if not sys.stdin.isatty():
        prompt = sys.stdin.read()
        hits = _secret_text_hits(prompt)
        if hits:
            raise ValueError(f"refusing stdin prompt: secret-like content patterns: {', '.join(hits)}")
        return prompt
    raise SystemExit("provide --prompt, --prompt-file, or stdin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Draft code with the hft3 coding delegate endpoint.")
    parser.add_argument("--prompt", help="Task prompt. Prefer --prompt-file for anything non-trivial.")
    parser.add_argument("--prompt-file", help="Path to a prompt file.")
    parser.add_argument("--context-file", action="append", default=[], help="Relevant file excerpt to include. Repeatable.")
    parser.add_argument("--mode", choices=("patch", "plan", "review"), default="patch")
    parser.add_argument("--output", help="Write delegate output to this file instead of stdout.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--timeout-s", type=float, default=float(os.environ.get("HFT3_CODE_DELEGATE_TIMEOUT_S", DEFAULT_TIMEOUT_S)))
    parser.add_argument("--status", action="store_true", help="Print key presence status without making a network call.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        print(json.dumps(configured_status(), sort_keys=True))
        return 0

    try:
        contexts = [read_context_file(Path(path), max_chars=args.max_context_chars) for path in args.context_file]
    except ValueError as exc:
        print(f"ai_coding_delegate failed: {exc}", file=sys.stderr)
        return 2
    try:
        prompt = _read_prompt(args)
    except ValueError as exc:
        print(f"ai_coding_delegate failed: {exc}", file=sys.stderr)
        return 2

    output_path: Path | None = None
    if args.output:
        try:
            output_path = validate_output_path(Path(args.output))
        except ValueError as exc:
            print(f"ai_coding_delegate failed: {exc}", file=sys.stderr)
            return 2

    result = generate_delegate(
        prompt,
        contexts=contexts,
        mode=args.mode,
        model=args.model,
        base_url=args.base_url,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout_s=args.timeout_s,
    )
    if result.error or result.text is None:
        print(f"ai_coding_delegate failed: {result.error}", file=sys.stderr)
        return 2

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.text, encoding="utf-8")
        print(f"wrote delegate output: {output_path} model={result.model} elapsed_s={result.elapsed_s:.2f}", file=sys.stderr)
    else:
        print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
