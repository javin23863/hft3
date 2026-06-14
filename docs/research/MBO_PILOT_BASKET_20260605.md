# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# MBO Pilot Basket 20260605

This is the tracked locator for the local paid Databento MBO pilot basket used for backtesting. The paid data itself is local-only and intentionally ignored by git.

## Local Paths

- Manifest: `packages/data_system/config/mbo_pilot_basket_20260605_manifest.json`
- Runtime report: `runtime/data_downloads/mbo_pilot_basket_20260605.json`
- Raw DBN data: `data/raw/databento_mbo/mbo_pilot_basket_20260605/`
- Runnable NPZ data: `data/npz/`

## Request

- Dataset: `GLBX.MDP3`
- Schema: `mbo`
- Symbol type: `continuous`
- Range: `2023-06-05T00:00:00+00:00` to `2026-06-05T08:30:00+00:00`
- Planned cost: `$145.736351`

## Verified Coverage

- Raw DBN windows: `720`
- Raw DBN bytes: `8058506157`
- Expected event-symbol NPZ slots: `5040`
- Present runnable NPZ slots: `4829`
- Missing or unavailable slots: `211`
- Invalid NPZ slots: `0`
- No-market windows: `29`
- No-market slots: `203`
- Partial-window missing slots: `8`

## Event-Type Coverage

| Event type | Windows | Present NPZ slots | Missing slots |
|---|---:|---:|---:|
| CPI | 14 | 98 | 0 |
| EIA_CRUDE | 157 | 1085 | 14 |
| EIA_NATGAS | 157 | 1085 | 14 |
| FED_BEIGE_BOOK | 24 | 98 | 70 |
| FED_H41 | 157 | 1070 | 29 |
| FOMC_PRESS | 24 | 126 | 42 |
| NFP | 15 | 105 | 0 |
| PROP_FLATTEN_TOPSTEP | 3 | 21 | 0 |
| TREASURY_AUCTION | 12 | 42 | 42 |
| UNEMPLOYMENT_CLAIMS | 157 | 1099 | 0 |

## Remaining Gaps

The run finished as `completed_with_gaps`.

The `211` missing or unavailable slots split into `203` full no-market slots and
`8` partial-window symbol absences. The full no-market slots are `29` windows
multiplied by the `7` canonical CME symbols in the pilot basket.

The no-market windows by event type are:

| Event type | No-market windows | No-market slots |
|---|---:|---:|
| EIA_CRUDE | 2 | 14 |
| EIA_NATGAS | 2 | 14 |
| FED_BEIGE_BOOK | 10 | 70 |
| FED_H41 | 3 | 21 |
| FOMC_PRESS | 6 | 42 |
| TREASURY_AUCTION | 6 | 42 |

The only partial windows are:

- `FED_H41_2024_06_19_TIGHT`: missing `ES.v.0`, `ZB.v.0`, `RTY.v.0` after redownload.
- `FED_H41_2024_07_03_TIGHT`: missing `MES.v.0`, `MNQ.v.0`, `ES.v.0`, `NQ.v.0`, `RTY.v.0` after redownload.

All other missing slots are classified as `no_market_data`; see the JSON manifest for the complete list. These gaps are classified evidence, not runnable coverage.

## Backtesting Agent Notes

- Use the tracked manifest for discovery.
- Use the runtime report for full per-window evidence when running on this MSI workstation.
- Do not treat ignored paid data as missing from the repo.
- Do not treat `no_market_data` or partial rows as successful runnable coverage.
