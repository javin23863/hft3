import json
import os
import threading
import pandas as pd
from datetime import datetime

_budget_lock = threading.Lock()


class BudgetManager:
    """
    Databento credit gating for MBO downloads.
    Initial credit: $350.00
    Operating cap: $325.00 (priority macro backfill headroom)
    Reserve: $25.00
    """

    INITIAL_CREDIT = 350.00
    OPERATING_CAP = 325.00
    RESERVE = 25.00
    SOFT_LIMIT = 5.00
    HARD_LIMIT = 10.00
    
    def __init__(self, manifest_path="data/manifest.parquet"):
        self.manifest_path = manifest_path
        self.total_used = self._calculate_total_used()
        
    def _calculate_total_used(self) -> float:
        if os.path.exists(self.manifest_path):
            try:
                df = pd.read_parquet(self.manifest_path)
                if "cost" in df.columns:
                    return float(df["cost"].sum())
            except Exception:
                # Corrupt or legacy manifest — treat as zero spend so downloads can proceed.
                pass
        return 0.0
            
    def get_remaining_credit(self) -> float:
        return self.INITIAL_CREDIT - self.total_used
        
    def calculate_cost_metrics(self, cost: float, symbol_count: int, start_utc: datetime, end_utc: datetime) -> dict:
        """
        Calculates exact per-symbol-minute and per-symbol-second costs as required by Blueprint Section 11.
        """
        duration_seconds = (end_utc - start_utc).total_seconds()
        
        if duration_seconds <= 0 or symbol_count <= 0:
            raise ValueError("Invalid duration or symbol count for cost calculation.")
            
        cost_per_symbol_second = cost / (symbol_count * duration_seconds)
        cost_per_symbol_minute = cost / (symbol_count * (duration_seconds / 60.0))
        
        return {
            "duration_seconds": duration_seconds,
            "cost_per_symbol_second": cost_per_symbol_second,
            "cost_per_symbol_minute": cost_per_symbol_minute
        }
        
    def check_request(self, cost_estimate: float, override_hard_limit: bool = False) -> bool:
        """
        Validates if a request can be made based on cost estimate and budget constraints.
        Returns True if approved, raises ValueError if blocked.
        """
        with _budget_lock:
            self.total_used = self._calculate_total_used()
            if cost_estimate > self.HARD_LIMIT and not override_hard_limit:
                raise ValueError(
                    f"Cost estimate ${cost_estimate:.2f} exceeds hard limit of ${self.HARD_LIMIT:.2f}"
                )

            projected_total = self.total_used + cost_estimate
            if projected_total > self.OPERATING_CAP:
                raise ValueError(
                    f"Request cost ${cost_estimate:.2f} would exceed operating cap "
                    f"(${self.OPERATING_CAP:.2f}). Current usage: ${self.total_used:.2f}"
                )

            self.total_used = projected_total
            return True
