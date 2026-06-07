import os
import threading

import databento as db
from datetime import datetime, timezone

from .budget_manager import BudgetManager
from .manifest_io import append_manifest_record

_manifest_lock = threading.Lock()

class DatabentoResearchClient:
    """
    Controlled Databento downloader using metadata.get_cost for budget gating.
    Records metadata in manifest.parquet.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DATABENTO_API_KEY")
        if not self.api_key:
            raise ValueError("DATABENTO_API_KEY must be set")
            
        self.client = db.Historical(self.api_key)
        self.manifest_path = "data/manifest.parquet"
        self.budget = BudgetManager(self.manifest_path)
        
    def estimate_cost(
        self,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        schema="mbo",
        stype_in: str = "continuous",
    ) -> float:
        return self._estimate(schema, dataset, symbols, start_utc, end_utc, stype_in)

    def estimate_cost_mbp10(
        self,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        stype_in: str = "continuous",
    ) -> float:
        return self._estimate("mbp-10", dataset, symbols, start_utc, end_utc, stype_in)

    def _estimate(
        self,
        schema: str,
        dataset: str,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        stype_in: str,
    ) -> float:
        return float(
            self.client.metadata.get_cost(
                dataset=dataset,
                schema=schema,
                symbols=symbols,
                stype_in=stype_in,
                start=start_utc,
                end=end_utc,
            )
        )

    def download_mbp10_window(
        self,
        event_id: str,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        stype_in: str = "continuous",
        output_path: str | None = None,
        override_hard_limit: bool = False,
        override_operating_cap: bool = False,
    ) -> str:
        """Download MBP-10 aggregated depth (not order-level MBO).

        Out of macro MBO-only lane — do not call from download_mbo_release_data.py
        or default macro scripts.
        """
        return self.download_event_window(
            event_id,
            symbols,
            start_utc,
            end_utc,
            dataset=dataset,
            schema="mbp-10",
            stype_in=stype_in,
            output_path=output_path,
            override_hard_limit=override_hard_limit,
            override_operating_cap=override_operating_cap,
        )

    def download_event_window(
        self,
        event_id: str,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        schema="mbo",
        stype_in: str = "continuous",
        requested_symbol: str | None = None,
        output_path: str | None = None,
        override_hard_limit: bool = False,
        override_operating_cap: bool = False,
        cost_estimate: float | None = None,
    ):
        """
        Calculates exact cost per Section 11 math, checks budget, and downloads if approved.
        """
        dest = output_path or f"data/{event_id}_{schema}.dbn.zst"
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return dest

        if cost_estimate is None:
            cost_estimate = self.estimate_cost(
                symbols, start_utc, end_utc, dataset, schema, stype_in
            )

        if override_operating_cap:
            if cost_estimate > self.budget.HARD_LIMIT and not override_hard_limit:
                raise ValueError(
                    f"Cost estimate ${cost_estimate:.2f} exceeds hard limit "
                    f"${self.budget.HARD_LIMIT:.2f}"
                )
        else:
            self.budget.check_request(cost_estimate, override_hard_limit=override_hard_limit)

        self.client.timeseries.get_range(
            dataset=dataset,
            schema=schema,
            symbols=symbols,
            stype_in=stype_in,
            start=start_utc,
            end=end_utc,
            path=dest,
        )

        metrics = self.budget.calculate_cost_metrics(
            cost_estimate, len(symbols), start_utc, end_utc
        )

        resolved_symbol = symbols[0] if symbols else ""
        record = {
            "event_id": event_id,
            "symbols": str(symbols),
            "requested_symbol": requested_symbol or resolved_symbol,
            "resolved_symbol": resolved_symbol,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "cost": cost_estimate,
            "duration_seconds": metrics["duration_seconds"],
            "cost_per_symbol_minute": metrics["cost_per_symbol_minute"],
            "output_path": dest,
            "dataset": dataset,
            "schema": schema,
            "download_time": datetime.now(timezone.utc),
        }

        self._record_manifest(record)
        return dest
        
    def _record_manifest(self, record: dict):
        with _manifest_lock:
            append_manifest_record(self.manifest_path, record)
