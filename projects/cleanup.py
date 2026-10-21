#!/usr/bin/env python3
"""
Cleanup script for YT Projects.
Moves specific files to YT Archive and optionally deletes remaining files.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


YT_PROJECTS_DIR = Path.home() / "Videos" / "YT Projects"
YT_ARCHIVE_DIR = Path.home() / "Videos" / "YT Archive"

FILES_TO_ARCHIVE = [
    "script.excalidraw",
    "outline.md",
    "metadata.json",
]

EXTENSIONS_TO_ARCHIVE = [
    ".png",
    ".jpg",
]


def find_project_path(project_arg: str | None) -> Path:
    """Determine the project path from argument or current directory."""
    if project_arg:
        project_path = Path(project_arg).resolve()
        if not project_path.exists():
            print(f"Error: Project path does not exist: {project_path}")
            sys.exit(1)
        return project_path

    cwd = Path.cwd().resolve()
    if YT_PROJECTS_DIR in cwd.parents or cwd == YT_PROJECTS_DIR:
        if cwd == YT_PROJECTS_DIR:
            print("Error: Current directory is the YT Projects root, not a specific project folder.")
            print("Please provide a project path or run from within a project folder.")
            sys.exit(1)
        return cwd

    print("Error: Could not determine project folder.")
    print("Either run this script from within a YT project folder,")
    print("or provide the project path as an argument.")
    sys.exit(1)


def get_project_name(project_path: Path) -> str:
    """Extract project name from path."""
    try:
        return project_path.relative_to(YT_PROJECTS_DIR).parts[0]
    except ValueError:
        return project_path.name


def archive_files(project_path: Path, archive_path: Path, full_backup: bool) -> None:
    """Move specified files to archive directory."""
    archive_path.mkdir(parents=True, exist_ok=True)

    if full_backup:
        for item in project_path.iterdir():
            if item.is_file():
                dest = archive_path / item.name
                if dest.exists():
                    print(f"Warning: {dest.name} already exists in archive, skipping.")
                else:
                    shutil.move(str(item), str(dest))
                    print(f"Moved: {item.name}")
        return

    for filename in FILES_TO_ARCHIVE:
        src = project_path / filename
        if src.exists():
            dest = archive_path / filename
            shutil.move(str(src), str(dest))
            print(f"Moved: {filename}")
        else:
            print(f"Warning: {filename} not found in project folder.")

    for ext in EXTENSIONS_TO_ARCHIVE:
        found = False
        for file in project_path.iterdir():
            if file.is_file() and file.suffix.lower() == ext.lower():
                dest = archive_path / file.name
                if dest.exists():
                    print(f"Warning: {file.name} already exists in archive, skipping.")
                else:
                    shutil.move(str(file), str(dest))
                    print(f"Moved: {file.name}")
                found = True
        if not found:
            print(f"Warning: No {ext} files found in project folder.")


def delete_remaining_files(project_path: Path, full_backup: bool) -> None:
    """Delete remaining files from project folder."""
    if full_backup:
        return

    for file in project_path.iterdir():
        if file.is_file() and file.suffix.lower() == ".mp4":
            file.unlink()
            print(f"Deleted: {file.name}")


def main():
    parser = argparse.ArgumentParser(description="Clean up YT project folder")
    parser.add_argument("project", nargs="?", help="Path to YT project folder")
    parser.add_argument("--full", action="store_true", help="Backup entire folder including mp4 files")
    args = parser.parse_args()

    project_path = find_project_path(args.project)
    project_name = get_project_name(project_path)
    archive_path = YT_ARCHIVE_DIR / project_name

    print(f"Project: {project_name}")
    print(f"Source: {project_path}")
    print(f"Archive: {archive_path}")
    print(f"Full backup: {args.full}")
    print()

    archive_files(project_path, archive_path, args.full)
    delete_remaining_files(project_path, args.full)

    print("\nDone!")


if __name__ == "__main__":
    main()