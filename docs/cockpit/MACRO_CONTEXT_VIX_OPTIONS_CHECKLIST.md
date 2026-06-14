# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# Macro Context, VIX, and Options Feature Checklist

Date: 2026-06-14

Audience: human developer, Codex agents, and review subagents working on the CME cockpit, model research loop, VIX feature lane, and CME options lane.

Purpose: capture the exact behavior the owner requested before any implementation pass. This is not permission to invent a new pipeline. It is a checklist for implementing the missing pieces inside the existing hft3 ontology, using the vault math library, the existing replay/research stack, and the existing fail-closed cockpit gates.

## Owner Intent

- [ ] Treat major volatility events as primary target events: CPI, CORE_CPI, NFP, unemployment claims, FOMC, GDP, PCE, PPI, and similar scheduled shocks.
- [ ] Allow smaller or adjacent events to remain standalone tradable targets if they independently pass robustness.
- [ ] Also allow smaller or adjacent events to act as point-in-time context clues for a later target event. Examples: ADP, JOLTS, ECI, durable goods, ISM, EIA, Baker Hughes, construction spending, and similar macro prints.
- [ ] Measure both concepts separately. A model being profitable on ADP alone is not the same thing as ADP improving NFP or CPI trading.
- [ ] Add VIX and VIX options state as context features when available and point-in-time valid.
- [ ] Add CME options state as context features when available and point-in-time valid.
- [ ] Make the options lane visible as a first-class cockpit lane, not just a hidden background check.
- [ ] Keep research autonomous. Codex can observe, audit, and implement missing scaffolding, but the pipeline must not depend on a runtime LLM to continue working.
- [ ] Do not enable live trading, paper routing, Rithmic order paths, or options shadow/live arm as part of this work.

## Current Evidence

- [ ] VaultGate sources checked: `wiki/hot.md`, `Home.md`, `Memory Stack.md`, `library/Math Library Index.md`, `library/System Implications.md`, options decisions/sessions, and targeted vault searches for macro, VIX, options, context, and methodology.
- [ ] GraphGate ran before this doc edit with query `macro context VIX options checklist cockpit target event context event`; graph output was mostly lifecycle/cockpit, so vault notes and targeted code searches remain the stronger evidence for this checklist.
- [ ] The Model Detail event bar chart currently reports per-event expected value segments. It does not prove that one event is being used as a clue for another later target event.
- [ ] Current model unit is too coarse for the requested behavior. Required unit is `model + target_event + allowed_context_set + symbol + latency_band`.
- [ ] Existing VIX feature code exists in `packages/features_engine/src/features/vix_features.py` and VIX hypotheses exist in `packages/features_engine/src/hypotheses/vix_modules.py`.
- [ ] Existing replay injection path exists through `sensor_feature_npz` and `MarketState.cross_asset_features`.
- [ ] Current local lake observation on 2026-06-14: `C:\hft3-lake\features\VIX.OPT` has 342 files and `C:\hft3-lake\vix_options` has 865 files.
- [ ] Current local Stage A artifacts observed on 2026-06-14 showed zero visible VIX coverage: `research_cards/stage_a_full/stage_a_result.json` and `research_cards/stage_a_full_v2/stage_a_result.json` each reported `sum_n_events_with_vix=0`.
- [ ] Existing CME options data exists locally: definitions 2648 files, fixing MBO 788 files, OHLCV 1 file, statistics 149 files under `C:\hft3-lake\options`.
- [ ] Existing options adapters are not enough for this request: `packages/hft3/validation/lanes/adapters/cme_options_adapter.py` reports structural-only evidence, and `apps/workbench/src/adapters/options_lane_adapter.py` uses `fixture-backtest`.
- [ ] System page exposes options data and defect ledger checks, but there is no first-class options cockpit page or pipeline lifecycle view.
- [ ] Latency inputs must be artifact-backed. The known CME M5 p99 runner convention is `6.255764` ms / approximately `6.256` ms from the vault hot cache. Any newer offensive/defensive latency value, including a `23us` claim, must be located, unit-checked, artifact-cited, and surfaced before use.

## Ontology Guardrails

