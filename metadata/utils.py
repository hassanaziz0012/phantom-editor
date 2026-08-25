#!/usr/bin/env python3
"""
Shared utilities for metadata generation and BrowserLLM/Claude interactions.
"""

from __future__ import annotations

import json
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Any

# Import BrowserLLM query and prompt utilities from the centralized agentic package
from agentic.ask_browserllm import (
    PROMPTS_DIR,
    REPO_ROOT,
    ask_browserllm,
    ask_chatgpt,
    ask_claude,
    ask_gemini,
    extract_json_block,
    load_prompt,
    parse_claude_json,
    parse_json_response,
    query_browserllm,
    query_chatgpt,
    query_claude,
    query_gemini,
)

__all__ = [
    "REPO_ROOT",
    "PROMPTS_DIR",
    "ask_browserllm",
    "ask_claude",
    "ask_chatgpt",
    "ask_gemini",
    "query_browserllm",
    "query_claude",
    "query_chatgpt",
    "query_gemini",
    "extract_json_block",
    "parse_json_response",
    "parse_claude_json",
    "load_prompt",
    "parse_srt_to_timestamped_transcript",
    "resolve_project_paths",
    "find_captions_file",
    "highlight_json",
]




def parse_srt_to_timestamped_transcript(srt_path: str | Path) -> str:
    """Parses an SRT subtitle file into a formatted transcript with [MM:SS] cues."""
    path = Path(srt_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"SRT captions file not found: {path}")

    content = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", content)

    timestamp_pattern = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})(?:[,\.]\d{1,3})?\s*-->"
    )

    lines: list[str] = []
    for block in blocks:
        block_lines = block.strip().split("\n")
        if len(block_lines) < 2:
            continue

        time_line = block_lines[1] if len(block_lines) >= 2 and ("-->" in block_lines[1]) else block_lines[0]
        match = timestamp_pattern.search(time_line)
        if not match:
            continue

        h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
        ts = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

        time_line_idx = block_lines.index(time_line)
        raw_text = " ".join(block_lines[time_line_idx + 1 :]).strip()
        cleaned_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if cleaned_text:
            lines.append(f"[{ts}] {cleaned_text}")

    if not lines:
        raise ValueError(f"No valid captions could be extracted from: {path}")

    return "\n".join(lines)


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
        title = project_dir.name.replace("-", " ").replace("_", " ").title()

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


def highlight_json(json_str: str) -> str:
    """Highlights JSON string with ANSI color codes."""
    if not sys.stdout.isatty():
        return json_str

    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import JsonLexer
        return highlight(json_str, JsonLexer(), TerminalFormatter()).rstrip()
    except ImportError:
        pass

    pattern = r'("(?:\\.|[^"\\])*")(\s*:)?|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'

    def colorize(match: re.Match[str]) -> str:
        string_val, colon, bool_val, num_val = match.groups()
        if string_val is not None:
            if colon:
                return f"\033[1;36m{string_val}\033[0m{colon}"
            return f"\033[32m{string_val}\033[0m"
        if bool_val is not None:
            return f"\033[1;35m{bool_val}\033[0m"
        if num_val is not None:
            return f"\033[33m{num_val}\033[0m"
        return match.group(0)

    return re.sub(pattern, colorize, json_str)
