import os
from hftbacktest.data.utils.databento import convert
import numpy as np

class DatabentoConverter:
    """
    Converts Databento MBO files (.dbn.zst) to HftBacktest compatible .npz arrays.
    """
    def __init__(self, output_dir: str = "data/npz"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        
    def convert_file(self, input_path: str, symbol: str) -> str:
        """
        Converts a single DBN/ZST file to NPZ format for hftbacktest.
        Returns the path to the converted .npz file.
        """
        filename = os.path.basename(input_path)
        output_filename = filename.replace('.dbn.zst', '.npz').replace('.dbn', '.npz')
        output_path = os.path.join(self.output_dir, f"{symbol}_{output_filename}")
        
        print(f"Converting {input_path} -> {output_path}...")
        
        # HftBacktest provides a built-in Databento MBO converter
        # convert(input_files, output_filename=None, **kwargs)
        # Note: The output structure is typically [event_type, timestamp, side, price, qty, order_id]
        convert(input_path, output_filename=output_path)
        
        print("Conversion complete.")
        return output_path