- [ ] Filtration: no feature may read data with timestamp after the target decision timestamp.
- [ ] Event-time ordering: replay and context joins must use exchange/event time, not wall-clock file order.
- [ ] Walk-forward discipline: context feature selection cannot be fitted on holdout or future slices.
- [ ] Context is not alpha by itself. Every context feature must show incremental out-of-sample value versus a target-only baseline.
- [ ] Options data is research-only until the options defect ledger is empty and the shadow/live gates are explicitly satisfied.
- [ ] Workstation remains offline research only. No live/paper Rithmic capture or order routing may be wired through the Windows machine.
- [ ] Do not treat local data existence as proof of model usage. Coverage must be measured in research artifacts and cockpit cards.
- [ ] Do not collapse futures, VIX, VIX options, and CME options into one anonymous feature blob. Each feature group needs source IDs, timestamp IDs, units, and missingness status.
- [ ] Do not use 2026 options data for alpha fitting unless the options 2026 usage-class policy permits it and the research card declares it.
- [ ] Do not mark cockpit GREEN because a doc says the feature exists. GREEN requires fail-closed evidence from the existing gates.

## Feature Template

- [ ] Define `target_event_type`: the event being traded.
- [ ] Define `context_event_type`: an earlier or adjacent event allowed to inform the target.
- [ ] Define `context_window`: lookback horizon and cutoff relative to target event timestamp.
- [ ] Define `context_source`: macro event result, futures order flow, VIX sensor, VIX options, CME options, or cross-symbol futures context.
- [ ] Define `context_feature_id`: stable ID for artifact, packet, and cockpit display.
- [ ] Define `source_ids`: source files or lake paths used to derive the feature.
- [ ] Define `timestamp_ids`: event timestamp, source timestamp, feature availability timestamp, and target decision timestamp.
- [ ] Define `missing_policy`: absent, stale, partial, embargoed, malformed, or valid.
- [ ] Define `leakage_proof`: explicit assertion that feature availability is before or equal to target decision time.
- [ ] Define `measurement_row`: target-only EV, target plus context EV, delta EV, DSR, PBO, CSCV count, bootstrap confidence interval, fee/slippage stress, and latency band.

## Macro Context Checklist

- [ ] Add a context-event catalog mapping smaller events to candidate target events without hardcoding a new pipeline.
- [ ] Start with conservative explicit mappings, not fuzzy natural-language inference.
- [ ] Ensure ADP and JOLTS can inform NFP/unemployment targets only when their release timestamps are before the target decision timestamp.
- [ ] Ensure ECI, PPI, durable goods, ISM, construction spending, and similar events can inform CPI/PCE/GDP only under explicit lookback rules.
- [ ] Preserve standalone evaluation of smaller events as their own target events.
- [ ] Extend research artifacts so charts distinguish `standalone_event_profitability` from `context_uplift_for_target_event`.
- [ ] Add an ablation matrix: target-only, target plus macro context, target plus VIX, target plus options, and full context.
- [ ] Add negative controls where future context is intentionally withheld and rejected if it improves results through leakage.

## VIX Checklist

- [ ] Confirm VIX feature files are located by event ID for every eligible target event.
- [ ] Add artifact-level counts for `n_events_with_vix`, `n_events_without_vix`, and VIX feature missingness reason.
- [ ] Fail non-GREEN if VIX hypotheses are evaluated while VIX coverage is zero and the UI implies VIX is active.
- [ ] Add tests where a VIX sibling file exists and at least one research cell reports non-zero VIX coverage.
- [ ] Add tests where no VIX sibling exists and all cells report honest zero VIX coverage.
- [ ] Treat VIX and VVIX as sensors, not executable instruments.
- [ ] Ensure VIX options features are not interpreted as clean directional signal without decomposition or missingness warnings.
- [ ] Surface VIX coverage on the Models page and Pipeline page so the owner can see whether VIX is actually used.

## CME Options Context Checklist

