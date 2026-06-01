"""
Converts Databento MBO (.dbn.zst) to HftBacktest 2.x compatible .npz event arrays.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from hftbacktest.data.utils.databento import convert


class DatabentoConverter:
    def __init__(self, output_dir: str = "data/npz"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def convert_file(self, input_path: str, symbol: str) -> str:
        filename = os.path.basename(input_path)
        output_filename = (
            filename.replace(".dbn.zst", ".npz").replace(".dbn", ".npz")
        )
        output_path = os.path.join(self.output_dir, f"{symbol}_{output_filename}")

        print(f"Converting {input_path} -> {output_path} (symbol={symbol})...")
        convert(
            input_path,
            symbol=symbol,
            output_filename=output_path,
        )
        print("Conversion complete.")
        return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to .dbn.zst file")
    parser.add_argument("--symbol", required=True, help="Symbol in file e.g. MES.v.0")
    parser.add_argument("--out-dir", default="data/npz")
    args = parser.parse_args()

    conv = DatabentoConverter(args.out_dir)
    conv.convert_file(args.input, args.symbol)


if __name__ == "__main__":
    main()
