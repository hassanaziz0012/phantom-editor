#!/usr/bin/env python3
"""
YouTube Promotional Tweet Template Generator
=============================================
Generates a promotional tweet template (<= 240 chars + {url}) from video captions
using Claude and BrowserLLM, applying anti-AI writing principles, and saves it
to metadata.json as tweetTemplate.
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


def format_tweet_template(raw_tweet: str) -> str:
    """Formats the tweet text as a template containing the {url} placeholder."""
    cleaned = raw_tweet.strip().strip('"\'')

    # If the LLM already included {url}, keep it
    if "{url}" in cleaned:
        template = cleaned
    else:
        template = f"{cleaned}\n\n{{url}}"

    # Validate length with a sample YouTube short link
    sample_url = "https://youtu.be/12345678901"
    rendered = template.replace("{url}", sample_url)
    if len(rendered) > 280:
        # 280 - len(sample_url) - 1 (space) - 3 (ellipsis)
        max_base_len = 280 - len(sample_url) - 4
        cleaned_short = cleaned[:max_base_len].rstrip() + "..."
        template = f"{cleaned_short} {{url}}"

    return template


def generate_tweet_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
) -> str:
    """Generates a promotional tweet template and saves it to metadata.json for the project."""
    project_dir, default_meta_path, captions_path, title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    if not captions_path or not captions_path.is_file():
        raise FileNotFoundError(f"Captions file (.srt) not found for target '{target or project_dir}'")

    print(f"📖 Reading captions from {captions_path.name}...")
    transcript = utils.parse_srt_to_timestamped_transcript(captions_path)

    print("🤖 Generating promotional tweet template with Claude via BrowserLLM...")
    prompt = utils.load_prompt("generate_video_tweet.md", transcript=transcript)
    raw_response = utils.query_claude(prompt)

    parsed = utils.parse_claude_json(raw_response)
    if isinstance(parsed, dict):
        tweet_text = str(parsed.get("tweet") or parsed.get("description") or parsed.get("text") or "").strip()
    elif isinstance(parsed, str):
        tweet_text = parsed.strip()
    else:
        raise ValueError(f"Unexpected response format from Claude: {type(parsed)}")

    if not tweet_text:
        raise ValueError(f"Could not extract tweet text from Claude response:\n{raw_response}")

    tweet_template = format_tweet_template(tweet_text)

    # Load and update metadata.json
    metadata: dict[str, Any] = {}
    if save_meta_path.is_file():
        try:
            metadata = json.loads(save_meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    metadata["tweetTemplate"] = tweet_template
    save_meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return tweet_template


def main():
    parser = argparse.ArgumentParser(
        description="Generate promotional tweet template from .srt captions using Claude and update metadata.json."
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
        tweet_template = generate_tweet_for_project(args.target, metadata_path=custom_meta)

        print("\n🐦 Generated Tweet Template:")
        print("-" * 50)
        print(tweet_template)
        print("-" * 50)
        print("\n💾 Saved tweet template to metadata.json")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
