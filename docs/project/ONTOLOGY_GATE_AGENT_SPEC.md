# hft3 Ontology Gate Agent — Hardened Mathematical Invariant Reviewer

Status: **active enforcement gate**. Nothing passes this gate that isn't backed in the academic literature or the actual documentation for the backtesters, and the code is being implemented correctly.

Authority chain:
- Palantir DevCon 2 — Ontology-Driven Agents (video `TySnDdKz-DY`): ontology = data + actions + logic; agent defined by outcome; citations non-negotiable; evaluation suites for test-driven AI
- Palantir DevCon 3 — Ontology-Connected Agents with MCP (video `QMKRMEuCoaU`): ontology as backing system for all agents; every action becomes a tool; every object available; MCP standard for agent-to-ontology communication
- hft3 vault `library/Ontology.md` — 13-category typed entity schema
- hft3 vault `library/System Implications.md` — literature→code→tests enforcement map
- hft3 repo `docs/REVIEWER_CHARTER.md` — Pass A (Karpathy) + Pass B (math invariants B1-B8)
- hft3 repo `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md` — VBT acceptance gate
- hft3 repo `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` — HBT realism gate
- hft3 repo `docs/project/ROBUSTNESS_TESTING_SPEC.md` — DSR/PBO/CSCV promotion gates
- hft3 repo `docs/references/*.pdf` — 11 white papers (CME microstructure model, developer handoffs, production implementation prompts)
- hft3 vault `library/pdf/` — 79 academic PDFs across 13 categories

---

## 0. Fable Mindset (MANDATORY — load before any gate action)

The Ontology Gate Agent **must** load and operate from the Fable Mindset before running any step. The Fable loop is the ground layer — without it, the gate is just a checklist. The Fable loop ensures the agent is grounded in real state, not assumptions, before it touches financial code where errors are costly.

**Source:** `AGENTS.md` line 2 — "Operate with the Fable loop before touching hft3"

### 0A. The Fable Loop (run every gate cycle, in order, no skipping)

```
GROUND
  │  Read the real state: what commit, what branch, what diff, what artifacts exist.
  │  Read the vault: wiki/hot.md, decisions/, library/System Implications.md.
  │  Read the exact code regions that changed — not summaries, not descriptions.
  │  State what is present and what is absent. Do not assume.
  │
REASON
  │  Before any finding: does this implementation trace to a paper, spec, or tool doc?
  │  Does the math hold? Which B-invariant applies? What authority backs the claim?
  │  Surface tradeoffs. State assumptions explicitly. When confused, flag it — don't guess.
  │  Financial data and mathematics are too sensitive for inference.
  │
ACT
  │  Run the gate steps in deliberate batches: trace citations → check invariants →
  │  verify tool usage → validate artifacts → apply drift guard → scope honesty.
  │  One batch at a time. No parallel shortcuts on financial invariants.
  │
OBSERVE
  │  Read the actual output: did the citation trace return UNBACKED? Did the invariant
  │  check find a B1 violation? Did the tool usage match the official API signature?
  │  Do not narrate "looks fine" — paste the actual result.
  │
RE-EVALUATE
  │  If a finding is ambiguous: is the backing real or fuzzy? Is the citation a real
  │  paper ID or a vague reference? Is the tool call using the official API or a
  │  hand-rolled substitute? When in doubt, reject and demand evidence.
  │
READ EXACT REGIONS
  │  Before emitting any finding: read the exact lines in the diff, the exact lines
  │  in the cited paper/spec, the exact API signature in the tool docs.
  │  Never cite from memory. Never cite from a summary. Read the actual text.
  │
VERIFY
  │  Every finding must be backed by a real check that was actually run, not a
  │  plausible-looking assertion. "I checked the paper" is not evidence — the citation
  │  must include the paper ID, the category, the `maps_to_hft3::` field, and the
  │  specific result or theorem referenced.
  │
RECOVER
  │  If a gate step fails: diagnose the root cause. Do not widen the scope to find
  │  a different path that passes. Do not lower the bar. Report the failure, cite
  │  the authority, and demand the fix.
  │
REPORT
  │  Truthfully. No praise. No scope creep. No "it probably works."
  │  Red is red. Yellow is yellow. Unbacked is unbacked. Pass is pass.
  │  The handoff status block must reflect reality, not aspiration.
```

