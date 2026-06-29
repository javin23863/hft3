# Ponytail — mandatory agent coding discipline

**Repo:** https://github.com/DietrichGebert/ponytail  
**Install path:** `vendor/ponytail/` (vendored clone; update with `git -C vendor/ponytail pull`)  
**Cursor runtime (#2 after Fable):** `.cursor/rules/01-ponytail-mindset.mdc` (hardened always-on rule)  
**Legacy redirect:** `.cursor/rules/ponytail.mdc` → points to `01-ponytail-mindset.mdc`

Ponytail is **not** a Vast/SSH tool. It is the standing **lazy-senior-dev** ruleset: YAGNI, stdlib-first, minimal diffs, deletion over addition. Safety rails (validation, security, accessibility, finance/math invariants) are never cut.

## Fable preflight (mandatory first)

Before applying Ponytail, agents must load Fable:

1. `.cursor/rules/00-fable-mindset.mdc`
2. `docs/vault/FABLE_MINDSET.md`
3. Full reference when needed: `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md`

Only after the Fable loop is active should Ponytail trim scope. Fable supplies
the ground/reason/act/observe/verify discipline; Ponytail supplies the minimum
code shape inside that discipline.

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

## HBT-only evidence-port review

For HftBacktest-only work, Ponytail means porting only the minimum useful
evidence shape, not dragging forward the old pipeline. Before adding code or
docs that mention VectorBT, robustness bridges, screening artifacts, Stage A, or
parameter search, ask:

1. Is this evidence shape needed by the HBT-only campaign?
2. Has it been rewritten to `canonical_model_id`, registry/source hashes,
   `adapter_status`, `authority_refs`, `hbt_run_status`, and
   `promotion_decision_path`?
3. Does any sentence imply VectorBT or robustness decides what HBT receives?
4. Does any adapter, bridge, data, or feature-shape failure become model failure?

If the answer to 3 or 4 is yes, stop and repair the plan before implementation.
Parameter proposals with `objective_evaluations=0` are proposal manifests only,
not adaptive optimizers or pre-HBT rejection evidence.

Full-campaign HBT work is not a place for local proof shortcuts. Do not add a
handpicked-symbol subset, hidden preference filter, or permanent shortcut path
unless the owner explicitly orders that diagnostic. Keep the minimum code shape
by extending the manifest/runner already present.

Contract metadata must be explicit per executable HBT product. Do not inherit
ES-shaped defaults, fall back from missing `contract` to `symbol`, or substitute
nearby instruments for authority gaps. Missing product metadata writes a blocker
receipt and remains in the evidence surface.

## Vast operations (hft3 — not ponytail)

Use `vastai` CLI + `scripts/vast_ssh_run_vbt_paid_screen.sh` or direct SSH (`VAST_SSH_HOST`, `VAST_SSH_PORT`). Resolve instance: `vastai show instances --raw` or REST `https://console.vast.ai/api/v0/instances/`.

**Lake manifest authority:** gate `pilot_hashes.lake_manifest_hash` is `sha256(manifest.parquet)[:32]`. On Vast, sync workstation `C:\hft3-lake\manifest.parquet` to `/data/npz/manifest.parquet` and set `HFT3_MANIFEST_PATH` accordingly. `/data/npz/manifest.json` is a different artifact with a different hash — do not use it for gate lineage.

**Ops scripts:** `runtime/vast_d3_preflight.sh` (lake + handoff), `runtime/vast_d4_launch.sh` (tmux full run). Attach: `ssh -p <port> root@<ssh_host> -t tmux attach -t vbt_full_v2`.
