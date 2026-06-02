# KNOWN GAPS — read this before claiming “done”

**Last updated:** 2026-06-02  
**Audience:** Next human or agent developer on hft3.

This file is the **single billboard** for what is missing, broken, misleading, or on the wrong machine. Lane-specific addenda still apply ([crypto](packages/crypto_lane/docs/VALIDATION_HONESTY.md), [validation charter](docs/VALIDATION_HONESTY.md)); this doc ties them together.

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

### 3.3 Crypto — not loaded by imbalance / macro download

| Item | Status |
|------|--------|
| `data/crypto/normalized/*.csv` | **Empty** (only `.gitkeep` in repo) |
| Production ingest | Requires `python -m crypto_lane.pipeline` (`pull-bronze`, `pull-mempool`, `normalize`) + B2/BTC env |
| Tests | Charter example: **not scope-green** (`tests/test_crypto_lane/`) |
| PIT / clock | **Partial** — see [packages/crypto_lane/docs/PIT_AVAILABILITY_BOUNDARY.md](packages/crypto_lane/docs/PIT_AVAILABILITY_BOUNDARY.md) |

Entry: [docs/vault/RESEARCH_ENTRYPOINTS.md](docs/vault/RESEARCH_ENTRYPOINTS.md) §1d · [research/reports/crypto_alpha_engine_extraction_report.md](research/reports/crypto_alpha_engine_extraction_report.md)

---

## 4. Code & research integrity gaps (imbalance + workbench)

| ID | Severity | Issue | Where to fix |
|----|----------|--------|--------------|
| I-01 | **P0** | Macro auction uses **test fixture** when no real file | [`packages/features_engine/src/imbalance/auction_events.py`](packages/features_engine/src/imbalance/auction_events.py) → `tests/fixtures/imbalance_auction_sample.ndjson` |
| I-02 | **P0** | Ablation = wrapper boost on HYP score, **not** toggling real feature slots 34–37 | [`packages/features_engine/src/imbalance/apply.py`](packages/features_engine/src/imbalance/apply.py) `wrap_hypothesis_for_ablation` |
| I-03 | **P0** | CPI real NPZ replay: **0 PnL**, **0 delta** across ablation modes | Replay + hypothesis path |
| I-04 | P1 | MBP-10 on disk but **not wired** into main replay/workbench | [`mbp_replay.py`](packages/features_engine/src/imbalance/mbp_replay.py) only |
| I-05 | P1 | C++ hot path **no** imbalance v1 slots | [docs/hft3_imbalance_runbook.md](docs/hft3_imbalance_runbook.md) |
| C-01 | P1 | CLI default event = `CPI_2024_09_11_TIGHT` for `imbalance-ablation` | [`apps/workbench/__main__.py`](apps/workbench/__main__.py) |
| C-02 | P1 | Most models bound to **CPI_TIGHT / NFP_TIGHT** only in YAML | [`apps/workbench/config/model_event_binding.yaml`](apps/workbench/config/model_event_binding.yaml) |
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
| CR-01 | Not in download orchestrator | This file §3.3 |
| CR-02 | `data/crypto/` empty in git clone | Run crypto_lane ingest |
| CR-03 | `tests/test_crypto_lane/` not green | [docs/VALIDATION_HONESTY.md](docs/VALIDATION_HONESTY.md) |
| CR-04 | Default smokes use **fixtures** only | `packages/crypto_lane/fixtures/` |
| CR-05 | θ convention audit **open** | [packages/crypto_lane/docs/VALIDATION_HONESTY.md](packages/crypto_lane/docs/VALIDATION_HONESTY.md) |
| CR-06 | Venue RTT often **synthetic**, not measured | `venue_profiles.json` / calibrate-ws-rtt |

Hypotheses: `CRYPTO_H1` … `CRYPTO_H7` — configs under `backtests/configs/crypto_hypotheses/`.

---

## 7. What “green” does NOT mean

- `audit_all_research_data.py` exit 0 → macro + equity **files** present; **not** OPRA complete, **not** crypto, **not** scientifically valid ablation.
- `download_all_research_data.ps1` → Databento workstation pull; **not** CHI404 live path.
- `workbench run` / imbalance artifacts on **one CPI event** → **not** proof across catalog.
- Imbalance commit `8de7de3` + data commit `0171ced` → **not merge-ready** without full verify + reviewer + real ablation/PnL proof.

---

## 8. Suggested fix order (next developer)

1. **P-01** — fix `daily_coverage_calendar_days` import; refresh manifest.  
2. **I-01** — remove macro auction fixture fallback (fail closed).  
3. **I-02 / I-03** — real ablation on feature slots; prove non-zero delta on ≥1 real event.  
4. **I-04** — wire MBP-10 into replay when configured.  
5. **C-01 / C-02** — de-CPI-default CLI and bindings; document NFP + prop-flatten + equities.  
6. **CR-02** — crypto ingest plan (B2 + optional bitcoind), separate from Databento scripts.  
7. **D-02** — OPRA symbology failures: quarantine in catalog or alternate symbology — 10 sessions.  
8. **Topology doc** — optional `scripts/sync_chi404_data.sh` if colo must hold NPZ.

---

## 9. Quick commands

| Task | Command |
|------|---------|
| Full data gap audit | `python scripts/audit_all_research_data.py` |
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
