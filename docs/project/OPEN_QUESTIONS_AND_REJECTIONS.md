# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Open Questions And Rejections

Status: v0.1 planning-control artifact. Rejected ideas and unresolved planning
questions must be recorded instead of silently disappearing.

## Open Questions

| ID | Question | Affects | Why it matters | Required resolution |
|---|---|---|---|---|
| Q001 | What exact CME futures/options historical datasets are available for full universe research after the lane split? | F001, F002, F005 | Full model-universe claims require real coverage, not assumed coverage. | Current status: `inventory-with-warnings`, not closed/green. See [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md). The 2026-06-14 `python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes` run content-verified the active NPZ manifest and classified the MBO gaps as `203` full no-market slots plus `8` partial symbol absences. [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) records the proposed MBO gap rejection ledger, and [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) records the proposed strict options MBO warning ledger. Q001 remains open until both ledgers are owner-accepted, filled, or explicitly rejected for model scope; after any owner decision, rerun `python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes` and update the Q001 status doc. |
| Q002 | Are VIX/volatility snapshots point-in-time at the decision timestamp for every event window? | F004 | Context features are invalid if volatility data is joined after the fact. | PIT join proof and leakage test. |
| Q003 | What CME options chain/quote/trade data exists at each event timestamp? | F004, F005 | Options can be a futures feature and a standalone lane only if options data is temporally valid. | Options data readiness artifact with expiries, strikes, quotes/trades, greeks/underlying, timestamps. |
| Q004 | Which macro events are target events versus context-only events? | F003, F004 | The system must distinguish independently tradable events from features that improve another target. | Event taxonomy with target/context labels and test rules. |
| Q005 | What robustness thresholds define promotion candidate versus rejected? | F006, F008 | The cockpit cannot show green without numeric gate thresholds. | Threshold policy in certification docs and cockpit gates. |
| Q006 | What is the exact model lifecycle state machine? | F008 | Manual or ambiguous status changes create false confidence. | State transition table with allowed transitions and required artifacts. |
| Q007 | What is the current M6 full-sweep artifact status after fresh-start boundaries? | F001, F002, F006 | Stale or smoke artifacts must not contaminate current research. | Active-run id, full-sweep artifact path, coverage report, and rejection ledger. |
| Q008 | How are CME options standalone models separated from futures models using options-derived features? | F004, F005 | Prevents "options as feature" from swallowing the first-class options lane. | Separate model registry and dashboard lane contract. |

## Experimental Items

| ID | Item | Classification | Required proof before promotion |
|---|---|---|---|
| E001 | Macro/VIX/options context-uplift feature families | EXPERIMENTAL | Target-only baseline, context-uplift ablation, PIT proof, robustness pass. |
| E002 | Standalone CME options strategy models | EXPERIMENTAL until options data/lifecycle is proven | Options-specific artifacts, fee/tick rules, PIT chain data, robustness pass. |
| E003 | Cross-event context hierarchy, e.g. smaller events improving CPI/NFP/FOMC decisions | EXPERIMENTAL | Event taxonomy, no-lookahead feature construction, target-vs-context evaluation. |

## Rejected Concepts

| ID | Rejected concept | Reason |
|---|---|---|
| R001 | "Options as clue" terminology | Ambiguous. The correct modeling term is feature or contextual feature. |
| R002 | Decorative dashboard panels | Cockpit must reflect backend truth, not product theater. |
| R003 | Generic quant dashboard scope | The project is CME futures/options microstructure research and validation, not a generic analytics UI. |
| R004 | Model promotion from one in-sample profitable chart | Violates robustness and anti-overfit doctrine. |
| R005 | Treating stale, smoke, fixture, or structural-only artifacts as green | Violates validation honesty and cockpit truth gates. |
| R006 | Creating a new pipeline when workbench/replay/certification ontology already covers the behavior | Duplicates system control and makes future correctness harder. |
| R007 | Workstation live/paper routing | Violates CHI404 live/paper topology. |
| R008 | Unsupported LLM-generated trading logic | No literature/ontology basis, no data boundary, no tests. |
| R009 | Silent disappearance of failed models | Model universe testing requires rejection reasons and lifecycle status. |

## Update Rule

When a question is resolved, move it into the relevant feature row in
[FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)
or into the appropriate implementation/spec doc. When an idea is rejected,
record the rejection reason here even if no code changes are made.
