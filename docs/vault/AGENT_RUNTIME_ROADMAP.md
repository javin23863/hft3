# Agent Runtime Roadmap — any LLM, every session

**Obsidian canonical copy:** `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\architecture\Agent Runtime Roadmap.md`
**Vault hot cache:** `wiki/hot.md` (top section, updated 2026-06-20)
**Decision:** Obsidian `decisions/2026-06-20 Agent runtime Fable Ponytail load order.md`

This repo mirror exists so agents with repo access only still see the roadmap. Keep in sync with the Obsidian note when load order changes.

---

## Session load order (non-negotiable)

| # | Context | Path |
|---|---------|------|
| **1** | **Fable mindset** | `.cursor/rules/00-fable-mindset.mdc` · [FABLE_MINDSET.md](FABLE_MINDSET.md) |
| **2** | **Ponytail mindset** | `.cursor/rules/01-ponytail-mindset.mdc` · [docs/ai/PONYTAIL.md](../ai/PONYTAIL.md) · `vendor/ponytail/` |
| **3** | Vault ontology | `scripts/vault_gate.ps1` · Obsidian `wiki/hot.md` |
| **4** | Code graph | Waived: `waived-by-owner-2026-06-16` — targeted source reads |
| **5** | Task loop | [AGENTS.md](../../AGENTS.md) · [docs/ai/ONBOARDING.md](../ai/ONBOARDING.md) |

**Blocking:** Steps 1–2 before any codebase touch. Step 3 before edits.

---

## Quick charter links

- Fable full reference: `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md`
- Ponytail upstream: https://github.com/DietrichGebert/ponytail
- Validation honesty: [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md)
- Topology: [BLUEPRINT.md](../../BLUEPRINT.md) §4 — CHI404 only for CME live
