#!/usr/bin/env python3
"""
Shared utilities for metadata generation and BrowserLLM/Claude interactions.
"""

from __future__ import annotations

import json
from pathlib import Path

# Import BrowserLLM query and prompt utilities from the centralized agentic package
from agentic.ask_browserllm import (
    PROMPTS_DIR,
    REPO_ROOT,
    ask_browserllm,
    load_prompt,
    parse_json_response,
)
from shared.srt import parse_srt_to_timestamped_transcript
from shared.terminal import highlight_json

__all__ = [
    "REPO_ROOT",
    "PROMPTS_DIR",
    "ask_browserllm",
    "parse_json_response",
    "load_prompt",
    "parse_srt_to_timestamped_transcript",
    "resolve_project_paths",
    "find_captions_file",
    "highlight_json",
]


def resolve_project_paths(target: str | Path | None = None) -> tuple[Path, Path, Path | None, str]:
    """
    Resolves (project_dir, metadata_path, captions_path, title) from a target argument.
    Target can be:
    - Path to project directory
    - Path to a video file (.mp4)
    - Path to a captions file (.srt)
    - Path to metadata.json
    - None (current working directory)
    """
    if target is None:
        target_path = Path.cwd()
    else:
        target_path = Path(target).expanduser().resolve()

    captions_path: Path | None = None

    if target_path.is_file():
        if target_path.suffix.lower() == ".srt":
            project_dir = target_path.parent
            captions_path = target_path
        elif target_path.name.lower() == "metadata.json" or target_path.suffix.lower() == ".json":
            project_dir = target_path.parent
        else:
            project_dir = target_path.parent
    else:
        project_dir = target_path

    metadata_path = project_dir / "metadata.json"

    # Search for captions file if not explicitly targeted or if target was a directory / video
    if captions_path is None and project_dir.exists():
        captions_path = find_captions_file(project_dir)

    # Determine title
    title = ""
    if metadata_path.is_file():
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            title = data.get("title", "")
        except Exception:
            pass

    if not title:
        title = project_dir.name

    return project_dir, metadata_path, captions_path, title


def find_captions_file(project_dir: Path) -> Path | None:
    """Finds best phrase-level .srt captions file in the project directory."""
    if not project_dir.is_dir():
        return None

    # 1. Exact check for trimmed.srt if it already exists
    trimmed_path = project_dir / "trimmed.srt"
    if trimmed_path.is_file() and trimmed_path.stat().st_size > 0:
        return trimmed_path

    srt_files = list(project_dir.glob("*.srt"))
    if not srt_files:
        return None

    # Filter out 1-word-per-timestamp SRT files
    phrase_candidates = [
        f for f in srt_files
        if not (f.stem.endswith("-1word") or f.stem.endswith("_1word") or "1word" in f.name.lower())
    ]
    if not phrase_candidates:
        return srt_files[0]

    # 2. Check for any trimmed variations (e.g. after-trim.srt, trimmed_video.srt)
    for cand in phrase_candidates:
        if "trimmed" in cand.name.lower() or "trim" in cand.name.lower():
            return cand

    # 3. Check for standard priority names in order
    for name in ("captions.srt", "subtitles.srt", "transcript.srt", "final.srt"):
        for cand in phrase_candidates:
            if cand.name.lower() == name:
                return cand

    # 4. Fallback to first non-1word phrase candidate
    return phrase_candidates[0]
