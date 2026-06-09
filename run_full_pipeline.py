"""Run full pipeline end-to-end."""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\packages")
sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\apps")
sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3")

from hft3_pipeline.__main__ import main

sys.argv = [
    "hft3_pipeline",
    "run",
    "--lane", "cme_futures",
    "--model", "SPREAD_BLOWOUT_RECOMPRESSION",
    "--symbol", "MES.v.0",
    "--event", "CPI_2024_09_11_TIGHT",
    "--output", "artifacts/pipeline_test.json",
]

sys.exit(main())
