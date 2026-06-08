# KNOWN GAPS — read this before claiming “done”

**Last updated:** 2026-06-08  
**Audience:** Next human or agent developer on hft3.

This file is the **single billboard** for what is missing, broken, misleading, or on the wrong machine. Lane-specific addenda still apply ([crypto](packages/crypto_lane/docs/VALIDATION_HONESTY.md), [validation charter](docs/VALIDATION_HONESTY.md)); this doc ties them together.

**Model names:** Use **canonical slugs** only (`SPREAD_BLOWOUT_RECOMPRESSION`, `DEALER_HEDGING`, …). Do **not** use deprecated `HYP_N` / `PDF_MODEL_N` in new docs, tickets, or CLI examples.

| Discovery | Command / file |
|-----------|----------------|
| All 51 workbench models (slug = CLI `--model`) | `python -m workbench list` |
| Registry + `legacy_id` (deprecated) | [`packages/features_engine/config/model_registry.yaml`](packages/features_engine/config/model_registry.yaml) |
| Macro `event_id` catalog | `python packages/data_system/src/macro_event_cli.py` |
| Crypto lane (separate from workbench 51) | `CRYPTO_H1` … `CRYPTO_H7` in [`packages/crypto_lane/config/universe.yaml`](packages/crypto_lane/config/universe.yaml) |

---

## 0. Macro event catalog (not CPI-only)

**Full catalog (45 types):** [`packages/economic_event_universe/config/event_universe.yaml`](packages/economic_event_universe/config/event_universe.yaml). **Runnable subset:** [`packages/data_system/config/events.csv`](packages/data_system/config/events.csv) (55 rows, 3 types).

| `event_type` | Count | Example `event_id` |
|--------------|-------|------------------|
| CPI | 19 | `CPI_2024_09_11_TIGHT` |
| NFP | 33 | `NFP_2024_01_05_TIGHT` |
| PROP_FLATTEN_TOPSTEP | 3 | `PROP_FLATTEN_TOPSTEP_2024_09_18_MAIN` |

```bash
python packages/data_system/src/macro_event_cli.py
```

**CLI policy (2026-06-02):** Replay, workbench `imbalance-ablation`, pipeline gates, and PDF hybrid scripts **require `--event-id`** — no repo default to a single CPI row. Optional automation only: env `HFT3_DEFAULT_EVENT_ID`.

**Still CPI-biased (not fixed):** [`apps/workbench/config/model_event_binding.yaml`](apps/workbench/config/model_event_binding.yaml) binds most workbench slugs to `CPI_TIGHT` / `NFP_TIGHT` *context families* (not one event id, but macro-heavy). [`docs/vault/RESEARCH_ENTRYPOINTS.md`](docs/vault/RESEARCH_ENTRYPOINTS.md) examples still cite CPI in places — use `macro_event_cli.py` for truth.

---

## 1. Topology — which machine owns what

| Host | Role | Data / code |
|------|------|-------------|
| **CHI404** (Chicago colo) | **Live / paper trading only** — R\|Trader, SMB bridge, order submit/ack, trial capture, latency sweeps | Repo at `/root/hft3/repo`. **Not** the default place historical Databento pulls land. See [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md). |
| **Dev workstation** | Offline research: Databento NPZ, workbench backtest, pytest, git, SSH sync | `data/npz/`, `data/replay/`, `data/equities/`, etc. under local clone (e.g. `C:\Users\...\hft3`). |

**Do not:** run live capture, Rithmic trial hot path, or paper latency that depends on workstation RTT.  
**Do not:** assume `download_all_research_data.ps1` populated CHI404 — it writes to **whatever repo root you run it from**.

Sync repo to CHI404: `bash scripts/sync_chi404_repo.sh`  
Sync **data** to colo: **not automated** — rsync/scp `data/` if colo replay is required.

---

## 2. Three research lanes (do not conflate)

