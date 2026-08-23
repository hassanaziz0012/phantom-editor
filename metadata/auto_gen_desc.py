#!/usr/bin/env python3
"""
YouTube Video Description Generator
====================================
Generates a concise 2-3 sentence video description from a phrase-level .srt captions
file using Claude and BrowserLLM, applying anti-AI writing principles, and saves it
to metadata.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    import metadata.utils as utils
except ImportError:
    import utils  # noqa: E402


def generate_description_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
) -> str:
    """Generates a video description and saves it to metadata.json for the project."""
    project_dir, default_meta_path, captions_path, title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    if not captions_path or not captions_path.is_file():
        raise FileNotFoundError(f"Captions file (.srt) not found for target '{target or project_dir}'")

    print(f"📖 Reading captions from {captions_path.name}...")
    transcript = utils.parse_srt_to_timestamped_transcript(captions_path)

    print("🤖 Generating video description with Claude via BrowserLLM...")
    prompt = utils.load_prompt("generate_video_description.md", transcript=transcript)
    raw_response = utils.query_claude(prompt)

    parsed = utils.parse_claude_json(raw_response)
    if isinstance(parsed, dict):
        description = str(parsed.get("description") or parsed.get("text") or "").strip()
    elif isinstance(parsed, str):
        description = parsed.strip()
    else:
        raise ValueError(f"Unexpected response format from Claude: {type(parsed)}")

    if not description:
        raise ValueError(f"Could not extract description from Claude response:\n{raw_response}")

    # Load and update metadata.json
    metadata: dict[str, Any] = {}
    if save_meta_path.is_file():
        try:
            metadata = json.loads(save_meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    metadata["description"] = description
    save_meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return description


def main():
    parser = argparse.ArgumentParser(
        description="Generate video description from .srt captions using Claude and update metadata.json."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to .srt captions file, video file, or project folder (default: current directory).",
    )
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        help="Path to metadata.json (default: metadata.json in the project folder).",
    )

    args = parser.parse_args()

    try:
        custom_meta = Path(args.metadata).resolve() if args.metadata else None
        description = generate_description_for_project(args.target, metadata_path=custom_meta)

        print("\n📄 Generated Description:")
        print("-" * 50)
        print(description)
        print("-" * 50)
        print("\n💾 Saved description to metadata.json")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
