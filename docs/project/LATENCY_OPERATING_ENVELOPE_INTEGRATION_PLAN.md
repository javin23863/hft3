# Latency Operating Envelope Integration Plan

## Summary

Integrate latency operating capability into the existing HFT3 Workbench robustness path, not as a standalone latency subsystem. Every Workbench event run, WFC fold, campaign, and model behavior envelope carries placement-speed limits, async acknowledgment risk, composition feasibility, competitor-speed sensitivity, and promotion blockers.

## Requirements

- Generate `latency_operating_envelope.json` and `latency_operating_envelope.md` for every Workbench event run.
- Generate `campaign_latency_operating_envelope.json` and `campaign_latency_operating_envelope.md` for every campaign.
- Keep C++/CHI404 measured latency as the promotion-authoritative source.
- Keep Python wall-clock timing informational only.
- Separate placement speed from acknowledgment latency.
- Treat acknowledgments as asynchronous state confirmation unless an explicit test configuration uses blocking behavior.
- Add envelope checks to the existing robustness registry instead of bypassing it.
- Block promotion when latency operating feasibility fails.
- Carry latency operating bounds into the existing model metrics behavior envelope.

## Acceptance

- Placement metrics include `tick_to_decision_us`, `decision_to_send_us`, and `tick_to_send_us`.
- Confirmation metrics include `send_to_ack_us`, `cancel_to_ack_us`, and `replace_to_ack_us`.
- Offensive, defensive, and hybrid/composition feasibility are reported.
- Competitor-speed and opportunity-decay assumptions are configurable and visible.
- Workbench, campaign, WFC, robustness, and model metrics remain the authority.
