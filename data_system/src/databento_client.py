import os
import databento as db
from .budget_manager import BudgetManager
import pandas as pd
from datetime import datetime, timezone

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
        
    def download_event_window(
        self,
        event_id: str,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        schema="mbo",
        stype_in: str = "continuous",
    ):
        """
        Calculates exact cost per Section 11 math, checks budget, and downloads if approved.
        """
        # 1. Get cost estimate
        cost_estimate = self.client.metadata.get_cost(
            dataset=dataset,
            schema=schema,
            symbols=symbols,
            stype_in=stype_in,
            start=start_utc,
            end=end_utc,
        )
        
        # 2. Check budget constraints
        self.budget.check_request(cost_estimate)
        
        # 3. Request data
        output_path = f"data/{event_id}_{schema}.dbn.zst"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        self.client.timeseries.get_range(
            dataset=dataset,
            schema=schema,
            symbols=symbols,
            stype_in=stype_in,
            start=start_utc,
            end=end_utc,
            path=output_path,
        )
        
        # 4. Calculate required cost metrics per blueprint
        metrics = self.budget.calculate_cost_metrics(cost_estimate, len(symbols), start_utc, end_utc)
        
        # 5. Record to manifest.parquet
        record = {
            "event_id": event_id,
            "symbols": str(symbols),
            "start_utc": start_utc,
            "end_utc": end_utc,
            "cost": cost_estimate,
            "duration_seconds": metrics["duration_seconds"],
            "cost_per_symbol_minute": metrics["cost_per_symbol_minute"],
            "output_path": output_path,
            "dataset": dataset,
            "schema": schema,
            "download_time": datetime.now(timezone.utc),
        }
        
        self._record_manifest(record)
        return output_path
        
    def _record_manifest(self, record: dict):
        df_new = pd.DataFrame([record])
        if os.path.exists(self.manifest_path):
            df_existing = pd.read_parquet(self.manifest_path)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_parquet(self.manifest_path)
        else:
            os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
            df_new.to_parquet(self.manifest_path)
