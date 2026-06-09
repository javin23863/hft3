"""Test VectorBT adaptive bar sizing fix."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages"))
sys.path.insert(0, str(Path(__file__).parent))

from backtest_pipeline.src.vectorbt_adapter import _default_data_loader, InsufficientVectorBTInputError

def test_adaptive_bar_sizing():
    """Test that adaptive bar sizing produces ≥30 bars."""
    repo_root = Path(__file__).parent
    event_id = "CPI_2024_09_11_TIGHT"
    
    print(f"Testing adaptive bar sizing for event: {event_id}")
    
    try:
        ohlcv = _default_data_loader(event_id, repo_root)
        
        if ohlcv is None:
            print("ERROR: No data returned")
            return False
        
        n_bars = len(ohlcv)
        print(f"[PASS] Produced {n_bars} bars")
        
        if n_bars < 30:
            print(f"[FAIL] Only {n_bars} bars (minimum 30 required)")
            return False
        
        print(f"[PASS] {n_bars} bars >= 30 minimum")
        
        # Check OHLCV structure
        if ohlcv.shape[1] != 5:
            print(f"[FAIL] Expected 5 columns (OHLCV), got {ohlcv.shape[1]}")
            return False
        
        print(f"[PASS] OHLCV structure correct: {ohlcv.shape}")
        
        # Check for valid prices
        if (ohlcv[:, 3] <= 0).any():
            print("[FAIL] Some close prices are <= 0")
            return False
        
        print(f"[PASS] All prices valid")
        print(f"  Price range: {ohlcv[:, 3].min():.2f} - {ohlcv[:, 3].max():.2f}")
        
        return True
        
    except InsufficientVectorBTInputError as e:
        print(f"✗ FAIL: InsufficientVectorBTInputError raised")
        print(f"  {e}")
        return False
    except Exception as e:
        print(f"✗ FAIL: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_adaptive_bar_sizing()
    sys.exit(0 if success else 1)
