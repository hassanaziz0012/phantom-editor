#!/usr/bin/env python3
"""
YouTube Video Topics & Timestamps Generator
============================================
Reads a phrase-level .srt captions file, formats the transcript with timestamps,
sends it to Claude via BrowserLLM to generate structured topics/timestamps,
and saves them to the project's metadata.json.
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


def format_timestamps(data: Any) -> list[dict[str, str]]:
    """Normalizes raw JSON data from Claude into standard timestamp entries."""
    if isinstance(data, dict):
        data = data.get("timestamps", data.get("chapters", []))

    if not isinstance(data, list):
        raise ValueError(f"Expected list of timestamps, got: {type(data)}")

    results: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            ts = str(item.get("timestamp") or item.get("time") or "").strip().strip("[]()")
            topic = str(item.get("topic") or item.get("title") or "").strip()
            if ts and topic:
                parts = ts.split(":")
                if len(parts) == 2 and len(parts[0]) == 1:
                    ts = f"0{parts[0]}:{parts[1]}"
                results.append({"timestamp": ts, "topic": topic})

    if not results:
        raise ValueError("No valid timestamp entries could be parsed from response.")

    # Ensure first timestamp starts at 00:00
    if results and results[0]["timestamp"] not in ("00:00", "0:00", "00:00:00"):
        results.insert(0, {"timestamp": "00:00", "topic": "Introduction"})

    return results


def generate_timestamps_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
) -> list[dict[str, str]]:
    """Generates timestamps and saves them to metadata.json for the targeted project."""
    project_dir, default_meta_path, captions_path, title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    if not captions_path or not captions_path.is_file():
        raise FileNotFoundError(f"Captions file (.srt) not found for target '{target or project_dir}'")

    print(f"📖 Reading captions from {captions_path.name}...")
    transcript = utils.parse_srt_to_timestamped_transcript(captions_path)

    print("🤖 Generating timestamps with Claude via BrowserLLM...")
    prompt = utils.load_prompt("generate_timestamps.md", title=title, transcript=transcript)
    raw_response = utils.query_claude(prompt)

    parsed_json = utils.parse_claude_json(raw_response)
    timestamps = format_timestamps(parsed_json)

    # Load and update metadata.json
    metadata: dict[str, Any] = {}
    if save_meta_path.is_file():
        try:
            metadata = json.loads(save_meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    metadata["timestamps"] = timestamps
    save_meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return timestamps


def main():
    parser = argparse.ArgumentParser(
        description="Generate video topics and timestamps from phrase-level .srt captions and update metadata.json."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to the .srt captions file, video file, or project folder (default: current directory).",
    )
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        help="Path to metadata.json (default: metadata.json in the project folder).",
    )

    args = parser.parse_args()

    try:
        custom_meta = Path(args.metadata).resolve() if args.metadata else None
        timestamps = generate_timestamps_for_project(args.target, metadata_path=custom_meta)

        print("\n✅ Generated Timestamps:")
        for item in timestamps:
            print(f"  {item['timestamp']:<8} {item['topic']}")
        print(f"\n💾 Saved {len(timestamps)} timestamps to metadata.json")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
