# Grep Loop Workflow

Purpose: distinguish local preflight hygiene from the external PR AI review
GrepLoop. Codex self-review is not a substitute. Local `rg` preflight is
required after every repo edit, including docs-only edits, before reviewer time,
but it is not GrepLoop.

Local preflight does not replace VaultGate, GraphGate, reviewer, pytest, or
GraphPost. It is a cheap negative-search pass that catches stale terminology, old API
fields, missing proof rows, and review-drift before the heavier gates run.

External pattern: PR GrepLoop is the installed external PR/MR/CL AI review
loop on a real review surface. Prefer **Greptile** when it is installed; if it
is not installed for this repo, use the repo's installed external PR AI
connector instead, such as ChatGPT Codex Connector, GitHub Copilot PR review,
or an equivalent GitHub-integrated reviewer. Local Codex self-review and local
`rg` preflight do **not** count.

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
1–8). **External PR AI GrepLoop runs LAST** on the review surface (Phase 9
only) — never interleaved with implementation phases.

**Stacked split PRs (A→B→C):** do **not** trigger the external PR AI reviewer
on PR-B or PR-C until the prior PR in the stack reaches the repo's accepted
clean threshold on **current head SHA** with **zero unresolved actionable**
findings. For Greptile, that means **5/5 confidence** unless an active plan
explicitly records a different owner-approved threshold. Premature review on
downstream PRs does not count toward merge-ready and must be paused with an
explicit PR comment.

Run local preflight after each edit pass and before claiming the diff is ready
for the dual-pass reviewer:

```text
VaultGate -> GraphGate/GraphPre when active -> Plan -> Code -> Local Preflight -> Review (cavecrew) -> Verify -> Plan Drift -> Review Surface -> PR GrepLoop (external PR AI, Phase 9) -> GraphPost when active
```

If reviewer or tests find issues, fix them and run the relevant local preflight
again before the next review. External PR AI is **not** a substitute for
cavecrew-reviewer during build; cavecrew-reviewer is **not** a substitute for
PR GrepLoop at Phase 9.

Authority: vault decision `2026-06-17 GrepLoop connector generalization`
supersedes `2026-06-16 GrepLoop process correction`; assignment background:
[§24 External PR AI GrepLoop](../project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md#24-mandatory-external-pr-ai-greploop).

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
   - `scripts/graphify_rebuild.ps1` after code edits only when graph gates are
     active. While `waived-by-owner-2026-06-16` is active, report the waiver and
     do not claim graph freshness.

## PR GrepLoop (External PR AI)

Required in addition to local preflight on an actual PR/MR/CL review surface.
An external connector must be independent of the local agent loop:

| Reviewer | Counts for PR GrepLoop? |
|----------|-------------------------|
| **Greptile** (`@greptileai` or repo-specific trigger) | **Yes, preferred when installed** |
| Installed ChatGPT Codex Connector / GitHub Copilot PR review / equivalent GitHub-integrated external AI reviewer | **Yes, when that is the repo's available connector** |
| `request-codex-review` GitHub Action with no external PR review surface | **No** |
| Agent self-review or dual-pass cavecrew-reviewer | **No** (required separately) |

When using Greptile, respect its hard limit: **100 changed files** per review.
Target **<80 files** per PR; split stacked PRs (A→B→C) and trigger the external
reviewer on **one PR at a time** after fixes land — do not batch-review the
monolith.

If no PR/MR/CL review surface exists after Plan Drift Review passes, create or reuse a
branch plus review surface before claiming merge-ready. If publishing is
blocked or owner-forbidden, record `pr-ai-review: unavailable(no-pr)` and
`merge-ready: no`. If the connector is missing or unauthenticated, record
`pr-ai-review: unavailable(no-connector|not-authenticated)`. Do not pretend
local Codex review or local agent review satisfied this gate.

1. Detect the PR for the current branch:

```powershell
gh pr view --json number,headRefName,headRefOid
```

2. Push current work, then trigger the installed external PR AI connector (one
   PR at a time):

```powershell
git push
gh pr comment <PR_NUMBER> --body "@greptileai"
```

The exact trigger is connector-specific. Use `@greptileai` only when Greptile
is installed for the repo. Do **not** treat local Codex review as PR GrepLoop.
If a GitHub-integrated Codex/Copilot connector is the installed external
reviewer, use its PR-surface output; otherwise treat Codex output as advisory
only.

When `.github/workflows/codex_pr_review.yml` is present, it posts `@codex review`
automatically. That workflow is **not** PR GrepLoop evidence unless it is wired
to an installed external PR review connector and produces PR-surface feedback.

3. Fetch all current review surfaces, especially the latest AI reviewer general
   PR comment by `updated_at`, because bot summaries may be edited in place:

```powershell
gh pr view <PR_NUMBER> --json body,reviews,comments,statusCheckRollup
gh api --paginate "repos/{owner}/{repo}/issues/<PR_NUMBER>/comments?per_page=100"
gh api "repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments"
```

4. Fix only actionable comments. Treat informational comments or false
   positives as review notes, not architecture changes.

5. Stop when the external reviewer on **current head SHA** reports:
   - clean status; for Greptile, confidence **5/5** unless an active plan records
     an owner-approved alternate threshold, **and**
   - zero unresolved actionable findings, **and**
   - scope-appropriate verification green.

   Run at most **five** external PR AI fix iterations per PR. On max-iteration stop,
   report remaining unresolved comments and do not claim merge-ready. Do not
   advance to the next split PR (PR-B/C) or Phase 10 until the current PR
   meets the accepted clean threshold plus zero actionable findings on current
   head.

### Stacked PR gate (A→B→C)

| Prior PR | Before external PR AI on next PR |
|----------|---------------------------|
| PR-A (#8) | PR-B (#9) blocked until PR-A is clean + 0 actionable |
| PR-B (#9) | PR-C (#10) blocked until PR-B is clean + 0 actionable |

If an agent prematurely triggers external PR AI on a downstream PR, post a
pause comment and resume PR-A (or the lowest incomplete PR) only.

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

- VaultGate still comes first; GraphGate/GraphPre/GraphPost run only when graph
  gates are active. GrepLoop is not permission for blind repo spelunking.
- Search output is evidence, not proof of correctness. Tests, external review
  where available, and reviewer verdict still decide merge-ready status.
- Codex self-review, a local prose summary, or local `rg` output does **not**
  satisfy the PR GrepLoop gate; only an installed external PR/MR/CL AI connector
  on a current review surface counts.
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
pr-ai-review: pending | run | unavailable(no-pr|no-connector|not-authenticated) | waived-by-user
review-surface: <PR/MR/CL URL or id>; head=<sha>; split-needed yes|no | none(blocked: <reason>) | none(waived-by-user: <reason>)
remaining-risk: <none or blocker>
```

If `local-preflight` is anything other than `run`, report `merge-ready: no`.
If `pr-ai-review` is `unavailable(no-pr)`, report `merge-ready: no`. If the
owner explicitly waives the external PR AI gate, use `pr-ai-review:
waived-by-user` and `review-surface: none(waived-by-user: <reason>)`.
