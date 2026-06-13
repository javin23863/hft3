# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# Cockpit + Options Build-Out Correctness Checklist

Session review date: 2026-06-14. Scope: `main` cockpit build-out commits plus the options/cockpit lane commits on `origin/options/cockpit-integration`.

This is a fix ledger, not a confidence memo. A checkbox is complete only when the implementation, tests, and review evidence prove the exact invariant. Finance/math/HFT rules apply: no silent downgrade, no approximate readiness, no invented pipeline, no unpriced data request, no off-by-one date/window.

## Sources Reviewed

- Vault gate: `Home.md`, `Memory Stack.md`, `wiki/hot.md`, `validation/Lane Architecture.md`, `sessions/2026-06-13 Options backfill, study verdicts, cockpit integration.md`, `decisions/2026-06-13 Lane split - hft3 CME-only core.md`.
- Cockpit ledger: `docs/cockpit/BUILDOUT_REVIEW.md`.
- Current `main`: `46a455c` after cockpit commits `46c8272`, `396f653`, `478f80a`.
- Options branch-only commits ahead of merge base `9db382e`: `701bbdb`, `2d37ec8`, `623cce7`.
- GraphGate query: `options lane cockpit integration cme_options data doctor lanes block FOPT dataset vocabulary`.

## Pass Rules For Agents

- [ ] Read this file, `AGENTS.md`, and the vault hot/context files before editing.
- [ ] Work one checkbox at a time unless the user explicitly assigns a batch.
- [ ] Do not mark a checkbox done until the listed acceptance test exists or the file documents why a stronger test supersedes it.
- [ ] Treat WARN/MISSING/STALE as non-OK in any dashboard, promotion, or live-readiness path unless the ontology explicitly says that state is safe.
- [ ] Do not run or add paid Databento pulls without explicit budget/cost gating and manifest-safe idempotency.

## P1 - Must Fix Before Any Readiness Claim

- [x] `C-001` Cockpit control jobs must not disappear without executing.
  - Source: `apps/cockpit/backend/control.py`, `apps/cockpit/backend/worker.py`, commits `46c8272`/`478f80a`.
  - Failure: cockpit kicks local worker, while worker accepts `host=="chi404"` jobs and can mark disruptive capture jobs done with `executed:false`.
  - Fix invariant: a job is either executed by the intended host, explicitly rejected before enqueue, or remains failed with a visible reason; never `done` for a skipped operational command.
  - Tests: backend tests for local-vs-CHI404 dispatch, `roll_now`, `capture_restart`, nonzero command exit, and missing host entitlement.
  - Evidence 2026-06-14: `packages/lifecycle_orchestrator/src/worker.py` fails skipped/nonzero jobs with artifacts; `tests/test_executor.py`.

- [x] `C-002` Cockpit portfolio/live state must describe the actual session, not an environment wish.
  - Source: `apps/cockpit/backend/aggregate/portfolio.py`, commits `396f653`/`478f80a`.
  - Failure: `live_session` is derived from `EXECUTION_MODE`; a LIVE/no-session state can look green and source-labeled `rithmic_pnl`.
  - Fix invariant: LIVE mode without a readable current session is at least AMBER; stale state uses artifact timestamps, not only directory mtime.
  - Tests: LIVE/no-session, LIVE/malformed-session, LIVE/stale-artifact, killswitch RED, normalized positions.
  - Evidence 2026-06-14: `apps/cockpit/backend/aggregate/portfolio.py`; `test_portfolio_live_without_session_is_amber`.

- [x] `D-001` Event-tape DBN reuse must be keyed by the complete request identity.
  - Source: `scripts/download_event_tape.py`, commit `e4c72b5`.
  - Failure: `.dbn` reuse keyed by `event_id` can reuse a partial symbol set for a later broader request.
  - Fix invariant: reuse requires exact dataset, schema, stype, time range, requested symbols, resolved symbols, and output checksum/size evidence.
  - Tests: same event id with different symbol set must not reuse; exact same request may reuse.
  - Evidence 2026-06-14: `scripts/download_event_tape.py` writes request/checksum sidecars; `tests/test_download_event_tape.py`.

