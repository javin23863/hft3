# Ontology citations — hft3 ODL extension pack

> Source of truth for grounding every `integrations/openfoundry/domain-packs/hft3/schema/*.odl`
> extension in the canonical math-model PDF. Read by `validate_ontology_citations()` in
> `packages/data_layer/openfoundry_bridge.py` (planned phase 5).
>
> See also: [`docs/references/MANIFEST.md`](../references/MANIFEST.md) (after-action packet field citations),
> [`docs/structural_models/PDF_MODELS.md`](../structural_models/PDF_MODELS.md) (per-model formula spec).

---

## Why this file exists

Every hft3 ODL extension declared in
[`integrations/openfoundry/hft3-cme-mbo.yaml`](../../integrations/openfoundry/hft3-cme-mbo.yaml) is
a claim about a microstructure concept. If the LLM-side code uses that extension to make a
narrative claim, the extension itself must be grounded in a real PDF section — otherwise the
LLM is hallucinating the math.

A sidecar citation (`citations/<extension>.yaml`) is the **only** acceptable way to attach a
PDF citation to an ODL extension. See "ODL parser leniency" below for the reason.

---

## ODL parser leniency (Phase 1 finding)

The vendor ODL parser at
[`vendor/openfoundry/packages/odl/src/parser/index.ts`](../../vendor/openfoundry/packages/odl/src/parser/index.ts)
has a **hard-coded directive allowlist**. Unknown directives (`@pdf_cite(...)`, `@cite(...)`,
etc.) are **silently dropped** by the parser — no error, no warning. The Python bridge
`packages/data_layer/openfoundry_bridge.py` is **not** an ODL parser; it only handles
connector YAML and the vendor lock file.

Therefore:

- ❌ Inline `@pdf_cite(pdf: "...", section: ..., page: ...)` would be silently discarded.
- ✅ Sidecar `citations/<extension>.yaml` is the only reliable channel.
- ✅ The validate step in phase 5 must read the sidecar, not the parsed ODL.

The ODL files themselves can still use **recognized** directives (`@primary`, `@readonly`,
`@indexed`, etc.) — those are preserved by the parser.

---

## Citation table — 9 hft3 ODL extensions → math model PDF

Primary PDF: [`chicago_cme_microstructure_mathematical_model.pdf`](../references/chicago_cme_microstructure_mathematical_model.pdf)
(text-extractable, 9 pages). When an extension is also covered by the structural-models PDF
([`algorithmic_trading_strategy_development.pdf`](../references/algorithmic_trading_strategy_development.pdf)),
both are cited; **the more specific citation wins** per BLUEPRINT.md §2 invariant precedent.

