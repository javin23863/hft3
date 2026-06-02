# Rithmic trial validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../../VALIDATION_HONESTY.md)

**Scope-green:** `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py tests/test_rithmic_api_bridge.py tests/test_chi404_canonical_guardrails.py -q`

## Known gaps (open)

1. **Rithmic UAT credentials unauthorized (rp code 13)** — R|API+ `loginRepository` is
   now correctly calling `pCnnctPt : login_agent_repositoryc` (see
   [RAPI_PLUS_HANDOFF §4](../RAPI_PLUS_HANDOFF_2026_06_02.md#4-session-4-misdiagnosis-and-real-fix)),
   but the Rithmic UAT server responds with:
   `AlertInfo : ||Repository Connection Login Failed. Please contact the FCM/IB who issued your login id for assistance.|5|5|13|permission denied`
   This is an account-level rejection on `rithmic_uat_dmz_domain` for the user in
   `RITHMIC_USERNAME`. User action: contact Rithmic / FCM to authorize the account
   on this UAT cluster, or supply paper trading credentials.
2. **R|API+ order callbacks** — wired and tested (commits 67f249c, 42f925b, 00848cc).
   `librithmic_gateway_shared.so` exports `hft_rithmic_adapter_try_pop_order_event`;
   `RithmicApiConnector.poll_order_events()` adapts bridge events to daemon-shaped
   dicts (`order_ack`, `fill`, `cancel`, `order_replace`, `reject`, `order_failure`).
   `paper_latency_daemon` will pair them as soon as (1) is resolved.
3. **Trial quarantine** — must not write into trusted `data/npz/` without explicit approval.
4. **CHI404 `cmake --build` from Windows** — Windows has no MSVC; the C++ shared library
   is built on CHI404 only. Static `rithmic_gateway` target ships MSVC `.lib` archives
   that MinGW cannot link; `hft_research_sim` continues to use the static target.

Live validation requires CHI404 artifacts per [README.md](README.md).