- [x] `D-002` Paid Databento pulls need global cost accounting and no abandoned in-flight retries.
  - Source: `scripts/download_event_tape.py`, `scripts/pull_pm_options_backfill.py`.
  - Failure: watchdog timeout can abandon a paid call and retry; per-shard or per-chunk cost caps are not a global cap.
  - Fix invariant: one process-wide manifest/budget source controls total spend; an in-flight request cannot be reissued until disk/manifest state proves the previous request is absent or corrupt.
  - Tests: simulated timeout after file creation, shard cap aggregation, manifest-lock contention, partial file deletion.
  - Evidence 2026-06-14: current-main `download_event_tape.py` reserves spend in a locked JSONL ledger before paid calls, passes priced cost into download, refuses duplicate attempts without identity proof, and never retries abandoned watchdog timeouts. Branch-only PM puller/reconcile files are tracked under `O-012`.

- [x] `O-001` Options data readiness must never report OK for WARN, MISSING, STALE, or absent lake.
  - Source: `apps/cockpit/backend/aggregate/system.py`, `scripts/data_doctor.py`, `apps/cockpit/backend/aggregate/alerts.py`, commit `6506746`.
  - Failure: `_options_data_readiness()` maps anything except FAIL to OK; missing options lake can be WARN/no-summary and cockpit can show OK.
  - Fix invariant: cme options readiness status is the max severity across options checks, report freshness, summary presence, and lake presence.
  - Tests: absent options dir, missing report, stale report, WARN statistics, WARN recent gap, FAIL coverage; all must surface non-OK as appropriate.
  - Evidence 2026-06-14: `apps/cockpit/backend/aggregate/system.py`, `apps/cockpit/backend/aggregate/alerts.py`, `tests/test_cockpit.py`.

- [x] `O-002` Options dashboard gap display must read the exact coverage check.
  - Source: `apps/cockpit/frontend/src/views/SystemView.tsx`, `scripts/data_doctor.py`, commit `6506746`.
  - Failure: UI searches for check names containing `gap`; real `gap_count` lives under `options-fixing-coverage`, so the card can render `gaps: none` during coverage failure.
  - Fix invariant: UI selects `options-fixing-coverage`, `options-fixing-mbo`, `options-statistics`, and lake checks by exact name and displays their real detail fields.
  - Tests: frontend/unit or fixture render with `gap_count>0`, stale gaps, statistics WARN, missing coverage check.
  - Evidence 2026-06-14: `apps/cockpit/frontend/src/views/SystemView.tsx` exact-name selectors.

- [x] `O-003` Options lane research-only status must block promotion/live/shadow gates.
  - Source: `packages/hft3/validation/lanes/lane.py`, `packages/hft3/validation/lanes/lane_aware_promotion.py`, `packages/hft3/validation/promotion_gate.py`, `specs/OPTIONS_LANE.md`.
  - Failure: `CME_OPTIONS_RESEARCH_PROFILE.research_only=True`, but `_capability_failures()` has no generic research-only failure and no CME_OPTIONS case.
  - Fix invariant: any lane with `research_only=True` fails promotion, shadow, and live readiness until the approved Phase 2 gate flips it.
  - Tests: `FOPT_` candidate fails promotion with `research_only` reason; legacy `OPTIONS_/PARITY_` behavior remains intentional and tested.
  - Evidence 2026-06-14: `lane_aware_promotion.py`; `test_fopt_candidate_fails_while_cme_options_research_only`.

- [x] `O-004` FOPT lane resolution must not depend only on `model_id`.
  - Source: `packages/hft3/validation/lanes/lane_aware_promotion.py`, `packages/hft3/validation/lanes/lane.py`.
  - Failure: `resolve_lane_for_candidate()` checks `FOPT_` only through `model_id`; `symbol` or `event_id` with FOPT can fall through to CME futures.
  - Fix invariant: `FOPT_` in model id, symbol, or event id resolves to `Lane.CME_OPTIONS`.
  - Tests: model-id-only, symbol-only, event-id-only, mixed-case, and legacy `OPTIONS_/PARITY_` routing.
  - Evidence 2026-06-14: `lane_aware_promotion.py`; `tests/test_hft3_validation/test_lane_aware_promotion.py`.

- [x] `O-005` Workbench options coverage must be dataset-specific.
  - Source: `apps/workbench/src/data/coverage_check.py`, commit `6506746`.
  - Failure: `_option_dates()` pools any dated file under options roots/fixtures; `fixing_mbo`, `options_ohlcv`, `options_definitions`, `options_statistics`, and `options_chain` can satisfy one another.
  - Fix invariant: each required options dataset has its own manifest/file validator; definitions-only files cannot satisfy fixing trades, statistics, OHLCV, or chain coverage.
  - Tests: one dataset present and others absent must fail the absent dataset; fixtures cannot count as production coverage unless explicitly requested.
  - Evidence 2026-06-14: `apps/workbench/src/data/coverage_check.py`; `tests/test_workbench/test_coverage_check.py`.

