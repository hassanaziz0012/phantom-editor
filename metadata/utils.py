#!/usr/bin/env python3
"""
Shared utilities for metadata generation and BrowserLLM/Claude interactions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "agentic" / "prompts"


def query_claude(prompt_text: str) -> str:
    """Invokes browserllm with Claude provider using a temporary prompt file."""
    browserllm_bin = shutil.which("browserllm") or str(Path.home() / ".local" / "bin" / "browserllm")

    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as pf, \
         tempfile.NamedTemporaryFile("r+", suffix=".txt", delete=False, encoding="utf-8") as of:
        prompt_path = pf.name
        output_path = of.name
        pf.write(prompt_text)
        pf.flush()

    try:
        cmd = [browserllm_bin, "-p", prompt_path, "--provider", "claude", "-o", output_path]
        res = subprocess.run(cmd, capture_output=True, text=True)

        output = Path(output_path).read_text(encoding="utf-8").strip() if Path(output_path).exists() else ""
        if not output:
            output = res.stdout.strip()

        if not output:
            raise RuntimeError(f"BrowserLLM Claude query returned empty output. Stderr: {res.stderr}")

        return output
    finally:
        for p in (prompt_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def extract_json_block(text: str) -> str:
    """Extracts a JSON substring (object or array) from text or markdown code fences."""
    text = text.strip()

    # Look for ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        start_idx = -1
        idx_brace = text.find("{")
        idx_bracket = text.find("[")

        if idx_brace != -1 and idx_bracket != -1:
            start_idx = min(idx_brace, idx_bracket)
        elif idx_brace != -1:
            start_idx = idx_brace
        elif idx_bracket != -1:
            start_idx = idx_bracket

        end_idx = max(text.rfind("}"), text.rfind("]"))

        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            text = text[start_idx : end_idx + 1]

    return text


def parse_claude_json(response_text: str) -> Any:
    """Extracts and parses JSON object or array from Claude's response."""
    json_str = extract_json_block(response_text)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to decode JSON from Claude response: {e}\nRaw response:\n{response_text}") from e


def load_prompt(prompt_name: str, **kwargs: Any) -> str:
    """
    Loads prompt template from agentic/prompts/, auto-injecting deslopify_prompt
    if {deslopify_prompt} is present in the template.
    """
    prompt_path = PROMPTS_DIR / (prompt_name if prompt_name.endswith(".md") else f"{prompt_name}.md")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt template not found at '{prompt_path}'")

    content = prompt_path.read_text(encoding="utf-8")

    if "{deslopify_prompt}" in content:
        deslopify_path = PROMPTS_DIR / "deslopify_text.md"
        deslopify_text = deslopify_path.read_text(encoding="utf-8").strip() if deslopify_path.exists() else ""
        content = content.replace("{deslopify_prompt}", deslopify_text)

    for k, v in kwargs.items():
        content = content.replace(f"{{{k}}}", str(v))

    return content


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
            captions_path = target_path
            project_dir = target_path.parent
        elif target_path.name.lower() == "metadata.json" or target_path.suffix.lower() == ".json":
            project_dir = target_path.parent
        else:
            project_dir = target_path.parent
    else:
        project_dir = target_path

    metadata_path = project_dir / "metadata.json"

    # Search for captions file if not explicitly targeted
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

    srt_files = list(project_dir.glob("*.srt"))
    if not srt_files:
        return None

    # Prefer non-1word srt files (e.g. captions.srt, subtitles.srt, final.srt)
    phrase_candidates = [
        f for f in srt_files
        if not (f.stem.endswith("-1word") or f.stem.endswith("_1word") or "1word" in f.name.lower())
    ]
    if phrase_candidates:
        # Sort to prioritize files named captions.srt or matching project name
        for cand in phrase_candidates:
            if cand.name.lower() in ("captions.srt", "subtitles.srt", "transcript.srt", "final.srt"):
                return cand
        return phrase_candidates[0]

    return srt_files[0]


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