| Lane | Catalog | On-disk root | In `download_all_research_data`? |
|------|---------|--------------|----------------------------------|
| **Macro futures** | [`packages/data_system/config/events.csv`](packages/data_system/config/events.csv) (55 events: CPI, NFP, prop-flatten) | `data/npz/`, `data/replay/mbp10/` | Yes |
| **Low-float equities** | [`packages/equities_lane/config/decadal_runners.yaml`](packages/equities_lane/config/decadal_runners.yaml) (16 sessions) | `data/equities/`, `data/options/equity_chains/` | Partial (equities enrich + `pull-decadal` for full lane) |
| **Crypto alpha** | [`packages/crypto_lane/config/universe.yaml`](packages/crypto_lane/config/universe.yaml) (`CRYPTO_H1`–`H7`) | `data/crypto/` | **No** |

**Imbalance integration** (`packages/features_engine/src/imbalance/`) targets macro + equities + options parity — **not crypto**.

### 2.1 Workbench slugs × equities OPRA (decadal sessions)

Low-float **sessions** live in [`decadal_runners.yaml`](packages/equities_lane/config/decadal_runners.yaml). The **workbench** has **51 slugs** (44 hypothesis + 7 PDF structural per `model_registry.yaml`). Only one slug needs per-session equity OPRA chains:

| Workbench slug | `legacy_id` (do not use) | Needs `data/options/equity_chains/<session>.ndjson`? |
|----------------|--------------------------|------------------------------------------------------|
| **`DEALER_HEDGING`** | `PDF_MODEL_5` | **Yes** — `options_chain` binding |
| All other 50 slugs | various `HYP_*` / `PDF_MODEL_*` | **No** — macro `mbo_npz` + CPI/NFP/prop context families |

**OPRA gap impact:** Only **`DEALER_HEDGING`** is blocked for the **10 symbology-failed** sessions (SPI, INDO, HKD, TOP, HOLO, CYCC, AIRE, BIRD, AMST, SNAL) and the **3 `skip_pull`** sessions (DRYS, LFIN, LBCC). **`DEALER_HEDGING` OK** on `kodk_2020`, `gme_2021`, `expr_2021`.

**Not the same lane:**

| Lane | Entry | Models / ids |
|------|--------|----------------|
| Workbench macro backtest | `python -m workbench run --model <SLUG> --event-id <from events.csv>` | 51 slugs above |
| Low-float equities research | `python -m equities_lane.pipeline` | Session ids in `decadal_runners.yaml` + [`universe.yaml`](packages/equities_lane/config/universe.yaml) features — **not** workbench slugs |
| Options parity (ES/SPX) | `options_lane/` | `data/options/` parity universe — **not** `data/options/equity_chains/` |

Default imbalance download campaign slugs (see `scripts/download_imbalance_research_data.py`): `SPREAD_BLOWOUT_RECOMPRESSION`, `END_OF_DAY_FORCED_FLATTEN_FLOW`, `DEALER_HEDGING`.

---

## 3. Data on disk — honest status (workstation audit)

Re-check anytime:

```powershell
python scripts/audit_all_research_data.py
# → runtime/data_audits/research_data_gaps.json
```

### 3.1 Macro (55 events) — generally OK

| Product | Status |
|---------|--------|
| MBO NPZ | 55/55 expected |
| MBP-10 DBN | 55/55 under `data/replay/mbp10/` |

Download: `python scripts/download_imbalance_research_data.py --all` or [`scripts/download_all_research_data.ps1`](scripts/download_all_research_data.ps1).

**CME MBO pilot basket (separate, larger store):** ~4,829 runnable NPZ + raw DBN for **7 symbols** (MES, MNQ, ES, NQ, ZN, ZB, RTY) × **720 windows** — not the full 39-instrument HOT universe. Canonical paid store: `C:\Users\MSI\Documents\New project\data` (gitignored). Wire clones via `HFT3_PAID_DATA_ROOT` in `.env` or sync:

```powershell
python -m workbench verify-data --event-id NFP_2024_01_05_TIGHT --symbol NQ.v.0
python scripts/paid_data_inventory.py --dry-run
python scripts/paid_data_inventory.py --sync
# or: .\scripts\ensure_paid_data.ps1
```

