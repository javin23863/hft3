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

The only partial windows are:

- `FED_H41_2024_06_19_TIGHT`: missing `ES.v.0`, `ZB.v.0`, `RTY.v.0` after redownload.
- `FED_H41_2024_07_03_TIGHT`: missing `MES.v.0`, `MNQ.v.0`, `ES.v.0`, `NQ.v.0`, `RTY.v.0` after redownload.

All other missing slots are classified as `no_market_data`; see the JSON manifest for the complete list.

## Backtesting Agent Notes

- Use the tracked manifest for discovery.
- Use the runtime report for full per-window evidence when running on this MSI workstation.
- Do not treat ignored paid data as missing from the repo.
- Do not treat `no_market_data` or partial rows as successful runnable coverage.

## Canonical store and clone wiring

Paid NPZ/DBN files live outside git. The canonical store on this workstation is:

- `C:\Users\MSI\Documents\New project\data`

Set in `.env` (see `.env.example`):

```
HFT3_PAID_DATA_ROOT=C:\Users\MSI\Documents\New project\data
```

The backtest resolver searches `{repo}/data/npz` first, then `{HFT3_PAID_DATA_ROOT}/npz`. Either sync into the clone or use a directory junction.

### Before backtest checklist

1. Preflight one event/symbol:

   ```powershell
   python -m workbench verify-data --event-id NFP_2024_01_05_TIGHT --symbol NQ.v.0
   ```

2. Inventory and sync missing files into the active clone:

   ```powershell
   python scripts/paid_data_inventory.py --dry-run
   python scripts/paid_data_inventory.py --sync
   # or:
   .\scripts\ensure_paid_data.ps1
   ```

3. Optional junction (no duplicate disk):

   ```powershell
   mklink /J "C:\Users\MSI\Documents\GitHub\hft3\data\npz" "C:\Users\MSI\Documents\New project\data\npz"
   ```

### Scope note

This pilot basket covers **7 symbols** (MES, MNQ, ES, NQ, ZN, ZB, RTY) × **720 event windows** — not the full 39-instrument HOT universe. The manifest documents **211** known gaps (`partial_windows`, `no_market_windows`).

## HOT universe expansion (30 MBO_MISSING symbols)

Window catalog (717 rows): [`packages/data_system/config/mbo_pilot_window_catalog.json`](../packages/data_system/config/mbo_pilot_window_catalog.json)

| Item | Value |
|------|------:|
| Pending slots (28 symbols, skip no-market) | ~19,952 |
| Live Databento estimate (2026-06-06) | **~$177** |
| Linear extrapolation (pilot $145.74 / 7 sym) | ~$625 for 30 sym |
| `VX.v.0` | Symbology failure — excluded from estimate |

### Pilot 211 gaps — permanent vs retry

Run triage (no paid spend on holidays):

```powershell
python scripts/mbo_pilot_gap_triage.py --write-manifest --reconvert-partials
```

- **203 slots** — `no_market_windows` (Christmas, New Year, Saturday Beige Book, etc.) → **permanent**
- **8 slots** — `partial_windows` → try `--reconvert-partials` from existing raw DBN first

### HOT backfill commands

Estimate before download:

```powershell
python scripts/mbo_hot_universe_backfill.py --from-inventory --estimate
```

Six batched downloads (~$104 cap each):

```powershell
.\scripts\mbo_hot_universe_run_batches.ps1
# or per batch:
python scripts/mbo_hot_universe_backfill.py --batch 1 --download --max-cost-usd 104
```

After each batch: `python packages/hfc3/audits/mbo_inventory.py`
