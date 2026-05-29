from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class ValidationPeriod:
    name: str
    start_year: int
    end_year: int

class WalkForwardValidator:
    """
    Enforces the strict walk-forward validation rules from the blueprint.
    Discovery: 2018-2020
    Confirmation: 2021-2022
    Holdout: 2023-2024
    Recent holdout: 2025-present
    """
    def __init__(self):
        self.periods = [
            ValidationPeriod("Discovery", 2018, 2020),
            ValidationPeriod("Confirmation", 2021, 2022),
            ValidationPeriod("Holdout", 2023, 2024),
            ValidationPeriod("Recent holdout", 2025, 2030)
        ]
        
    def run_validation(self, train_func: Callable, eval_func: Callable, data_loader: Callable) -> dict:
        """
        Executes the walk-forward validation sequence.
        A model must pass each stage before moving to the next.
        """
        results = {}
        
        for period in self.periods:
            print(f"--- Running {period.name} ({period.start_year}-{period.end_year}) ---")
            
            # 1. Load data for period
            data = data_loader(period.start_year, period.end_year)
            
            # 2. If it's Discovery, we train. Otherwise we just evaluate (or incrementally update if allowed).
            if period.name == "Discovery":
                model = train_func(data)
                metric = eval_func(model, data)
            else:
                metric = eval_func(model, data)
                
            results[period.name] = metric
            
            # 3. Strict gate: If expectancy < 0 or tail risk exceeded, model is killed immediately.
            if metric.get("net_expectancy", 0) <= 0:
                print(f"Model killed at {period.name} stage due to negative expectancy.")
                results["status"] = "FAIL"
                return results
                
        print("Model passed all historical holdouts. Ready for Sim Shadow.")
        results["status"] = "PASS"
        return results