Manifest: [`docs/research/MBO_PILOT_BASKET_20260605.md`](docs/research/MBO_PILOT_BASKET_20260605.md). **211** documented gaps — **203 are permanent `no_market` holidays**; do not re-download. **30 HOT symbols** still `MBO_MISSING`; backfill via `scripts/mbo_hot_universe_backfill.py` (~$177 live estimate for 28 symbols, 2026-06-06).

```powershell
python scripts/mbo_pilot_gap_triage.py --write-manifest
python scripts/mbo_hot_universe_backfill.py --from-inventory --estimate
python scripts/mbo_hot_universe_backfill.py --batch 1 --download --max-cost-usd 104
```

#### 3.1.1 Rithmic as a fill source for missing event types

Per-event-type coverage (gap analysis, 2026-06-08): **17 event types are missing from `data/npz/` entirely** — CASH_EQUITY_OPEN, CPI, ECI, EXISTING_HOME_SALES, FACTORY_ORDERS, FED_SPEAKER, FRIDAY_CLOSE, HOUSING_STARTS, INDUSTRIAL_PRODUCTION, JOLTS, NEW_HOME_SALES, NFP, PCE, PPI, PRODUCTIVITY, PROP_REOPEN, TRADE_BALANCE. Databento-only backfill is the default; the corrected developer prompt and `scripts/rithmic_download_test.py` proof established a Rithmic-as-source path. The MBO release lane now supports that path natively.

**Status (2026-06-08):** Rithmic fill source implemented in `packages/mbo_release_lane/rithmic_source.py` and `source_priority.py`. Wired into `download_catalog_slot` with transparent fallback to Databento. CLI flag `--source {auto,rithmic_api,databento}` added to `scripts/download_mbo_release_data.py`. 41/41 new tests pass on Windows; pre-existing `test_catalog_types_exceed_events_csv_types` failure unrelated.

**What is wired:**

| Layer | Where | What |
|---|---|---|
| Source priority | `mbo_release_lane.constants.SOURCE_PRIORITY` | `("rithmic_api", "databento")` |
| Topology guard | `mbo_release_lane.rithmic_topology_guard` | Refuses on Windows (BLUEPRINT §4) |
| Connect | `mbo_release_lane.rithmic_source._connect_rithmic` | `async_rithmic.RithmicClient` with chain-aware SSL context |
| Fetch | `mbo_release_lane.rithmic_source.fetch_event_window` | History Plant `get_historical_tick_data` |
| Schema mapping | `mbo_release_lane.rithmic_source._tick_to_event` | Rithmic tick → MBO release lane normalized event |
| Hard labeling rule | `mbo_release_lane.rithmic_source._infer_data_label` | Refuses non-MBO schema; emits `ticks` / `depth/mbp` / `mbo` |
| Slot writer | `mbo_release_lane.rithmic_source.write_release_artifact` | `events.jsonl` + `release_event_path.json` + `validation.json` + `hashes.json` |
| NPZ derivation | `mbo_release_lane.rithmic_source.derive_npz_from_rithmic_release` | Bypasses DBN→NPZ; uses HftBacktest numpy writer |
| Resolver | `mbo_release_lane.source_priority.resolve_source` | Picks first applicable source; short-circuits when slot filled |
| CLI | `scripts/download_mbo_release_data.py --source` | `auto` (default) / `rithmic_api` / `databento` |

**Topology rule (unchanged from BLUEPRINT §4):** Rithmic fill source runs on **CHI404 only**. Workstation connection attempts are refused by the topology guard before any socket is opened.

**Hard labeling rule (re-stated for the MBO release lane):** if Rithmic returns schema that does not prove order-level MBO events (order_id + action + side + flags), the source refuses to write a `release_event_path.json` manifest. The slot falls through to Databento. This is identical to the rule in `scripts/rithmic_download_test.py` (proof script) and is enforced both at fetch time and at write time. The MBO release lane is not polluted with non-MBO data.

**Acceptance criteria for "Rithmic fill source is ready" — current status:**