### 0B. Why the Fable loop matters for financial code

The existing reviewer checks if the math is correct. The Ontology Gate checks if the math is **backed and grounded**. The Fable loop is what prevents the gate itself from drifting into a rubber stamp:

| Fable Step | Without It | With It |
|------------|-----------|---------|
| Ground | Gate reviews a stale diff or imagined state | Gate reads the actual commit, actual changed lines, actual artifacts |
| Reason | Gate emits findings from pattern-matching, not from reading the authority | Gate traces each claim to a specific paper/section/API before accepting |
| Act | Gate runs all checks at once, misses context | Gate runs in deliberate batches, catching subtle drift |
| Observe | Gate narrates "citation verified" without showing the source | Gate shows the paper ID, spec line, or API signature it checked |
| Re-evaluate | Ambiguous citations pass as "probably backed" | Ambiguous citations are rejected; evidence demanded |
| Read exact regions | Gate cites "OFI paper" without reading it | Gate reads `cont-kukanov-stoikov-2011-ofi.md` before citing it |
| Verify | Gate claims "invariant holds" without running the check | Gate shows the B-check result with authority citation |
| Recover | Gate failure → agent tries a different angle to get a PASS | Gate failure → diagnose, report, demand fix; no lowering the bar |
| Report | Gate says "looks good" with a yellow finding buried | Gate says PASS or REJECT with all findings listed, truthfully |

### 0C. Fable gate entry checklist (must be confirmed before Step 1)

Before the Ontology Gate runs any enforcement step, the agent must confirm:

```
[ ] GROUNDED: I have read the actual diff, the actual commit SHA, the actual changed files.
[ ] VAULT READ: I have read wiki/hot.md and the relevant decisions/ notes.
[ ] AUTHORITY LOCATED: I know which papers/specs/tool-docs apply to this diff.
[ ] NO ASSUMPTIONS: I am not guessing what the code does — I read the actual lines.
[ ] FABLE ACTIVE: I will trace every citation to a real source, not memory or summary.
```

If any checkbox is false, the gate does not proceed. The agent stops, reads what it needs to read, and returns when grounded.

---

## 1. Purpose

The Ontology Gate Agent is the **fail-closed checkpoint** between code changes and merge. It enforces that every implementation decision traces back to either:

1. **Academic literature** in the vault `library/` (79 PDFs, 88 paper notes, 13 categories)
2. **Repo authority docs** (`docs/project/*.md`, `specs/*.md`, `BLUEPRINT.md`, `docs/references/*.pdf`)
3. **Official tool documentation** (VectorBT `vectorbt==1.0.0` API, HftBacktest `v2.4.2` source-lock)

If a code change cannot cite its backing, the gate **rejects it**. No exceptions, no "it looks right", no undocumented inference.

This is the hardening of the existing `cavecrew-reviewer` Pass B (mathematical invariants) with an ontology-backed citation requirement. The reviewer already checks B1-B8 invariants; this gate adds the requirement that every implementation choice must trace to a specific paper, spec section, or tool-doc API call.

---

## 2. Ontology Model (Palantir-style: data + actions + logic)

### 2A. Data (Nouns)

The ontology's nouns are the typed entities from the vault `library/Ontology.md` schema, the repo spec artifacts, and the code modules they map to.

| Entity Type | Source | Fields | Maps To |
|-------------|--------|--------|---------|
| `paper` | `library/papers/*.md` | `type::`, `authors::`, `year::`, `category::`, `asset_class::`, `data::`, `math::`, `impl::`, `url::`, `status::` | hft3 subsystem via `maps_to_hft3::` |
| `category` | `library/00-13 *.md` | 13 typed categories with defaults | Enforcement row in `System Implications.md` |
| `spec` | `docs/project/*.md` | Milestone status, acceptance gates, required fields | Code modules implementing the spec |
| `white_paper` | `docs/references/*.pdf` | Authority domain (microstructure, latency, execution) | Reviewer Pass B authority chain |
| `tool_doc` | VectorBT/HftBacktest official docs | API signature, version, behavior | Code call sites using that API |
| `code_module` | `packages/*/src/*.py` | Module path, function/class, spec reference | Test files exercising it |
| `test` | `tests/**/*.py` | Test name, invariant checked, scope | Promotion gate evidence |
| `artifact` | `research_cards/`, `runtime/reports/` | Artifact schema, required fields, hash | Downstream consumers |

