"""C++ production stack verify gate (approach 3 prerequisite — not NPZ replay)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_layer.stack_check_contract import REQUIRED_STACK_CHECKS
from workbench.src.sim.cpp_binary import resolve_cpp_binary

_PROCESS_CACHE: Dict[str, "CppStackVerifyResult"] = {}


@dataclass
class CppStackVerifyResult:
    """Outcome of hft_research_sim --verify-stack (link + runtime self-test)."""

    stack_verified: bool = False
    reason: str = "C++ stack verify not run"
    checks: Dict[str, bool] = field(default_factory=dict)
    subprocess_ran: bool = False


class CppStackVerifyHarness:
    """
    Invokes hft_research_sim --verify-stack: MBO queue roundtrip, features,
    decision evaluate, risk precheck. Does not replay NPZ history.
    """

    def __init__(self, engine_binary: Optional[Path] = None, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else None
        self.engine_binary = engine_binary

    def binary_path(self) -> Optional[Path]:
        if self.engine_binary is not None and self.engine_binary.is_file():
            return self.engine_binary
        if self.repo_root is not None:
            return resolve_cpp_binary(self.repo_root, "hft_research_sim")
        return None

    def binary_exists(self) -> bool:
        return self.binary_path() is not None

    def verify(self) -> CppStackVerifyResult:
        binary = self.binary_path()
        if binary is None:
            return CppStackVerifyResult(
                reason="Build hft_research_sim: cmake -B build && cmake --build build",
            )

        cmd = [str(binary), "--verify-stack"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120.0,
                cwd=str(self.repo_root) if self.repo_root else None,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CppStackVerifyResult(reason=str(exc), subprocess_ran=True)

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            return CppStackVerifyResult(
                reason=err or f"exit {proc.returncode}",
                subprocess_ran=True,
            )

        meta: Dict[str, Any] = {}
        parsed_json = False
        for line in (proc.stdout or "").splitlines():
            if line.startswith("HFT_RESEARCH_SIM_JSON:"):
                try:
                    meta = json.loads(line.split(":", 1)[1].strip())
                    parsed_json = True
                except json.JSONDecodeError:
                    pass

        checks_raw = meta.get("checks") if isinstance(meta.get("checks"), dict) else {}
        checks = {str(k): bool(v) for k, v in checks_raw.items()}
        missing = REQUIRED_STACK_CHECKS - set(checks)
        stack_verified = bool(
            parsed_json
            and meta.get("stack_verified")
            and not missing
            and all(checks[k] for k in REQUIRED_STACK_CHECKS)
        )
        if not parsed_json:
            reason = "missing HFT_RESEARCH_SIM_JSON line"
        elif missing:
            reason = f"stack checks missing keys: {', '.join(sorted(missing))}"
        elif not stack_verified:
            failed = [k for k in REQUIRED_STACK_CHECKS if not checks.get(k)]
            reason = f"stack checks failed: {', '.join(failed)}"
        else:
            reason = "C++ stack verified (queue, features, decision, risk)"

        return CppStackVerifyResult(
            stack_verified=stack_verified,
            reason=reason,
            checks=checks,
            subprocess_ran=True,
        )


def stack_verify_policy() -> str:
    """HFT3_CPP_STACK_VERIFY: off | once (default) | always."""
    return os.environ.get("HFT3_CPP_STACK_VERIFY", "once").strip().lower()


def get_cached_stack_verify(
    repo_root: Path,
    harness: Optional[CppStackVerifyHarness] = None,
) -> CppStackVerifyResult:
    """
    Run stack verify at most once per process (policy ``once``).
    ``off`` skips subprocess; ``always`` re-runs every call.
    """
    key = str(Path(repo_root).resolve())
    policy = stack_verify_policy()

    if policy in ("0", "false", "off", "skip", "never"):
        return CppStackVerifyResult(reason="disabled by HFT3_CPP_STACK_VERIFY")

    if policy in ("once", "auto", "") and key in _PROCESS_CACHE:
        return _PROCESS_CACHE[key]

    h = harness or CppStackVerifyHarness(repo_root=repo_root)
    result = h.verify()

    if policy in ("once", "auto", ""):
        _PROCESS_CACHE[key] = result
    return result


def reset_stack_verify_cache_for_tests() -> None:
    _PROCESS_CACHE.clear()