| Criterion | Status |
|---|---|
| Rithmic connect refuses on Windows (BLUEPRINT §4) | **PASS** (41 unit tests) |
| Hard labeling rule refuses non-MBO schema | **PASS** (unit tests) |
| Source priority resolver picks Rithmic first | **PASS** (unit tests) |
| Transparent Databento fallback when Rithmic not applicable | **PASS** (unit tests) |
| Rithmic slots write `release_event_path.json` with `source_vendor=rithmic_api` | **PASS** (unit test) |
| CLI flag `--source {auto,rithmic_api,databento}` | **PASS** (script-level smoke only — not yet exercised end-to-end) |
| End-to-end CHI404 fill of a real event window | **PENDING** — requires CHI404 run with valid Rithmic Test creds (see `docs/rithmic_trial/HISTORICAL_DOWNLOAD_RUN.md`) |
| Reviewer verdict on the diff | **PENDING** |
| Audit shows rithmic_api slots in `runtime/data_audits/research_data_gaps.json` | **PENDING** (next audit run) |

**To run end-to-end on CHI404:**

```bash
# 1. confirm creds in /root/hft3/.env
grep -E "RITHMIC_" /root/hft3/.env

# 2. probe connectivity (will not request data)
python scripts/rithmic_download_test.py --probe-only

# 3. phase 1 — fill one event type (e.g. CPI for 2024-09-11)
python scripts/download_mbo_release_data.py \
  --download --source rithmic_api --only-event-type CPI \
  --start-year 2024 --end-year 2024 --limit 1

# 4. inspect the slot
cat data/mbo_release/CPI_2024_09_11_TIGHT/MES.v.0/release_event_path.json
cat data/mbo_release/CPI_2024_09_11_TIGHT/MES.v.0/validation.json
ls data/mbo_release/CPI_2024_09_11_TIGHT/MES.v.0/
```

**Do not run on workstation.** The Rithmic topology guard refuses at the orchestrator level, the source-priority layer, and the sync fetch wrapper — belt and suspenders.

### 3.2 Low-float equities (16 sessions in YAML)

| Session IDs | Ticker | Date | Equity MBO / daily / normalized | OPRA chain |
|-------------|--------|------|----------------------------------|------------|
| `drys_2016` | DRYS | 2016-11-11 | **No** — `skip_pull` (ITCH before 2018-05-01) | No |
| `lfin_2017` | LFIN | 2017-12-18 | **No** — `skip_pull` | No |
| `lbcc_2017` | LBCC | 2017-12-21 | **No** — `skip_pull` | No |
| `kodk_2020` | KODK | 2020-07-28 | On disk | **OK** (`kodk_2020.ndjson`) |
| `gme_2021` | GME | 2021-01-27 | On disk | **OK** (`gme_2021.ndjson`) |
| `expr_2021` | EXPR | 2021-01-27 | On disk | **OK** (`expr_2021.ndjson`) |
| `spi_2020` | SPI | 2020-09-23 | On disk | **Failed** — OPRA symbology |
| `indo_2022` | INDO | 2022-03-07 | On disk | **Failed** |
| `hkd_2022` | HKD | 2022-08-02 | On disk | **Failed** |
| `top_2023` | TOP | 2023-04-27 | On disk | **Failed** |
| `holo_2024` | HOLO | 2024-02-07 | On disk | **Failed** |
| `cycc_2025` | CYCC | 2025-07-15 | On disk | **Failed** |
| `aire_2025` | AIRE | 2025-07-10 | On disk | **Failed** |
| `bird_2026` | BIRD | 2026-04-15 | On disk | **Failed** |
| `amst_2026` | AMST | 2026-05-12 | On disk | **Failed** |
| `snal_2026` | SNAL | 2026-05-08 | On disk | **Failed** |

OPRA error text: `422 symbology_invalid_request` in [`data/equities/manifest/decadal_pull.json`](data/equities/manifest/decadal_pull.json).

Full lane pull: `python -m equities_lane.pipeline pull-decadal --resume --pull-options --override-hard-limit --override-operating-cap`

### 3.3 Crypto — orchestrated separately from macro download