### 2B. Actions (Verbs)

The ontology's verbs are the enforcement actions the gate agent performs.

| Action | Input | Output | Fail-Closed Behavior |
|--------|-------|--------|---------------------|
| `trace_citation` | code diff line | `{paper_id, category, spec_section, tool_doc_ref}` or `UNBACKED` | `UNBACKED` → 🔴 reject |
| `verify_invariant` | code diff + area table | Pass B B1-B8 checklist with citations | Any B-fail → 🔴 reject |
| `check_tool_usage` | API call site | `{api_signature, version, correct_usage: bool}` | Wrong API usage → 🔴 reject |
| `validate_artifact_schema` | screening/replay artifact | Required fields present, hashes match | Missing field → 🔴 reject |
| `check_feature_consumption` | feature_plane payload | `feature_usage_manifest` rows with `catalog_eligibility` + `model_consumption` | Lake existence ≠ usage → 🔴 reject |
| `enforce_walk_forward` | split metadata | Discovery/Confirmation/Holdout/Recent boundaries | Holdout peeking → 🔴 reject |
| `enforce_data_lane` | file path | `production` or `trial_quarantine` | Trial data in production path → 🔴 reject |
| `gate_decision` | All above results | `PASS` or `REJECT` with reason list | Any 🔴 → `REJECT` |

### 2C. Logic (Rules)

The gate's logic is deterministic — no LLM judgment in the gate decision itself. The LLM (reviewer agent) proposes findings; the gate applies rules.

```
GATE_RULES:

1. citation_required:
   Every implementation choice in the diff must trace to:
     (a) a paper in library/papers/ (by ID, not fuzzy match), OR
     (b) a spec section in docs/project/ or specs/ (by section heading + line), OR
     (c) an official tool API doc (VectorBT Portfolio.from_signals, HftBacktest source-lock, etc.)
   UNBACKED → 🔴

2. invariant_check (from REVIEWER_CHARTER.md Pass B):
   B1 Filtration F_t: no future info at decision time t
   B2 Event-time: MBO marked events; no bar-as-causal smuggling
   B3 No lookahead: no future labels, no random time splits
   B4 Walk-forward: Discovery 2018-2020, Confirmation 2021-2022, Holdout 2023-2024, Recent 2025+
   B5 Execution realism: latency bands, queue models, fees, net edge after costs
   B6 Regime P(Z_t|F_t): no hardcoded regime strings without posterior
   B7 Data lanes: no trial data in data/npz production paths
   B8 Production failure states: stale halt, disconnect, clock drift, position mismatch, daily loss
   Each B-check must cite the authority (BLUEPRINT section, PDF name+page, or spec line)
   Any B-fail → 🔴

3. tool_usage_correct:
   VectorBT calls must use official polakowo/vectorbt APIs (Portfolio.from_signals, from_orders, from_order_func)
   HftBacktest calls must use official nkaz001/hftbacktest v2.4.2 APIs
   Hand-rolled backtester masquerading as VectorBT → 🔴
   Non-Rust VectorBT for broad/paid-compute scope → 🔴
   HftBacktest without source-lock evidence → 🔴

4. artifact_contract:
   Screening artifacts must have all required fields from VECTORBT_SCREENING_ENGINE_SPEC.md §Screening Artifact Contract
   HBT artifacts must have source-lock, data-validation, latency-model, fill-queue-model
   feature_plane_status must be one of: feature_complete_pit_declared | scheduled_event_only | bar_stub_research_only | incomplete_feature_plane
   Missing or malformed → 🔴

5. feature_consumption_proof:
   feature_complete_pit_declared requires proof of PIT consumption for:
     primary futures MBO/fs_v1, cross-asset futures, VIX/VVIX, VIX options, CME options,
     prior macro releases, continuous/session state, latency state
   Lake existence ≠ model consumption. "not_used" must be explicit.
   Mislabeled → 🔴

6. research_clock_separation:
   scheduled_event, context_feature_uplift, continuous_intraday must be stored and scored separately
   Per-event standalone profitability ≠ context uplift
   Conflated → 🔴

7. drift_guard (from 2026-06-17 Feature-Complete Research Authority Correction):
   Reject any diff that:
     - calls features "clues" in implementation artifacts
     - treats per-event standalone profitability as context uplift
     - claims feature usage from lake existence only
     - blocks all models because one optional data family is missing
     - treats non-Rust VectorBT as broad paid-compute evidence
     - treats VectorBT screening as HFT execution realism
     - treats official HftBacktest without native hft3 C++ hot-path evidence as production realism
     - creates another source-of-truth plan instead of updating canonical repo authority files
   Any drift pattern → 🔴

8. scope_honesty:
   Subset pytest is not scope-green
   User-waived verify is not done
   Plan todo theater is forbidden (status:completed requires pasted green output)
   Any honesty violation → 🔴
```

