# Ponytail — mandatory agent coding discipline

**Repo:** https://github.com/DietrichGebert/ponytail  
**Install path:** `vendor/ponytail/` (vendored clone; update with `git -C vendor/ponytail pull`)  
**Cursor runtime (#2 after Fable):** `.cursor/rules/01-ponytail-mindset.mdc` (hardened always-on rule)  
**Legacy redirect:** `.cursor/rules/ponytail.mdc` → points to `01-ponytail-mindset.mdc`

Ponytail is **not** a Vast/SSH tool. It is the standing **lazy-senior-dev** ruleset: YAGNI, stdlib-first, minimal diffs, deletion over addition. Safety rails (validation, security, accessibility, finance/math invariants) are never cut.

## When agents MUST apply ponytail

- Any code or doc edit in hft3 (always-on via `.cursor/rules/01-ponytail-mindset.mdc`, second after Fable)
- Refactors, new scripts, pipeline wiring — prefer extending existing scripts over new abstractions
- Vast/CHI404 ops wrappers — one shell script, env vars, no orchestration frameworks
- Review pass before merge: ask "can this be one line / already-installed / deleted?"

## Intensity (optional)

Default: **full**. Override per session:

- Env: `PONYTAIL_DEFAULT_MODE=lite|full|ultra|off`
- Config: `%APPDATA%\ponytail\config.json` → `{ "defaultMode": "full" }`

## Minimal usage (from ponytail docs)

**Cursor / Windsurf / Cline:** hft3 uses `.cursor/rules/01-ponytail-mindset.mdc` (load order #2). Sync from upstream: copy `vendor/ponytail/.cursor/rules/ponytail.mdc` into `01-ponytail-mindset.mdc` when updating vendor.

**Review current diff (skill hosts):** `/ponytail-review` — over-engineering delete-list.

**Audit repo:** `/ponytail-audit`

**The ladder (every implementation):**

1. Does this need to exist? (YAGNI)
2. Stdlib?
3. Native platform feature?
4. Already-installed dependency?
5. One line?
6. Minimum that works

**Mark deferred shortcuts:** `ponytail: <ceiling> — upgrade via <path>`

## Vast operations (hft3 — not ponytail)

Use `vastai` CLI to rent/inspect instances, then the sole paid-screen deploy contract: `scripts/vast_deploy_and_verify.ps1` from the workstation. It must print `DEPLOY_CONTRACT_PASS` before any full paid run. Resolve instance: `vastai show instances --raw` or REST `https://console.vast.ai/api/v0/instances/`.

**Lake manifest authority:** gate `pilot_hashes.lake_manifest_hash` is `sha256(manifest.parquet)[:32]`. On Vast, sync workstation `C:\hft3-lake\manifest.parquet` to `/data/npz/manifest.parquet` and set `HFT3_MANIFEST_PATH` accordingly. `/data/npz/manifest.json` is a different artifact with a different hash — do not use it for gate lineage.

**Ops scripts:** `scripts/vast_deploy_and_verify.ps1` syncs repo/gate/events/`manifest.parquet` and runs `scripts/vast_remote_verify.sh`. After that contract passes, launch on Vast with `bash scripts/run_vbt_paid_screen_vast_full.sh`. Attach manually only after launch: `ssh -p <port> root@<ssh_host> -t tmux attach -t vbt_full_v2`.