| Item | Status |
|------|--------|
| Orchestrator | **Phase C** in `scripts/download_all_research_data.py` (`pull-gold`, `fill-l3-gaps`, `normalize`, optional blockspace) |
| Audit | `scripts/audit_all_research_data.py` reports `crypto_*` + `lanes_ready.crypto` |
| True L3 bookticker | `fill-l3-gaps --dry-run` first; B2 → Vision monthly; `--replace-synthetic` only when preflight `purge_safe` (or `--force`) |
| 2024-04+ bookticker on B2/Vision | **Gap** — CAE Contabo backfill required for true L3; use `--allow-degraded` for klines research fill only |
| `data/crypto/normalized/*.csv` | Local workstation only — run normalize after gold pull |
| Tests | Scope-green gate: `python -m pytest tests/test_crypto_lane/ -q` |
| PIT / clock | **Partial** — θ sign tested; sub-second book PIT remains non-goal |

Entry: [docs/vault/RESEARCH_ENTRYPOINTS.md](docs/vault/RESEARCH_ENTRYPOINTS.md) §1d · [research/reports/crypto_alpha_engine_extraction_report.md](research/reports/crypto_alpha_engine_extraction_report.md)

---

## 4. Code & research integrity gaps (imbalance + workbench)

| ID | Severity | Issue | Where to fix |
|----|----------|--------|--------------|
| I-01 | **P0** | Macro auction uses **test fixture** when no real file | [`packages/features_engine/src/imbalance/auction_events.py`](packages/features_engine/src/imbalance/auction_events.py) → `tests/fixtures/imbalance_auction_sample.ndjson` |
| I-02 | **P0** | Ablation = wrapper boost on hypothesis score, **not** toggling real feature slots 34–37 | [`packages/features_engine/src/imbalance/apply.py`](packages/features_engine/src/imbalance/apply.py) `wrap_hypothesis_for_ablation` |
| I-03 | **P0** | Example macro replay (`SPREAD_BLOWOUT_RECOMPRESSION` on a CPI event): **0 PnL**, **0 delta** across ablation modes | Replay + hypothesis path |
| I-04 | — | MBP-10 **intentionally excluded** from MBO-only macro lane | Use `scripts/download_mbo_release_data.py`; MBP not routed into replay |
| I-05 | P1 | C++ hot path **no** imbalance v1 slots | [docs/hft3_imbalance_runbook.md](docs/hft3_imbalance_runbook.md) |
| C-01 | ~~P1~~ | ~~CLI default CPI for `imbalance-ablation`~~ **Fixed:** `--event-id` required | [`apps/workbench/__main__.py`](apps/workbench/__main__.py) |
| C-02 | P1 | Most slugs bound to **CPI_TIGHT / NFP_TIGHT** context families only (not equities/crypto) | [`apps/workbench/config/model_event_binding.yaml`](apps/workbench/config/model_event_binding.yaml) |
| I-06 | P2 | Fast ablation = 4 modes unless `--imbalance-ablation-full` | workbench CLI |

Macro futures: **no venue auction imbalance feed** in inventory (labels only) — [docs/hft3_imbalance_inventory.md](docs/hft3_imbalance_inventory.md).

---

## 5. Pipeline bugs

| ID | Issue | Where |
|----|--------|--------|
| P-01 | `pull-decadal` fails: `name 'daily_coverage_calendar_days' is not defined` | Import from [`daily_bars_io.py`](packages/equities_lane/src/ingest/daily_bars_io.py) into [`decadal_pull.py`](packages/equities_lane/src/ingest/decadal_pull.py) |
| P-02 | `audit_all_research_data.py` `ready: true` while listing 10 `options_failed` | [`scripts/audit_all_research_data.py`](scripts/audit_all_research_data.py) — misleading flag |
| P-03 | `decadal_pull.json` can show `failed` while files exist on disk | Re-run pull after P-01 |

---

## 6. Crypto lane gaps (summary)

