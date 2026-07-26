"""Development entry point for the bundled example project."""
from pathlib import Path
import sys

# Permit running this bundled example straight from the Mangoo source checkout.
source_root = Path(__file__).resolve().parents[1] / "src"
if source_root.is_dir():
    sys.path.insert(0, str(source_root))

from mangoo.management.commands import execute_from_command_line

if __name__ == '__main__':
    execute_from_command_line()
