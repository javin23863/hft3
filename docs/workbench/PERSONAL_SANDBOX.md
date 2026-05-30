# Personal sandbox (local replay lock)

Authority: `BLUEPRINT.md` §8, `docs/REVIEWER_CHARTER.md` B3/B4/B7.

## Purpose

Quarantine **2026-03-01 … 2026-05-30** local replay data from walk-forward **promotion**. Personal runs are evaluate-only and never affect `promote_candidate`.

## Config

- `workbench/config/personal_lock.yaml` — date range, default lock, artifact root
- `workbench/config/walk_forward.yaml` — `personal_sandbox` block mirrors dates

## Lock behavior

| State | Promotion campaigns | Personal tab / `--personal` |
|-------|---------------------|-----------------------------|
| **Locked (default)** | 2026 sandbox dates excluded from `list_campaign_events(..., mode="promotion")` | Hidden |
| **Unlocked** | Still excluded from promotion | `list_personal_events()` visible; artifacts under `research_cards/workbench_personal/` |

Lock marker: `.workbench_personal_unlock` at repo root (local only).

**The lock does not SSH to CHI404 or start colo processes.**

## UI

Streamlit sidebar: **Personal sandbox lock** toggle (red when locked).

**Personal Runs** tab shows sandbox events when unlocked.

## CLI

Personal replay uses the same event catalog with `mode="personal"` (future `--personal` flag on `workbench run`).

Metadata on personal artifacts: `promotion_eligible: false`.