- [ ] `O-006` Branch-only OI store must cover the approved PM root universe, not one root.
  - Source: `origin/options/cockpit-integration:packages/options_lane/studies/fixing_window_study.py`, `docs/ops/options-oi-backfill-handoff.md`, commit `701bbdb`.
  - Failure: `OIStore(root_asset="EW3")` loads one root, while the handoff says the gate needs 25 PM roots. Third-pass branch review confirmed `OIStore` loads only `statistics/<root>` and `definitions/pm/<root>`, so PM-settled OI conditioning can undercount or return `None` for other expiry roots.
  - Fix invariant: OI aggregation loads all approved PM roots or the caller explicitly scopes one root and labels the output as scoped.
  - Tests: synthetic two-root expiry where totals require both roots; scoped one-root mode must be labeled and cannot feed full-lane gate.
  - Status 2026-06-14: branch-only on this checkout; `OIStore` and handoff doc are absent from current `main`.

- [x] `O-007` DBN measurement path must thread OI or label OI unavailable.
  - Source: `origin/options/cockpit-integration:packages/options_lane/studies/fixing_window_study.py`, commit `701bbdb`.
  - Failure: NPZ `measure_file()` can include OI, but `measure_file_from_arrays()` always emits `"oi": None`; DBN results can be mistaken for OI-conditioned evidence.
  - Fix invariant: OI-conditioned studies require OI on both NPZ and DBN paths, or DBN output carries an explicit `oi_conditioned=false` blocker.
  - Tests: DBN measure with store includes OI; DBN measure without store marks result non-conditionable.
  - Evidence 2026-06-14: current-main `fixing_window_study.py` emits `oi_conditioned=false` and `oi_blocker=OI_UNAVAILABLE` for DBN/NPZ until OI store exists.

- [x] `O-013` Options readiness must fail when mandatory checks are absent, not merely non-failing.
  - Source: `apps/cockpit/backend/aggregate/system.py`, `apps/cockpit/backend/aggregate/alerts.py`.
  - Failure: readiness could miss a mandatory absent check and still look complete.
  - Fix invariant: exact mandatory checks must be present before readiness can be OK.
  - Tests: cockpit backend fixtures with missing mandatory options checks.
  - Evidence 2026-06-14: `tests/test_cockpit.py`; combined cockpit run `81 passed`.

- [x] `O-014` Options defect ledger must block canonical, FOPT, and legacy options/parity candidates.
  - Source: `packages/hft3/validation/options_defect_ledger.py`, `packages/hft3/validation/promotion_gate.py`.
  - Failure: only `OPEN` rows and canonical options names were blocking; unknown/malformed statuses and legacy `OPTIONS_/PARITY_` surfaces could fail open.
  - Fix invariant: missing, malformed, unknown, or open ledger rows block `CME_OPTIONS`, `FOPT_`, `OPTIONS_`, and `PARITY_` promotion/live paths.
  - Tests: unknown status, malformed row, legacy prefix blocking, empty-ledger pass-through.
  - Evidence 2026-06-14: `tests/test_options_defect_ledger.py`, `tests/test_gate_schema.py`; validation slice `71 passed`.

- [x] `O-015` Workbench options campaigns and paper-shadow updates must obey the defect ledger.
  - Source: `apps/workbench/src/run/campaign_runner.py`.
  - Failure: options fixture campaigns and `record_paper_shadow()` could set `promote_candidate=true` after paper-shadow PASS even while the options defect ledger was open or malformed.
  - Fix invariant: options-like workbench surfaces append an options-defect blocking gate and keep `promote_candidate=false` unless the ledger is empty.
  - Tests: open, malformed, and unknown ledgers; FOPT/OPTIONS_/PARITY_/CME options scopes; paper-shadow PASS with open ledger.
  - Evidence 2026-06-14: `tests/test_workbench/test_options_lane_campaign.py`; focused run `12 passed`.

