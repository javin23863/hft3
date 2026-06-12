# Rithmic / FCM vendor question list and owner-action memo

**STATUS: DRAFT FOR OWNER — agent-written, owner must review before sending. Do not send as-is.**

*Generated 2026-06-12 from ws0-1-rithmic-fop-capability.md, ws0-6-latency-truth-closure.md, and runtime/latency_reports/latency_truth.json (CHI404 measurements, 2026-06-11).*

---

## Why now

- **Conformance gates everything.** The trial R|API+ SDK v13.7.0.0 ships no `Rithmic 01` (live) `connection_params.txt`; that file is issued only after conformance testing passes (ws0-6, `live_unknown.sdk_finding`). Conformance must also pass before Paper Trading API access — no live or even paper flow until it clears. Every downstream plan item (latency measurement, options execution, UDS spread testing) is blocked until conformance starts. Timeline public estimate: days for simple integrations, weeks-to-longer for complex systems (ws0-1, conformance section).
- **Critical FOP capability gaps are unconfirmed in public sources.** RFQ submission, exercise/assignment callback delivery, subscribeByUnderlying fan-out limits under ~1,000-instrument 0-45DTE chains, and submitQuoteList permissions for non-market-maker accounts are all publicly unconfirmed (ws0-1, vendor question list Q2/Q5/Q7/Q4). The architecture of WS-2 (order entry), WS-3 (chain subscription), and WS-5 (expiry handling) depends on answers.
- **Live wire latency is completely unmeasured.** `latency_truth.json` records `live_wire.status = "OPEN - not closeable without live Rithmic 01 credentials"`. The best-available inference is ~1–4 ms TCP RTT from CHI404 to a co-located Paper Trading cluster node (ritpz01004, 184.105.22.229, 1.023 ms TCP p50), but `how_to_close.live_wire` states this requires Rithmic 01 `connection_params.txt` obtained post-conformance. The paper order endpoint (ritpz04031, 38.98.144.227) is reachable at 3.687 ms TCP p50 / 4.144 ms p99 from CHI404 (network RTT only). Actual paper-system order-ack latency is higher: 4.192 ms p50 / 13.687 ms p99 per `latency_truth.json` MESU6 test run. Live equivalent latencies are unknown and unbudgetable without Rithmic confirmation.

---

## Email draft (to rapi@rithmic.com)

