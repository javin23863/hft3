# Rithmic trial validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../../VALIDATION_HONESTY.md)

**Scope-green:** `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py tests/test_rithmic_api_bridge.py tests/test_chi404_canonical_guardrails.py -q`

## Known gaps (open)

1. **R|API+ order callbacks** — `librithmic_gateway_shared.so` `try_pop_event` is wired for
   market data only; `orderSubmit` / `orderAck` callbacks are not yet pushed into the SPSC
   queue. `paper_latency_daemon` paired count stays at 0 until that lands
   (see [RAPI_PLUS_HANDOFF_2026_06_02.md §3](../RAPI_PLUS_HANDOFF_2026_06_02.md#3-known-gaps)).
2. **R|API+ UAT port 45454** — `loginRepository` requires MML_LOGGER_ADDR (TCP 45454),
   which is firewalled by Rithmic for free UAT accounts. Workaround: ask Rithmic to
   whitelist `64.44.98.219`, supply paper creds, or roll back to `rtrader`.
3. **Trial quarantine** — must not write into trusted `data/npz/` without explicit approval.
4. **CHI404 `cmake --build` from Windows** — Windows has no MSVC; the C++ shared library
   is built on CHI404 only. Static `rithmic_gateway` target ships MSVC `.lib` archives
   that MinGW cannot link; `hft_research_sim` continues to use the static target.

Live validation requires CHI404 artifacts per [README.md](README.md).