- [x] `O-016` Lifecycle recovery must not bypass the re-arm gate chain.
  - Source: `packages/lifecycle_orchestrator/src/run_lifecycle_eval.py`, `packages/lifecycle_orchestrator/src/rearm.py`.
  - Failure: a clean GREEN read could move `DEGRADED -> LIVE` directly from decay evaluation.
  - Fix invariant: recovery to LIVE is counted only when `rearm.attempt_rearm()` passes G0-G8, lane capability/profile checks, and actually leaves the registry LIVE.
  - Tests: missing gates refuse recovery; mocked re-arm success is the only counted recovery; FOPT recovery remains blocked by options profile/promotion gate.
  - Evidence 2026-06-14: `tests/test_lifecycle_eval_recovery_gates.py`, `tests/test_orchestrator_rearm.py`; lifecycle slice `26 passed`.

- [x] `O-017` Legacy scorecards must not bypass options lane capability.
  - Source: `packages/hft3/validation/promotion_gate.py`.
  - Failure: a GREEN legacy scorecard without `lane_coverage` could let `FOPT_` fall through flat scorecard coverage and rely only on an empty options defect ledger, bypassing `CME_OPTIONS.research_only`.
  - Fix invariant: options-like candidates always get an independent lane capability/profile gate, regardless of scorecard schema.
  - Tests: `FOPT_` with legacy GREEN scorecard and empty options ledger remains blocked with a `research_only`/capability failure.
  - Evidence 2026-06-14 third pass: `test_fopt_legacy_scorecard_still_blocked_by_research_only_lane`; focused gate/workbench/lifecycle run `58 passed`.

- [x] `O-018` Paper-shadow PASS must not erase existing blockers.
  - Source: `apps/workbench/src/run/campaign_runner.py`.
  - Failure: `record_paper_shadow()` recomputed `promote_candidate` from status/periods/ledger and ignored existing `blocking_gates`, so a later paper-shadow PASS could launder a prior data/lane blocker.
  - Fix invariant: `promote_candidate=true` requires campaign PASS, all mandatory period gates pass, paper shadow PASS, empty options ledger, and no existing blocking gates.
  - Tests: summary with an existing `data_coverage` blocker remains non-promotable after paper-shadow PASS and empty ledger.
  - Evidence 2026-06-14 third pass: `test_record_paper_shadow_does_not_clear_existing_blocking_gate`; focused gate/workbench/lifecycle run `58 passed`.

- [x] `O-019` Lifecycle re-arm evidence parsing must fail closed on ambiguous values.
  - Source: `packages/lifecycle_orchestrator/src/run_lifecycle_eval.py`, `packages/lifecycle_orchestrator/src/rearm.py`.
  - Failure: JSON strings such as `"false"` parsed truthy for re-arm gates; unknown defect-ledger statuses looked closed; GREEN certs missing explicit `stale:false` and `promotion_eligible:true` could pass.
  - Fix invariant: only literal JSON `true` is true; unknown/missing defect status is open; GREEN cert also needs explicit fresh and promotion-eligible fields.
  - Tests: string `"false"` promotion gate refuses recovery; `PENDING_REVIEW` ledger status fails closed; cert missing freshness/eligibility fails.
  - Evidence 2026-06-14 third pass: `tests/test_lifecycle_eval_recovery_gates.py`, `tests/test_orchestrator_rearm.py`; focused gate/workbench/lifecycle run `58 passed`.

## P2 - High Risk, Fix Before Broad Use

- [x] `C-003` Worker must not mark nonzero command exits as successful.
  - Source: `apps/cockpit/backend/worker.py`.
  - Failure: subprocess failures can be reported as done without propagating returncode as failure.
  - Fix invariant: nonzero return code means failed job with returncode, tail, and command metadata in state.
  - Tests: command returns 2; job state is failed and logs remain visible.
  - Evidence 2026-06-14: `packages/lifecycle_orchestrator/src/worker.py`; `test_worker_fails_nonzero_returncode`.

- [x] `C-004` Cockpit rate limiting must not trust spoofable XFF by default.
  - Source: `apps/cockpit/backend/main.py`, commit `478f80a`.
  - Failure: `COCKPIT_TRUST_PROXY=1` trusts leftmost `X-Forwarded-For`; spoofing can bypass per-client limits.
  - Fix invariant: trusted proxy mode requires a trusted proxy allowlist or uses the rightmost untrusted hop algorithm.
  - Tests: direct spoofed XFF ignored; trusted proxy path honors the configured client IP.
  - Evidence 2026-06-14: `apps/cockpit/backend/main.py`; `test_rate_limit_ignores_xff_without_trusted_proxy`, `test_rate_limit_honors_xff_only_from_allowlisted_proxy`.

