# Grep Loop Workflow

Purpose: distinguish local preflight hygiene from the external PR AI review
GrepLoop. Codex self-review is not a substitute. Local `rg` preflight is
required after every repo edit, including docs-only edits, before reviewer time,
but it is not GrepLoop.

Local preflight does not replace VaultGate, GraphGate, reviewer, pytest, or
GraphPost. It is a cheap negative-search pass that catches stale terminology, old API
fields, missing proof rows, and review-drift before the heavier gates run.

External pattern: **Greptile** (`@greptileai`) is the **only** connector that
satisfies the PR GrepLoop gate for this repo (developer assignment §23). Local
Codex self-review, GitHub Actions `@codex review`, and the ChatGPT/Codex
Connector do **not** count — they may run in parallel but never substitute for
Greptile sign-off.

Video-derived additions from `https://youtu.be/WIDIV8oDDC8`:

- `04:40-07:00`: read review comments, fix actionable issues, push, wait for
  the new review, and repeat until the external reviewer reports clean or a
  bounded turn limit is reached.
- `23:48-28:00`: use the PR review surface as external feedback; do not treat
  IDE-local or agent-local confidence as enough.
- `29:09-31:00`: split oversized review surfaces. A change above roughly 1000
  lines, or one spanning multiple subsystems, should be split where possible so
  each unit has one coherent review surface.
- `34:10+`: keep PRs minimal, structured, and documented so both humans and
  agents can find artifacts later.

## Position

**cavecrew-reviewer runs during build** (every code-change batch in Phases
1–8). **Greptile PR GrepLoop runs LAST** (Phase 9 only) — never interleaved
with implementation phases.

**Stacked split PRs (A→B→C):** do **not** ping `@greptileai` on PR-B or PR-C
until the prior PR in the stack reaches **≥ 4/5 Greptile confidence** with
**zero unresolved actionable** findings on **current head SHA**. Premature
Greptile on downstream PRs does not count toward merge-ready and must be
paused with an explicit PR comment.

Run local preflight after each edit pass and before claiming the diff is ready
for the dual-pass reviewer:

```text
VaultGate -> GraphGate -> GraphPre -> Plan -> Code -> Local Preflight -> Review (cavecrew) -> Verify -> PR GrepLoop (Greptile, Phase 9) -> GraphPost
```

If reviewer or tests find issues, fix them and run the relevant local preflight
again before the next review. Greptile is **not** a substitute for
cavecrew-reviewer during build; cavecrew-reviewer is **not** a substitute for
Greptile at Phase 9.

Assignment authority: [§23 Greptile PR GrepLoop](../project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md#23-mandatory-greptile-pr-greptile-loop).

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

## PR GrepLoop (Greptile-only)

Required in addition to local preflight when there is an actual PR/MR/CL review
surface. **Only Greptile** satisfies this gate:

| Reviewer | Counts for PR GrepLoop? |
|----------|-------------------------|
| **Greptile** (`@greptileai` on a PR with ≤100 changed files) | **Yes** |
| Codex connector / `@codex review` / `request-codex-review` GitHub Action | **No** |
| ChatGPT-Codex-Connector / Copilot PR review | **No** |
| Agent self-review or dual-pass cavecrew-reviewer | **No** (required separately) |

Greptile hard limit: **100 changed files** per review. Target **<80 files** per
PR; split stacked PRs (A→B→C) and trigger `@greptileai` on **one PR at a time**
after fixes land — do not batch-review the monolith.

If no PR exists, or Greptile is not installed/authenticated, record
`pr-ai-review: unavailable(no-pr|no-greptile|not-authenticated)`; do not pretend
Codex review or local agent review satisfied this gate.

1. Detect the PR for the current branch:

```powershell
gh pr view --json number,headRefName,headRefOid
```

2. Push current work, then trigger Greptile **only** (one PR at a time):

```powershell
git push
gh pr comment <PR_NUMBER> --body "@greptileai"
```

Do **not** post `@codex review` to satisfy PR GrepLoop. The
`request-codex-review` GitHub Action may still run automatically; treat its
output as advisory only.

When `.github/workflows/codex_pr_review.yml` is present, it posts `@codex review`
automatically. That workflow is **not** PR GrepLoop evidence.

3. Fetch all current review surfaces, especially the latest AI reviewer general
   PR comment by `updated_at`, because bot summaries may be edited in place:

```powershell
gh pr view <PR_NUMBER> --json body,reviews,comments,statusCheckRollup
gh api --paginate "repos/{owner}/{repo}/issues/<PR_NUMBER>/comments?per_page=100"
gh api "repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments"
```

4. Fix only actionable comments. Treat informational comments or false
   positives as review notes, not architecture changes.

5. Stop when **Greptile** on **current head SHA** reports:
   - confidence **≥ 4/5** (4/5 or 5/5 in Greptile summary when present), **and**
   - zero unresolved actionable findings, **and**
   - scope-appropriate verification green.

   Run at most **five** Greptile fix iterations per PR. On max-iteration stop,
   report remaining unresolved comments and do not claim merge-ready. Do not
   advance to the next split PR (PR-B/C) or Phase 10 until the current PR
   meets **≥ 4/5 confidence + zero actionable** on current head.

### Stacked PR gate (A→B→C)

| Prior PR | Before Greptile on next PR |
|----------|---------------------------|
| PR-A (#8) | PR-B (#9) blocked until PR-A **≥ 4/5** + 0 actionable |
| PR-B (#9) | PR-C (#10) blocked until PR-B **≥ 4/5** + 0 actionable |

If an agent prematurely triggers Greptile on a downstream PR, post a pause
comment and resume PR-A (or the lowest incomplete PR) only.

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
  AI/human review until proven otherwise.
- Each split unit should have one coherent purpose, one verification surface,
  and one GrepLoop report.

## HFT3 Guardrails

- VaultGate and GraphGate still come first. GrepLoop is not permission for
  blind repo spelunking.
- Search output is evidence, not proof of correctness. Tests, external review
  where available, and reviewer verdict still decide merge-ready status.
- Codex self-review, `@codex review`, Codex Connector, or a prose summary does
  **not** satisfy the PR GrepLoop gate — **Greptile only**.
- For finance/math changes, search for stale units, timestamp fields,
  old feature names, missing source IDs, fake GREEN status, and unverified
  robustness claims.
- Keep shell commands bounded. No unbounded polling or overnight waits.
- Do not route live/paper data or orders through workstation workflow helpers.

## Handoff Fields

Every handoff after a repo edit must include:

```text
local-preflight: run | waived-by-user
patterns: <patterns searched>
hits: 0 | <summary>
pr-ai-review: run | unavailable(no-pr|no-connector|not-authenticated) | waived-by-user
review-surface: <files/changed-lines>; split-needed yes|no
remaining-risk: <none or blocker>
```

If `local-preflight` is anything other than `run`, report `merge-ready: no`.
