"""Map lane ticks to structural model outputs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from equities_lane.src.features.l3_stubs import compute_l3_features
from equities_lane.src.models import SessionTick
from equities_lane.src.types import DegradedModeFlags, FeatureToggles

if TYPE_CHECKING:
    from equities_lane.src.options.chain_loader import OptionsChainLoader


@dataclass
class FeatureSnapshot:
    ofi_zscore: float = 0.0
    mlofi_pc1: float = 0.0
    vpin_value: float = 0.0
    vpin_percentile: float = 0.0
    hawkes_score: float = 0.0
    hmm_state: str = "unknown"
    hmm_markup_prob: float = 0.0
    l3: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    degraded_assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ofi_zscore": self.ofi_zscore,
            "mlofi_pc1": self.mlofi_pc1,
            "vpin_value": self.vpin_value,
            "vpin_percentile": self.vpin_percentile,
            "hawkes_score": self.hawkes_score,
            "hmm_state": self.hmm_state,
            "hmm_markup_prob": self.hmm_markup_prob,
            "l3": self.l3,
            "options": self.options,
            "degraded_assumptions": self.degraded_assumptions,
        }


def compute_features(
    ticks: list[SessionTick],
    toggles: FeatureToggles,
    degraded: DegradedModeFlags,
    options_loader: "OptionsChainLoader | None" = None,
) -> list[FeatureSnapshot]:
    from features_engine.src.structural_models.model_01_book_pressure import BookPressureModel
    from features_engine.src.structural_models.model_03_vpin_toxicity import VPINToxicityModel
    from features_engine.src.structural_models.model_11_hawkes_toxic import HawkesToxicFlowModel

    from equities_lane.src.features.hmm_regime import infer_regime

    book = BookPressureModel() if toggles.ofi else None
    vpin_model = VPINToxicityModel() if toggles.vpin else None
    hawkes = HawkesToxicFlowModel() if toggles.hawkes else None

    snapshots: list[FeatureSnapshot] = []
    event_times: list[float] = []
    vpin_history: list[float] = []

    for i, t in enumerate(ticks):
        ts_sec = t.ts_ns / 1e9
        snap = FeatureSnapshot(degraded_assumptions=list(degraded.assumptions))

        if book and toggles.ofi:
            out = book.update_bbo(
                t.bid_px, t.bid_sz, t.ask_px, t.ask_sz, t.ts_ns
            )
            snap.ofi_zscore = float(out.payload.OFI_zscore)
            snap.mlofi_pc1 = float(out.payload.MLOFI_PC1)

        if vpin_model and toggles.vpin and t.trade_px and t.trade_sz:
            mid = (t.bid_px + t.ask_px) / 2.0 if t.bid_px and t.ask_px else t.trade_px
            out = vpin_model.evaluate(
                timestamp_ns=t.ts_ns,
                price=float(t.trade_px),
                volume=float(t.trade_sz),
                mid=float(mid),
                aggressor=t.aggressor,
            )
            if out:
                snap.vpin_value = float(out.payload.VPIN_value)
                snap.vpin_percentile = float(out.payload.VPIN_percentile)
                vpin_history.append(snap.vpin_value)

        if hawkes and toggles.hawkes and t.event == "trade":
            event_times.append(ts_sec)
            out = hawkes.evaluate(
                timestamp_ns=t.ts_ns,
                t=ts_sec,
                market_order_times=event_times,
            )
            snap.hawkes_score = float(out.payload.toxic_cascade_score)

        if toggles.hmm:
            regime = infer_regime(snap.mlofi_pc1, snap.vpin_value, snap.ofi_zscore)
            snap.hmm_state = regime.state
            snap.hmm_markup_prob = regime.markup_prob

        l3 = compute_l3_features(toggles, degraded)
        snap.l3 = l3.to_dict()

        if options_loader is not None:
            spot = (t.bid_px + t.ask_px) / 2.0 if (t.bid_px and t.ask_px) else (t.trade_px or 0.0)
            if spot > 0:
                opt_snap = options_loader.to_snapshot(t.ts_ns, spot)
                snap.options = opt_snap.to_dict()
            else:
                snap.options = {"spot": 0.0, "num_quotes": 0, "coverage": 0.0}

        snapshots.append(snap)

    return snapshots
