"""Fixing-VWAP replication utilities — WS-3.4.

ZERO-ALPHA hedge operations plumbing.  This package schedules futures child
orders so that a position in expiring European ES options can be delta-
flattened at approximately the CME 15:00 CT 30-second VWAP settlement price.

Rule 575 boundary
-----------------
The replicator operates with PASSIVE intent only.  It produces a time-bucketed
schedule and tracking math; it does NOT place orders or instruct the transport
layer to be aggressive.  The transport layer (not this module) decides fill
style for each slice.  Never use this schedule to trade in a way that is
intended to or likely to affect the fixing — that would violate CME Rule 575
and Reg T market manipulation prohibitions.  Sizes generated here are benign
relative to typical fixing-window volume; operator verification is required
before live use.

Public surface
--------------
    FixingWindow         — DST-correct start/end UTC datetimes for the window
    ReplicationSchedule  — bucketed child quantities summing exactly to target
    make_schedule        — factory: target_qty + n_slices → ReplicationSchedule
    vwap_from_trades     — VWAP helper consistent with fixing_window_study
    replication_error    — signed slippage in ticks vs realized fixing VWAP
"""

from options_lane.src.fixing.vwap_engine import (
    FixingWindow,
    ReplicationSchedule,
    Slice,
    make_schedule,
    replication_error,
    vwap_from_trades,
)

__all__ = [
    "FixingWindow",
    "ReplicationSchedule",
    "Slice",
    "make_schedule",
    "replication_error",
    "vwap_from_trades",
]