> **To:** rapi@rithmic.com
> **Cc:** [FCM rep name and email — owner to insert]
> **Subject:** R|API+ conformance scheduling, FOP capability confirmation, and live latency questions — [Your Company Name]
>
> Hello,
>
> We are a proprietary trading firm developing an automated options-on-futures execution system targeting CME ES and NQ weekly/daily expirations (0–45 DTE). Our FCM is [FCM name — owner to insert], and our infrastructure is co-located at CHI404 (Aurora-area). We are currently running R|API+ v13.7.0.0 against the Rithmic Paper Trading system and need to schedule conformance testing and resolve several open capability questions before committing to the architecture design.
>
> **1. Conformance test scheduling**
>
> We would like to schedule R|API+ conformance testing for Rithmic 01 (live system) at your earliest availability. Our side is ready to run the full test suite: connection management and heartbeats, market-data subscription handling, full order lifecycle (new/modify/cancel/partial-fill/reject for both futures and FUTURE_OPTION instruments), disconnect/recovery/resync, error handling, risk-limit adherence (we will pre-coordinate FCM-set limits), message-rate throttling, and timestamp/logging compliance. We are requesting:
>
> - Your current conformance scheduling queue / estimated lead time for a new applicant in 2026.
> - Whether options order flow (FUTURE_OPTION instruments on ES/NQ) is included in the standard test script or requires an additional session.
> - Whether conformance is per-application (4-char app prefix) or per-developer, and whether any fee applies beyond FCM facilitation.
> - The list of deliverables we need to provide before scheduling: development agreement, app name, FCM sponsorship letter, etc.
> - How to obtain the Rithmic 01 `connection_params.txt` and SSL certificate file after passing, and which gateway we would be assigned for a CHI404 client.
>
> **2. FOP capability confirmation — numbered questions (please answer inline)**
>
> 1. Is CME options-on-futures order routing enabled end-to-end for ES and NQ weekly and daily expirations on R|API+ / Rithmic 01? Specifically: are LIMIT, MARKET, and STOP_LIMIT order types accepted for FUTURE_OPTION instruments, or is options entry restricted to LIMIT and MARKET_WITH_PROTECTION at the iLink layer (CME currently routes options through iLink MKT_WITH_PROT semantics)?
>
> 2. Is there any path in R|API+ or R|Diamond API to submit a CME RFQ (FIX 35=R / iLink QuoteRequest message) from a client application? If not, is this on the roadmap? This is material for 0–45DTE spread liquidity sourcing.
>
> 3. Does `createUserDefinedSpread` (REngine, R|API+) map to CME-listed UDS for options strategies — verticals, straddles, and calendar spreads with a futures hedge leg? Specifically: which `pStrategyType` values are accepted for options-leg UDS, what is the typical creation-to-tradable latency on Rithmic 01, and is UDS creation available via Protocol API (protobuf), or only via the compiled R|API+ C++ library?
>
> 4. Is `submitQuoteList` (R|API+ mass quoting API) available to a regular DMA customer account, or is it restricted to registered market-maker programs? Does it map to CME iLink Mass Quote (35=i) with quote-protection fields, and is it supported for FUTURE_OPTION instruments?
>
> 5. How are exercise, auto-exercise at expiry, early exercise election, assignment, and abandonment notifications surfaced to an R|API+ session? Is there a dedicated callback, or do resulting futures positions appear only via position/PnL replay (`replayPnl`) at next login, or only via FCM back-office clearing files? Can early exercise be submitted programmatically via API?
>
> 6. Is Depth-by-Order (MBO) data available for CME options-on-futures instruments via `subscribeDbo`? CME GLBX.MDP3 publishes options MBO, but we have not been able to confirm that Rithmic exposes this for options. What are the aggregated order-book depth levels available for options subscriptions, and are there incremental exchange data fees for options DBO?
>
> 7. What are the scaling limits for `subscribeByUnderlying`? We plan to subscribe full 0–45DTE chains on ES and NQ simultaneously (~1,000 option instruments at peak). What is the maximum number of symbols or subscriptions per R|API+ session, and are there server-side market-data throttles or `kMaxSymbols`-style caps we need to design around?
>
> 8. Does `cancelAllOrders` (REngine) include resting quotes submitted via `submitQuoteList`, UDS leg orders, and options orders? What are the cancel-on-disconnect / kill-switch semantics — does the Rithmic OMS cancel all open orders on session drop, and is this configurable per account or per app?
>
> 9. What are the current R|API+ (non-Diamond) expected order-ack latency ranges for FUTURE_OPTION instruments on Rithmic 01 from a CHI404 co-located client? We have measured 3.687 ms TCP p50 / 4.144 ms p99 to the Paper Trading order endpoint (ritpz04031.04.theomne.net) from CHI404 — do you have internal reference latency statistics for the Rithmic 01 live TS endpoint under normal load from a CHI404 cross-connect? We are specifically asking for order-received-by-Rithmic-gateway to exchange-ack timing, not end-to-end.
>
> 10. What are the eligibility criteria, pricing, and conformance requirements for R|Diamond API? We understand it requires Aurora co-location, C++/Linux, and provides sub-250 µs transit by connecting directly to Rithmic's exchange-facing gateways. Is Diamond available for FUTURE_OPTION instruments, and is a separate conformance test required?
>
> 11. For the R Protocol API (protobuf / `async_rithmic`-style clients): is the feature set for options instruments — specifically `createUserDefinedSpread`, `submitQuoteList`, and `cancelQuoteList` — available on the Protocol API path, or only via the compiled R|API+ C++ library? The public proto set does not appear to contain UDS creation messages.
>
> 12. Does Rithmic publish or distribute an options greeks / implied volatility feed for subscribed instruments, or is the market data output limited to price, settlement, open interest, and depth?
>
> **3. FOP capability confirmation — continued**
>
> 13. On the R|API+ order-record callbacks, the SDK exposes `iSsboe` and `iUsecs` fields (time received by Rithmic) and equivalent simulator-received timestamps. For live Rithmic 01 orders, which timestamp fields are populated on order acknowledgement callbacks: Rithmic-gateway-receive time, CME exchange-ack time (TransactTime from iLink), or both? Is there a field that carries the exchange-side SendingTime or TransactTime from the iLink fill/ack message?
>
> We recognize this is a substantial list (13 questions above). Please feel free to answer inline by question number. We are happy to schedule a call if that is faster.
>
> Thank you,
> [Owner name]
> [Company]
> [Phone / email]

---

## FCM parallel ask (short)

