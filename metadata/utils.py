#!/usr/bin/env python3
"""
Shared utilities for metadata generation and BrowserLLM/Claude interactions.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

# Import BrowserLLM query and prompt utilities from the centralized agentic package
from agentic.ask_browserllm import (
    PROMPTS_DIR,
    REPO_ROOT,
    BrowserLLMUsageLimitError,
    load_prompt,
    parse_json_response,
)
from agentic.ask_browserllm import ask_browserllm as _ask_browserllm
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


# BrowserLLM profiles tried in order when one hits its usage limit. None is BrowserLLM's own
# default profile (p9222), so a caller that passes no profile behaves as before until it's limited.
FALLBACK_PROFILES: list[str | None] = [None, "p9223", "p9224", "p9225"]

_limited_profiles: set[str | None] = set()
_limited_lock = threading.Lock()


def _profile_order(profile: str | None) -> list[str | None]:
    """The profiles to try for a call: the requested one, then the later ones in the chain."""
    if profile == "p9222":
        profile = None
    if profile in FALLBACK_PROFILES:
        return FALLBACK_PROFILES[FALLBACK_PROFILES.index(profile):]
    return [profile, *FALLBACK_PROFILES[1:]]


def _profile_label(profile: str | None) -> str:
    return profile or "p9222 (default)"


def ask_browserllm(*args: Any, profile: str | None = None, **kwargs: Any) -> Any:
    """agentic's ask_browserllm, moving to the next BrowserLLM profile on a usage limit.

    Order is p9222, p9223, p9224, p9225. A profile that hit its limit is remembered for the rest
    of the run, so the parallel metadata steps skip it instead of each rediscovering it. When
    every profile is limited the last BrowserLLMUsageLimitError is raised.
    """
    last_error: BrowserLLMUsageLimitError | None = None
    order = _profile_order(profile)
    for candidate in order:
        with _limited_lock:
            if candidate in _limited_profiles:
                continue
        try:
            return _ask_browserllm(*args, profile=candidate, **kwargs)
        except BrowserLLMUsageLimitError as e:
            last_error = e
            with _limited_lock:
                _limited_profiles.add(candidate)
            remaining = [c for c in order[order.index(candidate) + 1:]]
            if remaining:
                print(f"⚠️  BrowserLLM profile {_profile_label(candidate)} hit its usage limit; "
                      f"switching to {_profile_label(remaining[0])}.")
    if last_error is None:
        # Every profile was already marked limited by an earlier call
        raise BrowserLLMUsageLimitError(
            f"All BrowserLLM profiles are at their usage limit: {', '.join(_profile_label(c) for c in order)}."
        )
    raise BrowserLLMUsageLimitError(
        f"All BrowserLLM profiles are at their usage limit ({', '.join(_profile_label(c) for c in order)}). "
        f"Last error: {last_error}"
    )


def load_metadata_json(path: Path) -> dict[str, Any]:
    """metadata.json as a dict, or {} when the file doesn't exist.

    Raises ValueError when the file exists but can't be read as a JSON object, so a generator
    that updates it and writes it back never replaces a damaged file with only its own keys.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"Could not read {path}: {e}. Fix or remove it, then retry.") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object. Fix or remove it, then retry.")
    return data


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

    title = load_metadata_json(metadata_path).get("title") or project_dir.name

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