- [x] `C-005` Portfolio latest-session selection must use artifact freshness, not directory mtime.
  - Source: `apps/cockpit/backend/aggregate/portfolio.py`.
  - Failure: stale directory metadata can select the wrong session when live artifacts update under an older folder.
  - Fix invariant: current session selection uses the newest expected artifact timestamp and reports stale/missing artifacts as non-green.
  - Tests: multiple sessions with divergent directory/artifact mtimes.
  - Evidence 2026-06-14: `apps/cockpit/backend/tests/test_cockpit.py`; cockpit/unified slice `81 passed`.

- [x] `D-003` Options data doctor must validate bytes, schema, and consumer requirements.
  - Source: `scripts/data_doctor.py`, tests `tests/test_data_doctor_options.py`.
  - Failure: date coverage is inferred from filenames; zero-byte/corrupt/wrong-schema files can count. Trades-only counts as covered even when a consumer needs MBO/quotes. Statistics missing is WARN even for OI-dependent workflows.
  - Fix invariant: checks are consumer-aware and open/decode at least a cheap schema sample. OI-dependent workflows fail without statistics and definitions.
  - Tests: zero byte, corrupt DBN, wrong schema, trades-only consumer mismatch, missing stats for OI-conditioned gate.
  - Evidence 2026-06-14: `scripts/data_doctor.py` rejects zero-byte, tiny corrupt DBN without sidecar, wrong-schema sidecars, large corrupt DBN samples, trades-only fixing coverage, and missing statistics.

- [x] `D-004` Hardcoded options coverage exceptions must be manifest-proven.
  - Source: `scripts/data_doctor.py`.
  - Failure: `OPTIONS_FIXING_COVERED_ELSEWHERE` removes dates from expected gaps without machine-verifiable artifact proof.
  - Fix invariant: covered-elsewhere is derived from a manifest or signed ledger entry that names dataset, schema, time window, and path.
  - Tests: hardcoded exception without manifest fails; manifest-backed alternate file passes.
  - Evidence 2026-06-14: `options/coverage_manifest.json` rows must name date/dataset/schema/window/path and pass artifact validation before clearing a gap.

- [x] `D-005` Options data doctor sidecars must prove the DBN they certify.
  - Source: `scripts/data_doctor.py`.
  - Failure: arbitrary sidecars or metadata files could clear coverage/statistics gaps without matching DBN bytes, schema, count, size, and checksum evidence.
  - Fix invariant: sidecar acceptance requires valid schema, record count or explicit no-data proof, byte size, SHA-256 identity, and DBN decode/count confirmation for positive-record artifacts; `proof.txt` and unrelated `job_status.json` cannot clear gaps.
  - Tests: invalid sidecar, wrong hash/size, arbitrary proof file, arbitrary job status file, forged matching sidecar over dummy bytes, valid no-data proof.
  - Evidence 2026-06-14 third pass: `tests/test_data_doctor_options.py`, `tests/test_download_event_tape.py`; focused data run `31 passed`.

- [x] `D-006` Paid event-tape idempotency must reject paid superset/subset ambiguity.
  - Source: `scripts/download_event_tape.py`.
  - Failure: a prior paid superset/subset request could be mistaken for exact identity and permit unsafe reuse or reissue.
  - Fix invariant: only exact request identity can reuse DBN; paid superset/subset conflicts block fail-closed unless a new exact proof exists. Partial conversion keeps DBN sidecar evidence.
  - Tests: superset->subset duplicate, subset->superset conflict, partial-overlap conflict, corrupt budget ledger, partial conversion sidecar retention.
  - Evidence 2026-06-14 third pass: `tests/test_download_event_tape.py`; focused data run `31 passed`.

- [x] `D-007` Workbench coverage must not count unproved NPZ or options artifacts.
  - Source: `apps/workbench/src/data/coverage_check.py`.
  - Failure: official MBO coverage counted filename-matching `*_mbo.npz` shells without manifest/hash/load checks; options coverage counted arbitrary dated files under dataset directories.
  - Fix invariant: runnable MBO days require manifest row, positive event count, matching SHA-256, and loadable `data`; options dataset dates require data-doctor-valid DBN artifacts with the dataset schema.
  - Tests: corrupt NPZ with matching date is excluded; corrupt one-byte options DBN is excluded; definitions/fixing/statistics remain independent.
  - Evidence 2026-06-14 third pass: `tests/test_workbench/test_coverage_check.py`, `tests/test_fixing_window_study.py`; focused coverage/fixing run `69 passed`.