- [ ] Convert options data from "exists in lake" to explicit context features before using it in model evaluation.
- [ ] Candidate features: implied volatility level, skew, term structure, quote intensity, spread stress, open interest/statistics, chain liquidity, expiry pressure, parity deviation, gamma/vega pressure, and dealer-hedging proxy.
- [ ] Separate VIX options context from CME futures-options context.
- [ ] Require point-in-time joins for definitions, OHLCV, statistics, and fixing MBO.
- [ ] Require usage-class metadata for any 2026 options read.
- [ ] Treat options order imbalance as contaminated until parity-arbitrage and informed-flow components are separated or explicitly marked as proxy-only.
- [ ] Add options context coverage to research artifacts and cockpit cards.
- [ ] Keep options live/shadow arm blocked while `specs/OPTIONS_LANE.md` defect ledger has open items.

## Options Lane Cockpit Checklist

- [ ] Add a first-class options lane view or page in the cockpit.
- [ ] Show options lane status independent of CME futures status.
- [ ] Show data readiness: definitions, fixing MBO, fixing coverage, OHLCV, statistics, and mandatory missing checks.
- [ ] Show open options defects with counts and links to `specs/OPTIONS_LANE.md`.
- [ ] Show research-only status clearly.
- [ ] Show last options research/backtest job, latest artifact path, and whether artifact is structural-only, fixture-only, or real-data-backed.
- [ ] Show context-feature coverage for options-as-clue features separately from options-standalone strategy tests.
- [ ] Keep live/paper controls absent or disabled unless options gates are explicitly cleared in future work.

## Research Measurement Checklist

- [ ] For each `model + target_event + context_set + symbol + latency_band`, compute target-only baseline.
- [ ] Compute incremental context uplift net of fees, slippage, latency, and queue loss.
- [ ] Measure robustness using DSR, PBO, sufficient CSCV slices, bootstrap confidence intervals, and walk-forward holdout.
- [ ] Apply multiple-testing control across models, target events, context sets, symbols, and latency bands.
- [ ] Require non-GREEN for insufficient event counts, invalid PBO, insufficient CSCV, stale certification, malformed thresholds, or smoke-only scope.
- [ ] Store every context feature and measurement row in artifacts, not only in UI state.
- [ ] Add explicit `context_feature_coverage` and `context_ablation` sections to research cards.
- [ ] Add a "why not used" reason for every missing context group.

## Autonomy Checklist

- [ ] Pipeline lifecycle must remain self-sufficient: research, robustness, implementation candidates, model trading readiness, observation, and rejection loops.
- [ ] Codex must not become a runtime dependency.
- [ ] Controls may launch audited durable jobs only. Controls must not become hidden manual steps required for progress.
- [ ] Cockpit should observe and explain state, not fake green state.
- [ ] Background sweeps should be resumable and visible through artifacts/logs.

## Implementation Passes

- [ ] Pass 1: documentation and audit only. Commit this checklist so a human developer has the exact owner intent and evidence map.
- [ ] Pass 2: implement smallest code changes for measurement visibility first: artifact schema, coverage counts, cockpit display, and tests.
- [ ] Pass 3: implement context feature generation only after the measurement schema is fail-closed.
- [ ] Pass 4: implement options lane cockpit page/API only after the existing system options checks are preserved.
- [ ] Pass 5: implement real options backtest pipeline only after the structural-only and fixture-only adapters are replaced or clearly bypassed for real-data research.
- [ ] Every pass requires VaultGate, GraphGate, subagent locate/edit/review for code, bounded tests, and no unstated live/paper changes.

## Test Checklist

- [ ] Unit tests for `target_event` versus `context_event` semantics.
- [ ] Unit tests for point-in-time event context joins.
- [ ] Unit tests that future event context is rejected.
- [ ] Unit tests that VIX coverage is non-zero when VIX files exist for matching events.
- [ ] Unit tests that VIX coverage remains honestly zero when VIX files are absent.
- [ ] Unit tests that options context features include source IDs, timestamp IDs, units, and missingness.
- [ ] Backend tests that cockpit exposes options lane state separately from CME futures.
- [ ] Frontend build after any cockpit UI changes.
- [ ] Artifact regression tests for `context_ablation` and `context_feature_coverage`.
- [ ] Validation command candidates: `python -B -m pytest -q apps\cockpit\backend\tests\test_cockpit.py tests\test_stage_a tests\test_features -p no:cacheprovider`, `npm run build` in `apps/cockpit/frontend`, and `git diff --check`.

## Vault Academic Link Inventory

Extracted from unique `url::` fields under `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\library`.

