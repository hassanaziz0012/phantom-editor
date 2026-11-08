#!/usr/bin/env python3
"""
YouTube Video Title Generator
=============================
Reads a project's phrase-level `trimmed.srt` captions, asks Claude (via BrowserLLM) to
extract the video's key facts and generate scored title candidates, then saves:
- the full JSON response to `titles.json` in the project folder
- the ranked top titles (with scores) to the `titles` list in `metadata.json`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


import metadata.utils as utils


TRAFFIC_GOALS = ("search", "browse", "both")
CHANNEL_SIZES = ("small", "mid", "large")


def format_titles(data: Any) -> list[dict[str, Any]]:
    """Normalizes the ranked `top` list from Claude's response into metadata.json entries."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object with a 'top' list, got: {type(data)}")

    top = data.get("top")
    if not isinstance(top, list):
        raise ValueError("Response has no 'top' list of titles.")

    results: list[dict[str, Any]] = []
    for item in top:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        results.append(
            {
                "rank": item.get("rank"),
                "title": title,
                "archetype": item.get("archetype"),
                "traffic": item.get("traffic"),
                "char_count": len(title),
                "scores": item.get("scores") or {},
                "total": item.get("total"),
                "payoff_timestamp": item.get("payoff_timestamp"),
                "why": item.get("why"),
                "thumbnail_text": item.get("thumbnail_text"),
            }
        )

    if not results:
        raise ValueError("No valid titles could be parsed from response.")

    # Claude's own rank/total can disagree or be missing: re-rank by total, keeping order for ties
    results.sort(key=lambda t: t["total"] if isinstance(t["total"], (int, float)) else -1, reverse=True)
    for rank, entry in enumerate(results, start=1):
        entry["rank"] = rank

    return results


def generate_titles_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
    traffic_goal: str = "both",
    channel_size: str = "small",
    thumbnail_concept: str = "none yet",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Generates title candidates, writes titles.json, and saves the ranked list to metadata.json."""
    project_dir, default_meta_path, captions_path, title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    if not captions_path or not captions_path.is_file():
        raise FileNotFoundError(f"Captions file (.srt) not found for target '{target or project_dir}'")

    print(f"📖 Reading captions from {captions_path.name}...")
    transcript = utils.parse_srt_to_timestamped_transcript(captions_path)

    print("🤖 Generating video titles with Claude via BrowserLLM...")
    # transcript goes last so any braces inside it are never mistaken for other placeholders
    prompt = utils.load_prompt(
        "generate_video_titles.md",
        traffic_goal=traffic_goal,
        channel_size=channel_size,
        thumbnail_concept=thumbnail_concept,
        title=title,
        transcript=transcript,
    )
    raw_response = utils.ask_browserllm(prompt, model="sonnet", effort="high", output_json=True, profile=profile)

    parsed_json = utils.parse_json_response(raw_response)
    titles = format_titles(parsed_json)

    # The full response (extracted facts, all 25 titles, flags) is long, so it lives in titles.json
    titles_path = project_dir / "titles.json"
    titles_path.write_text(json.dumps(parsed_json, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

    # Load and update metadata.json
    metadata = utils.load_metadata_json(save_meta_path)

    metadata["titles"] = titles
    save_meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return titles


def main():
    parser = argparse.ArgumentParser(
        description="Generate scored video title ideas from trimmed.srt captions using Claude and update metadata.json."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to the project folder, .srt captions file, or video file (default: current directory).",
    )
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        help="Path to metadata.json (default: metadata.json in the project folder).",
    )
    parser.add_argument(
        "--traffic",
        choices=TRAFFIC_GOALS,
        default="both",
        help="Traffic goal: search keywords, browse/home-feed emotion, or both (default: both).",
    )
    parser.add_argument(
        "--channel-size",
        choices=CHANNEL_SIZES,
        default="small",
        help="Channel size; smaller channels get more concrete titles (default: small).",
    )
    parser.add_argument(
        "--thumbnail",
        default="none yet",
        help="Thumbnail concept/text so titles don't repeat it (default: 'none yet').",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="BrowserLLM browser profile to use (default: BrowserLLM's default).",
    )

    args = parser.parse_args()

    try:
        custom_meta = Path(args.metadata).resolve() if args.metadata else None
        titles = generate_titles_for_project(
            args.target,
            metadata_path=custom_meta,
            traffic_goal=args.traffic,
            channel_size=args.channel_size,
            thumbnail_concept=args.thumbnail,
            profile=args.profile,
        )

        print("\n✅ Generated Titles:")
        for item in titles:
            print(f"  {item['rank']}. [{item['total']}/16] {item['title']}  ({item['archetype']})")
            if item.get("thumbnail_text"):
                print(f"       thumbnail: {item['thumbnail_text']}")
        print(f"\n💾 Saved {len(titles)} titles to metadata.json (full response in titles.json)")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
