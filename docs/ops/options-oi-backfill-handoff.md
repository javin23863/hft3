# Options OI backfill — handoff (2026-06-13)

Status snapshot for the WS-1.1 OI-conditioned fixing-gate work. Branch
`options/cockpit-integration` (off `main` @ `9db382e`).

## TL;DR

- The original `ES.OPT` statistics+definitions buy was the **wrong product**: `ES.OPT`
  resolves to **quarterly, AM/SOQ-settled** E-mini S&P options only. The 15:00 CT fixing
  flow is driven by **PM-settled** weeklies/EOM/dailies under **separate** CME parent roots.
- Owner approved a re-scope (~$121): statistics + definitions for the **25 PM roots**
  (`EW`, `EW1`–`EW4`, `E1A`–`E5D`; A=Mon B=Tue C=Wed D=Thu) over 2023-05-01 → 2026-06-13.
- OI **plumbing is built, tested, committed** (`701bbdb`). The **gate evaluation** runs
  after the backfill completes.

## Commits (this branch, ahead of origin/main)

- `701bbdb` — OI decode + definition map + no-lookahead `load_expiry_oi` (65 tests + T0 green).
- (this commit) — fail-fast patch to the backfill pull + ledger reconcile script + this handoff.

## Code (committed)

| File | What |
|---|---|
| `packages/options_lane/studies/oi_decode.py` | statistics DBN → OI arrays. OI value from `quantity` (price is INT64_MAX sentinel), date from `ts_ref`, `update_action`==NEW, **last-wins dedup** of CME's twice-per-session OI dissemination. `load_oi_from_dbn` / `load_oi_dir`. |
| `packages/options_lane/studies/definition_map.py` | cumulative `instrument_id`→`OptionMeta` (expiry/strike/right/underlying/root). Latest-wins across **per-day definition deltas** (files are NOT full snapshots); spreads/sentinel-strike excluded. |
| `packages/options_lane/studies/fixing_window_study.py` | `load_expiry_oi` rewritten — **filtration F_t safe**: OI of options expiring on D, as of the latest `ts_ref` **strictly < D** (prior session). Plus `load_expiry_oi_split` (C/P) and `classify_heavy_light`. `OIStore` container. Back-compat: returns None when lake absent. |
| `scripts/pull_pm_options_backfill.py` | per-root monthly statistics+definition pull for the 25 PM roots. Idempotent (existing files skip). **Fail-fast manifest lock (6s)** + accept-on-file-present (see Contention below). |
| `scripts/reconcile_pm_backfill_ledger.py` | scans the PM-option lake dirs vs the manifest and appends ledger rows for any chunk recorded on disk but not in the ledger (priced via `get_cost`). Run AFTER the pull. `--dry-run` supported. |

Verified vs real EW3 2023-05 fixture: 609 instruments expire 2023-05-19; expiring-series
OI = 1,074,322 as of T-1 (2023-05-18, last-wins), 0 on the expiry day.

## Running on THIS workstation (offline research box)

- **Backfill** (`scripts/pull_pm_options_backfill.py both`) — writes
  `C:\hft3-lake\options\statistics\<root>\` and `C:\hft3-lake\options\definitions\pm\<root>\`.
  Progress log: **`C:\hft3-lake\options\pm_backfill_log.txt`** (`tail` it). ~$121, ~120 GB,
  several hours. Idempotent: safe to re-run; it resumes.
- **Pause snapshot (2026-06-14)** — fill intentionally paused with no live
  `pull_pm_options_backfill.py` process. Last active file:
  `C:\hft3-lake\options\statistics\E1A\E1A_stats_2024-07.dbn.zst`, 14,313,902 bytes,
  DBN EOF validation OK (`785060` `StatMsg` records). Resume by rerunning the same script;
  after `END`, run ledger reconcile. Durable note:
  `docs/vault/PM_OPTIONS_BACKFILL_PAUSE_2026_06_14.md`.
- Claude background task IDs (this session only): pull = `bmgw7fpl5`, log monitor = `bxp83mlx2`.
  (Ephemeral — a new operator just watches the log file + checks the manifest.)

### How to check status / finish (portable)

```
# env
$env:PYTHONPATH="C:\Users\MSI\.claude\shims;<wt>;<wt>\packages"
$env:HFT3_MANIFEST_PATH="C:\hft3-lake\manifest.parquet"; $env:HFT3_NPZ_ROOT="C:\hft3-lake\npz"

tail -f C:\hft3-lake\options\pm_backfill_log.txt      # watch; ends with "END done=... unrecorded=N"
python scripts\pull_pm_options_backfill.py both       # resume if interrupted (idempotent)
python scripts\reconcile_pm_backfill_ledger.py --dry-run   # preview unrecorded chunks
python scripts\reconcile_pm_backfill_ledger.py        # record them -> ledger whole
```

## Contention note (why fail-fast + reconcile)

A concurrent 6-shard `download_event_tape.py` job shares `C:\hft3-lake\manifest.parquet`.
The lock is exclusive (atomic, **no clobber risk**) but under the tape job's write-bursts a
single writer can starve. The pull therefore uses a 6s lock timeout and, when a chunk
downloads but the ledger append times out, logs `UNRECORDED` and moves on — the **data lands**;
`reconcile_pm_backfill_ledger.py` writes the missing ledger rows from disk afterward. The
manifest is the spend source of truth; per-chunk `get_cost` estimates were verified exact
(statistics has no MBO-style snapshot blowout).

## Sunk / do-not-chase

- The stuck `ES.OPT` statistics batch `GLBX-20260612-5WLAWUBM3Q` ($22.93) is **billed at
  submission, uncancellable** (no API; portal cancel does not refund). Quarterly-only —
  not usable for the fixing gate. Ignore it.

## Next (after backfill END)

1. Run `reconcile_pm_backfill_ledger.py` → ledger whole.
2. Widen `OIStore` to **all roots** (currently single-root `EW3` default; `instrument_id` is
   globally unique so merge the def map + OI across roots) and **vectorize** the O(n) date
   scans for the full multi-million-row dataset.
3. Run the heavy/light gate: split expiry days by pre-window OI (and call/put / gamma proxy),
   test whether fixing direction is predictable BEFORE the window, executable from a
   pre-window position, net of cost. This is the only surviving form of WS-1.1 (post-fixing
   fade already tested CLOSED on the futures side).

Background: vault `decisions/` + `sessions/2026-06-13 …`; memory `project-hft3-options-lane`,
`feedback-databento-cost-lessons`.
