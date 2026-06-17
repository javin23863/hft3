# Grep Loop Workflow

Purpose: distinguish local preflight hygiene from the external Greptile PR
GrepLoop. Codex self-review is not a substitute. Local `rg` preflight is
required after every repo edit, including docs-only edits, before reviewer time,
but it is not GrepLoop.

Local preflight does not replace VaultGate, GraphGate, reviewer, pytest, or
GraphPost. It is a cheap negative-search pass that catches stale terminology, old API
fields, missing proof rows, and review-drift before the heavier gates run.

External pattern: Greptile's `greploop` skill
(`https://github.com/greptileai/skills`) triggers review, fetches the latest
review result, fixes actionable comments, re-reviews, and stops at a perfect
confidence score with zero unresolved comments or a bounded iteration limit.
HFT3 adapts that loop shape to Codex while preserving the project ontology:
VaultGate first, local grep evidence always, and Greptile PR review whenever a
PR/MR/CL review surface exists.

Video-derived additions from `https://youtu.be/WIDIV8oDDC8`:

- `04:40-07:00`: read review comments, fix actionable issues, push, wait for
  the new review, and repeat until 5/5 or a bounded turn limit.
- `23:48-28:00`: use the GitHub/Greptile review surface as external feedback;
  do not treat IDE-local or agent-local confidence as enough.
- `29:09-31:00`: split oversized review surfaces. A change above roughly 1000
  lines, or one spanning multiple subsystems, should be split where possible so
  each unit has one coherent review surface.
- `34:10+`: keep PRs minimal, structured, and documented so both humans and
  agents can find artifacts later.

## Position

Run local preflight after each edit pass and before claiming the diff is ready
for the dual-pass reviewer:

```text
VaultGate -> GraphGate -> GraphPre -> Plan -> Code -> Local Preflight -> Review -> Verify -> PR GrepLoop -> GraphPost
```

If reviewer or tests find issues, fix them and run the relevant local preflight
again before the next review.

If local preflight was not run, the change is not merge-ready. The only allowed
exception is an explicit user waiver, and that still reports `merge-ready: no`.

## Local Preflight Hygiene

Mandatory for every repo edit. Use `rg`, not broad manual reading. Keep the
patterns task-specific.

1. Define the loop contract before editing:
   - Required vocabulary, for example `feature` rather than informal wording.
   - Forbidden legacy fields, strings, or API keys.
   - Required authority/citation rows when docs or finance logic change.
   - Required fail-closed states and tests.

2. After the edit, run narrow negative searches:

```powershell
rg -n "<forbidden-term>|<old-field>|<stale-label>" <changed-scope>
rg -n "<required-new-term>|<required-field>|<authority-row>" <changed-scope>
git diff --check
```

3. Inspect non-empty hits:
   - Actionable hit: patch it.
   - Intentional hit: keep it only if the surrounding text clearly explains why.
   - Ambiguous hit in finance/math code: fail closed and review before moving on.

4. Repeat at most three local iterations. If still not clean, stop and report
   the remaining hits as blockers instead of widening blindly.

5. Then run the normal HFT3 gates:
   - Dual-pass reviewer when code changed.
   - Scope-appropriate pytest/build commands.
   - `scripts/graphify_rebuild.ps1` after code edits when graph files are tracked.

## PR GrepLoop

Required in addition to local preflight when there is an actual PR/MR/CL review
surface and Greptile is installed for the repo. If no PR exists, or Greptile is
not installed/authenticated, record `pr-greptile: unavailable(...)`; do not
pretend local Codex review satisfied this external review gate.

1. Detect the PR for the current branch:

```powershell
gh pr view --json number,headRefName,headRefOid
```

2. Push current work, then trigger or wait for Greptile:

```powershell
git push
gh pr comment <PR_NUMBER> --body "@greptileai"
```

3. Fetch all current review surfaces, especially the latest Greptile general
   PR comment by `updated_at`, because bot summaries may be edited in place:

```powershell
gh pr view <PR_NUMBER> --json body,reviews,comments,statusCheckRollup
gh api --paginate "repos/{owner}/{repo}/issues/<PR_NUMBER>/comments?per_page=100"
gh api "repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments"
```

4. Fix only actionable comments. Treat informational comments or false
   positives as review notes, not architecture changes.

5. Stop when Greptile reports 5/5 confidence with zero unresolved comments,
   or after five PR review iterations. On max-iteration stop, report remaining
   unresolved comments and do not claim merge-ready.

## Review Surface Size

Before opening or updating a PR/MR/CL, check the size and shape of the review
surface:

```powershell
git diff --stat
git diff --numstat
```

- Prefer a few hundred changed lines per review surface.
- Above roughly 1000 changed lines, or any change spanning unrelated
  subsystems, plan a split unless the user explicitly approves the larger unit.
- Above roughly 2000 changed lines, assume the surface is too large for reliable
  Greptile/human review until proven otherwise.
- Each split unit should have one coherent purpose, one verification surface,
  and one GrepLoop report.

## HFT3 Guardrails

- VaultGate and GraphGate still come first. GrepLoop is not permission for
  blind repo spelunking.
- Search output is evidence, not proof of correctness. Tests, external review
  where available, and reviewer verdict still decide merge-ready status.
- Codex self-review, a prose summary, or "looks good" does not satisfy
  GrepLoop.
- For finance/math changes, search for stale units, timestamp fields,
  old feature names, missing source IDs, fake GREEN status, and unverified
  robustness claims.
- Keep shell commands bounded. No unbounded polling or overnight waits.
- Do not route live/paper data or orders through workstation workflow helpers.

## Handoff Fields

Every handoff after a repo edit must include:

```text
grep-loop: run | waived-by-user
patterns: <patterns searched>
hits: 0 | <summary>
pr-greptile: run | unavailable(no-pr|not-installed|not-authenticated) | waived-by-user
review-surface: <files/changed-lines>; split-needed yes|no
remaining-risk: <none or blocker>
```

If `grep-loop` is anything other than `run`, report `merge-ready: no`.
