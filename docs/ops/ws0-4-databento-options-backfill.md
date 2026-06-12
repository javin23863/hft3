# Databento GLBX.MDP3 options-on-futures: pricing model, exact cost quotes (a)-(d), parent-symbology gotchas

*Generated 2026-06-12 by WS-0 research workflow (web sources; verify before contract decisions).*

## Findings

- PRICING MODEL: pure $/GB metered on UNCOMPRESSED size in Databento Binary Encoding (DBN); GB = 2^30 bytes (verified: 24,598,524,096 bytes billed as 22.909 GB). Rates vary per schema per dataset, identical for streaming and batch-download modes. CSV/JSON conversion free (billed on smaller encoding). Batch: billed once, unlimited re-downloads for 30 days; streaming: billed per outbound byte, unsent data on dropped connections not charged. $125 free credits per team, 6-month expiry [2][5].
- GLBX.MDP3 UNIT PRICES (historical AND historical-streaming, $/GB), pulled 2026-06-12 from metadata.list_unit_prices: mbo $1.80, mbp-1 $1.80, mbp-10 $0.50, tbbo $28, trades $28, bbo-1s/1m $18, ohlcv-1s/1m $70, ohlcv-1h/1d $190, definition $1.70, statistics $1.00, status $4.00. Live metered (grandfathered only) = 1.2x: mbo $2.16 [5][8].
- CME SUBSCRIPTION PLANS (launched 2025-04-16; live usage-based pricing discontinued same day): Standard $179/mo monthly (live data, no license fees; historical included: 16+ yrs of L0 = OHLCV+Definitions+STATISTICS+Status, 12 mo of L1 = trades/tbbo/bbo/mbp-1, 1 mo of L2/L3); Plus $1,500/mo annual contract (16+ yrs L1, 1 mo L2/L3); Unlimited $4,000/mo annual (16+ yrs ALL schemas incl. MBO) [1][7].
- DATASET RANGE: GLBX.MDP3 starts 2010-06-06, but mbo schema starts 2017-05-21 (all other schemas 2010) — full 2018-2026 span available for every schema requested [8][9].
- PARENT SYMBOLOGY GOTCHA (critical): docs state 'ES.OPT refers to all QUARTERLY E-mini S&P 500 options and option spreads' — weeklies are SEPARATE parents keyed on the definition `asset` field. ES complex = 26 parents: ES.OPT, EW.OPT (EOM), EW1-EW4.OPT (Fri), E1A-E5A.OPT (Mon), E1B-E5B (Tue), E1C-E5C (Wed), E1D-E5D (Thu). NQ complex = 27 parents: NQ.OPT, QNE.OPT (EOM), QN1-QN5.OPT (Fri), Q1A-Q5D.OPT (Mon-Thu). Verified empirically 2024-01-11: ES.OPT resolves to only 3,350 instruments (1,928 ES-root outrights + ~1,420 UD spreads) while EW3.OPT alone = 7,303, EW.OPT = 4,655; full ES complex ~43k instrument-days [3][8].
- PARENT SYMBOLOGY API SUPPORT: stype_in=parent accepted by timeseries.get_range AND batch.submit_job AND metadata.get_cost/get_billable_size; symbology.resolve is free but parent->raw_symbol is NOT a supported combination (422 error) — only parent->instrument_id; to get raw symbols/strikes pull the definition schema. Parent symbols also include UDS (user-defined spreads); excluding them requires raw-symbol requests [3][4][8].
- (a) STATISTICS 2018-01-01 to 2026-06-11 (exact metadata.get_cost quotes, 2026-06-12): literal ES.OPT $22.91 (22.9 GB) + NQ.OPT $21.01 = $43.92; FULL complexes: ES 26-parent $201.35 + NQ 27-parent $169.00 = $370.35 (~370 GB @ $1.00/GB). Statistics schema cannot be filtered to settlement+OI at request time — you pay for all stat types (settle, OI, highs/lows, cleared volume, fixings). CHEAPER PATH: statistics is L0 -> 16+ yrs included in one $179 Standard month [1][6][8].
- (b) DEFINITION 2018-01-01 to 2026-06-11: literal ES.OPT $5.56 + NQ.OPT $3.20 = $8.76; FULL complexes: ES $45.33 + NQ $21.65 = $66.98 (~39 GB @ $1.70/GB). Definition is also L0 -> included in Standard $179/mo. (a)+(b) usage-based full-complex total $437.33 vs one Standard month $179 [1][6][8].
- (c) MBO EVENT WINDOWS, 10-min, FULL chain (all parents incl. UDS): ES complex per window $5.45-$9.21 (CPI 2024-01-11 $8.96 = ~5.0 GB; FOMC 2024-03-20 $9.21; FOMC 2026-01-28 $7.79; NFP 2025-06-06 $5.45; mean ~$7.9); NQ complex $3.43-$3.96 (mean ~$3.7). At 70 events/yr, 2023-01-01 to 2026-06-11 ~241 windows: ES ~$1,900 + NQ ~$890 = ~$2,800; through 2026-12-31 (280 windows) ~$3,235. Quarterly-only ES.OPT windows are 10-20x cheaper ($0.42-$0.87) but miss all weeklies [6][8].
- (c) STRIKE-BANDED ALTERNATIVE: 146 raw symbols (0DTE Thu E2DF4 + 1DTE Fri EW2F4 ATM±80pt @5pt steps, C+P, plus front quarterly ESH4 ±80 @25pt) for the same CPI 2024-01-11 window = $0.397 vs $8.96 full chain = 4.4% of cost. Extrapolated: ES banded ~$96 for 241 windows; ES+NQ banded ~$140-150 total vs ~$2,800 full chain (95% saving). Requires per-event raw-symbol lists built from the definition schema (parent->raw resolve unsupported) [6][8].
- (d) FUTURES-ONLY MBO, daily 14:55-15:05 CT (19:55Z CDT / 20:55Z CST): ES.FUT per window $0.034 (2024-01-16), $0.079 (2024-06-12), $0.120 (2023-03-15), $0.041 (2025-07-08), mean ~$0.068; MES.FUT $0.018-$0.061, mean ~$0.036. ~867 trading days 2023-01-01 to 2026-06-11: ES ~$59 + MES ~$31 = ~$90; through 2026-12-31 (~1,008 days) ~$105. ES.FUT parent includes calendar spreads [6][8].
- QUOTE TOOLING: metadata.get_cost and get_billable_size are free preflight endpoints; the public pricing widget itself calls them via api.databento.com with anonymous 'preview:' Basic auth (embedded in databento.com site JS) — all numbers above are live API quotes from 2026-06-12, not estimates. ES-complex quotes for 2023 dates intermittently 504 (server-side quote timeout on large parent lists over older data) [6][8].