| ID | Issue | Doc |
|----|--------|-----|
| CR-01 | ~~Not in download orchestrator~~ **Fixed** — Phase C in download orchestrator | `scripts/download_all_research_data.py` |
| CR-02 | `data/crypto/` empty in fresh git clone | Run Phase C or `python -m crypto_lane.pipeline ingest` locally |
| CR-03 | Re-verify after each crypto_lane change | `python -m pytest tests/test_crypto_lane/ -q` |
| CR-04 | Default smokes use **fixtures** only | `packages/crypto_lane/fixtures/` — not production claims |
| CR-05 | θ sign convention | Covered by `tests/test_crypto_lane/test_theta_sign_convention.py` |
| CR-06 | Production `pit_strict` needs **live_measured** venue RTT | `calibrate-ws-rtt --live-measured --ws-rtt-ms <ms>` |
| CR-07 | Synthetic/degraded bookticker blocks production validation | Preflight before purge; `fill-l3-gaps --allow-degraded` for research-only holes; true L3 needs CAE→B2 |

Crypto hypotheses (lane-local ids, not workbench slugs): `CRYPTO_H1` … `CRYPTO_H7` — configs under `backtests/configs/crypto_hypotheses/`.

---

## 7. What “green” does NOT mean

- `audit_all_research_data.py` exit 0 → macro + equity **files** present; **not** OPRA complete, **not** crypto, **not** scientifically valid ablation.
- `download_all_research_data.ps1` → Databento workstation pull; **not** CHI404 live path.
- `workbench run` / imbalance artifacts on **one macro event** → **not** proof across the 55-row catalog.
- Imbalance commit `8de7de3` + data commit `0171ced` → **not merge-ready** without full verify + reviewer + real ablation/PnL proof.

---

## 8. Suggested fix order (next developer)

1. **P-01** — fix `daily_coverage_calendar_days` import; refresh manifest.  
2. **I-01** — remove macro auction fixture fallback (fail closed).  
3. **I-02 / I-03** — real ablation on feature slots; prove non-zero delta on ≥1 real event.  
4. **I-04** — wire MBP-10 into replay when configured.  
5. **C-02** — widen `model_event_binding.yaml` beyond CPI/NFP context families; document prop-flatten + equities.  
6. **CR-02** — crypto ingest plan (B2 + optional bitcoind), separate from Databento scripts.  
7. **D-02** — OPRA symbology failures (blocks **`DEALER_HEDGING`** only among workbench slugs): quarantine or alternate symbology — 10 sessions.  
8. **Topology doc** — optional `scripts/sync_chi404_data.sh` if colo must hold NPZ.

---

## 9. Quick commands

| Task | Command |
|------|---------|
| **List workbench model slugs** | `python -m workbench list` |
| **List macro event ids** | `python packages/data_system/src/macro_event_cli.py` |
| Full data gap audit | `python scripts/audit_all_research_data.py` |
| Paid CME pilot NPZ preflight | `python -m workbench verify-data --event-id <id> --symbol <sym>` |
| Sync paid data into clone | `python scripts/paid_data_inventory.py --sync` or `.\scripts\ensure_paid_data.ps1` |
| Imbalance-only gaps | `python scripts/audit_imbalance_data_gaps.py` |
| Download macro + equities enrich | `.\scripts\download_all_research_data.ps1` then `-ConfirmPull` after estimate |
| Equities full lane | `python -m equities_lane.pipeline pull-decadal --resume --pull-options` |
| Crypto discover | `python -m crypto_lane.pipeline discover` |
| CHI404 entrypoints | [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md) |
| Research entrypoints | [docs/vault/RESEARCH_ENTRYPOINTS.md](docs/vault/RESEARCH_ENTRYPOINTS.md) |
| Imbalance runbook | [docs/hft3_imbalance_runbook.md](docs/hft3_imbalance_runbook.md) |

---

## 10. Related files

- `runtime/data_audits/research_data_gaps.json` — machine-readable audit output  
- `runtime/data_audits/research_data_status.json` — last orchestrator run  
- `data/equities/manifest/decadal_pull.json` — per-session pull status  
- `packages/data_system/config/events.csv` — macro event catalog  

**If you close a gap:** update this file and the relevant lane addendum in the same PR.