- [x] `O-008` Structural backtest adapters must not look like evidence backtests.
  - Source: `packages/hft3/validation/lanes/adapters/cme_options_adapter.py`, `packages/hft3/validation/lanes/registration.py`.
  - Failure: options adapters return zero-trade, zero-PnL, `degraded=false` results; certification can confuse registration smoke with strategy evidence.
  - Fix invariant: structural runs are labeled `structural_only`, `research_only`, and non-promotable; evidence backtests require real data, fills, costs, and nonzero sample accounting.
  - Tests: certification card distinguishes structural smoke from evidence; promotion ignores structural-only result.
  - Evidence 2026-06-14: CME options adapters return degraded structural-only non-promotable results.

- [x] `O-009` Unified certification must fail closed when lane tests are missing or skipped.
  - Source: `packages/hft3/validation/lanes/unified_certification_runner.py`.
  - Failure: missing test paths can produce skip text with `overall_pass=True`; `skip_pytest=True` writes passed results.
  - Fix invariant: missing lane test path is a failure unless the caller passes an explicit documented waiver that cannot feed promotion/live gates.
  - Tests: missing test path fails; explicit waiver records non-promotable skipped state.
  - Evidence 2026-06-14: `unified_certification_runner.py`; missing/skipped pytest paths fail closed.

- [x] `O-010` Open options known-defect ledger must feed cockpit and gates.
  - Source: `specs/OPTIONS_LANE.md`.
  - Failure: ledger entries `o-a` through `o-i` say Phase 2/live is blocked, but cockpit and gates do not surface the open defect count.
  - Fix invariant: cockpit options card and promotion gates surface open options defects and block readiness until ledger is empty or entries are explicitly waived by the ontology.
  - Tests: open ledger count blocks; empty ledger permits next gate only when all other checks pass.
  - Evidence 2026-06-14: `hft3.validation.options_defect_ledger`, cockpit system/alerts/frontend, promotion gate, and lifecycle rearm all read `specs/OPTIONS_LANE.md`.

- [ ] `O-011` Branch-only definition map should avoid float strike as an authority value.
  - Source: `origin/options/cockpit-integration:packages/options_lane/studies/definition_map.py`, tests `tests/test_options_definition_map.py`.
  - Failure: DBN fixed-point strike is converted to `float`; tests allow `abs(... ) < 1e-6`.
  - Fix invariant: strike authority is integer fixed-point or `Decimal`; display floats may be derived only at the edge.
  - Tests: exact fixed-point conversion, sentinel exclusion, no approximate tolerance in authority tests.
  - Status 2026-06-14: branch-only on this checkout; `definition_map.py` and `tests/test_options_definition_map.py` are absent from current `main`.

- [ ] `O-012` Branch-only lock-timeout reconcile path must prove files are complete.
  - Source: `origin/options/cockpit-integration:scripts/pull_pm_options_backfill.py`, `origin/options/cockpit-integration:scripts/reconcile_pm_backfill_ledger.py`, commits `2d37ec8`/`623cce7`.
  - Failure: lock-timeout path accepts file-present/nonzero as complete in the puller; reconcile opens one record and returns true even for empty ranges. Third-pass branch review also found resume/idempotence treats any existing `dest` as complete before the manifest-lock timeout guard and reconcile reconstructs `2026-06` as a full calendar month even though the pull caps the final chunk at `2026-06-13`.
  - Fix invariant: accepted chunks have schema-open, end-of-file/decompression integrity, request identity, and billable cost proof before manifest append.
  - Tests: truncated `.dbn.zst` fails; manual/pre-623cce7 partial file does not resume as success; valid empty range has explicit vendor no-data proof; final June 2026 reconcile uses the exact capped request window; valid nonempty chunk reconciles once.
  - Status 2026-06-14: branch-only on this checkout; PM backfill puller/reconcile files are absent from current `main`.

## P3 - Tighten After P1/P2

- [ ] `Q-001` Replace approximate options pricing/risk placeholders before any execution claim.
  - Source: `packages/options_pricing/src/iv_solver.py`, `packages/options_data/src/expiry_calendar.py`, `packages/options_risk/src/margin_approx.py`, `packages/options_risk/src/stress_grid.py`, `specs/OPTIONS_LANE.md`.
  - Failure: placeholders are acceptable research scaffolds, not execution math.
  - Fix invariant: execution path uses verified calendar, exchange-consistent expiry/style handling, production-grade solver bounds, and real margin methodology or explicit broker API authority.
  - Tests: exchange expiry known values, solver property/round-trip tests, stress grid convergence, margin cross-check against authoritative fixture.
  - Status 2026-06-14: intentionally open research/math blocker per `specs/OPTIONS_LANE.md`; blocks shadow/live/execution claims, not Phase 0-1 research.

