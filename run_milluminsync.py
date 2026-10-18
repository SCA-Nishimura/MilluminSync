"""Launcher script (used by PyInstaller and for `python run_milluminsync.py`)."""

import sys

from milluminsync.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
