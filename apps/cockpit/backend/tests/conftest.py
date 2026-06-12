import sys
from pathlib import Path

# tests/ -> backend -> cockpit -> apps -> repo root is parents[4]
REPO = Path(__file__).resolve().parents[4]
for p in (str(REPO), str(REPO / "packages")):
    if p not in sys.path:
        sys.path.insert(0, p)