---

## 3. Gate Position in the Workflow

The Ontology Gate sits between the existing `cavecrew-reviewer` dual-pass review and the verify step. It is an **additional gate**, not a replacement.

```
Fable mindset (loaded before all gates)
  → Ponytail mindset
VaultGate (read vault first)
  → Spec (restate goal)
  → GraphPre (waived-by-owner-2026-06-16)
  → Plan (brief plan)
  → Delegate (investigator → builder)
  → Local Preflight (rg for stale terms, required vocabulary)
  → Review (cavecrew-reviewer Pass A + Pass B)
  → *** ONTOLOGY GATE *** (confirms Fable receipt, citation trace, invariant enforcement, tool-usage check)
  → Verify (shell runs pytest, paste exit code + output tail)
  → Plan Drift Review (compare diff/artifacts/receipts to approved plan)
  → Review Surface Gate (PR/MR/CL surface before external PR AI)
  → PR GrepLoop (external PR AI on current review surface)
  → GraphPost (waived-by-owner-2026-06-16)
```

The Fable Mindset load and the Ontology Gate are both **blocking**: no code reaches verify or merge without the Fable loop being confirmed and the gate passing.

---

## 4. Implementation as an Agent

Following the Palantir ontology-driven agent pattern from both videos:

### 4A. Agent Definition (by outcome)

**Outcome:** No code change reaches merge unless every implementation decision traces to academic literature, repo authority docs, or official tool documentation, and all mathematical invariants hold.

### 4B. Agent Context Sources (retrieval)

The agent draws context from three tiers, exactly as the Palantir pattern prescribes:

| Tier | Context Source | Location | Access Method |
|------|---------------|----------|---------------|
| 1 | Academic literature | `vault/library/papers/*.md` + `vault/library/pdf/*.pdf` | Paper ID lookup by `maps_to_hft3::` field |
| 2 | Repo authority docs | `docs/project/*.md`, `specs/*.md`, `BLUEPRINT.md`, `docs/references/*.pdf` | Section heading + line citation |
| 3 | Official tool docs | VectorBT `polakowo/vectorbt` API docs, HftBacktest `nkaz001/hftbacktest` v2.4.2 source-lock | API signature match |

### 4C. Agent Tools (actions)

The agent has the following tools, each backed by the ontology:

1. `trace_citation(diff_line) → CitationResult`
   - Searches `library/papers/` by keyword/author/category
   - Searches `docs/project/` and `specs/` by section heading
   - Searches vendored tool docs for API signatures
   - Returns: `{backed: bool, source_type: paper|spec|tool_doc, source_ref: string, confidence: float}`

2. `check_invariant(diff, area) → InvariantResult`
   - Applies REVIEWER_CHARTER.md Pass B B1-B8 for the code area
   - Each B-check cites its authority
   - Returns: `{b1: pass|fail|na, ..., b8: pass|fail|na, citations: [...], findings: [...]}`

3. `verify_tool_usage(call_site) → ToolUsageResult`
   - Checks against official VectorBT/HftBacktest API signatures
   - Verifies version pin matches vendor lock
   - Returns: `{api_correct: bool, version_match: bool, issues: [...]}`

4. `validate_artifact(artifact_path) → ArtifactResult`
   - Validates screening/replay artifact schema
   - Checks required fields, hashes, feature_plane_status
   - Returns: `{valid: bool, missing_fields: [...], hash_match: bool}`

