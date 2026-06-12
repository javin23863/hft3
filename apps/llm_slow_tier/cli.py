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


def _cmd_morning_brief(args: argparse.Namespace) -> int:
    """F2: generate the morning brief for a trade date."""
    import logging
    import os

    log = logging.getLogger("llm_slow_tier.morning_brief")

    from .src.config import load_config
    from .src.sources import load_calendar_hits, load_gdelt_summary
    from .src.brief import generate_brief, write_brief, verify_brief

    cfg = load_config()

    # Resolve date (default: today UTC)
    trade_date: str = getattr(args, "date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    offline: bool = getattr(args, "offline", False) or cfg.offline
    model_override: Optional[str] = getattr(args, "model", None)

    model = (
        model_override
        or os.environ.get("HFT3_SLOW_TIER_MODEL")
        or cfg.model
    )

    log.info(json.dumps({"cmd": "morning-brief", "trade_date": trade_date, "model": model, "offline": offline}))

    # Load sources
    calendar_hits = load_calendar_hits(trade_date, cfg)

    if offline:
        gdelt_records: list = []
        gdelt_error: Optional[str] = "offline mode"
    else:
        gdelt_records, gdelt_error = load_gdelt_summary(trade_date, cfg)
        if gdelt_error:
            log.warning("GDELT error: %s", gdelt_error)

    # Manifest dir for capture health
    from pathlib import Path
    manifest_dir = Path(cfg.artifact_root) / cfg.manifest_subdir / trade_date
    labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

    # Generate brief
    try:
        brief_text, model_used = generate_brief(
            trade_date=trade_date,
            gdelt_records=gdelt_records,
            gdelt_error=gdelt_error,
            calendar_hits=calendar_hits,
            manifest_dir=manifest_dir,
            labels_path=labels_path,
            model=model,
            cfg=cfg,
        )
    except Exception as exc:
        log.error("brief generation failed: %s", exc)
        return 1

    # Verify headings
    missing = verify_brief(brief_text)
    if missing:
        log.error("brief missing section headings: %s", missing)
        return 1

    # Write artifacts
    try:
        brief_path, log_path = write_brief(
            brief_text=brief_text,
            trade_date=trade_date,
            artifact_root=cfg.artifact_root,
            model=model,
            gdelt_n=len(gdelt_records),
            calendar_n=len(calendar_hits),
            model_used=model_used,
        )
    except Exception as exc:
        log.error("write failed: %s", exc)
        return 1

    log.info(json.dumps({"written": str(brief_path), "model_used": model_used}))
    print(f"Morning brief written: {brief_path}", file=sys.stderr)
    return 0


def _cmd_hypothesis_intake(args: argparse.Namespace) -> int:
    """F3: template-fill a hypothesis candidate from the taxonomy."""
    import logging
    import os

    log = logging.getLogger("llm_slow_tier.hypothesis_intake")

    from .src.config import load_config
    from .src.intake import run_intake

    cfg = load_config()

    family: str = getattr(args, "family", "")
    model_override: Optional[str] = getattr(args, "model", None)
    trade_date: str = getattr(args, "date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    model = (
        model_override
        or os.environ.get("HFT3_SLOW_TIER_MODEL")
        or cfg.model
    )

    log.info(json.dumps({"cmd": "hypothesis-intake", "family": family, "trade_date": trade_date, "model": model}))

    exit_code, message = run_intake(
        family_name=family,
        trade_date=trade_date,
        model=model,
        cfg=cfg,
    )

    if exit_code == 3:
        print(message, file=sys.stderr)
    elif exit_code == 4:
        print(message, file=sys.stderr)
    elif exit_code != 0:
        log.error(message)
        print(f"Error: {message}", file=sys.stderr)
    else:
        print(message)
        log.info(json.dumps({"intake_status": "success", "family": family}))

    return exit_code


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

    # morning-brief (F2)
    mb = sub.add_parser("morning-brief", help="F2: generate morning brief for a trade date")
    mb.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Trade date (default: today UTC)",
    )
    mb.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Skip GDELT network call",
    )
    mb.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Override ollama model name",
    )

    # hypothesis-intake (F3)
    hi = sub.add_parser(
        "hypothesis-intake",
        help="F3: template-fill a hypothesis candidate (requires intake.enabled=true in config)",
    )
    hi.add_argument(
        "--family",
        required=True,
        metavar="FAMILY",
        help=(
            "Taxonomy family name: queue_position_mm, second_wave_ofi_momentum, "
            "cross_product_lead_lag, toxicity_provision_overlay, "
            "hawkes_cascade_triggers, gex_dealer_gamma_overlay"
        ),
    )
    hi.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Trade date for hypothesis_id prefix (default: today UTC)",
    )
    hi.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Override ollama model name",
    )

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
