# Runtime contract — backend and UI artifact parity

Single schema for where the backend writes and the Streamlit UI reads. Override root with `HFT3_ARTIFACTS_ROOT` (default: `artifacts/`).

## Roots

| Root | Env var | Purpose |
|------|---------|---------|
| `artifacts/` | `HFT3_ARTIFACTS_ROOT` | Research outputs (campaigns, replays, KG) |
| `runtime/` | — | Machine ephemeral (validation, audits, latency) |

Legacy shim: `research_cards/` → same as `artifacts/` when present.

## Workbench campaign tree

```
artifacts/workbench_runs/{campaign_id}/
  campaign.json
  summary.json          # campaign rollup (canonical)
  diagnostics.json      # campaign rollup mirror for UI Report tab
  status.json
  wfc/wfc_summary.json
  periods/{period}/events/{event_id}/
    diagnostics.json
    report.md
    research_card.json
    trades.parquet
    config.yaml
    signal_diagnostics.json
    after_action_packet.json
    after_action_symbolic.json
    after_action_response.json   # canonical LLM output (schema_aar_response_v1)
    after_action_report.md       # derived from narrative_md
    after_action_annotations.json
    after_action_meta.json
```

## Launch modes

| Mode | Flags | After-action LLM |
|------|-------|------------------|
| Quick trial | default UI quick run | Skipped |
| Full audit | `--full-sweep`, `trial_mode=False` | Runs via `packet_runner` + GPT-5.5 XHIGH (`HFT3_AAR_LLM_MODEL`) |

## Event replay

```
artifacts/event_replays/{event_id}/
  result.json
  report.md
```

## Certification (runtime)

```
runtime/validation/
  certification_registry.json
  fast_gate_report.json
  backtester_certification_scorecard.json
  champion_promotion_gate_report.json
```

UI **System** tab reads these paths.

## JSON schemas

See `runtime/schemas/` for machine-readable shapes (`schema_v1.json`, `schema_aar_response_v1.json`, pipeline request/response, `campaign_summary.json`, `event_diagnostics.json`).

## Shared path helper

`workbench.src.artifacts.paths` (or `apps/workbench/src/artifacts/paths.py` after layout move):

- `artifact_root()`
- `campaign_dir(campaign_id)`
- `campaign_event_dir(campaign_id, period, event_id)`
- `runtime_validation_dir()`

Backend writers and UI readers must import from this module only.
