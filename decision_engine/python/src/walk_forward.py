from dataclasses import dataclass
from typing import Callable, Any, List
import struct

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
            ValidationPeriod("Recent holdout", 2025, 2025)
        ]
        
    def run_validation(self, train_func: Callable, eval_func: Callable, data_loader: Callable) -> dict:
        """
        Executes the walk-forward validation sequence.
        A model must pass each stage before moving to the next.
        """
        results = {}
        model = None
        
        for period in self.periods:
            print(f"--- Running {period.name} ({period.start_year}-{period.end_year}) ---")
            
            data = data_loader(period.start_year, period.end_year)
            
            if period.name == "Discovery":
                model = train_func(data)
                metric = eval_func(model, data)
            else:
                metric = eval_func(model, data)
                
            results[period.name] = metric
            
            if metric.get("net_expectancy", 0) <= 0:
                print(f"Model killed at {period.name} stage due to negative expectancy.")
                results["status"] = "FAIL"
                return results
                
        print("Model passed all historical holdouts. Ready for Sim Shadow.")
        results["status"] = "PASS"
        results["model"] = model
        return results

def export_weights_to_cpp(weights: List[float], output_path: str, model_id: int = 1, feature_count: int = 64):
    """
    Exports trained weights to a binary format that exactly maps to the 
    C++ `alignas(64) std::array<double, 1024> weights_` structure, 
    preceded by a safety header.
    
    Header format (16 bytes):
    - Magic Number (uint32): 0x48465433 ('HFT3')
    - Version (uint32): 1
    - Model ID (uint32)
    - Feature Count (uint32)
    """
    if len(weights) > 1024:
        raise ValueError("Model exceeds maximum C++ weights capacity of 1024.")
        
    padded_weights = weights + [0.0] * (1024 - len(weights))
    
    with open(output_path, "wb") as f:
        # Write safety header
        magic = 0x48465433
        version = 1
        header = struct.pack("IIII", magic, version, model_id, feature_count)
        f.write(header)
        
        # Write 1024 doubles (d) in native byte order
        binary_data = struct.pack(f"{len(padded_weights)}d", *padded_weights)
        f.write(binary_data)
        
    print(f"Exported {len(weights)} active weights (padded to 1024) to {output_path} with safety header.")
