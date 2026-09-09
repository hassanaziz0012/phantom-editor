#!/usr/bin/env python3
"""
Organize a raw video recording into YouTube Videos or Loom Outreach folders.

Usage:
    phantom pipeline organize <video_file> <project_name> (--ytlong | --loom)
    or
    python pipelines/organize.py <video_file> <project_name> (--ytlong | --loom)
"""

import os
import sys
import shutil
import argparse
from pathlib import Path

YOUTUBE_VIDEOS_DIR = Path("/home/hassan/Videos/YouTube Videos")
LOOM_OUTREACH_DIR = Path("/home/hassan/Videos/Loom Outreach")


def organize_video(video_path: Path, project_name: str, target_dir: Path) -> Path:
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        print(f"Error: Video file '{video_path}' does not exist or is not a file.", file=sys.stderr)
        sys.exit(1)

    project_name = project_name.strip()
    if not project_name:
        print("Error: Project name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    project_dir = target_dir / project_name
    if not project_dir.exists():
        project_dir.mkdir(parents=True, exist_ok=True)

    dest_file = project_dir / f"raw{video_path.suffix}"

    if video_path.resolve() == dest_file.resolve():
        print(f"Video file is already at {dest_file}")
        return dest_file

    shutil.move(str(video_path), str(dest_file))
    print(f"Moved {video_path.name} -> {dest_file}")
    return dest_file


def main():
    parser = argparse.ArgumentParser(
        description="Organize a video recording into YouTube Videos or Loom Outreach project folders."
    )
    parser.add_argument(
        "video_file",
        help="Path to the video file to organize."
    )
    parser.add_argument(
        "project_name",
        help="Name of the project folder."
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--ytlong",
        action="store_true",
        help="Organize into YouTube Videos folder (/home/hassan/Videos/YouTube Videos)."
    )
    group.add_argument(
        "--loom",
        action="store_true",
        help="Organize into Loom Outreach folder (/home/hassan/Videos/Loom Outreach)."
    )

    args = parser.parse_args()

    target_dir = YOUTUBE_VIDEOS_DIR if args.ytlong else LOOM_OUTREACH_DIR
    organize_video(Path(args.video_file), args.project_name, target_dir)


if __name__ == "__main__":
    main()
