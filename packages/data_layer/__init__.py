"""Post-run after-action layer — OpenFoundry bridge, packet, symbolic, LLM."""

from __future__ import annotations

__all__ = ["run_after_action_report"]

def run_after_action_report(*args, **kwargs):
    from data_layer.pipeline.after_action import run_after_action_report as _run_after_action_report

    return _run_after_action_report(*args, **kwargs)
