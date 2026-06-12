# Fixing-window study (WS-1.1) — run log

Stage: **first full-sample screening complete** (2026-06-13); OI-conditioned gate evaluation pending statistics backfill.

## Run 2026-06-13 — full backfill measure (772 expiry days, 2023-05-01 → 2026-06-11)

Data: purchased ES futures windows 14:55–15:05 CT per expiry day (275 MBO + 497 trades-schema
files, `C:\hft3-lake\options\fixing_mbo`); `measure-dbn`; output
`fixing_window_measure_dbn_full_20260613.json`. Median 17.5k trades / 104k contracts per window.
One missing date (2026-06-12, vendor availability lag).

### Screening results (ES points; screening t only, no DSR/PBO)

**Unconditional post-fixing markouts ≈ 0** (|t| < 0.5 at 30s/2m/5m) — no systematic drift.

**Fixing-window aggressor flow REVERTS after the fixing** (sign(imbalance) × markout,
+ = continuation):

| Horizon | mean | t | hit rate |
|---|---|---|---|
| +30s | −0.277 | −2.97 | 0.442 |
| +2m | −0.464 | −3.39 | 0.434 |
| +5m | **−0.633** | **−3.73** | 0.438 |

Signature consistent with fixing-replication flow pressing price into 15:00 CT and
mean-reverting after — the mechanism the study was designed to detect.

Pre-window drift (14:55 → 14:59:30) shows NO continuation/reversal signal (|t| < 1);
year-by-year drift conditioning is noise.

### CAVEATS (read before acting)

1. **Markouts are measured against the 30s fixing VWAP** — a price no trader can capture
   after observing the full window's imbalance. The imbalance is only known at 15:00:00;
   an honest tradability screen must re-base markouts on the last trade at/after 15:00:00.
   Within-window reversal already counted here inflates the apparent edge. **Do not size
   from this table.**
2. Screening t-stats over 772 days, single hypothesis family — real inference goes through
   the gauntlet with a pre-registered card.
3. The WS-1.1 GATE question (sign predictable BEFORE the window from OI/gamma) is still
   open — `oi` is null on all rows until the ES.OPT statistics batch lands.
4. Any trade near the window interacts with the Rule 575 control
   (docs/compliance/rule_575_fixing_window.md): a post-15:00:00 futures fade by a book
   NOT holding expiring options is outside the restricted window, but the compliance doc
   governs.

### Executable-entry re-screen (same day, 2026-06-13): fade NOT tradable

Fade the window imbalance, entry = first trade at/after 15:00:00 CT, exit +2m/+5m,
net of 1.3 ticks RT (n = 772):

| Exit | gross mean | net mean | t (net) | hit |
|---|---|---|---|---|
| +2m | −0.170 | −0.495 | −3.67 | 0.418 |
| +5m | −0.043 | −0.368 | −2.23 | 0.455 |

Negative every year (2023–2026). **The reversal completes inside the window** — by the
first executable post-window trade it is gone; the VWAP-based table above was
uncapturable microstructure, not edge. Post-fixing fade: CLOSED, not tradable.
This matches the adversarial research prior ("in-window LP is dead").

### Next actions (one open question remains)

- Join OI when the ES.OPT statistics batch delivers → heavy/light expiry split →
  evaluate the pre-registered WS-1.1 gate: is fixing direction predictable BEFORE
  the window from OI/gamma, executable from a pre-window position, super-tick net?
  That is the only surviving form of this study.

---

## Run 2026-06-12 (first end-to-end on real lake)

- Lake: `C:\Users\MSI\Documents\New project\data\npz` (HFT3_NPZ_ROOT), manifest = 315 entries.
- Inventory: `fixing_window_inventory_20260612T130534Z.json` — **3 of 315** entries cover 14:55–15:05 CT
  (lake is macro-event-windowed; events cluster 07:30/12:30/13:00 CT — fixing window almost never captured).
  3 manifest entries reference missing NPZ files (see inventory JSON).
- Covering files (all MES.v.0, `PROP_FLATTEN_TOPSTEP_*_MAIN`, 14:45–15:20 CT windows):
  2023-09-15 (Sep quarterly expiry Fri), 2024-09-18 (FOMC Wed), 2025-06-20 (Jun quarterly expiry Fri).
- Measure: `fixing_window_measure_20260612T130707Z.json` — per-window signed imbalance, 30s fixing VWAP,
  pre-window drift, markouts +30s/+2m/+5m. `oi` is null on all rows (no statistics backfill yet).

## Interpretation

n=3 is far below the gate threshold (n_events ≥ 15, >2 SE). No directional conclusion is drawn or
implied by these three rows. What this run establishes:

1. Harness works end-to-end on real lake data (timezone, aggressor-sign, VWAP, markout logic exercised).
2. **The owned lake cannot answer the WS-1.1 gate.** Required backfill is small and targeted:
   - futures MBO for ~10-minute windows (14:55–15:05 CT) on option-expiry days only — much cheaper
     than full event windows; scope/quote under WS-0.4 before purchase (BudgetManager caps apply).
   - daily statistics (OI by strike/expiry) — covered by Databento Standard plan per ws0-3 memo.
3. OI join (`load_expiry_oi`) stays stubbed until the statistics store exists.

## Next actions

- WS-0.4a: price the expiry-day 10-min MBO pull + statistics backfill; PO needs explicit budget uplift.
- Re-run inventory after any backfill lands; gate evaluation only at n ≥ 15 heavy-expiry days.
