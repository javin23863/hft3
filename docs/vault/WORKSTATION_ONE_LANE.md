# Workstation research lane (one lane)

Single trusted path for macro MBO replay and walk-forward campaigns on the dev workstation.

## Lane

```
events.csv → Databento GLBX.MDP3 MBO → data/npz/ → workbench / run_event_replay.py
```

**Not this lane:** Rithmic trial NPZ under `data/replay/hftbacktest/rithmic_trial/` (CHI404 quarantine only).

## Keys

Copy `.env.example` to repo-root `.env` and set `DATABENTO_API_KEY` (required for event-window MBO download).

| Key | Role |
|-----|------|
| `DATABENTO_API_KEY` | Event-window MBO download → `data/npz/` |
| `CHI404_*` | SSH / latency sync (optional for offline replay) |
| `RITHMIC_*` | CHI404 paper/live only — not trusted macro replay |

Backfill loads `.env` via `workbench/scripts/backfill_catalog.py`.

## Research identifiers (not secrets)

| Key | Source | Example |
|-----|--------|---------|
| `event_id` | `data_system/config/events.csv` | `CPI_2024_09_11_TIGHT` |
| `symbol` | Campaign `--symbol` + events.csv | `MES.v.0` |
| NPZ path | `data/npz/{symbol}_{event_id}_mbo.npz` | `MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz` |
| Latency | CHI404 probe | `runtime/latency_reports/latency_summary.json` |

Never use calendar folder dates (`YYYY-MM-DD`) as research keys.

## Catalog vs runnable

For `HYP_5` / `MES.v.0`, the walk-forward catalog lists **52** macro events (Discovery 2018–2020 through Recent holdout 2025). Runnable count = events with NPZ on disk (primary or documented fallback).

Check:

```bash
python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MES.v.0 --dry-run
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run
```

Download missing (~$1–2 total for full HYP_5 catalog):

```bash
python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MES.v.0 --download-missing --max-cost-usd 25
```

## ES fallback (pre-MES listing)

Authority: `chicago_cme_microstructure_a_plus_developer_handoff.pdf` §8 — primary symbols `MES.v.0, MNQ.v.0, ES.v.0, NQ.v.0` for 2018–present core research.

MES micro futures listed **May 2019**. For Discovery windows before MES exists, backfill downloads **`ES.v.0`** NPZ instead. Files are named `ES.v.0_{event_id}_mbo.npz`; manifest records the resolved symbol. Separate listing-date effects from event alpha (handoff §8.1).

Events that use ES fallback when `--symbol MES.v.0`:

- `NFP_2018_01_05_TIGHT`, `CPI_2018_01_11_TIGHT`, `NFP_2018_02_02_TIGHT`, `NFP_2018_03_09_TIGHT`
- `NFP_2019_01_04_TIGHT`, `NFP_2019_02_01_TIGHT`, `CPI_2019_02_13_TIGHT`, `NFP_2019_03_08_TIGHT`

## Related

- [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) — canonical script order
- [CPI_2024_09_11_TIGHT_BASELINE.md](CPI_2024_09_11_TIGHT_BASELINE.md) — baseline metrics
- [../workbench/WALK_FORWARD_CAMPAIGNS.md](../workbench/WALK_FORWARD_CAMPAIGNS.md) — B4 periods
- Cross-asset L3 research: `hfc3/` package + `runtime/audits/hfc3_l3_cross_asset_repo_audit.md`
