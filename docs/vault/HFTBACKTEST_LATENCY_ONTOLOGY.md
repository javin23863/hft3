# HftBacktest three-component latency ontology

**Load second** (immediately after [FABLE_MINDSET.md](FABLE_MINDSET.md)) for any backtest, realism, or latency-measurement work.

Canonical repo: `C:\Users\MSI\repos\hft3`.

---

## 1. Why this note exists

[FABLE_MINDSET.md](FABLE_MINDSET.md) prevents µs-vs-ms confusion on the CHI404 probe. It does **not** define how HftBacktest models latency in replay.

HftBacktest distinguishes **three core components** (official docs: [latency_models.html](https://hftbacktest.readthedocs.io/en/latest/latency_models.html)):

| Component | Ends when | Backtest field |
|-----------|-----------|----------------|
| **Feed latency** | Local system receives exchange market data | `feed_latency` / NPZ `local_ts - exch_ts` |
| **Order-entry latency** | Matching engine processes the request | `order_entry_latency` / IntpOrderLatency `req_ts → exch_ts` |
| **Order-response latency** | Local system receives ack/exec report | `order_response_latency` / IntpOrderLatency `exch_ts → resp_ts` |

Repo contract: [docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md).

**Do not collapse these into one scalar** unless `latency_proxy_status` explicitly says which parts are approximated.

---

## 2. Three ontology layers (do not conflate)

| Layer | Vocabulary | Authority |
|-------|------------|-----------|
| **HftBacktest API** | feed / order-entry / order-response; `IntpOrderLatency` | Official HftBacktest docs + realism spec |
| **hft3 engine chain** | `feed_delay_us + decision_compute_us + decision_to_send_us + send_to_ack_us` | `integrations/openfoundry/domain-packs/hft3/citations/LatencyChainUs.yaml` → `chicago_cme_microstructure_mathematical_model.pdf` §4 |
| **Operator FABLE gate** | µs placement vs ms ack | [FABLE_MINDSET.md](FABLE_MINDSET.md) |

Academic / PDF references:

- **Injection sweep regimes:** `docs/references/chicago_cme_a_plus_production_implementation_prompt.pdf` (Injection sweep) — see [MANIFEST.md](../references/MANIFEST.md)
- **Latency chain validation:** `chicago_cme_microstructure_mathematical_model.pdf` §4, §19 — `LatencyChainUs`, `InjectionSweepResult` sidecars
- **Market-design latency:** Obsidian `library/10 HFT Market Design and Latency.md` (external vault: `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\`)

---

## 3. Corrected minimum metric table

| Tactic | Canonical metric | HftBacktest component |
|--------|-------------------|----------------------|
| Market-data ingress | `feed_latency_us` | Feed |
| Offensive local | `tick_to_send_us` | Local only (not injected as ack) |
| Offensive exchange entry | `new_send_to_exchange_us` | Order-entry |
| Offensive confirmation | `new_exchange_to_ack_us` | Order-response |
| Defensive local | `cancel_decision_to_send_us` | Local only |
| Defensive exchange entry | `cancel_send_to_exchange_us` | Order-entry (cancel) |
| Defensive confirmation | `cancel_exchange_to_ack_us` | Order-response (cancel) |
| Inventory awareness | `fill_exchange_to_local_us` | Order-response (fill) |
| Full local round trip | `new_send_to_ack_us` (distribution) | Entry + response combined |

**Naming rule:** Never use a field named `*_p99_ms` that also holds p50/p99 columns. Use distribution objects: `{ "p50_us": …, "p99_us": …, "count": … }`.

Legacy alias (deprecated): `live_order_ack_p99_ms` → read `new_send_to_ack_ms` distribution instead.

---

## 4. Defensive stale-order math

Two quantities must stay separate:

**Cancel effective time** (adverse fill risk):

```text
cancel_effective ≈ feed_latency + cancel_decision_to_send + cancel_send_to_exchange
```

**Cancel confirmed time** (local uncertainty window):

```text
cancel_confirmed ≈ cancel_effective + cancel_exchange_to_ack
```

The first controls whether the order can still fill against you. The second controls how long the strategy must treat the order as pending cancel.

---

## 5. Backtest regimes

Do **not** combine unrelated quantiles (e.g. p50 entry + p99 response). Prefer replaying timestamped samples.

| Regime | Configuration | Artifact |
|--------|---------------|----------|
| Fast | ~p50 | `latency_model_fast.json` |
| Normal | Empirical samples or p75–p90 | `latency_model_normal.json` + `intp_samples.bin` |
| Stress | p99 | `latency_model_stress.json` |
| Extreme | p99.9, max, or explicit timeout | `latency_model_extreme.json` |
| Burst-conditioned | Samples tagged by MD event rate | `latency_model_burst_*.json` |

Preferred model when dense live data exists: **`IntpOrderLatency`** (`req_ts`, `exch_ts`, `resp_ts`, `_padding`).

Fallback: **`ConstantLatency`** with separate `feed_latency_ms`, `order_entry_latency_ms`, `order_response_latency_ms`.

---

## 6. Queue position + latency coupling

In replay, **order-entry latency** determines when the order reaches the exchange and therefore **initial queue placement**. The queue model determines whether it later fills. Test both together; do not sweep latency without recording queue model choice.

See [CME_M6_SWEEP_CONTROL_PLAN.md](../cockpit/CME_M6_SWEEP_CONTROL_PLAN.md).

---

## 7. Measurement status per band

Each band in `runtime/latency_reports/latency_truth.json` → `component_bands` carries:

| Status | Meaning |
|--------|---------|
| `MEASURED` | Native C++ probe + sufficient samples + clock calibration when exchange timestamps used |
| `INFERRED` | Partial data; exchange segment estimated |
| `OPEN` | Not yet instrumented or campaign not run |
| `UNMEASURED` | Campaign ran but samples missing (e.g. cancel ack timeout) |

Execution realism is **`latency_proxy_only`** or **`research_only`** until all **Critical** bands are `MEASURED`. See realism spec status precedence.

---

## 8. Campaign IDs (CHI404)

| Campaign | Target | Bands |
|----------|--------|-------|
| CC-2 | ≥1000 MD ticks, 10 min | `feed_latency_us` |
| CC-3 | ≥200 paired new orders | `new_send_to_exchange_us`, `new_exchange_to_ack_us`, IntpOrderLatency |
| CC-4 | ≥200 cancels (at-market mode) | `cancel_send_to_exchange_us`, `cancel_exchange_to_ack_us` |
| CC-5 | Opportunistic fills | `fill_exchange_to_local_us` |
| CC-6 | Invalid/throttle stress | modify + reject bands |

Orchestrator: `scripts/chi404_run_latency_component_campaign.sh`

Probe authority: `rithmic_gateway/tools/rithmic_latency_probe.cpp` (`hot_path_language=c++`, `wrapper=none`).

---

## 9. Why FABLE alone was insufficient

FABLE solved operator vocabulary (latency test = µs probe). Backtest accuracy requires **exchange-segmented** entry/response clocks that FABLE deliberately does not cover. The realism spec lived in `docs/project/` outside the vault first-load path, so agents defaulted to `constant_order_latency(single_ms, single_ms)` and `live_order_ack_p99_ms`.

**Read order for backtest latency:**

1. [FABLE_MINDSET.md](FABLE_MINDSET.md)
2. **This file**
3. [HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md)
4. [specs/LATENCY.md](../../specs/LATENCY.md)
5. [docs/LATENCY_BASELINE.md](../LATENCY_BASELINE.md)
