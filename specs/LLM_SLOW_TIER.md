# LLM_SLOW_TIER.md — Slow-Tier LLM Lane Contract

Version: 2026-06-12. Authority: delegated-growing-metcalfe.md.

---

## 1. Scope and Authority

This spec governs the slow-tier LLM lane only. It does NOT modify the engine,
gauntlet, campaign milestones, or the CONTINUOUS_CME roadmap. The CONTINUOUS_CME
milestone table, strategy classes, and do-not-build list remain binding.

**AlphaGeometry pattern (binding):** The model proposes; deterministic code
disposes. Model output NEVER becomes truth until verification passes. In every
conflict between model text and tape statistics, tape statistics win.

Flows covered by this spec:
- F1 — nightly session labeler (P1, implemented)
- F2 — morning brief (P2, contract only)
- F3 — hypothesis intake (P3, contract only)

---

## 2. Model Policy

**2.1 Local only.** All flows in this spec MUST use a locally-running ollama
instance. Cloud-routed models (e.g., `gemma4:31b-cloud`, any model whose
inference is performed outside the workstation) are FORBIDDEN for these flows.
Rationale: privacy, cost, determinism, latency tolerance.

**2.2 Primary model.** The default model is:

```
hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL
```

**2.3 Environment override.** The model name MAY be overridden via the
environment variable `HFT3_SLOW_TIER_MODEL`. CLI `--model` further overrides
the environment variable. Resolution order:

1. CLI `--model` argument (highest priority)
2. `HFT3_SLOW_TIER_MODEL` environment variable
3. `config/slow_tier.yaml` `model` field
4. Compiled default `hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL`

**2.4 ollama host.** Default `http://127.0.0.1:11434`. Overridable via config.
The existing `packages/data_layer/llm/ollama_client.py` client MUST be used;
no new HTTP client shall be written.

**2.5 format_json.** All structured-output calls MUST use `format_json=True`.

---

## 3. Flow F1 — Nightly Session Labeler

### 3.1 Purpose

Classify each trade date into a market-regime label for use in CC8
regime-stratified calibration and the capturability-decay study.

### 3.2 Inputs

| Input | Source | Notes |
|---|---|---|
| Per-symbol capture manifests | Local manifest dir (default `artifacts/research_cards/slow_tier/manifests/{date}/`) | JSON files; one per symbol |
| Tape-stat digest | Computed deterministically from manifests | See §3.4 |
| Release-calendar hits | `packages/data_system/config/release_calendars/*.csv` mapped via `event_universe.yaml` | Which scheduled events fell on the date |
| GDELT day summary | `GdeltWorldEventConnector.fetch()` | Top 20 records by num_mentions; skipped in offline mode |

**Capture manifest schema (produced on CHI404):**
```json
{
  "symbol": "string",
  "exchange": "string",
  "trade_date": "YYYY-MM-DD",
  "records": "integer",
  "trades": "integer",
  "quotes": "integer",
  "first_ts_exch_ns": "integer",
  "last_ts_exch_ns": "integer",
  "max_queue_gap_flag_count": "integer",
  "md_drops_total": "integer",
  "reconnects": "integer",
  "file_bytes": "integer",
  "updated_wall_ns": "integer",
  "unknown_symbol_drops": "integer"
}
```

### 3.3 Digest Fields

The digest is computed deterministically by `src/digest.py`. Fields:

| Field | Type | Description |
|---|---|---|
| `trade_date` | str | YYYY-MM-DD |
| `symbols` | list[str] | Symbols present in manifest dir |
| `total_records` | int | Sum across symbols |
| `total_trades` | int | Sum across symbols |
| `total_quotes` | int | Sum across symbols |
| `total_gap_flags` | int | Sum of max_queue_gap_flag_count |
| `total_drops` | int | Sum of md_drops_total |
| `total_reconnects` | int | Sum of reconnects |
| `per_symbol` | dict | Per-symbol records/trades/quotes/gap_flags/drops/reconnects |
| `trade_count_zscores` | dict | Per-symbol z-score vs trailing 20-session baseline |
| `insufficient_baseline` | list[str] | Symbols with fewer than 5 prior sessions |
| `n_symbols` | int | Number of symbols captured |

### 3.4 Digest Z-Score Computation

For each symbol, compute z = (trades_today - mean_prior) / std_prior over up to
20 prior sessions found in the same manifest tree. When fewer than 5 prior
sessions exist for a symbol, z MUST be 0.0 and the symbol MUST be added to
`insufficient_baseline`. std_prior of 0 is treated as z = 0.0 (no variation).

