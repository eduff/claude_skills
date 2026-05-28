import nbformat
import sys
import os
from pathlib import Path

def lighten_notebook(input_path, output_path=None, keep_metadata=False):
    """
    Reads a Jupyter Notebook, removes all cell outputs and execution counts,
    and saves it to reduce file size and speed up remote saving.
    """
    try:
        # Load the notebook
        with open(input_path, 'r', encoding='utf-8') as f:
            nb = nbformat.read(f, as_version=4)

        # Iterate through cells and strip outputs
        cell_count = 0
        for cell in nb.cells:
            if cell.cell_type == 'code':
                cell.outputs = []
                cell.execution_count = None
                cell_count += 1
            
            # Optionally clear cell-level metadata that can get bloated
            if not keep_metadata:
                cell.metadata = {}

        # If no output path, overwrite the original (careful!)
        target = output_path or input_path
        
        with open(target, 'w', encoding='utf-8') as f:
            nbformat.write(nb, f)
            
        print(f"Success: Lightened {cell_count} cells in '{input_path}'.")
        print(f"Saved to: {target}")

    except Exception as e:
        print(f"Error processing notebook: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python lighten_notebook.py <path_to_notebook.ipynb> [output_path.ipynb]")
    else:
        in_file = sys.argv[1]
        out_file = sys.argv[2] if len(sys.argv) > 2 else None
        lighten_notebook(in_file, out_file)