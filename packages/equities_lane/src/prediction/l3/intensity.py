from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from .event_types import MBOAction, MBOEvent, MBOSide


@dataclass
class HawkesIntensity:
    event_type: str
    intensity: float
    baseline: float
    excitation: float
    decay_rate: float


class HawkesProcessIntensity:
    def __init__(self, decay_rate: float = 0.1):
        self._decay_rate = decay_rate
        self._event_times: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
        self._baselines: dict[str, float] = defaultdict(lambda: 1.0)
        self._alphas: dict[str, float] = defaultdict(lambda: 0.5)

    def add_event(self, event: MBOEvent):
        event_type = self._classify_event(event)
        self._event_times[event_type].append(event.ts_event_ns)

    def _classify_event(self, event: MBOEvent) -> str:
        if event.action == MBOAction.ADD:
            return f"ADD_{event.side.value}"
        elif event.action == MBOAction.CANCEL:
            return f"CANCEL_{event.side.value}"
        elif event.action == MBOAction.MODIFY:
            return f"MODIFY_{event.side.value}"
        elif event.action in (MBOAction.TRADE, MBOAction.EXECUTE):
            return f"TRADE_{event.side.value}"
        elif event.action == MBOAction.FILL:
            return "FILL"
        elif event.action == MBOAction.AUCTION:
            return "AUCTION_IMBALANCE"
        return "OTHER"

    def compute_intensity(self, event_type: str, ts_ns: int) -> HawkesIntensity:
        times = self._event_times[event_type]
        if not times:
            return HawkesIntensity(
                event_type=event_type,
                intensity=self._baselines[event_type],
                baseline=self._baselines[event_type],
                excitation=0.0,
                decay_rate=self._decay_rate,
            )

        baseline = self._baselines[event_type]
        alpha = self._alphas[event_type]

        excitation = 0.0
        for t in times:
            dt_sec = (ts_ns - t) / 1e9
            if dt_sec >= 0:
                excitation += alpha * np.exp(-self._decay_rate * dt_sec)

        intensity = baseline + excitation

        return HawkesIntensity(
            event_type=event_type,
            intensity=intensity,
            baseline=baseline,
            excitation=excitation,
            decay_rate=self._decay_rate,
        )

    def compute_all_intensities(self, ts_ns: int) -> dict[str, HawkesIntensity]:
        event_types = [
            "ADD_BID",
            "ADD_ASK",
            "CANCEL_BID",
            "CANCEL_ASK",
            "MODIFY_BID",
            "MODIFY_ASK",
            "TRADE_BID",
            "TRADE_ASK",
            "FILL",
            "AUCTION_IMBALANCE",
        ]
        return {et: self.compute_intensity(et, ts_ns) for et in event_types}

    def compute_burst_features(self, ts_ns: int) -> dict[str, float]:
        intensities = self.compute_all_intensities(ts_ns)

        features = {}

        ask_cancel_int = intensities.get("CANCEL_ASK")
        trade_buy_int = intensities.get("TRADE_BID")
        bid_add_int = intensities.get("ADD_BID")

        if ask_cancel_int and trade_buy_int:
            features["ignition_burst_score"] = (
                ask_cancel_int.intensity * trade_buy_int.intensity
            )
        else:
            features["ignition_burst_score"] = 0.0

        if ask_cancel_int and bid_add_int:
            features["air_pocket_formation"] = (
                ask_cancel_int.intensity + bid_add_int.intensity
            ) / 2.0
        else:
            features["air_pocket_formation"] = 0.0

        total_excitation = sum(i.excitation for i in intensities.values())
        features["total_self_excitation"] = total_excitation

        if trade_buy_int:
            features["aggressive_buy_intensity"] = trade_buy_int.intensity
        else:
            features["aggressive_buy_intensity"] = 0.0

        return features

    def update_baseline(self, event_type: str, new_baseline: float):
        self._baselines[event_type] = new_baseline

    def update_alpha(self, event_type: str, new_alpha: float):
        self._alphas[event_type] = new_alpha