- [x] `Q-002` Options fixing-window boundary rules need exact half-open/closed policy.
  - Source: `packages/options_lane/studies/fixing_window_study.py`, `packages/options_lane/studies/dbn_trades.py`.
  - Failure: multiple helpers include both lower and upper boundaries; double-count or off-by-one-nanosecond errors are catastrophic around 15:00 CT.
  - Fix invariant: every study window documents and tests `[start, end)` or `[start, end]` with exchange rationale; adjacent windows cannot double-count the boundary trade.
  - Tests: trades exactly at 14:59:30, 15:00:00, 15:00:00.000000001 CT; DST transition days.
  - Evidence 2026-06-14: `fixing_window_study.py` uses half-open `[start, end)` windows; `TestHalfOpenWindows`.

- [x] `Q-003` Alerts should surface WARN/MISSING/STALE when they affect readiness.
  - Source: `apps/cockpit/backend/aggregate/alerts.py`.
  - Failure: alerts include only `FAIL`, so warning-grade missing statistics or absent lake can be quiet.
  - Fix invariant: readiness-impacting WARN/MISSING/STALE creates an alert with severity matching cockpit status.
  - Tests: WARN statistics and missing options lake appear in alerts.
  - Evidence 2026-06-14: `apps/cockpit/backend/aggregate/alerts.py`; `tests/test_cockpit.py`.

- [x] `Q-004` Fixing-window signed imbalance must not include post-fixing outcome flow.
  - Source: `packages/options_lane/studies/fixing_window_study.py`.
  - Failure: `imbalance_signed` was computed across the full 14:55-15:05 scan, leaking post-15:00 markout-window flow into the predictor.
  - Fix invariant: `imbalance_signed` equals fixing-window-only `[14:59:30, 15:00:00)` flow; pre/fix/post imbalances are separately emitted.
  - Tests: post-only flow leaves `imbalance_signed`/`imbalance_fix` empty and populates `imbalance_post`.
  - Evidence 2026-06-14: `tests/test_fixing_window_study.py`; focused run `55 passed`.

- [x] `Q-005` Fixing-window 2026 options usage and lake-root policy must be machine-enforced.
  - Source: `packages/options_lane/studies/fixing_window_study.py`, `research_cards/fixing_window/README.md`.
  - Failure: `--root` was resolved then ignored, and 2026 options rows could be measured without the vault-required usage class.
  - Fix invariant: inventory/measure use the requested lake root; any 2026 options row requires `cost-calibration`, `alpha-fit`, or `oos-eval`, with `alpha-fit` refused after 2026-06-30. Research card states OI_UNAVAILABLE plus parity/regime caveats.
  - Tests: explicit lake root, missing usage class, accepted 2026 usage class, alpha-fit after 2026-06-30 refusal.
  - Evidence 2026-06-14: `tests/test_fixing_window_study.py`; focused run `55 passed`.

- [x] `Q-006` Legacy fixing-window artifacts must not remain valid evidence after leakage fixes.
  - Source: `packages/options_lane/studies/fixing_window_study.py`, `research_cards/fixing_window/README.md`.
  - Failure: the 2026-06-13 research-card tables were based on legacy output lacking `imbalance_fix`, `imbalance_post`, `oi_blocker`, and 2026 usage metadata; `_pa_last_price()` selected the first row at a tied max timestamp, not the last event/file row.
  - Fix invariant: old tables are explicitly invalidated for signal inference; DBN measurement records must include current predictor/outcome/OI/usage fields; tied timestamps use the last row among the max timestamp.
  - Tests: legacy 2026 record rejected; missing 2026 usage class rejected; tied timestamp chooses the last file row.
  - Evidence 2026-06-14 third pass: `tests/test_fixing_window_study.py`; focused coverage/fixing run `69 passed`.

## Verification Matrix

