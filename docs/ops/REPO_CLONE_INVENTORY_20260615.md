# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# Repo Clone Inventory 2026-06-15

Date: 2026-06-15.

Canonical working tree remains:

```text
C:\Users\MSI\repos\hft3
```

This inventory is read-only evidence for retiring or migrating alternate hft3 checkouts. No alternate checkout is safe to delete yet.

## `C:\Users\MSI\Documents\GitHub\hft3`

Role: dirty standalone stale clone.

Git state:

- Branch: `chore/repo-cleanup-and-data-fill`.
- HEAD: `0641e18` (`feat(vix): derive NPZ from OPRA VIX.OPT cmbp-1, upgrade databento SDK`).
- Upstream: `origin/chore/repo-cleanup-and-data-fill` at `713d1e8`.
- Divergence: ahead 1, behind 1.
- HEAD-only commit `0641e18` is already present on `origin/stocks-lane-restored`, so it is not unique versus all origin refs.
- Unique local commit: `71ac096` on `wip/audit-and-inventory-snapshots`.
- Stashes: 5.

Dirty state:

- Status summary: 115 modified, 12 deleted, 24 untracked.
- Main categories: packages 71, tests 33, scripts 26, data 12, runtime 4, apps 2, tools 2, `desk_env.py` 1.
- Actual content diff: 15 files changed, 207 insertions, 214 deletions. Most other modified paths appear to be CRLF line-ending churn.

Preserve before retirement:

- `packages/equities_lane/src/prediction/**` entire untracked prediction/L3 module.
- `scripts/convert_vix_npz_gap.py`.
- `runtime/data_downloads/vix_opt_cmbp1_download_report.json`.
- `runtime/data_downloads/vix_opt_features_build_report.json`.
- `runtime/data_downloads/vix_opt_npz_convert_report.json`.
- Local-only commit `71ac096`.
- All 5 stashes.
- Ignored/local artifacts: `graphify-out` (~1.21 GB), `runtime/data_downloads` (~75.9 MB), `runtime/data_audits` (~38.4 MB), `_rithmic_staging` (~39.0 MB), `artifacts` (~27.3 MB), `rithmic_portable.zip` (~17.5 MB).
- Secret-ish ignored files `.env` and `.btc-node.env`; preserve only securely and never copy into public git history.

Recommendation:

Do not retire/delete this checkout. First create a local snapshot or migration bundle, then port or reject the prediction/VIX files, local-only audit commit, and stashes.

## `C:\Users\MSI\Documents\opencode\hft3`

Role: mostly clean standalone clone with local artifacts.

Git state:

- Branch: `main`.
- HEAD: `fca308b24fb9` (`fix(backtest): L3 engine tier for L3 lake data - first real sim fills`).
- Upstream: `origin/main`.
- Divergence: 0 ahead, 0 behind against local `origin/main`.
- Tracked dirty state: clean.
- Untracked non-ignored: 8 files under `runtime/universe_logs`.
- Ignored untracked: 10,109 files.

Remotes:

- `origin`: `https://github.com/javin23863/hft3.git`.
- `primary`: `C:\Users\MSI\Documents\New project`.

Preserve before retirement:

- Local-only branch/worktree: `perf/event-driven-quoting` at `fb9df99`, worktree `C:\Users\MSI\repos\hft3-lever2`.
- `runtime/universe_logs` 8 files (~126 KB): `universe_full.*`, `universe_v2.*`, `universe_v3.*`, `universe_v4.*`.
- `data` (~13.0 MB), including CPI DBN/ZST, NPZ, and crypto bronze parquet.
- `artifacts` (~25.6 MB).
- `rithmic_gateway` ignored SDK/vendor material (~1.1 GB), including `RApiPlus.cpp.13.7.0.0.zip` (~138 MB).
- `graphify-out` (~421 MB), likely regenerable unless exact old graph snapshot matters.
- Secret-ish ignored files `.env` and `.btc-node.env`; preserve only securely and never copy into public git history.

Recommendation:

Do not retire/delete this checkout until `perf/event-driven-quoting` is pushed or archived, and local data/artifacts/logs/Rithmic SDK material are copied or deliberately declared disposable.

## Physical Stub State

`C:\Users\MSI\Documents\hft3` remains a legacy Codex entry stub containing only `AGENTS.md`. Its stray empty `.git` directory was moved to `C:\Users\MSI\Documents\hft3.git.legacy-empty-20260615-184124`, so the legacy path is no longer a second git working tree. Replacing the stub itself with a junction to `C:\Users\MSI\repos\hft3` was attempted again on 2026-06-15, but Windows still reported the directory as in use because this active Codex session was opened there.

Required next step when unlocked:

1. Verify the stub still contains only `AGENTS.md`.
2. Move the stub to `C:\Users\MSI\Documents\hft3.stub-archived-<timestamp>`.
3. Create a junction at `C:\Users\MSI\Documents\hft3` targeting `C:\Users\MSI\repos\hft3`.