## Open questions

- Does Standard $179/mo 'included' L0 history permit unlimited bulk batch downloads of full 26/27-parent complexes in a single month (pricing page says 'unlimited downloads, streaming, and API calls') — confirm plan ToS before relying on the $179-beats-$437 arbitrage for (a)+(b).
- 2023 full-complex MBO window quotes 504 twice (CPI 2023-02-14); 2023 event-window costs are extrapolated from 2024-2026 samples — re-quote with split parent lists or a real API key.
- NQ strike-banded ratio assumed equal to ES's 4.4%; measure once with an NQ raw-symbol band.
- The 70-events/yr sample mean uses tier-1 releases (CPI/FOMC/NFP); quieter events will quote lower, so $2,800 full-chain / $150 banded are conservative-high.
- Preview-auth quotes power the public widget and matched billable_size exactly, but final invoicing applies per-account credits/plan — re-verify totals with the team's real DATABENTO_API_KEY before purchase (key not present in hft3 .env; C:/QuantX/keys.env not inspected — access denied as out of scope).
- Whether UDS (user-defined spread) books are wanted in event-window MBO; they are bundled into every .OPT parent and inflate window size materially (1,420 of 3,350 ES.OPT instruments on 2024-01-11).

## Recommended actions

- Buy ONE month of CME Standard ($179) to bulk-pull statistics + definition for both full complexes 2018-2026 (usage-based equivalent $437.33), then drop to usage-based; keep MBO event windows on pay-as-you-go.
- Hard-code the full parent lists in databento_client.py: 26 ES parents (ES,EW,EW1-4,E1A-E5A,E1B-E5B,E1C-E5C,E1D-E5D + .OPT) and 27 NQ parents (NQ,QNE,QN1-5,Q1A-Q5A..Q1D-Q5D + .OPT); never use bare ES.OPT/NQ.OPT for chain-wide pulls — it silently drops all weeklies (the entire 0-45DTE short-dated complex except quarterlies).
- Build a definition-driven strike-bander (pull definition first, select 0-2DTE families ATM±band, request MBO by raw_symbol): cuts event-window MBO from ~$2,800 to ~$150 for 2023-2026 at 70 events/yr; parent->raw_symbol resolve is unsupported so banding MUST go through definitions.
- Pull futures fixing-window MBO (ES.FUT+MES.FUT, 14:55-15:05 CT daily 2023-2026) in full — only ~$90-105 total; mind DST: window = 19:55-20:05Z (CDT) / 20:55-21:05Z (CST).
- Wire metadata.get_cost as a mandatory free preflight before every batch.submit_job (matches existing $25-cap pattern in apps/workbench/src/data/catalog_backfill.py); use batch mode for windows (re-downloadable free for 30 days) and store zstd DBN (billing is on uncompressed size; disk footprint will be several-fold smaller).
- Remember MBO history floor: 2017-05-21 — any pre-2018 backtest extension must use mbp-10 ($0.50/GB, back to 2010-06-06) instead.

## Sources

- [1] https://databento.com/pricing (rendered 2026-06-10; CME plan matrix: Usage-based / Standard $179 / Plus $1,500 / Unlimited $4,000; L0-L3 inclusion table)
- [2] https://databento.com/docs/faqs/usage-pricing-and-data-credits (billing per uncompressed DBN byte; streaming vs batch billing; $125 credits)
- [3] https://databento.com/docs/standards-and-conventions/symbology (parent = 'smart symbology'; 'ES.OPT refers to all quarterly E-mini S&P 500 options and option spreads'; roots from definition asset field)
- [4] https://databento.com/docs/api-reference-historical/basics/symbology (parent supported on timeseries.get_range and batch.submit_job; symbology.resolve free)
- [5] https://databento.com/docs/api-reference-historical/metadata/metadata-list-unit-prices + live response for GLBX.MDP3, 2026-06-12
- [6] https://databento.com/docs/api-reference-historical/metadata/metadata-get-cost + live quotes via api.databento.com/v0/metadata.get_cost, 2026-06-12
- [7] https://databento.com/blog/introducing-new-cme-pricing-plans (2025-04-16: Standard launch, live usage-based discontinued, grandfathering)
- [8] api.databento.com/v0/ live calls 2026-06-12: metadata.list_unit_prices, metadata.get_cost, metadata.get_billable_size, metadata.get_dataset_range, symbology.resolve (anonymous preview auth used by Databento's own public pricing estimator)
- [9] https://databento.com/datasets/GLBX.MDP3 (dataset overview)