> **To:** [FCM rep — owner to insert name/email]
> **Subject:** Rithmic conformance sponsorship + options capability check
>
> Hi [name],
>
> We are ready to start R|API+ conformance testing for Rithmic 01. A few things we need from your side:
>
> 1. Can you send the conformance sponsorship introduction to rapi@rithmic.com on our behalf, or confirm the correct procedure for your FCM?
> 2. We need to confirm that options-on-futures order routing (ES/NQ weekly/daily expirations) is enabled on our account for R|API+ — can you verify that the FOP entitlement is live, and confirm the fee structure ($0.10/contract routing — does that apply equally to options)?
> 3. We are asking Rithmic about the assigned order gateway for CHI404 clients and whether a 10Gb cross-connect from CHI404 into TheOmne DC04 exchange-facing routers is possible without renting TheOmne hardware. Can you advise whether your FCM has done this for other clients, and who at Rithmic / TheOmne to contact?
>
> Thanks,
> [Owner name]

---

## What unblocks what

| Answer received | Plan item gated | Workstream |
|---|---|---|
| Conformance scheduled + Rithmic 01 `connection_params.txt` issued | Live wire latency measurement (`latency_truth.json` `how_to_close.live_wire`); switch from Paper to Live; cancel-ack measurement (paper simulator does not return cancel-ack for far-from-market orders) | WS-0 latency closure; WS-2 order entry go-live |
| Q1: LIMIT/MARKET/STOP confirmed for FUTURE_OPTION on Rithmic 01 | Order-type enum selection in order entry module; CME MKT_WITH_PROT handling in error path | WS-2 order entry |
| Q2: RFQ path confirmed or ruled out | Architecture decision: RFQ solicitation vs screen-liquidity-only for spreads; if absent, use UDS + passive resting only | WS-3 execution strategy |
| Q3: UDS creation confirmed, pStrategyType values, Protocol API availability | Spread order entry design; whether to build UDS creation in C++ lib or Protocol API path | WS-3 spread execution |
| Q4: submitQuoteList permissions for DMA | Two-sided quoting design for options market-making path; if restricted, quote via individual limit orders only | WS-3 quoting architecture |
| Q5: exercise/assignment callback confirmed | Expiry and exercise handling module design; determines whether FCM clearing file reconciliation is mandatory on session start | WS-5 expiry/exercise handling |
| Q6: options DBO confirmed + depth levels | Order-book feature depth budget; whether to rely on BBO + aggregated depth or add DBO subscription | WS-1 market data |
| Q7: subscribeByUnderlying limits + throttles | Chain subscription architecture: single-session vs multi-session fan-out; whether ~1,000-instrument subscription fits one session | WS-1 market data |
| Q8: cancelAllOrders scope (quotes + UDS) | Kill-switch design; cancel-on-disconnect safety net; whether separate quote cancel is required at shutdown | WS-2 order entry safety |
| Q9: live Rithmic 01 ack latency reference stats | Latency budget for 0DTE quoting cancel-replace loop; determines whether R|API+ is sufficient or Diamond escalation is required | WS-0 latency closure; WS-6 performance budget |
| Q10: Diamond eligibility + FUTURE_OPTION support | Whether to open parallel Diamond track for sub-ms options execution | WS-6 performance / future |
| Q11: Protocol API vs R|API+ feature parity for UDS/quotes | Client library choice: compiled C++ R|API+ required vs pure-Python Protocol API sufficient | WS-2 / WS-3 infrastructure |
| Q12: greeks/IV feed availability | Whether to compute IV internally (already planned per ws0-1 recommendation) or whether Rithmic provides a feed | WS-1 market data |
| Q13 (timestamp fields): exchange-ack time in order callbacks | `latency_truth.json` `how_to_close`: MD wire-to-callback and cancel-ack instrumentation; determines whether exchange-side timestamps are available for latency decomposition without external loopback | WS-0 latency closure |
| FCM Q3: CHI404 cross-connect into TheOmne DC04 | Network topology decision; determines achievable wire latency floor (ritpz01004 at ~1ms TCP is the current best evidence of a co-located node — cross-connect could reach it) | WS-0 / WS-6 infrastructure |

---

*All latency figures in this memo cite `runtime/latency_reports/latency_truth.json` (run_host CHI404, measured 2026-06-11). FOP capability citations reference `docs/ops/ws0-1-rithmic-fop-capability.md`. Latency inference citations reference `docs/ops/ws0-6-latency-truth-closure.md`.*