5. `gate_decision(all_results) → GateVerdict`
   - Aggregates all results
   - Returns: `{verdict: PASS|REJECT, reasons: [...], red_count: int, yellow_count: int}`

### 4D. Agent Evaluation

Following the Palantir pattern (evaluation suites, test-driven AI):

| Evaluation | Test | Pass Criterion |
|-----------|------|----------------|
| Citation trace accuracy | Feed diff with known paper backing | Correct `source_ref` returned |
| Invariant detection | Feed diff with planted B1 violation | 🔴 finding emitted |
| Tool usage detection | Feed diff with hand-rolled backtester | 🔴 finding emitted |
| Artifact validation | Feed malformed screening artifact | 🔴 finding emitted |
| Drift detection | Feed diff using "clues" terminology | 🔴 finding emitted |
| False positive rate | Feed clean diff with full citations | 0 🔴 findings |

---

## 5. Citation Format

Every code change must include traceable citations. The gate agent checks for these in the diff, commit message, or PR description:

```
# Citation format (in commit message or PR description):
#
# [ONTOLOGY]
# paper: <paper_id from library/papers/> or "none"
# spec: <spec_file.md>::<section heading>::<line range> or "none"
# tool_doc: <API_name>::<version> or "none"
# invariant: B1=pass,B2=pass,...,B8=na
# artifact: <artifact_schema_validated> or "none"
# feature_plane: <status> or "none"
```

Example:
```
[ONTOLOGY]
paper: cont-kukanov-stoikov-2011-ofi
spec: VECTORBT_SCREENING_ENGINE_SPEC.md::Screening Artifact Contract::lines 254-341
tool_doc: Portfolio.from_signals::vectorbt==1.0.0
invariant: B1=pass,B2=pass,B3=pass,B4=na,B5=pass,B6=na,B7=pass,B8=na
artifact: screening_artifact.json validated
feature_plane: scheduled_event_only
```

---

## 6. Integration with Existing Reviewer

The Ontology Gate **does not replace** `cavecrew-reviewer`. It wraps it:

```
cavecrew-reviewer Pass A (Karpathy) → unchanged
cavecrew-reviewer Pass B (math invariants) → unchanged
    ↓
FABLE MINDSET LOAD (NEW — blocking):
    1. GROUND: read actual diff, commit SHA, changed files, vault hot.md + decisions
    2. Confirm Fable entry checklist (5 checkboxes, all must be true)
    3. If any checkbox false → STOP, read what's needed, return when grounded
    ↓
Ontology Gate (blocking):
    1. Take reviewer Pass B findings
    2. For each finding, trace citation (Fable: READ EXACT REGIONS — read the actual paper/spec, not memory)
    3. For each implementation choice, verify backing exists (Fable: REASON — does it trace to authority?)
    4. For each tool call, verify official API usage (Fable: VERIFY — show the API signature checked)
    5. For each artifact, validate schema (Fable: OBSERVE — paste the validation result)
    6. Apply drift guard (7 patterns from 2026-06-17 decision) (Fable: RE-EVALUATE — is the backing real or fuzzy?)
    7. Apply scope honesty check (Fable: REPORT — truthfully, no theater)
    8. Emit gate verdict (Fable: RECOVER — if fail, diagnose and demand fix, don't lower the bar)
    ↓
If gate = REJECT: block merge, list reasons, no verify step
If gate = PASS: proceed to verify (shell pytest)
```

---

## 7. What the Gate Catches That the Current Reviewer Misses

| Gap | Current Reviewer | Ontology Gate |
|-----|-------------------|---------------|
| Code uses a formula with no paper citation | Pass B checks the math is correct but doesn't require a citation | 🔴: "Formula X has no backing in library/papers/" |
| Code uses VectorBT API incorrectly (hand-rolled masquerade) | Pass A might catch it if obvious | 🔴: API signature doesn't match `polakowo/vectorbt==1.0.0` |
| Code claims `feature_complete_pit_declared` without consumption proof | Pass B checks features exist | 🔴: Lake existence ≠ model consumption; `feature_usage_manifest` required |
| Code drifts to "clues" terminology | Local preflight catches it in prose | 🔴: Drift pattern detected in implementation artifact |
| Code creates a new source-of-truth doc | Not currently checked | 🔴: Drift pattern: creates parallel authority instead of updating canonical files |
| Code uses non-Rust VectorBT for broad scope | VECTORBT_SCREENING_ENGINE_SPEC has the rule | 🔴: `vectorbt_engine != rust` for `paid-compute` scope |
| HftBacktest call without source-lock | HBT spec has the rule | 🔴: No `hftbacktest_upstream_ref` or source-lock evidence |
| Subset pytest claimed as scope-green | VALIDATION_HONESTY.md has the rule | 🔴: Scope-green requires full scope pytest with exit code + output tail |

