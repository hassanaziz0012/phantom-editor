"""
Runs one of the repo's modules as a command: ``python -m shared.launch <module> [args...]``.

The phantom CLI starts every Python command through here, so the repo's .env is loaded once,
in one place, before the module runs. Variables already set in the environment win.
"""

import runpy
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m shared.launch <module> [args...]")
    module = sys.argv.pop(1)
    # Before the module is imported: modules may read the environment at import time.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
