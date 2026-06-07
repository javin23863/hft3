# Sim shadow stage (B4 fifth gate)

Authority: `chicago_cme_microstructure_a_plus_developer_handoff.pdf`, `chicago_cme_a_plus_production_implementation_prompt.pdf`, `BLUEPRINT.md` §8.

## Purpose

After a model **PASS**es Discovery → Confirmation → Holdout → Recent holdout (2025 only) on Databento MBO replay, sim shadow is the **required fifth gate** before promotion. It validates execution realism on **CHI404** external broker colo evidence — not Windows NPZ replay.

## Policy (workbench config)

Configured in `workbench/config/walk_forward.yaml`:

| Field | Value |
|-------|--------|
| `anchor_date` | 2026-03-01 |
| `cme_days` | 60 CME sessions |
| `host` | CHI404 |
| `lane` | `rithmic_trial` (paper per production PDF) |

## Status hook (v1)

Workbench records attestation only; it does **not** auto-start CHI404 capture.

Campaign `summary.json` fields:

- `sim_shadow_anchor`
- `sim_shadow_cme_days`
- `sim_shadow_status` — `pending_CHI404` until manually recorded
- `sim_shadow_required` — true when historical campaign status is PASS
- `promote_candidate` — true only when status PASS **and** `sim_shadow_status` PASS

### Record PASS/FAIL after CHI404 run

```powershell
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --campaign-id <id> --record-sim-shadow PASS
```

Writes `research_cards/workbench_runs/<id>/sim_shadow.json`.

## CHI404 runbook

1. Sync repo: `bash scripts/sync_chi404_repo.sh`
2. Ensure Rithmic trial lane per `docs/rithmic_trial/README.md`
3. Run 60 CME sessions from anchor on CHI404 (not dev workstation)
4. Attest result via `--record-sim-shadow`

See also `docs/workbench/WALK_FORWARD_CAMPAIGNS.md`.