### 3.5 Label Enum

The model MUST choose exactly one of:

| Label | Meaning |
|---|---|
| `war_escalation` | Active military escalation or geopolitical shock driving tape |
| `macro_event` | Scheduled macro release (FOMC, NFP, CPI, etc.) dominates session |
| `calm` | Low-volatility, no notable macro or geopolitical driver |
| `mixed` | Multiple conflicting drivers; no single regime |
| `conflict_review` | Model is uncertain or verifier forced reclassification |

### 3.6 Model Output Schema (JSON)

```json
{
  "label": "one of war_escalation|macro_event|calm|mixed|conflict_review",
  "drivers": ["string", "..."],
  "confidence": 0.0
}
```

Constraints:
- `label` MUST be one of the five enum values
- `drivers` MUST be a list of strings, maximum 5 elements
- `confidence` MUST be a float in [0.0, 1.0]

### 3.7 Output Record

Appended to `artifacts/research_cards/slow_tier/session_labels.jsonl`:

```json
{
  "trade_date": "YYYY-MM-DD",
  "label": "string",
  "drivers": ["string"],
  "confidence": 0.85,
  "verifier_verdict": "accept|conflict_review",
  "verifier_reasons": ["string"],
  "model": "string",
  "prompt_template_hash": "first 12 hex chars of sha256(system_template)",
  "digest": { "...digest fields..." },
  "sources_summary": {
    "calendar_hits": ["string"],
    "gdelt_top_events": [{ "actor": "string", "event_code": "string", "goldstein_scale": 0.0, "avg_tone": 0.0, "num_mentions": 0 }],
    "gdelt_error": null,
    "offline": false
  },
  "generated_utc": "ISO-8601",
  "advisory": true
}
```

**Labels are ADVISORY** until the eval gate (§7) passes. Consumers MUST check
the `advisory` field before treating labels as CC8-consumable.

---

## 4. Flow F2 — Morning Brief (P2)

> **Status: P2 — contract only. Not implemented.**

### 4.1 Purpose

Produce a human-readable one-page markdown summary before the trading day.
No consumer dependencies; human-read only.

### 4.2 Inputs

- GDELT overnight records (last 18 hours)
- Today's release-calendar entries
- Yesterday's session label from `session_labels.jsonl`
- Capture-health summary (which symbols captured, gap counts)

### 4.3 Output

`artifacts/research_cards/slow_tier/morning_brief_{date}.md` — markdown, four
fixed sections: "Overnight Drivers", "Today's Scheduled Events",
"Capture Health", "Open Defects".

### 4.4 Verifier

Schema check only: output must be non-empty markdown with all four section
headers present.

---

## 5. Flow F3 — Hypothesis Intake (P3)

> **Status: P3 — contract only. Not implemented.**

### 5.1 Purpose

Template-fill `TestableHypothesis` candidates from a fixed taxonomy file.
Grounded in vault doctrine; no free generation.

### 5.2 Hard Quota

**Maximum 5 candidates per calendar week.** Rationale: every candidate inflates
n_trials in the DSR/Holm family correction. Spam weakens the entire family's
statistical power. The quota is enforced by code; attempts above the limit MUST
be rejected with an explanatory error.

### 5.3 Candidate Handling

Candidates land in `research_inputs/{research_id}/`. They are quarantined by
`detect_intake_quarantine()` from `packages/research_pipeline/intake_schema.py`.
Promotion into `features_engine/src/hypotheses/registry.py` MUST remain a
human action through the normal gauntlet. **NO auto-registration ever.**

### 5.4 Verifier

`intake_schema` validation + `detect_intake_quarantine()` + quota check.

---

## 6. Verifier Rules

The verifier (`src/verify.py`) applies deterministic code rules AFTER model
output is received. Its verdict is binding.

### 6.1 Schema Check

The model output MUST pass all constraints in §3.6. Any violation (missing
field, wrong type, label outside enum, drivers list > 5 elements, confidence
outside [0.0, 1.0]) MUST produce `verdict = "conflict_review"` with the schema
error as a reason. The raw model output is logged regardless.

### 6.2 Tape Cross-Check: Calm Override

If `label == "calm"` AND any of the following are true:
- Any symbol's trade-count z-score > `z_stress` threshold (default 3.0)
- `total_gap_flags > 0`
- `total_reconnects > 2`

