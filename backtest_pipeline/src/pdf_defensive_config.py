"""Plug-and-play defensive layer toggles for PDF_MODEL_4 hybrid replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List


@dataclass(frozen=True)
class DefensiveConfig:
    """Enable/disable PDF_MODEL_1 (OFI) and PDF_MODEL_3 (VPIN) inputs to PDF_MODEL_4."""

    use_ofi: bool = True
    use_vpin: bool = True

    @property
    def mode_id(self) -> str:
        if self.use_ofi and self.use_vpin:
            return "hybrid_full"
        if self.use_ofi and not self.use_vpin:
            return "ofi_only"
        if not self.use_ofi and self.use_vpin:
            return "vpin_only"
        return "as_baseline"

    @property
    def description(self) -> str:
        labels = {
            "hybrid_full": "Avellaneda-Stoikov + OFI drift + VPIN-scaled lambda/toxic flags",
            "ofi_only": "AS + OFI drift (VPIN inputs zeroed; no toxic cancel from VPIN)",
            "vpin_only": (
                "AS + VPIN lambda path (ofi_smooth=1.0 unit probe, not book OFI) + toxic flags"
            ),
            "as_baseline": "Pure Avellaneda-Stoikov (no OFI/VPIN defensive inputs)",
        }
        return labels[self.mode_id]


def all_defensive_configs() -> List[DefensiveConfig]:
    """Four-way ablation matrix: baseline, OFI-only, VPIN-only, full hybrid."""
    return [
        DefensiveConfig(use_ofi=False, use_vpin=False),
        DefensiveConfig(use_ofi=True, use_vpin=False),
        DefensiveConfig(use_ofi=False, use_vpin=True),
        DefensiveConfig(use_ofi=True, use_vpin=True),
    ]


def iter_ablation_configs(
    *,
    include_baseline: bool = True,
    include_ofi_only: bool = True,
    include_vpin_only: bool = True,
    include_full: bool = True,
) -> Iterator[DefensiveConfig]:
    for cfg in all_defensive_configs():
        if cfg.mode_id == "as_baseline" and not include_baseline:
            continue
        if cfg.mode_id == "ofi_only" and not include_ofi_only:
            continue
        if cfg.mode_id == "vpin_only" and not include_vpin_only:
            continue
        if cfg.mode_id == "hybrid_full" and not include_full:
            continue
        yield cfg
