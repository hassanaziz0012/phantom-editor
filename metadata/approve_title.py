#!/usr/bin/env python3
"""
Approve a Video Title
=====================
`projects process` auto-picks the top-scored title from metadata.json's `titles` list and
marks it `"titleReviewed": false`. This command shows the candidates and approves one:

    phantom meta approve-title <project>            # list candidates
    phantom meta approve-title <project> 3          # approve candidate #3
    phantom meta approve-title <project> "My title" # approve a title of your own

<project> is a project folder path, or a folder name inside the YT Projects folder.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import metadata.utils as utils
from metadata.read_metadata import write_metadata_json
from shared.paths import YT_PROJECTS_DIR


def resolve_project_dir(project: str) -> Path:
    """A project folder from a path, or from a folder name (case-insensitive) in YT Projects."""
    path = Path(project).expanduser()
    if path.is_file():
        path = path.parent
    if path.is_dir():
        return path.resolve()

    wanted = project.strip().lower()
    folders = [p for p in YT_PROJECTS_DIR.iterdir() if p.is_dir()] if YT_PROJECTS_DIR.is_dir() else []
    exact = [p for p in folders if p.name.lower() == wanted]
    if exact:
        return exact[0]
    partial = [p for p in folders if wanted in p.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        names = "\n".join(f"  - {p.name}" for p in partial)
        raise ValueError(f"'{project}' matches several projects:\n{names}")
    raise FileNotFoundError(f"No project folder found for '{project}' (looked in {YT_PROJECTS_DIR})")


def format_candidates(metadata: Any) -> str:
    """The current title and the ranked generated titles, the current one marked with →.

    ``metadata`` is a metadata.json dict or a VideoMetadata (anything with .get).
    """
    titles = metadata.get("titles") or []
    current = str(metadata.get("title") or "").strip()
    state = "approved" if metadata.get("titleReviewed") is not False else "awaiting approval"
    lines = [f"   Current title ({state}): {current or '-'}"]
    if not titles:
        lines.append("   No generated titles in metadata.json (run `phantom meta titles` first).")
    for item in titles:
        text = str(item.get("title") or "").strip()
        marker = "→" if text == current else " "
        total = item.get("total")
        score = f"[{total}/16]" if total is not None else "[-]"
        lines.append(f" {marker} {item.get('rank')}. {score} {text}  ({len(text)} chars)")
    return "\n".join(lines)


def resolve_choice(metadata: Any, choice: str) -> str:
    """The title a choice stands for: a rank number of a generated title, or the text itself."""
    choice = choice.strip()
    if not choice:
        raise ValueError("The title can't be empty.")
    if not choice.isdigit():
        return choice
    rank = int(choice)
    titles = metadata.get("titles") or []
    match = next((t for t in titles if t.get("rank") == rank), None)
    if match is None:
        raise ValueError(f"No title ranked #{rank} (there are {len(titles)} candidates).")
    return str(match["title"]).strip()


def approve_title(project: str, choice: str) -> str:
    """Sets metadata.json's title from a rank number or custom text and marks it reviewed."""
    project_dir = resolve_project_dir(project)
    meta_path = project_dir / "metadata.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"No metadata.json in {project_dir}")
    metadata = utils.load_metadata_json(meta_path)
    new_title = resolve_choice(metadata, choice)

    metadata["title"] = new_title
    metadata["titleReviewed"] = True
    write_metadata_json(meta_path, metadata)
    return new_title


def main():
    parser = argparse.ArgumentParser(
        description="List a project's generated titles, or approve one (by rank or custom text) as the video title."
    )
    parser.add_argument("project", help="Project folder path, or a folder name in the YT Projects folder.")
    parser.add_argument(
        "choice",
        nargs="?",
        default=None,
        help="Rank number of a generated title, or your own title text. Omit to list the candidates.",
    )
    args = parser.parse_args()

    try:
        if args.choice is None:
            project_dir = resolve_project_dir(args.project)
            print(f"📁 {project_dir.name}")
            print(format_candidates(utils.load_metadata_json(project_dir / "metadata.json")))
        else:
            new_title = approve_title(args.project, args.choice)
            print(f"✅ Approved title: {new_title}")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
