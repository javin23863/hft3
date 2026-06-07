#!/usr/bin/env bash
# Proof: historical MBO replay emits OrderIntent via HftBacktestSimulatedExchangeAdapter.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO"

export EXECUTION_MODE=REPLAY
GIT_HASH="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
NPZ="${REPLAY_PROOF_NPZ:-}"
AUDIT_DIR="$REPO/runtime/replay_audits"
REPORT="$REPO/runtime/reports/replay_execution_parity_proof.md"
mkdir -p "$REPO/runtime/reports" "$AUDIT_DIR"

if [[ ! -f "$NPZ" ]]; then
  echo "Building minimal fixture NPZ..."
  python3 - <<PY
from pathlib import Path
from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
build_minimal_mbo_npz(Path("$REPO/tests/fixtures/replay_minimal_mbo.npz"))
PY
  NPZ="$REPO/tests/fixtures/replay_minimal_mbo.npz"
fi

python3 - <<PY
import json
import os
from pathlib import Path

os.environ["EXECUTION_MODE"] = "REPLAY"
from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig

npz = Path("$NPZ")
cfg = ReplaySessionConfig(
    npz_path=str(npz),
    latency_ms=1.0,
    queue_model="LogProbQueueModel2",
    max_steps=1000,
    audit_dir=Path("$AUDIT_DIR"),
)
result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
print(json.dumps(result, indent=2))
if result.get("order_intent_count", 0) < 1:
    raise SystemExit("FAIL: order_intent_count < 1")
summary = result["order_lifecycle_summary"]
if summary.get("broker_call_count", -1) != 0:
    raise SystemExit("FAIL: broker_call_count != 0")
if summary.get("rithmic_order_call_count", -1) != 0:
    raise SystemExit("FAIL: rithmic_order_call_count != 0")

report = Path("$REPORT")
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "\n".join(
        [
            "# Replay execution parity proof",
            "",
            f"- git_commit: {os.environ.get('GIT_HASH', '$GIT_HASH')}",
            f"- npz: {npz}",
            f"- latency_ms: {cfg.latency_ms}",
            f"- queue_model: {cfg.queue_model}",
            f"- order_intent_count: {result.get('order_intent_count')}",
            f"- accepted_count: {summary.get('accepted_count')}",
            f"- filled_count: {summary.get('filled_count')}",
            f"- broker_call_count: {summary.get('broker_call_count')}",
            f"- rithmic_order_call_count: {summary.get('rithmic_order_call_count')}",
            f"- lifecycle: {result.get('lifecycle_path')}",
            f"- summary: {result.get('summary_path')}",
            "",
            "## Command",
            "",
            "```bash",
            "bash scripts/run_replay_execution_parity_proof.sh",
            "```",
        ]
    )
    + "\n",
    encoding="utf-8",
)
print(f"Wrote {report}")
PY

echo "OK: replay execution parity proof complete"
