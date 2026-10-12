#!/usr/bin/env python3
"""
Automatic YouTube Metadata Generator
====================================
Automatically creates or updates `metadata.json` in a YouTube video project folder.
If captions (.srt) are found, automatically generates:
1. Human-like 2-3 sentence video description
2. Promotional tweet template
3. Video recommendations from channel archive
4. Video chapters & timestamps
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    import metadata.utils as utils
    from metadata.auto_gen_desc import generate_description_for_project
    from metadata.auto_gen_tweet import generate_tweet_for_project
    from metadata.recommendations.recommend import recommend_videos_for_project
    from metadata.generate_timestamps import generate_timestamps_for_project
except ImportError:
    import utils  # noqa: E402
    from auto_gen_desc import generate_description_for_project  # noqa: E402
    from auto_gen_tweet import generate_tweet_for_project  # noqa: E402
    from recommendations.recommend import recommend_videos_for_project  # noqa: E402
    from generate_timestamps import generate_timestamps_for_project  # noqa: E402


T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 2.0,
    step_name: str = "operation",
) -> T | None:
    """Execute a function with retry logic and exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"⚠️  {step_name} failed (attempt {attempt}/{max_retries}): {e}")
                print(f"   Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                print(f"❌ {step_name} failed after {max_retries} attempts: {e}")
                return None
    return None


def auto_create_metadata(
    target: str | Path | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    category_id: str = "28",
    privacy_status: str = "public",
    made_for_kids: bool = False,
    skip_ai: bool = False,
    skip_recommendations: bool = False,
) -> dict[str, Any]:
    """Creates or updates metadata.json for the targeted video project."""
    project_dir, metadata_path, captions_path, default_title = utils.resolve_project_paths(target)

    # Load existing metadata if present
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            print(f"🔄 Updating existing metadata at: {metadata_path}")
        except Exception:
            metadata = {}
    else:
        print(f"✨ Creating new metadata at: {metadata_path}")

    # Set base properties
    final_title = title or metadata.get("title") or default_title
    metadata["title"] = final_title
    metadata.setdefault("description", "")
    metadata["tags"] = tags if tags is not None else metadata.get("tags", [])
    metadata["categoryId"] = category_id or metadata.get("categoryId", "28")
    metadata["privacyStatus"] = privacy_status or metadata.get("privacyStatus", "public")
    metadata["madeForKids"] = made_for_kids if made_for_kids is not None else metadata.get("madeForKids", False)
    metadata.setdefault("tweetTemplate", "🎬 New video just dropped! {url}")
    metadata.setdefault("recommendations", metadata.get("recommendations", []))
    metadata.setdefault("timestamps", metadata.get("timestamps", []))

    if not skip_ai:
        if captions_path and captions_path.is_file():
            print(f"\n📂 Found captions file: {captions_path.name}")

            # 1. Generate Description
            print("\n[1/4] Generating Description...")
            desc = retry_with_backoff(
                lambda: generate_description_for_project(captions_path, metadata_path=metadata_path),
                max_retries=3,
                base_delay=2.0,
                step_name="Description generation",
            )
            if desc is not None:
                metadata["description"] = desc
                print("✓ Generated video description.")
            else:
                print("⚠️  Skipping description generation after max retries.")

            # 2. Generate Tweet Template
            print("\n[2/4] Generating Promotional Tweet Template...")
            tweet = retry_with_backoff(
                lambda: generate_tweet_for_project(captions_path, metadata_path=metadata_path),
                max_retries=3,
                base_delay=2.0,
                step_name="Tweet template generation",
            )
            if tweet is not None:
                metadata["tweetTemplate"] = tweet
                print("✓ Generated promotional tweet template.")
            else:
                print("⚠️  Skipping tweet template generation after max retries.")

            # 3. Generate Recommendations
            if not skip_recommendations:
                print("\n[3/4] Generating Video Recommendations...")
                recs = retry_with_backoff(
                    lambda: recommend_videos_for_project(target=project_dir, metadata_path=metadata_path),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Recommendations generation",
                )
                if recs is not None:
                    metadata["recommendations"] = recs
                    print(f"✓ Generated {len(recs)} recommendations.")
                else:
                    print("⚠️  Skipping recommendations generation after max retries.")

            # 4. Generate Timestamps
            print("\n[4/4] Generating Video Timestamps...")
            timestamps = retry_with_backoff(
                lambda: generate_timestamps_for_project(target=project_dir, metadata_path=metadata_path),
                max_retries=3,
                base_delay=2.0,
                step_name="Timestamps generation",
            )
            if timestamps is not None:
                metadata["timestamps"] = timestamps
                print(f"✓ Generated {len(timestamps)} timestamps.")
            else:
                print("⚠️  Skipping timestamps generation after max retries.")

        else:
            print("\n⚠️  No phrase-level .srt captions file found in project folder.")
            print("   Skipping AI generation of description, tweet template, recommendations, and timestamps.")
            print("   Run `phantom edit transcribe <video>` to generate captions first if needed.")

    # Save to disk
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    formatted_json = json.dumps(metadata, indent=4, ensure_ascii=False)
    with open(metadata_path, "w", encoding="utf-8") as f:
        f.write(formatted_json + "\n")

    print("\n" + "=" * 50)
    print("📄 Saved Metadata Summary:")
    print("=" * 50)
    print(utils.highlight_json(formatted_json))
    print("=" * 50)
    print(f"\n✅ Successfully wrote {metadata_path}")

    return metadata


def main():
    parser = argparse.ArgumentParser(
        description="Automatically create or update metadata.json for a YouTube video project."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to the video file, captions file, or project folder (default: current directory).",
    )
    parser.add_argument("--title", "-t", default=None, help="Custom title for the video.")
    parser.add_argument("--tags", default=None, help="Comma-separated tags for the video.")
    parser.add_argument("--category-id", default="28", help="YouTube Category ID (default: 28 for Tech).")
    parser.add_argument(
        "--privacy-status",
        choices=["public", "private", "unlisted"],
        default="public",
        help="Privacy status (default: public).",
    )
    parser.add_argument(
        "--made-for-kids",
        action="store_true",
        default=False,
        help="Flag if the video is made for kids.",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        default=False,
        help="Skip AI generation of description, tweet template, recommendations, and timestamps.",
    )
    parser.add_argument(
        "--skip-recommendations",
        action="store_true",
        default=False,
        help="Skip generation of video recommendations.",
    )

    args = parser.parse_args()

    tags_list = [tag.strip() for tag in args.tags.split(",") if tag.strip()] if args.tags else None

    try:
        auto_create_metadata(
            target=args.target,
            title=args.title,
            tags=tags_list,
            category_id=args.category_id,
            privacy_status=args.privacy_status,
            made_for_kids=args.made_for_kids,
            skip_ai=args.skip_ai,
            skip_recommendations=args.skip_recommendations,
        )
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

