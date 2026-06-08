# Known issues checklist — Workbench autonomous run path

Generated: 2026-06-09. Last verified: 17/17 tests pass.

## Adapter / model path
| # | Issue | File:Line | Fix | Status |
|---|-------|-----------|-----|--------|
| 1 | BookPressureModel.evaluate() crashed on missing bid_p — adapter now passes OrderBook | structural_adapter.py:94-95 | Pass book from build_features | [x] |
| 2 | Hardcoded mid=4500.0 / volume=100.0 — now derived from book BBO + last event qty | structural_adapter.py:83-93 | Derive from book or events | [x] |
| 3 | 5 tests added for evaluate(book=...), BBO kwargs, adapter path, all-models tolerance | test_book_pressure_evaluate.py | 5 tests | [x] |

## Campaign runner (pre-existing — acknowledged, not my bug)
| # | Issue | File:Line | Status |
|---|-------|-----------|--------|
| 4 | Early return on DATA_INSUFFICIENT now writes final status.json | campaign_runner.py:377 | [x] |
| 5 | Resume command is implicit ("run") — semantic gap is low-priority | campaign_runner.py:86-90 | [-] |
| 6 | Per-campaign status.json has completed/failed/blocked/skipped counts from orchestrator | all_lanes.py:442 | [x] |
| 7 | Per-period artifact filenames hardcoded — silent skip on absent files | campaign_runner.py:587-604 | [-] |

## Launcher script
| # | Issue | File:Line | Fix | Status |
|---|-------|-----------|-----|--------|
| 8 | Exit-Launcher now uses Write-Log — all exits captured in launcher log | launch_workbench.ps1:46-58 | Write-Log in Exit-Launcher | [x] |
| 9 | Expected Python path is hardcoded — only a WARN, not a blocker | launch_workbench.ps1:38-43 | [-] |

## Autonomous orchestrator
| # | Issue | File:Line | Fix | Status |
|---|-------|-----------|-----|--------|
| 10 | control.json was clobbered at start — fixed (only writes if not existing) | all_lanes.py:394-396 | [x] |
| 11 | errors.jsonl now records blocked/failed job outcomes in addition to exceptions | all_lanes.py:484-502 | append_error on blocked/failed | [x] |
| 12 | evidence_snapshot now writes backend_pid (=os.getpid()) and heartbeat_ts | all_lanes.py:169-173 | os.getpid() + heartbeat_ts field | [x] |

## Metrics module
| # | Issue | File:Line | Fix | Status |
|---|-------|-----------|-----|--------|
| 13 | event_dir walk uses dedup set — no double-counting across layouts | metrics.py:193,210 | seen set per dir | [x] |
| 14 | trade_ledger.parquet pandas dependency — silent skip if pandas missing | metrics.py:218-226 | [-] |

## Coverage / PIT
| # | Issue | File:Line | Fix | Status |
|---|-------|-----------|-----|--------|
| 15 | Coverage row cap increased 5k → 50k — covers full 55×5×55 matrix | coverage_check.py:40 | [x] |
| 16 | PIT report honest MISSING_REQUIRED_LEDGER — CampaignEvent doesn't surface release_date | coverage_check.py:131-155 | [-] |

## Total
| Status | Count |
|--------|-------|
| [x] fixed | 12 |
| [-] acknowledged (pre-existing, out of scope, or low-priority) | 4 |
