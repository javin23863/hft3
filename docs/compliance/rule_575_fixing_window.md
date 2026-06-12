# Rule 575 Fixing-Window Control

**STATUS: DRAFT — owner and compliance review required before production use.**

Plan reference: WS-3.4 (fixing VWAP replication engine)
Date drafted: 2026-06-12
Regulation: CME Rule 575 (disruptive practices)

---

## Background

CME Rule 575 prohibits transactions, orders, or messages that constitute
disruptive practices. Relevant prohibitions include:

- Conduct that is "disruptive of fair and equitable trading or market
  operations" (Rule 575.A).
- Any order or trade intended to create or cause to be reported an artificial
  price for any futures contract (Rule 575.B, implied by existing exchange
  guidance).
- Spoofing, layering, and momentum ignition are named examples; the rule is
  broader than the enumerated examples.

Reference: CME Rule 575 as published in the CME Rulebook. See also current CME
MRAN on disruptive practices (no specific MRAN document number is cited here
because MRAN numbers and versions change; always consult the current CME MRAN
catalog at cmegroup.com for the operative guidance at time of review).

---

## The Specific Risk: 14:55–15:00:30 CT Window

CME ES and NQ European weekly and EOM options settle at the CME 15:00 CT
Special Opening Quotation (SOQ) fixing, derived from a 30-second VWAP of the
underlying ES or NQ futures over the window 14:59:30–15:00:00 CT (the "fixing
window"). The wider 14:55–15:00:30 CT window is operationally significant
because:

- Orders entered in the 14:55–15:00 CT period can participate in the fixing
  window (entered before fixing start but potentially filling within it).
- Orders entered in the 14:59:30–15:00:30 CT period participate in or
  immediately follow the fixing itself.
- An operator holding expiring options positions has a financial interest in
  the settlement price, creating a motive for manipulation even where none is
  intended.

Any pattern of aggressive underlying orders in this window — while holding
expiring options exposure — that could be construed as intended to influence
the fixing price falls within the conduct Rule 575 prohibits.

---

## Standing Control

**The only permitted underlying (futures) activity in the 14:55–15:00:30 CT
window while the operator holds expiring options exposure is:**

1. Order schedules carrying `intent_tag = 'FIXING_REPLICATION_PASSIVE'` as
   set by `packages/options_lane/src/fixing/vwap_engine.py` (the
   `ReplicationSchedule.intent_tag` field).
2. The schedule must be sized to hedge-replication need only — the target
   quantity (`ReplicationSchedule.target_qty`) must equal the net delta of
   expiring options positions at the time the schedule is built, and must not
   exceed it.
3. All child order aggression decisions are the transport layer's
   responsibility. The `vwap_engine.py` module produces a schedule only; it
   does not set order aggressiveness. The transport layer **must** classify
   child orders as passive (e.g., limit orders away from the market) or
   proportional liquidity-taking in line with expected volume. Aggressive
   orders crossing the spread and intended to move the price are prohibited.

**Prohibited in this window while holding expiring options exposure:**

- Any aggressive underlying order not tagged `FIXING_REPLICATION_PASSIVE`.
- Any order sized materially in excess of the hedge-replication need.
- Any order or sequence of orders whose placement or cancellation is intended
  to create artificial price pressure on the fixing VWAP.
- Spoofing, layering, or momentum ignition on the underlying during the window.

**Outside the window:** normal risk and order management applies. If the fixing
window has passed and expiring positions have been closed or delivery confirmed,
the underlying restriction lifts.

---

## Implementation Reference

The replication engine that implements the permitted activity is:

```
packages/options_lane/src/fixing/vwap_engine.py
  ReplicationSchedule.intent_tag  →  'FIXING_REPLICATION_PASSIVE'
  make_schedule(expiry_date, target_qty, n_slices=6)
```

The `intent_tag` field is immutable — frozen dataclass (decorator at line 127);
`intent_tag` default at line 162 of
`packages/options_lane/src/fixing/vwap_engine.py` —
and is set unconditionally by the factory function. Any schedule produced by
`make_schedule` carries this tag. The transport layer must check for this tag
before routing underlying orders in the window.

The fixing datetime (15:00 CT) and the 30-second window start (14:59:30 CT) are
computed DST-correctly by:

```
packages/options_data/src/expiry_calendar.fixing_datetime_utc(expiry_date)
```

---

## Monitoring

Post-trade review must be performed for any execution in the following scope:

- Instrument: underlying futures (ES, NQ, MES, MNQ) on any expiry day.
- Time: any fill with a timestamp in [14:55:00, 15:00:30] CT.
- Condition: at least one options position is open in a same-underlying
  expiring series at the time of the fill.

Review must confirm:

1. The fill corresponds to a schedule with `intent_tag = 'FIXING_REPLICATION_PASSIVE'`.
2. The aggregate signed quantity of fills in the window does not materially
   exceed the net delta of the expiring options position.
3. No fills exist in the window that are not attributable to a replication
   schedule (i.e., no unattributed underlying orders).

Monitoring is currently a manual post-trade process. Automated monitoring is a
Phase 2 item (cross-reference `options_kill_switch.yaml` trigger
`expiring_position_unmanaged`).

---

## Escalation Path

If the post-trade review identifies:

- Fills in the fixing window not tagged `FIXING_REPLICATION_PASSIVE`: treat as
  potential rule violation. Do not trade. Contact compliance immediately.
- Aggregate fills materially exceeding hedge-replication need: treat as
  potential rule violation. Do not trade pending review.
- Any pattern suggesting artificial price influence: self-report to CME and
  contact legal counsel as required by exchange agreement.

Escalation chain (owner to fill in):

1. On-call risk officer / senior trader.
2. Compliance officer.
3. CME Market Regulation (if required by CME agreement).
4. Legal counsel.

---

## Open Items

- [ ] Owner and compliance officer review and sign-off required.
- [ ] Automated monitoring implementation (Phase 2: cross-reference expiring OI
  in post-trade fill records; no statistics backfill available in Phase 0–1).
- [ ] Define "materially in excess" quantitatively (e.g., more than 1 contract
  above hedge-replication need) — currently qualitative.
- [ ] Confirm window bounds with FCM (14:55–15:00:30 is operationally defined
  here; verify against current CME bulletins on SOQ windows for the specific
  weekly/EOM expiry types traded).
- [ ] Review whether MES/MNQ micro options settle on the same SOQ fixing or a
  separate one; confirm with CME if trading micro options.
