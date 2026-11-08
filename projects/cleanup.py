#!/usr/bin/env python3
"""
Cleanup script for YT Projects.
Moves a finished project's files to YT Archive/<project>, then (after asking) deletes the
video and audio files left behind and removes the project folder. The Sheets sync then
marks the project's row Archived.

Without --full, video/audio files and node_modules folders aren't archived: they're the
large, re-creatable part of a project. With --full, everything is archived.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from projects.current_pipeline import VIDEO_EXTENSIONS
from shared.paths import YT_PROJECTS_DIR

YT_ARCHIVE_DIR = Path.home() / "Videos" / "YT Archive"

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
# Folders left out of the archive without --full (dependencies that a reinstall brings back)
SKIPPED_DIRS = {"node_modules"}


def find_project_path(project_arg: str | None) -> Path:
    """The project folder from the argument or the current directory; a path inside a project
    (e.g. its broll/ subfolder) resolves to the project folder itself."""
    path = Path(project_arg).expanduser().resolve() if project_arg else Path.cwd().resolve()
    if project_arg and not path.is_dir():
        print(f"Error: Project folder does not exist: {path}")
        sys.exit(1)

    projects_dir = YT_PROJECTS_DIR.resolve()
    if path == projects_dir:
        print("Error: That's the YT Projects root, not a specific project folder.")
        print("Please provide a project path or run from within a project folder.")
        sys.exit(1)
    if projects_dir in path.parents:
        return projects_dir / path.relative_to(projects_dir).parts[0]
    if project_arg:
        return path

    print("Error: Could not determine project folder.")
    print("Either run this script from within a YT project folder,")
    print("or provide the project path as an argument.")
    sys.exit(1)


def is_archived(path: Path, full_backup: bool) -> bool:
    """Whether a file (relative to the project) goes to the archive."""
    if full_backup:
        return True
    return path.suffix.lower() not in MEDIA_EXTENSIONS and not SKIPPED_DIRS.intersection(path.parts[:-1])


def free_destination(dest: Path) -> Path:
    """``dest``, or "name (2).ext", "name (3).ext"... if it's taken, so nothing in the archive is overwritten."""
    candidate, n = dest, 2
    while candidate.exists():
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    return candidate


def archive_files(project_path: Path, archive_path: Path, full_backup: bool) -> list[str]:
    """Move the project's files into the archive, keeping their subfolders. Returns the errors."""
    errors = []
    for root, dirs, files in os.walk(project_path):
        if not full_backup:
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        for name in sorted(files):
            src = Path(root) / name
            rel = src.relative_to(project_path)
            if not is_archived(rel, full_backup):
                continue
            dest = archive_path / rel
            if dest.exists():
                renamed = free_destination(dest)
                print(f"Warning: {rel} already exists in the archive, saving as {renamed.name}.")
                dest = renamed
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                print(f"Moved: {rel}")
            except OSError as e:
                errors.append(f"{rel}: {e}")
                print(f"Error: Could not move {rel}: {e}")
    return errors


def remaining_files(project_path: Path) -> list[Path]:
    return sorted(p for p in project_path.rglob("*") if p.is_file() or p.is_symlink())


def format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def delete_project_folder(project_path: Path) -> None:
    """List what's left in the project folder and delete it after a y/N confirmation."""
    leftovers = remaining_files(project_path)
    if leftovers:
        total = sum(p.lstat().st_size for p in leftovers)
        print(f"\nNot archived ({len(leftovers)} file(s), {format_size(total)}):")
        shown = [p for p in leftovers if not SKIPPED_DIRS.intersection(p.relative_to(project_path).parts[:-1])]
        for p in shown:
            print(f"  {p.relative_to(project_path)}  ({format_size(p.lstat().st_size)})")
        for skipped in sorted({d for p in leftovers for d in p.relative_to(project_path).parts[:-1] if d in SKIPPED_DIRS}):
            print(f"  ...and every {skipped}/ folder")
        answer = input(f"\nDelete these and the project folder {project_path.name}? [y/N] ").strip().lower()
        if answer != "y":
            print("Kept the project folder. Nothing was deleted.")
            return
    shutil.rmtree(project_path)
    print(f"Deleted the project folder: {project_path}")


def main():
    parser = argparse.ArgumentParser(description="Archive a finished YT project and delete its project folder")
    parser.add_argument("project", nargs="?", help="Path to YT project folder (default: the one you're in)")
    parser.add_argument("--full", action="store_true", help="Archive everything, video and audio files included")
    args = parser.parse_args()

    project_path = find_project_path(args.project)
    archive_path = YT_ARCHIVE_DIR / project_path.name

    print(f"Project: {project_path.name}")
    print(f"Source: {project_path}")
    print(f"Archive: {archive_path}")
    print(f"Full backup: {args.full}")
    print()

    errors = archive_files(project_path, archive_path, args.full)
    if errors:
        print(f"\n{len(errors)} file(s) could not be archived, so nothing was deleted:")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)

    delete_project_folder(project_path)

    print("\nDone!")


if __name__ == "__main__":
    main()
