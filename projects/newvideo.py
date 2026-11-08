#!/usr/bin/env python3
"""
Create a new video project folder inside the 'YT Projects' directory.
Usage:
    phantom projects newvideo <project_name>
"""

import sys
import argparse
from pathlib import Path

from shared.paths import YT_PROJECTS_DIR as DEFAULT_YT_PROJECTS_DIR
from video_editing.utils import (
    COLOR_YELLOW, COLOR_RESET, COLOR_BOLD,
    print_success, print_error
)


def sanitize_project_name(name: str) -> str:
    """Trim whitespace from project name."""
    return name.strip()


def create_video_project(project_name: str, base_dir: Path = DEFAULT_YT_PROJECTS_DIR) -> Path:
    """Creates a new project directory under base_dir/project_name."""
    base_dir = base_dir.expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    project_dir = base_dir / project_name

    if project_dir.exists():
        print_error(f"Error: Project folder already exists at:\n  {project_dir}")
        sys.exit(1)

    try:
        project_dir.mkdir(parents=True, exist_ok=False)
        print_success(f"✓ Created new YouTube project folder:")
        print(f"  {COLOR_BOLD}{project_dir}{COLOR_RESET}\n")
        print(f"{COLOR_YELLOW}Next steps:{COLOR_RESET}")
        print(f"  1. Script the video (outline.md with # Hook & # Outline sections and a [LIKE / SUBSCRIBE] marker) in: {project_dir}")
        print(f"  2. Record raw footage into: {project_dir}")
        print(f"  3. Run pipeline processing: `phantom projects process`")
        return project_dir
    except Exception as e:
        print_error(f"Failed to create project folder: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Create a brand new folder in the 'YT Projects' directory for a new video project."
    )
    parser.add_argument(
        "name",
        help="Name of the new video project."
    )
    parser.add_argument(
        "--dir", "-d",
        default=None,
        help=f"Base directory for YT Projects (default: {DEFAULT_YT_PROJECTS_DIR})."
    )
    args = parser.parse_args()

    base_dir = Path(args.dir) if args.dir else DEFAULT_YT_PROJECTS_DIR

    project_name = sanitize_project_name(args.name)
    if not project_name:
        print_error("Error: Project name cannot be empty.")
        sys.exit(1)

    create_video_project(project_name, base_dir=base_dir)


if __name__ == "__main__":
    main()
