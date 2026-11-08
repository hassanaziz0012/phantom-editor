"""
Locations the tools share: the repo itself and the folder that holds the video projects.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# One folder per video project. PHANTOM_PROJECTS_DIR overrides it (e.g. in .env).
YT_PROJECTS_DIR = Path(os.environ.get("PHANTOM_PROJECTS_DIR") or Path.home() / "Videos" / "YT Projects").expanduser()
