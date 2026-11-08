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
import sys
from pathlib import Path

import metadata.utils as utils
from metadata.read_metadata import write_metadata_json


def generate_description_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
    profile: str | None = None,
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
    raw_response = utils.ask_browserllm(prompt, model="sonnet", effort="high", output_json=True, profile=profile)

    parsed = utils.parse_json_response(raw_response)
    if isinstance(parsed, dict):
        description = str(parsed.get("description") or parsed.get("text") or "").strip()
    elif isinstance(parsed, str):
        description = parsed.strip()
    else:
        raise ValueError(f"Unexpected response format from Claude: {type(parsed)}")

    if not description:
        raise ValueError(f"Could not extract description from Claude response:\n{raw_response}")

    # Load and update metadata.json
    metadata = utils.load_metadata_json(save_meta_path)

    metadata["description"] = description
    write_metadata_json(save_meta_path, metadata)

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
    parser.add_argument(
        "--profile",
        default=None,
        help="BrowserLLM browser profile to use (default: BrowserLLM's default).",
    )

    args = parser.parse_args()

    try:
        custom_meta = Path(args.metadata).resolve() if args.metadata else None
        description = generate_description_for_project(args.target, metadata_path=custom_meta, profile=args.profile)

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
