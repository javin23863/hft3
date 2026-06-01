import numpy as np
from pathlib import Path

# Check existing replay fixtures
for p in Path("data/replay/hftbacktest").rglob("*.npz"):
    print("Found:", p)
    data = np.load(str(p))
    arr = data["data"]
    print("  shape:", arr.shape)
    print("  ev min:", arr["ev"].min(), "max:", arr["ev"].max())
    print("  exch_ts min:", arr["exch_ts"].min(), "max:", arr["exch_ts"].max())
    print("  px min:", arr["px"].min(), "max:", arr["px"].max())
    break
