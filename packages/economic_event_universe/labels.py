"""Map event_type + window_name -> E_t context label (filtration-safe)."""

from __future__ import annotations

from economic_event_universe.registry import event_definitions


def row_to_event_context(event_type: str, window_name: str) -> str:
    et = str(event_type)
    wn = str(window_name)
    defs = event_definitions()
    if et in defs:
        label = defs[et].get("event_context_label")
        if label:
            if wn == "MAIN" and defs[et].get("main_context_label"):
                return str(defs[et]["main_context_label"])
            return str(label)
    if wn == "TIGHT":
        if et == "CPI":
            return "CPI_TIGHT"
        if et == "NFP":
            return "NFP_TIGHT"
    if et == "PROP_REOPEN":
        return "PROP_REOPEN"
    if et == "CASH_EQUITY_OPEN" or et.endswith("_OPEN"):
        return "CASH_EQUITY_OPEN"
    if et == "PROP_FLATTEN_TOPSTEP":
        return "PROP_FLATTEN_TOPSTEP"
    if "FRIDAY" in et:
        return "FRIDAY_CLOSE"
    if "APEX" in et:
        return "APEX_FLATTEN"
    if "TPT" in et or "MyFunded" in et:
        return "TPT_FLATTEN"
    if "NEWS" in et:
        return "NEWS_RESTRICTION"
    return et
