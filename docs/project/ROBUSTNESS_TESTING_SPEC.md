# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent robustness methodology outside that authority.

# Robustness Testing Specification

Status: v0.1 planning-control specification. This document defines the target
robustness-testing product behavior. It is not evidence that the current repo
already implements every item.

## Purpose

The robustness layer decides whether a model is worth trusting with future
trading consideration. It must reject false edge caused by overfitting,
in-sample parameter peaks, unrealistic fills, missing fees, hidden slippage,
weak sample size, stale artifacts, or contaminated generated data.

This specification expands the robustness section of
[CANONICAL_PROJECT_PLAN.md](CANONICAL_PROJECT_PLAN.md). Future implementation
plans must map each slice back to this document.

## Source Hierarchy

| Layer | Authority | How it is used |
|---|---|---|
| Vault ontology | Obsidian vault `wiki/hot.md`, `Home.md`, `Memory Stack.md`, `library/System Implications.md`, `library/papers/Papers MOC.md` | Non-negotiable invariants, academic-paper map, and feature legitimacy. |
| HFT3 authority PDFs | `docs/references/MANIFEST.md` and referenced PDFs | Filtration, event-time, walk-forward discipline, topology, and acceptance gates. |
| Academic paper notes | Vault `library/papers/*.md` | Metric formulas, validation traps, cost accounting, fill realism, and econometric assumptions. |
| VectorBT | [polakowo/vectorbt](https://github.com/polakowo/vectorbt) and VectorBT docs | Fast vectorized parameter-grid screening and portfolio/stat surfaces. |
| hftbacktest | [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest), [official hftbacktest docs](https://hftbacktest.readthedocs.io/en/latest/index.html), and [HFTBACKTEST_REALISM_ENGINE_SPEC.md](HFTBACKTEST_REALISM_ENGINE_SPEC.md) | Tick/order-book replay, latency, queue position, fee, order, and fill simulation. |
| Walk Forward Correlation | User-provided transcript from "Martyn Tinsley - Walk Forward Correlation: A New Tool for Robust Strategy Design" | Defines WFC as full parameter-surface IS/OOS correlation, not parameter selection. |
| HFT3 current code | Existing packages, specs, docs, and artifacts | Adapter target. Current behavior does not override the higher authorities. |

## Non-Negotiable Invariants

1. Filtration: no feature, signal, join, metric, or artifact may use information
   unavailable at the decision timestamp.
2. Event-time ordering: replay/backtest must preserve exchange event time and
   nanosecond timestamp ordering.
3. Walk-forward discipline: discovery, confirmation, holdout, and recent holdout
   periods must stay explicit.
4. Execution realism: a model that only wins before fees, slippage, queue, and
   latency is not robust.
5. Artifact honesty: missing, smoke, stale, contaminated, fixture, or partial
   evidence cannot produce GREEN.
6. Metric definitions: every metric must declare formula, unit, denominator,
   aggregation level, sample size, and failure semantics before implementation.

## Terms

| Term | Meaning |
|---|---|
| Artifact | A persisted output from a run. Examples: parameter surface, fold matrix, replay result, scorecard, gate report, contamination manifest. |
| Hypothesis row | One measured candidate result at a defined grain, such as model x symbol x event x latency x parameter set x fold. It is not automatically a full backtest certification. |
| Parameter surface | Full grid of parameter combinations and their metric values for a model under a defined data split. |
| Walk-forward | Sequential train/validation/test discipline over time. It tests selected robust parameters on later unseen data. |
| Walk Forward Correlation | Correlation between in-sample and out-of-sample performance across the entire parameter matrix. It is a go/no-go test for predictive surface structure. |
| Scorecard | Trader-grade post-robustness metric summary for a model version. |
| Behavior envelope | Expected operating bounds produced from robustness/OOS/paper evidence for later monitoring. |

## Eleven-Phase Robustness Contract

### 1. Clean Run Boundary And Provenance

Before a trusted robustness run, generated outputs from partial, halted, stale,
or old runs must be quarantined or excluded by manifest.

Required outputs:

- `run_id`
- code commit
- data source hashes
- event CSV hash
- symbol universe
- latency source
- artifact reuse policy
- contaminated-artifact exclusion list

Acceptance: the run can prove which inputs were used and which old artifacts
were excluded. Missing provenance blocks GREEN.

### 2. Parameter Universe Definition

Each model must declare its parameter grid before testing.

Required fields:

- parameter name
- type and unit
- lower/upper bounds
- step size or candidate values
- default value
- reason for range
- literature or ontology citation
- forbidden post-hoc changes

Acceptance: no result can be promoted if the tested parameter universe was
created after seeing OOS performance.

### 3. VectorBT Fast Screen

VectorBT is the cheap broad-screen layer. It rejects weak models and weak
parameter regions before expensive tick replay.

Required behavior:

- run full parameter grid
- record fees and slippage assumptions
- produce return/equity/trade-level metrics
- preserve rejected candidates with reasons
- pass only candidates eligible for deeper replay

Required metrics at this phase:

- gross return
- total fees
- total slippage
- net return
- net PnL
- trade count
- hit rate
- expectancy per trade
- profit factor
- Sharpe
- Sortino
- max drawdown
- turnover

Acceptance: VectorBT may reject. VectorBT alone may not certify execution
tradability.

### 4. In-Sample Surface Robustness

The system must not pick the highest point just because it is highest.

Required checks:

- plateau width
- neighbor stability
- cliff distance from loss regions
- parameter perturbation sensitivity
- peak-vs-plateau comparison
- minimum sample size

Acceptance: a sharp isolated peak near loss regions is rejected or marked
experimental even if its in-sample PnL is high.

### 5. Regular Walk-Forward

Selected robust parameter regions must pass time-forward validation.

Required outputs:

- fold matrix
- fold train/test dates
- fold-specific metrics
- walk-forward efficiency
- fold dispersion
- IS/OOS gap
- OOS decay

Acceptance: any model that fails required OOS periods remains non-GREEN.

### 6. Walk Forward Correlation

WFC runs the same full parameter matrix in sample and out of sample.

Required outputs:

- one row per parameter combination
- `metric_in_sample`
- `metric_out_of_sample`
- Pearson correlation
- Spearman correlation
- scatter data for cockpit/charting
- quadrant counts
- high-IS/high-OOS stability region
- low-correlation rejection reason

Rules:

- WFC is a go/no-go robustness test.
- WFC is not the parameter selector.
- A profitable single OOS point does not rescue a zero-correlation surface.
- Low WFC can indicate overfit, insufficient trades, or no real edge; the
  artifact must say which follow-up diagnosis is required.

Acceptance: models with no predictive parameter-surface relationship do not
advance to parameter selection or pre-live optimization.

### 7. Pre-Live Optimization Discipline

Only after validation passes, run pre-live optimization with the same settings
and methodology shifted to the most recent approved window.

Required rules:

- same parameter grid shape
- same metric definitions
- same selection rule
- no new feature, threshold, or methodology introduced at pre-live stage
- selected pre-live parameters persisted for paper/live consideration

Acceptance: changing the method at pre-live invalidates comparability and blocks
promotion.

### 8. hftbacktest Microstructure Replay

Candidates that survive the cheap and statistical layers enter hftbacktest-style
replay. The implementation contract is
[HFTBACKTEST_REALISM_ENGINE_SPEC.md](HFTBACKTEST_REALISM_ENGINE_SPEC.md).

Required behavior:

- tick/order-book replay
- event-time ordering
- measured latency injection
- separate feed/order-entry/order-response latency assumptions
- HftBacktest source lock with upstream repo/docs/package version
- data dtype, timestamp-ordering, and L2/L3 validation before replay
- queue-position/fill model
- explicit fee model
- explicit slippage/impact assumptions
- missed-fill accounting
- adverse-selection/markout measurement
- explicit label when market impact is not modeled
- explicit label when accelerated mode is non-certifying

Required outputs:

- `hftbacktest_source_lock.json`
- `data_validation.json`
- `latency_model.json`
- `fill_queue_model.json`
- intended orders
- actual fills
- unfilled/cancelled orders
- queue-position statistics
- latency statistics
- fill rate
- partial-fill rate
- fees
- slippage
- spread capture/cost
- execution-adjusted PnL

Acceptance: a model that passes VectorBT but fails hftbacktest is
`execution_failed`, not `strategy_passed`.

### 9. Trader-Grade Metrics Layer

Every hypothesis row that reaches certification must expand into a complete
scorecard input record.

Required metric groups:

- return quality: gross/net return, gross/net PnL, expected return with named
  denominator, Sharpe, Sortino, Calmar/MAR where applicable, profit factor
- trade quality: trade count, hit rate, average win, average loss, payoff ratio,
  median trade return, expectancy per trade
- loss behavior: max drawdown, drawdown duration, time under water, VaR,
  expected shortfall/CVaR, tail ratio, max consecutive losses
- execution realism: fees, slippage, spread cost/capture, fill rate, queue
  decay, latency, market impact estimate, adverse selection, markout curve
- robustness stability: fold dispersion, WFE, parameter stability, feature
  stability, regime stability, symbol/event stability
- portfolio fit: correlation to other models, drawdown correlation, marginal
  risk contribution, liquidity overlap
- prediction quality where applicable: IC, rank IC, calibration error, Brier
  score, log loss, bucket monotonicity

Acceptance: missing required fields produce `unavailable` with explicit reason,
never silent zeros.

### 10. Statistical Reality Checks

The gauntlet must separate real edge from lucky search.

Required checks:

- bootstrap confidence interval
- Deflated Sharpe Ratio
- CSCV/PBO
- Holm/BH multiple-testing correction
- fee multiplier stress
- slippage multiplier stress
- latency stress
- parameter perturbation
- null strategy battery
- planted-alpha synthetic control
- adversarial perturbation

Acceptance: a model cannot be GREEN when a required anti-overfit check is
missing, malformed, stale, or failing.

Vault authority: DSR/PBO/CSCV planning is grounded in vault
`library/13 Robust Backtesting and Multiple Testing.md` and
`library/papers/dsr-pbo-bailey-lopezdeprado-source-map.md`. Formula
implementation must cite the specific source row and verify the paper formula,
unit, denominator, aggregation grain, and missing-data behavior before coding.

### 11. Certification, Cockpit, And Promotion Gate

The final artifact must be machine-checkable and cockpit-visible.

Required outputs:

- robustness artifact id
- source artifacts
- metric schema version
- pass/fail gate list
- rejection reason list
- certification freshness
- cockpit status
- dashboard drilldown fields

GREEN requires:

- clean provenance
- no leakage
- sufficient sample size
- valid VectorBT screen where applicable
- regular walk-forward pass
- WFC pass
- hftbacktest execution-realism pass
- complete trader-grade metrics
- anti-overfit gauntlet pass
- fresh certification

Any missing required evidence is non-GREEN.

## Academic Literature Binding

| Robustness area | Primary local source |
|---|---|
| Cost accounting and spreads | Vault `roll-1984-implicit-spread.md`, `stoll-2003-market-microstructure.md`, `hasbrouck-1991-information-content-trades.md` |
| Slippage and execution cost | Vault `almgren-chriss-2000-optimal-execution.md`, `obizhaeva-wang-2013-supply-demand.md`, `alfonsi-fruth-schied-2010-lob-shape.md` |
| Queue and fill realism | Vault `huang-lehalle-rosenbaum-2015-queue-reactive.md` |
| OFI and short-horizon impact | Vault `cont-kukanov-stoikov-2011-ofi.md`, `eisler-bouchaud-kockelkoren-2012-event-impact.md` |
| HF noise and volatility estimation | Vault `mykland-zhang-2012-econometrics-hf-data.md`, Barndorff-Nielsen/Shephard notes |
| LOB model robustness | Vault `gould-et-al-2013-limit-order-books.md`, `smith-farmer-gillemot-krishnamurthy-2003-cda-theory.md` |
| ML validation discipline | Vault `kolm-turiel-westray-2021-deep-ofi.md`, DeepLOB notes, Sirignano/Cont notes |
| DSR/PBO/CSCV | Vault `library/13 Robust Backtesting and Multiple Testing.md`; vault `library/papers/dsr-pbo-bailey-lopezdeprado-source-map.md`; source papers named in that map |

## Implementation Planning Requirements

Every future implementation slice must define:

```text
Feature ID:
Robustness phase(s):
Metric(s) added:
Source-of-truth citation:
Input artifact schema:
Output artifact schema:
Failure states:
Cockpit display:
Tests:
Acceptance command:
Rollback/quarantine rule:
Local preflight contract:
```

No implementation slice may add a metric unless its denominator, unit,
aggregation grain, and missing-data behavior are specified in the slice plan.

## Local Preflight Quality Gate

Every robustness implementation slice must pass the local preflight scorecard in
[../ai/GREPLOOP.md](../ai/GREPLOOP.md) with a minimum score of **4/5** before it
can move to reviewer or verification.

The local preflight contract for a robustness slice must include:

- forbidden stale terms, fields, status names, old artifact names, and old metric
  labels
- required new metric names, schema keys, gate names, and citation rows
- every changed file
- adjacent consumers such as cockpit aggregators, scorecard readers, index docs,
  or tests when applicable
- one anti-mimic search pattern derived from an expected failure mode, not only
  from text the agent already edited

Examples of anti-mimic searches for robustness work:

```powershell
rg -n "gross.*net|net.*gross|zero.*fee|fee.*0|slippage.*0|GREEN|strategy_passed" <changed-scope>
rg -n "sample_size|denominator|metric_unit|source_artifact|fail-closed|unavailable" <changed-scope>
rg -n "Walk Forward Correlation|metric_in_sample|metric_out_of_sample|Pearson|Spearman" <changed-scope>
```

If the score is below 4/5, the slice is not merge-ready even if tests pass.