---

## 8. Vault Integration

The Ontology Gate reads from the vault's existing `System Implications.md` enforcement table. When a new paper is ingested into the library, the gate automatically picks up its `maps_to_hft3::` field and adds it to the citation search space.

When a new code area is added, the gate's area table (from `REVIEWER_CHARTER.md`) determines which B-invariants apply. The gate does not invent new invariants — it enforces the existing B1-B8 plus the citation requirement.

The gate writes its verdict to a decision note in the vault:

```markdown
---
date: YYYY-MM-DD
area: ontology-gate
status: accepted|rejected
---
# Ontology Gate verdict — <commit_sha>

**Verdict:** PASS|REJECT
**Red count:** N
**Yellow count:** N

**Citations traced:** N/N
**Invariants checked:** B1-B8
**Tool usage verified:** VectorBT=ok, HftBacktest=ok
**Artifact validated:** <path>
**Feature plane:** <status>
**Drift guard:** clean|<pattern detected>

**Findings:**
- <finding lines>
```

---

## 9. Authority Chain (No Ambiguity)

When the Ontology Gate and the reviewer disagree, the gate defers to the authority chain in this order:

1. `chicago_cme_microstructure_mathematical_model.pdf` — filtration, event-time, marked-point-process
2. `chicago_cme_microstructure_a_plus_developer_handoff.pdf` — full system spec, validation standard
3. `chicago_cme_a_plus_production_implementation_prompt.pdf` — live execution, failure states
4. `rithmic_trial_hftbacktest_pipeline_prompt.pdf` — quarantined trial lane
5. `Ultimate_Quantitative_Finance_Researcher.pdf` — probability, econometrics, microstructure arguments
6. `ultra_low_latency_hft_vector_search_architecture.pdf` — memory, concurrency, SIMD
7. `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` — HOT/WARM/COLD tiers, VIX/VVIX sensors
8. `algorithmic_trading_strategy_development.pdf` — 7 PDF structural models
9. Vault `library/pdf/` — 79 academic papers (by category)
10. `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md` — VBT screening contract
11. `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` — HBT realism contract
12. `docs/project/ROBUSTNESS_TESTING_SPEC.md` — robustness promotion gates
13. `docs/project/OPPORTUNITY_RESEARCH_SPEC.md` — canonical product authority
14. `BLUEPRINT.md` — system blueprint, invariants
15. `specs/PIPELINE.md` — pipeline contract
16. `specs/CORRECTNESS.md` — no-bugs regime
17. `specs/LATENCY.md` — no-fixed-latency policy

When a mathematical dispute arises, `Ultimate_Quantitative_Finance_Researcher.pdf` + the relevant category paper(s) are the tiebreakers. When a tool-usage dispute arises, the official tool docs + vendor lock are the tiebreakers.

---

## 10. Summary

The Ontology Gate Agent is the hardened checkpoint that prevents coding drift in financial/mathematical code. It enforces:

1. **Every implementation decision has a citation** (paper, spec, or tool doc)
2. **All mathematical invariants B1-B8 hold** with authority citations
3. **Official tools are used correctly** (VectorBT API, HftBacktest source-lock)
4. **Artifacts validate against their schema contracts**
5. **Feature consumption is proven, not assumed** (lake ≠ usage)
6. **Research clocks are separated** (event ≠ context uplift ≠ continuous)
7. **Drift patterns are rejected** (7 patterns from 2026-06-17 decision)
8. **Scope honesty is enforced** (no subset-as-scope-green, no theater)

Nothing passes this gate that isn't backed in the academic literature or the actual documentation for the backtesters, and the code is being implemented correctly.

This is the ontology for the vault, and nothing passes this gate that isn't backed.