- `06 Optimal Execution.md` - https://arxiv.org/abs/0708.1756
- `papers/mike-farmer-2008-empirical-behavioral-model.md` - https://arxiv.org/abs/0709.0159
- `papers/bouchaud-farmer-lillo-2009-markets-digest.md` - https://arxiv.org/abs/0809.0822
- `papers/eisler-bouchaud-kockelkoren-2012-event-impact.md` - https://arxiv.org/abs/0904.0900
- `08 High-Frequency Econometrics.md` - https://arxiv.org/abs/0906.1444
- `papers/moro-etal-2009-hidden-orders.md` - https://arxiv.org/abs/0908.0202
- `papers/abergel-jedidi-2013-order-book-modeling.md` - https://arxiv.org/abs/1010.5136 (note: category linked HAL hal-00621253; HAL blocked, arXiv v4 mirror used)
- `papers/cont-kukanov-stoikov-2011-ofi.md` - https://arxiv.org/abs/1011.6402
- `02 Limit Order Book Foundations.md` - https://arxiv.org/abs/1012.0349
- `papers/cont-delarrard-2013-markovian-lob.md` - https://arxiv.org/abs/1104.4596
- `papers/toth-etal-2011-anomalous-impact.md` - https://arxiv.org/abs/1105.1694
- `papers/gueant-lehalle-fernandez-tapia-2013-inventory-risk.md` - https://arxiv.org/abs/1105.3115
- `05 Hawkes and Point Processes.md` - https://arxiv.org/abs/1112.1838
- `papers/alfonsi-acevedo-2012-time-varying-lob.md` - https://arxiv.org/abs/1204.2736
- `papers/yudovina-2012-simple-model-lob.md` - https://arxiv.org/abs/1205.7017
- `papers/carmona-webster-2012-hf-market-making.md` - https://arxiv.org/abs/1210.5781
- `papers/bacry-muzy-2014-hawkes-price-trades.md` - https://arxiv.org/abs/1301.1135
- `06 Optimal Execution.md` - https://arxiv.org/abs/1302.4592
- `03 MBO Event-Level Dynamics.md` - https://arxiv.org/abs/1312.0563
- `papers/bacry-mastromatteo-muzy-2015-hawkes-finance.md` - https://arxiv.org/abs/1502.04592
- `02 Limit Order Book Foundations.md` - https://arxiv.org/abs/1504.00579
- `papers/laub-taimre-pollett-2015-hawkes.md` - https://arxiv.org/abs/1507.02822
- `09 ML and Deep Learning for LOB.md` - https://arxiv.org/abs/1601.01987
- `papers/rambaldi-bacry-lillo-2017-volume-hawkes.md` - https://arxiv.org/abs/1602.07663
- `papers/bechler-ludkovski-2017-meso-resiliency.md` - https://arxiv.org/abs/1708.02715
- `03 MBO Event-Level Dynamics.md` - https://arxiv.org/abs/1709.01292
- `10 HFT Market Design and Latency.md` - https://arxiv.org/abs/1709.02015
- `papers/sirignano-cont-2019-universal-price-formation.md` - https://arxiv.org/abs/1803.06917
- `papers/wu-rambaldi-muzy-bacry-2019-queue-reactive-hawkes.md` - https://arxiv.org/abs/1901.08938
- `papers/xu-gould-howison-2019-mlofi.md` - https://arxiv.org/abs/1907.06230
- `09 ML and Deep Learning for LOB.md` - https://arxiv.org/abs/2008.12152
- `09 ML and Deep Learning for LOB.md` - https://arxiv.org/abs/2105.10430
- `papers/cont-degond-xuan-2023-order-book-framework.md` - https://arxiv.org/abs/2302.01169
- `papers/daniels-farmer-gillemot-iori-smith-2003-price-diffusion.md` - https://arxiv.org/abs/cond-mat/0112422 (open mirror of Cambridge book-chapter link)
- `papers/bouchaud-mezard-potters-2002-statistical-properties.md` - https://arxiv.org/abs/cond-mat/0203511
- `02 Limit Order Book Foundations.md` - https://arxiv.org/abs/cond-mat/0210475
- `papers/potters-bouchaud-2003-more-statistical-properties.md` - https://arxiv.org/abs/cond-mat/0210710
- `papers/weber-rosenow-2005-order-book-impact.md` - https://arxiv.org/abs/cond-mat/0311457
- `papers/lillo-mike-farmer-2005-long-memory.md` - https://arxiv.org/abs/cond-mat/0412708
- `08 High-Frequency Econometrics.md` - https://arxiv.org/abs/math/0503711
- `papers/farmer-gerig-lillo-mike-2006-efficiency-long-memory.md` - https://arxiv.org/abs/physics/0602015
- `papers/zhang-zohren-roberts-2019-deeplob.md` - https://arxiv.org/pdf/1808.03668
- `00 Books and Monographs.md` - https://assets.cambridge.org/97811070/91146/frontmatter/9781107091146_frontmatter.pdf
- `papers/abergel-et-al-2016-limit-order-books.md` - https://assets.cambridge.org/97811071/63980/frontmatter/9781107163980_frontmatter.pdf
- `papers/carmona-2013-tales-woes-hft.md` - https://carmona.princeton.edu/document/86
- `papers/lim-2022-deep-learning-order-flow-thesis.md` - https://discovery.ucl.ac.uk/10142659/1/Lim_thesis.pdf
- `10 HFT Market Design and Latency.md` - https://econweb.umd.edu/~sweeting/hft-arms-race.pdf
- `papers/kyle-1985-continuous-auctions-insider-trading.md` - https://faculty.fuqua.duke.edu/~qc2/BA532/1985%20EMA%20Kyle.pdf
- `papers/mykland-zhang-2012-econometrics-hf-data.md` - https://galton.uchicago.edu/~mykland/paperlinks/I.A.1-Econometrics_of_High_Frequency_Data.pdf
- `papers/zhou-mth9879-market-microstructure-models.md` - https://github.com/gjimzhou/MTH9879-Market-Microstructure-Models
- `papers/zhang-2019-deeplob-code.md` - https://github.com/zcakhaa/DeepLOB-Deep-Convolutional-Neural-Networks-for-Limit-Order-Books/blob/master/jupyter_tensorflow/run_train_tensorflow-version2.ipynb
- `03 MBO Event-Level Dynamics.md` - https://hal.science/hal-00621253v2/file/A_Mathematical_Approach_to_Order_Book_Modeling_II.pdf
- `papers/bergault-2021-market-making-thesis.md` - https://hal.science/tel-03592281v1/file/these_Bergault.pdf
- `papers/odonovan-yu-zhang-2023-option-mm-hedging-liquidity.md` - https://ink.library.smu.edu.sg/cgi/viewcontent.cgi?article=8869&context=lkcsb_research
- `00 Books and Monographs.md` - https://openlibrary.org/books/OL1103097M/Market_microstructure_theory
- `07 Market Making and Stochastic Control.md` - https://oxford-man.ox.ac.uk/wp-content/uploads/2020/05/Algorithmic-Trading-Stochastic-Control-and-Mutually-Exciting-Processes.pdf
- `papers/kyle-obizhaeva-2016-invariance.md` - https://pages.nes.ru/aobizhaeva/Kyle_Obizhaeva_Invariance.pdf
- `11 Options Microstructure.md` - https://papers.ssrn.com/sol3/Delivery.cfm/f15a0580-4c0f-4b11-b21e-d884422c41e7-MECA.pdf?abstractid=4353434
- `papers/hasbrouck-saar-2013-low-latency-trading.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695460
- `01 Classical Market Microstructure.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596
- `02 Limit Order Book Foundations.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=269908
- `papers/foucault-kadan-kandel-2005-market-for-liquidity.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=269908 (open copy: smallake.kr mirror; RFS doi:10.1093/rfs/hhi029)
- `05 Hawkes and Point Processes.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3306158
- `09 ML and Deep Learning for LOB.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3900141
- `papers/lee-ryu-yang-yu-2023-options-order-imbalance.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4353434
- `06 Optimal Execution.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=752022
- `papers/obizhaeva-wang-2013-supply-demand.md` - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=752022 (open copy: https://web.mit.edu/wangj/www/pap/ObizhaevaWang13.pdf)
- `papers/lovo-hec-financial-markets-microstructure.md` - https://people.hec.edu/lovo/teaching/financial-markets-microstructure/
- `papers/rosu-2009-dynamic-model-lob.md` - https://people.hec.edu/rosu/wp-content/uploads/sites/43/2020/03/limit_RFS_2009.pdf
- `07 Market Making and Stochastic Control.md` - https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf
- `08 High-Frequency Econometrics.md` - https://public.econ.duke.edu/~get/browse/courses/883/Spr16/COURSE-MATERIALS/Z_Papers/BNSJFEC2004.pdf
- `papers/biais-glosten-spatt-2005-microstructure-survey.md` - https://publications.ut-capitole.fr/1422/1/microstructure.pdf
- `papers/almgren-chriss-2000-optimal-execution.md` - https://quantitativebrokers.com/s/Optimal-Execution-of-Portfolio-Transaction-_-AlmgrenChriss-1999.pdf
- `01 Classical Market Microstructure.md` - https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2014/03/7927.pdf
- `papers/cartea-jaimungal-penalva-2015-algo-hf-trading-code.md` - https://sebastian.statistics.utoronto.ca/books/algo-and-hf-trading/
- `papers/jaimungal-sta4505-algorithmic-trading.md` - https://sebastian.statistics.utoronto.ca/courses/sta-4505-algorithmic-trading/
- `papers/rosenthal-2021-market-microstructure-course.md` - https://sites.google.com/site/dalerosenthal/teaching/market-microstructure
- `papers/starkov-2025-copenhagen-fmm-course.md` - https://starkov.site/teaching.html
- `papers/ohara-2015-high-frequency-market-microstructure.md` - https://statmath.wu.ac.at/~hauser/LVs/FinEtricsQF/References/oHara2015JFinEco_HighFrequ_Market_MiicroStruct.pdf
- `01 Classical Market Microstructure.md` - https://w4.stern.nyu.edu/finance/docs/WP/1996/pdf/wpa96034.pdf
- `papers/hasbrouck-1991-information-content-trades.md` - https://www.acsu.buffalo.edu/~keechung/MGF743/Readings/K2.pdf
- `papers/roll-1984-implicit-spread.md` - https://www.bauer.uh.edu/rsusmel/phd/roll1984.pdf
- `papers/bis-2011-hft-fx.md` - https://www.bis.org/publ/mktc05.pdf
- `10 HFT Market Design and Latency.md` - https://www.bis.org/publ/work955.pdf
- `02 Limit Order Book Foundations.md` - https://www.cambridge.org/core/books/trades-quotes-and-prices/santa-fe-model-for-limit-order-books/5972E3C5589EAD0D3E3263D2C64D4DBD
- `10 HFT Market Design and Latency.md` - https://www.cashmarket.deutsche-boerse.com/resource/blob/253272/6bbb6205e6651101288c2a0bfc668c45/High-frequency-trading-study-data.pdf
- `papers/ait-sahalia-brunetti-2019-hft-price-process.md` - https://www.cftc.gov/sites/default/files/2020-02/ABHFT20191129_ada.pdf
- `papers/kearns-nevmyvaka-2013-ml-microstructure-hft.md` - https://www.cis.upenn.edu/~mkearns/KN.html
- `03 MBO Event-Level Dynamics.md` - https://www.columbia.edu/~ww2040/orderbook.pdf
- `papers/brogaard-hendershott-riordan-2014-hft-price-discovery.md` - https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1602.pdf
- `01 Classical Market Microstructure.md` - https://www.econ.sdu.edu.cn/__local/F/CE/F2/A97EE00B1B5A4969CECF053D98D_97353554_2A130.pdf
- `papers/drissi-2024-oxford-hft-lecture-notes.md` - https://www.faycaldrissi.com/files/HFT_2024___Oxford___lecture_notes_2024.pdf
- `papers/glosten-milgrom-1985-bid-ask-specialist.md` - https://www.kellogg.northwestern.edu/research/math/papers/570.pdf
- `papers/engle-russell-1998-ultra-high-frequency.md` - https://www.nber.org/system/files/working_papers/w5816/w5816.pdf
- `11 Options Microstructure.md` - https://www.researchgate.net/publication/300444400_OPTION_MARKET_MICROSTRUCTURE
