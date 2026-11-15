#!/usr/bin/env python3
"""
Skip Repackaging a Video
========================
A video whose 48h performance check found its CTR below the channel's 28-day CTR sits at
Needs Repackaging (Stage 10) in `phantom projects status`. This command keeps its title and
thumbnail as they are and moves it back to Uploaded:

    phantom meta skip-repackage <project>          # skip repackaging
    phantom meta skip-repackage <project> --undo   # flag it for repackaging again

The skip is stored in metadata.json as performanceCheck.repackagingSkipped.
<project> is a project folder path, or a folder name inside the YT Projects folder.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import metadata.utils as utils
from metadata.approve_title import resolve_project_dir
from metadata.read_metadata import write_metadata_json

PERFORMANCE_CHECK_KEY = "performanceCheck"
SKIPPED_KEY = "repackagingSkipped"


def skip_repackage(project: str, undo: bool = False) -> str:
    """Marks (or with ``undo``, unmarks) a project's repackaging as skipped. Returns a status message."""
    project_dir = resolve_project_dir(project)
    meta_path = project_dir / "metadata.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"No metadata.json in {project_dir}")
    metadata = utils.load_metadata_json(meta_path)

    check = metadata.get(PERFORMANCE_CHECK_KEY)
    if not isinstance(check, dict):
        raise ValueError(f"{project_dir.name} has no performance check yet (it runs 48h after publish).")

    if undo:
        if SKIPPED_KEY not in check:
            return f"{project_dir.name}: repackaging wasn't skipped, nothing to undo."
        del check[SKIPPED_KEY]
        write_metadata_json(meta_path, metadata)
        return f"{project_dir.name}: flagged for repackaging again."

    if not check.get("needs_repackaging"):
        raise ValueError(f"{project_dir.name} passed its performance check, so it doesn't need repackaging.")
    if check.get("repackaged"):
        raise ValueError(f"{project_dir.name} has already been repackaged.")
    if SKIPPED_KEY in check:
        return f"{project_dir.name}: repackaging was already skipped."

    check[SKIPPED_KEY] = {"skipped_at": datetime.now().astimezone().isoformat()}
    write_metadata_json(meta_path, metadata)
    return f"{project_dir.name}: repackaging skipped, back to Uploaded."


def main():
    parser = argparse.ArgumentParser(
        description="Skip repackaging a video flagged by its 48h performance check, moving it back to Uploaded."
    )
    parser.add_argument("project", help="Project folder path, or a folder name in the YT Projects folder.")
    parser.add_argument("--undo", action="store_true", help="Flag the video for repackaging again.")
    args = parser.parse_args()

    try:
        print(f"✅ {skip_repackage(args.project, undo=args.undo)}")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
