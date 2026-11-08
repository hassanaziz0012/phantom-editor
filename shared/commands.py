"""Command lines for running the repo's own modules as subprocesses."""

import sys


def python_module(module: str, *args: str) -> list[str]:
    """``python -m <module> <args>`` with this interpreter.

    ``-P`` keeps the caller's working directory off sys.path, so a project folder's files
    can't shadow the repo's packages.
    """
    return [sys.executable, "-P", "-m", module, *args]