Then `verdict` MUST be overridden to `"conflict_review"` with reason
`"tape_contradicts_calm"`. Tape outranks model text.

### 6.3 Tape Cross-Check: Stress-But-Quiet Override

If `label` is `"war_escalation"` or `"macro_event"` AND all of the following
are true:
- All symbol z-scores < `z_quiet` threshold (default 1.0)
- No calendar hits for the date
- GDELT records list is empty (or offline)

Then `verdict` MUST be overridden to `"conflict_review"` with reason
`"tape_does_not_support_stress_label"`.

### 6.4 Accept Path

If neither override fires, `verdict = "accept"` and `final_label` equals the
model's label.

### 6.5 Conflict Review Final Label

When verdict is `"conflict_review"`, `final_label` is always `"conflict_review"`
regardless of the model's original label.

### 6.6 Tape Supremacy

In every conflict between model-proposed text and tape statistics,
**tape statistics win.** This is unconditional.

---

## 7. Eval Gate

### 7.1 Gate Criteria

Labels graduate from ADVISORY to CC8-consumable only when **both**:
- Golden agreement rate >= 90% (`agreement >= 0.90`)
- Verifier conflict rate < 10% (`conflict_rate < 0.10`)

### 7.2 Golden Set

Hand-labeled sessions in `artifacts/research_cards/slow_tier/golden/`.
Each file: `{date}.json` with schema `{"date": "YYYY-MM-DD", "label": "string", "notes": "string"}`.

Every verifier rejection and human correction is logged to
`artifacts/research_cards/slow_tier/golden/corrections.jsonl` — future LoRA
training data if the base model proves too inaccurate (fine-tuning is OUT OF
SCOPE for P1–P3).

### 7.3 Eval Artifact

`runtime/validation/slow_tier_eval.json` — produced by `eval` subcommand:

```json
{
  "generated_utc": "ISO-8601",
  "model": "string",
  "n_golden": 0,
  "agreement": 0.0,
  "conflict_rate": 0.0,
  "gate_pass": false,
  "per_date": [
    {
      "date": "YYYY-MM-DD",
      "golden_label": "string",
      "model_label": "string",
      "final_label": "string",
      "verifier_verdict": "string",
      "agree": false
    }
  ]
}
```

### 7.4 Advisory Flag Logic

`gate_pass = false` → all newly written records carry `"advisory": true`.
`gate_pass = true` → newly written records carry `"advisory": false` and are
CC8-consumable. The eval runner MUST re-check the gate on every run; the flag
is NOT cached.

---

## 8. Do-NOT-Build

The following are explicitly forbidden in P1, P2, and P3:

| Item | Reason |
|---|---|
| Runtime / hot-path integration | Model throughput is 4–5 orders of magnitude below MD rates |
| Order influence | Model output NEVER reaches the order path |
| Raw tape reading | Digest only; model never sees raw MBO/MBP bytes |
| Auto-registration of hypotheses | Human-only promotion through the gauntlet |
| Fine-tuning | Insufficient eval data; explicitly deferred |
| Paid news feeds | Free sources only (GDELT, release calendars) |
| Cloud-routed models | Local/private/free only; see §2 |
| features_engine imports | F3 is a later phase; P1 MUST NOT import features_engine |

---

## 9. Artifacts Table

| Path | Content | Producer | Cadence |
|---|---|---|---|
| `artifacts/research_cards/slow_tier/session_labels.jsonl` | One record per labeled trade date | `nightly-label` CLI | Nightly (post-market) |
| `artifacts/research_cards/slow_tier/manifests/{date}/{symbol}.manifest.json` | Raw capture manifests (scp'd from CHI404) | Manual / scheduled scp | Per capture session |
| `artifacts/research_cards/slow_tier/golden/{date}.json` | Hand-labeled golden sessions | Human | One-time seed + ongoing corrections |
| `artifacts/research_cards/slow_tier/golden/corrections.jsonl` | Verifier rejections + human corrections | `verify.py` + human | As corrections occur |
| `artifacts/research_cards/slow_tier/morning_brief_{date}.md` | Morning brief markdown (P2) | `morning-brief` CLI | Nightly (P2) |
| `runtime/validation/slow_tier_eval.json` | Eval gate result | `eval` CLI | On demand |
| `apps/llm_slow_tier/config/slow_tier.yaml` | Model + threshold + path config | Static config | At deploy / change |