- [x] `python -m pytest apps/cockpit/backend/tests -q`
- [x] `python -m pytest tests/test_data_doctor_options.py -q`
- [x] `python -m pytest tests/test_workbench/test_coverage_check.py -q`
- [x] `python -m pytest tests/test_lanes_cme_options.py tests/test_hft3_validation/test_lane_aware_promotion.py -q`
- [x] `python -m pytest tests/test_options_defect_ledger.py tests/test_gate_schema.py tests/test_hft3_validation/test_lane_aware_promotion.py tests/test_lanes_cme_options.py -q`
  - Evidence 2026-06-14: `71 passed`.
- [x] `python -m pytest tests/test_data_doctor_options.py tests/test_download_event_tape.py tests/test_fixing_window_study.py tests/test_lifecycle_eval_recovery_gates.py tests/test_orchestrator_rearm.py tests/test_workbench/test_options_lane_campaign.py -q`
  - Evidence 2026-06-14: `113 passed`, 2 legacy-id deprecation warnings.
- [x] Third-pass focused data proofing:
  `python -m pytest tests/test_download_event_tape.py tests/test_data_doctor_options.py -q`
  - Evidence 2026-06-14: `31 passed`.
- [x] Third-pass focused coverage/fixing:
  `python -m pytest tests/test_workbench/test_coverage_check.py tests/test_fixing_window_study.py -q`
  - Evidence 2026-06-14: `69 passed`, 1 legacy-id deprecation warning.
- [x] Third-pass focused promotion/workbench/lifecycle:
  `python -m pytest tests/test_gate_schema.py tests/test_workbench/test_options_lane_campaign.py tests/test_orchestrator_rearm.py tests/test_lifecycle_eval_recovery_gates.py -q`
  - Evidence 2026-06-14: `58 passed`, 2 legacy-id deprecation warnings.
- [x] `python -m pytest apps/cockpit/backend/tests tests/test_executor.py tests/test_hft3_validation/test_unified_certification_runner.py tests/test_workbench/test_coverage_check.py -q`
  - Evidence 2026-06-14: `81 passed`, 2 dependency/legacy warnings.
- [x] Full second-pass targeted matrix:
  `python -m pytest apps/cockpit/backend/tests tests/test_executor.py tests/test_lanes_cme_options.py tests/test_hft3_validation/test_lane_aware_promotion.py tests/test_hft3_validation/test_unified_certification_runner.py tests/test_workbench/test_coverage_check.py tests/test_workbench/test_options_lane_campaign.py tests/test_data_doctor_options.py tests/test_download_event_tape.py tests/test_fixing_window_study.py tests/test_options_defect_ledger.py tests/test_gate_schema.py tests/test_orchestrator_rearm.py tests/test_lifecycle_eval_recovery_gates.py -q`
  - Evidence 2026-06-14: `265 passed`, 4 dependency/legacy warnings.
- [x] Full third-pass targeted matrix:
  `python -m pytest apps/cockpit/backend/tests tests/test_executor.py tests/test_lanes_cme_options.py tests/test_hft3_validation/test_lane_aware_promotion.py tests/test_hft3_validation/test_unified_certification_runner.py tests/test_workbench/test_coverage_check.py tests/test_workbench/test_options_lane_campaign.py tests/test_data_doctor_options.py tests/test_download_event_tape.py tests/test_fixing_window_study.py tests/test_options_defect_ledger.py tests/test_gate_schema.py tests/test_orchestrator_rearm.py tests/test_lifecycle_eval_recovery_gates.py -q`
  - Evidence 2026-06-14 third pass: `279 passed`, 4 dependency/legacy warnings.
- [x] Cockpit frontend build: `npm run build` in `apps/cockpit/frontend`
  - Evidence 2026-06-14 third pass: build passed; Vite emitted only the pre-existing large-chunk warning.
- [x] GraphPost / `scripts/graphify_rebuild.ps1`
  - Evidence 2026-06-14 third pass: first attempt hit the script's old 300s update timeout and left graphify children that were stopped; timeout was widened to 900s update / 300s fallback; rerun completed `graphify update .` in 236s with no topology changes and no leftover graphify process.
- [ ] For options branch re-entry: run `tests/test_options_definition_map.py`, `tests/test_options_oi_decode.py`, `tests/test_options_load_expiry_oi.py` after merging/rebasing the branch.
- [x] For any paid-data path: run dry-run/cost-cap simulation and manifest idempotency tests before live pull.
  - Evidence 2026-06-14: `tests/test_download_event_tape.py` covers exact identity reuse, abandoned timeout no-retry, global cap aggregation, duplicate reservation, and unproved DBN rejection. No live paid pull was run.
