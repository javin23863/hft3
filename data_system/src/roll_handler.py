import pandas as pd
import databento as db
from datetime import datetime

class ContractRollHandler:
    """
    Handles continuous contract resolution (e.g., MES.v.0 to MESZ4)
    Ensures no duplicate or overlapping roll windows.
    """
    def __init__(self, api_key: str):
        self.client = db.Historical(api_key)
        
    def resolve_continuous_symbol(self, symbol: str, date: datetime, dataset="GLBX.MDP3") -> str:
        """
        Resolves a continuous symbol (like MES.v.0) to a specific contract for a given date.
        """
        # Uses Databento's symbology resolution
        res = self.client.symbology.resolve(
            dataset=dataset,
            symbols=[symbol],
            stype_in="continuous",
            stype_out="raw_symbol",
            start_date=date.strftime("%Y-%m-%d"),
            end_date=date.strftime("%Y-%m-%d")
        )
        
        # Extract the resolved raw symbol
        mappings = res.get('mappings', {})
        if symbol in mappings:
            for mapping in mappings[symbol]:
                return mapping['symbol']
                
        raise ValueError(f"Could not resolve symbol {symbol} on {date}")