| # | ODL extension | Math-model PDF § | Page | Structural PDF § | Notes |
|---|---------------|------------------|------|-------------------|-------|
| 1 | `MarkedMicroEvent` | §3 Limit Order Book · §4 MBO Marked Point Process | 2 | — | Filtration integrity (BLUEPRINT §2 #1); timestamp 1 of 33 |
| 2 | `BookSnapshotAtDecision` | §3 Limit Order Book | 1–2 | — | LOB state at decision time; same section as #1 |
| 3 | `QueuePositionEstimate` | §6 Queue / Fill model | 3 | — | Execution realism (BLUEPRINT §2 #3); queue + cancel |
| 4 | `LatencyChainUs` | §4 MBO Marked Point Process | 2 | — | Event-time correctness (BLUEPRINT §2 #2); end-to-end µs chain |
| 5 | `CppLatencyBudget` | §19 Validation framework | 7 | — | Latency + costs validation; C++ hot-path bound |
| 6 | `InjectionSweepResult` | §10 Optimization | 5 | — | Model agnosticism (BLUEPRINT §2 #4); sweep result rows |
| 7 | `StrategySignal` | §8 Action space | 4 | — | Walk-forward eval (BLUEPRINT §2 #5); strategy output enum |
| 8 | `FillOutcome` | §6 Queue / Fill model | 3 | — | Filled / partial / cancelled / timed-out outcomes |
| 9 | `EventContext` | §1 Information set · §15 Feature families · §17 Event windows | 1, 5, 6 | §structural / feature families | Composite context (information set + features + windowing) |

### Runner-exit promotion-boundary invariant (BLUEPRINT §2 R)

The "promote" gate in `run_autonomous.py` (option b in audit) is grounded in:

- §19 Validation framework, p. 7–8 (latency + costs)
- §11 Dynamic control form, p. 4 (NS ordering)

This is the **single combined invariant** `EV > 0 ∧ survives_latency ∧ wfc=PASS → PROMOTE else FAIL_CLOSED`
that the audit flagged as missing.

### Latency chain (math invariant #3)

The four end-to-end µs fields (`feed_ingress_us`, `mdp_to_strategy_us`, `strategy_to_order_us`,
`order_to_ack_us`) trace to §4 (page 2) of the math-model PDF. The **C++ hot-path bound**
(`CppLatencyBudget`) traces to §19 (page 7).

### Nanosecond ordering (math invariant #2)

The 33-timestamp schema and MBO event-time ordering trace to §1 (page 1) of the math-model PDF
for the *information set* definition, and §11 (page 4) for the *dynamic control form* that
defines decision-time precedence.

---

## Sidecar file convention (planned)

For each ODL extension `Foo`, the citation sidecar is
`integrations/openfoundry/domain-packs/hft3/citations/Foo.yaml`:

```yaml
# citations/MarkedMicroEvent.yaml
extension: MarkedMicroEvent
primary:
  pdf: chicago_cme_microstructure_mathematical_model.pdf
  section: "§4 MBO Marked Point Process"
  page: 2
secondary:
  - pdf: chicago_cme_microstructure_mathematical_model.pdf
    section: "§3 Limit Order Book"
    page: 1
claims:
  - "Every MBO event has a unique event-time in the filtration."
  - "The 33-timestamp schema is consistent with the marked point process."
notes: |
  Filtration integrity (BLUEPRINT §2 #1). The structural-models PDF
  does not cover MBO event-time, so this extension cites the math model only.
```

The validator (phase 5) will require:
1. Sidecar file exists for every extension declared in `hft3-cme-mbo.yaml`.
2. `primary.pdf` resolves to a real file in `docs/references/`.
3. `primary.section` is non-empty and matches a section heading in the PDF (case-insensitive substring).
4. `primary.page` is a positive integer.
5. `claims[]` is non-empty (no naked citation without a claim).

---

## Status

| Phase | Status | Notes |
|-------|--------|-------|
| 0 (math PDFs read, citations extracted) | **done** | Both PDFs text-extractable; section headings verified |
| 1 (ODL parser smoke test) | **done** | Parser is strict → sidecar-only |
| 2 (ground symbolic gate) | pending | `GroundedSymbolicResult` with `cite` field on every violation |
| 3 (hft3 ODL extension pack) | pending | `integrations/openfoundry/domain-packs/hft3/` with 9 schema files + 9 sidecar citations |
| 4 (extend MANIFEST.md) | pending | 17 rows → 26 rows; 9 new rows mirror this table |
| 5 (validate_ontology_citations()) | pending | Implemented in `openfoundry_bridge.py`; called by `assert_connector_valid()` |
| 6 (`_connector_gate` calls validator) | pending | New `llm_status` values for citation failures |
| 7 (closed-claim LLM schema) | pending | `kg_annotations[]` becomes `{source_type, source_id, field, value, cite}`; `narrative_md` becomes deterministic |
| 8 (docs) | pending | This file is the seed; `ONTOLOGY_HARDENING.md`, `PACKET_LLM_CONTRACT.md` rewrite, `VENDOR_BOUNDARIES.md` update, `integrations/openfoundry/README.md` |
| 9 (verify + commit + push) | pending | 9 commits (one per phase); targeted pytest on data_layer, packet, hft3_validation, certification_runner_lane_aware; lane-aware backtester cert GREEN |
