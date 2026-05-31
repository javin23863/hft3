"""T4 champion promotion gate logic."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hft3.validation.certification_registry import git_sha, load_registry, repo_root
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.fast_gate_report import load_fast_gate_report

REPORT_JSON_REL = Path("runtime/validation/champion_promotion_gate_report.json")
REPORT_MD_REL = Path("runtime/validation/champion_promotion_gate_report.md")


@dataclass
class PromotionGateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    event_id: str = ""
    symbol: str = ""
    latency_ms: float = 0.0
    queue_model: str = ""
    certification_status: str = ""
    certification_commit: str = ""
    current_commit: str = ""
    stale: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_t0_fast_gate(root: Path) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "tests/backtester_validation/fast", "-q"]
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stdout or "") + (proc.stderr or "")


def _scorecard_covers(
    registry: Any,
    *,
    event_id: str,
    symbol: str,
    latency_ms: float,
    queue_model: str,
) -> tuple[bool, str]:
    if registry.latest_certification_status != "GREEN":
        return False, f"registry_status={registry.latest_certification_status}"
    if symbol and registry.covered_symbols and symbol not in registry.covered_symbols:
        return False, f"symbol {symbol} not in covered_symbols"
    if event_id:
        prefix = event_id.split("_")[0].lower()
        covered = [e.lower() for e in registry.covered_event_types]
        if covered and prefix not in covered and "macro" not in covered and "synthetic" not in covered:
            return False, f"event_type for {event_id} not covered"
    if latency_ms and registry.covered_latency_bands:
        bands = [float(x) for x in registry.covered_latency_bands]
        if not any(abs(latency_ms - band) < 0.01 for band in bands):
            nearest = min(bands, key=lambda b: abs(latency_ms - b))
            return False, f"latency_ms {latency_ms} not in covered_latency_bands (nearest={nearest})"
    if queue_model and registry.covered_queue_models and queue_model not in registry.covered_queue_models:
        return False, f"queue_model {queue_model} not in covered_queue_models"
    return True, ""


def evaluate_promotion_gate(
    *,
    event_id: str = "",
    symbol: str = "",
    latency_ms: float = 0.0,
    queue_model: str = "",
    campaign_dir: Path | None = None,
    skip_t0_rerun: bool = False,
    root: Path | None = None,
) -> PromotionGateResult:
    root = root or repo_root()
    registry = load_registry(root)
    staleness = assess_staleness(root, registry=registry)
    failures: list[str] = []

    if registry.latest_certification_status != "GREEN":
        failures.append(f"latest_certification_status={registry.latest_certification_status} (need GREEN)")

    if not staleness.certification_is_current:
        failures.append(f"certification_stale: {staleness.stale_reason}")

    scorecard_path = root / "runtime/validation/backtester_certification_scorecard.json"
    if not scorecard_path.is_file():
        failures.append("missing backtester_certification_scorecard.json")
    else:
        ok, reason = _scorecard_covers(
            registry,
            event_id=event_id,
            symbol=symbol,
            latency_ms=latency_ms,
            queue_model=queue_model,
        )
        if not ok:
            failures.append(reason)

    if skip_t0_rerun:
        fg = load_fast_gate_report(root)
        if not fg or not fg.get("passed"):
            failures.append("fast_gate_report missing or not passed")
        elif fg.get("git_sha") and fg.get("git_sha") != git_sha(root):
            failures.append("fast_gate_report stale (git_sha mismatch)")
    else:
        t0_ok, t0_out = _run_t0_fast_gate(root)
        if not t0_ok:
            failures.append(f"T0 fast gate failed on promotion check: {t0_out[-500:]}")

    if campaign_dir is not None:
        summary_path = campaign_dir / "summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                stamp = summary.get("certification_stamp") or {}
                if not stamp.get("promotion_eligible"):
                    failures.append(
                        f"campaign stamp not promotion_eligible: {stamp.get('promotion_label')}"
                    )
            except json.JSONDecodeError:
                failures.append("campaign summary.json invalid JSON")

    return PromotionGateResult(
        passed=len(failures) == 0,
        failures=failures,
        event_id=event_id,
        symbol=symbol,
        latency_ms=latency_ms,
        queue_model=queue_model,
        certification_status=registry.latest_certification_status,
        certification_commit=registry.latest_certification_commit,
        current_commit=git_sha(root),
        stale=not staleness.certification_is_current,
    )


def write_promotion_gate_report(result: PromotionGateResult, root: Path | None = None) -> tuple[Path, Path]:
    root = root or repo_root()
    payload = {
        "tier": "T4",
        "passed": result.passed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    json_path = root / REPORT_JSON_REL
    md_path = root / REPORT_MD_REL
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_lines = [
        "# Champion Promotion Gate Report",
        "",
        f"- **Passed:** {result.passed}",
        f"- **Event ID:** {result.event_id}",
        f"- **Symbol:** {result.symbol}",
        f"- **Certification status:** {result.certification_status}",
        f"- **Stale:** {result.stale}",
        "",
    ]
    if result.failures:
        md_lines.append("## Failures")
        for f in result.failures:
            md_lines.append(f"- {f}")
        md_lines.append("")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="T4 champion promotion gate")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument("--queue-model", default="")
    parser.add_argument("--campaign-dir", default="")
    parser.add_argument("--skip-t0-rerun", action="store_true")
    args = parser.parse_args(argv)
    campaign = Path(args.campaign_dir) if args.campaign_dir else None
    result = evaluate_promotion_gate(
        event_id=args.event_id,
        symbol=args.symbol,
        latency_ms=args.latency_ms,
        queue_model=args.queue_model,
        campaign_dir=campaign,
        skip_t0_rerun=args.skip_t0_rerun,
    )
    write_promotion_gate_report(result)
    if result.failures:
        for f in result.failures:
            print(f"FAIL: {f}", file=sys.stderr)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
