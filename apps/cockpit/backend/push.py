"""Problem-only outbound notifier.

Fires a push ONLY when a *new* problem appears (a warn/crit alert id not seen on
the previous recompute) — never on healthy state, never repeatedly for the same
standing problem. When an alert clears and later recurs it notifies again. This
is the "track only if there's a problem" automation: the trader's phone stays
silent until something actually breaks.

Channels (first configured wins, best-effort, failures swallowed):
- ntfy:    NTFY_URL  (full topic URL, e.g. https://ntfy.sh/hft3-cockpit)
- webhook: COCKPIT_NOTIFY_WEBHOOK  (generic JSON POST {title, message})
"""
from __future__ import annotations

import json
import os
from typing import Optional

from . import paths

_NOTIFY_SEVERITIES = {"warn", "crit"}


def channel() -> Optional[str]:
    if os.environ.get("NTFY_URL"):
        return "ntfy"
    if os.environ.get("COCKPIT_NOTIFY_WEBHOOK"):
        return "webhook"
    return None


def notify(title: str, message: str) -> bool:
    """Send one notification. Returns True on apparent success, never raises."""
    ch = channel()
    if ch is None:
        return False
    try:
        import httpx

        if ch == "ntfy":
            url = os.environ["NTFY_URL"]
            r = httpx.post(url, content=message.encode("utf-8"),
                           headers={"Title": title, "Priority": "high"}, timeout=8.0)
            return r.status_code < 400
        url = os.environ["COCKPIT_NOTIFY_WEBHOOK"]
        r = httpx.post(url, json={"title": title, "message": message}, timeout=8.0)
        return r.status_code < 400
    except Exception:
        return False


def _load_seen() -> set[str]:
    data = paths.read_json(paths.ALERT_STATE)
    if isinstance(data, dict) and isinstance(data.get("seen"), list):
        return set(data["seen"])
    return set()


def _save_seen(ids: set[str]) -> None:
    paths.ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.ALERT_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"seen": sorted(ids), "ts": paths.now_iso()}), encoding="utf-8")
    tmp.replace(paths.ALERT_STATE)


def process_alerts(alerts_zone: dict) -> list[str]:
    """Diff vs last seen, notify on new problems, persist. Returns newly-notified ids."""
    alerts = alerts_zone.get("alerts", []) if isinstance(alerts_zone, dict) else []
    current = {a["id"] for a in alerts if a.get("severity") in _NOTIFY_SEVERITIES}
    seen = _load_seen()
    new = current - seen
    if new and channel() is not None:
        fresh = [a for a in alerts if a.get("id") in new]
        body = "\n".join(f"[{a.get('severity')}] {a.get('source')}: {a.get('message')}" for a in fresh)
        notify(f"HFT3 Cockpit: {len(new)} new problem(s)", body)
    # Persist current set (not union) so cleared-then-recurring problems re-alert.
    _save_seen(current)
    return sorted(new)
