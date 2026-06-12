"""CLI entry point for the LLM slow-tier lane.

Subcommands:
  nightly-label   F1: classify a trade date into a market-regime label.
  eval            Run the eval harness against the golden set.
  morning-brief   F2: generate morning brief (P2, not yet implemented).
  hypothesis-intake  F3: template-fill hypothesis candidates (P3, not yet implemented).

All structured output goes to disk artifacts; progress / errors are logged as
JSON lines to stderr so they can be captured by scheduled-task wrappers.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _setup_logging() -> None:
    """Configure JSON-line logging to stderr."""

    class _JsonLineFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            doc = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                doc["exc"] = self.formatException(record.exc_info)
            return json.dumps(doc)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonLineFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _cmd_nightly_label(args: argparse.Namespace) -> int:
    """F1: load manifests, build digest, call the labeler, verify, write output."""
    import logging

    log = logging.getLogger("llm_slow_tier.nightly_label")

    from .src.config import load_config
    from .src.digest import build_digest
    from .src.sources import load_calendar_hits, load_gdelt_summary
    from .src.labeler import run_labeler
    from .src.verify import verify_label_result
    from .src.writer import append_session_label

    cfg = load_config()

    trade_date: str = args.date
    manifest_dir: Optional[str] = getattr(args, "manifest_dir", None)
    offline: bool = getattr(args, "offline", False) or cfg.offline
    model_override: Optional[str] = getattr(args, "model", None)

    # Resolve manifest directory
    if manifest_dir:
        manifest_path = Path(manifest_dir)
    else:
        manifest_path = Path(cfg.artifact_root) / cfg.manifest_subdir / trade_date

    log.info("Starting nightly-label", extra={})
    log.info(json.dumps({"trade_date": trade_date, "manifest_dir": str(manifest_path), "offline": offline}))

    # Build deterministic digest
    try:
        digest = build_digest(trade_date, manifest_path)
    except Exception as exc:
        log.error(f"digest build failed: {exc}")
        return 1

    log.info(json.dumps({"digest_symbols": digest.n_symbols, "total_trades": digest.total_trades}))

    # Load sources
    calendar_hits = load_calendar_hits(trade_date, cfg)
    log.info(json.dumps({"calendar_hits": calendar_hits}))

    if offline:
        gdelt_records: list = []
        gdelt_error: Optional[str] = None
        log.info("offline mode — skipping GDELT")
    else:
        gdelt_records, gdelt_error = load_gdelt_summary(trade_date, cfg)
        if gdelt_error:
            log.warning(f"GDELT error: {gdelt_error}")

    sources_summary = {
        "calendar_hits": calendar_hits,
        "gdelt_top_events": gdelt_records,
        "gdelt_error": gdelt_error,
        "offline": offline,
    }

    # Resolve model
    import os
    model = (
        model_override
        or os.environ.get("HFT3_SLOW_TIER_MODEL")
        or cfg.model
    )

    # Run labeler
    label_result = run_labeler(digest, sources_summary, model=model, cfg=cfg)
    if label_result is None:
        log.error("labeler returned no result")
        return 1

    log.info(json.dumps({"raw_label": label_result.label, "confidence": label_result.confidence}))

    # Verify
    verifier_result = verify_label_result(label_result, digest, sources_summary, cfg)
    log.info(json.dumps({"verdict": verifier_result.verdict, "final_label": verifier_result.final_label}))

    # Write output
    try:
        out_path = append_session_label(
            trade_date=trade_date,
            label_result=label_result,
            verifier_result=verifier_result,
            digest=digest,
            sources_summary=sources_summary,
            model=model,
            cfg=cfg,
        )
    except Exception as exc:
        log.error(f"write failed: {exc}")
        return 1

    log.info(json.dumps({"written": str(out_path)}))
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    """Run the eval harness: compare against golden set, write slow_tier_eval.json."""
    import logging

    log = logging.getLogger("llm_slow_tier.eval")

    from .src.config import load_config
    from .src.golden import run_eval

    cfg = load_config()
    golden_dir_override: Optional[str] = getattr(args, "golden_dir", None)

    golden_dir = Path(golden_dir_override) if golden_dir_override else Path(cfg.artifact_root) / cfg.golden_subdir

    log.info(json.dumps({"golden_dir": str(golden_dir)}))

    try:
        result = run_eval(golden_dir, cfg)
    except Exception as exc:
        log.error(f"eval failed: {exc}")
        return 1

    log.info(json.dumps({
        "n_golden": result["n_golden"],
        "agreement": result["agreement"],
        "conflict_rate": result["conflict_rate"],
        "gate_pass": result["gate_pass"],
    }))
    return 0


def _cmd_morning_brief(_args: argparse.Namespace) -> int:
    """F2 morning brief — not implemented in P1."""
    print(
        "morning-brief: not implemented in P1 (scheduled for P2)",
        file=sys.stderr,
    )
    return 2


def _cmd_hypothesis_intake(_args: argparse.Namespace) -> int:
    """F3 hypothesis intake — not implemented in P1."""
    print(
        "hypothesis-intake: not implemented in P1 (scheduled for P3)",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="llm-slow-tier",
        description="LLM slow-tier lane: nightly session labeler, eval, and future flows.",
    )
    sub = parser.add_subparsers(dest="command")

    # nightly-label
    nl = sub.add_parser("nightly-label", help="F1: classify a trade date (YYYY-MM-DD)")
    nl.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="Trade date to label")
    nl.add_argument(
        "--manifest-dir",
        default=None,
        metavar="PATH",
        help="Override manifest directory (default: config artifact_root/manifest_subdir/date)",
    )
    nl.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Skip GDELT network call (use local data only)",
    )
    nl.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Override ollama model name (highest priority over env and config)",
    )

    # eval
    ev = sub.add_parser("eval", help="Run eval harness against golden set")
    ev.add_argument(
        "--golden-dir",
        default=None,
        metavar="PATH",
        help="Override golden directory (default: config artifact_root/golden_subdir)",
    )

    # morning-brief (P2 stub)
    sub.add_parser("morning-brief", help="F2: morning brief (P2 — not yet implemented)")

    # hypothesis-intake (P3 stub)
    sub.add_parser("hypothesis-intake", help="F3: hypothesis intake (P3 — not yet implemented)")

    args = parser.parse_args(argv)

    if args.command == "nightly-label":
        return _cmd_nightly_label(args)
    if args.command == "eval":
        return _cmd_eval(args)
    if args.command == "morning-brief":
        return _cmd_morning_brief(args)
    if args.command == "hypothesis-intake":
        return _cmd_hypothesis_intake(args)

    parser.print_help()
    return 2
