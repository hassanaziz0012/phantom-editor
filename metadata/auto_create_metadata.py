#!/usr/bin/env python3
"""
Automatic YouTube Metadata Generator
====================================
Automatically creates or updates `metadata.json` in a YouTube video project folder.
If captions (.srt) are found, automatically generates:
1. Video chapters & timestamps
2. Human-like 2-3 sentence video description
3. Promotional tweet template
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
    from metadata.auto_gen_desc import generate_description_for_project
    from metadata.auto_gen_tweet import generate_tweet_for_project
    from metadata.generate_timestamps import generate_timestamps_for_project
except ImportError:
    import utils  # noqa: E402
    from auto_gen_desc import generate_description_for_project  # noqa: E402
    from auto_gen_tweet import generate_tweet_for_project  # noqa: E402
    from generate_timestamps import generate_timestamps_for_project  # noqa: E402


def auto_create_metadata(
    target: str | Path | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    category_id: str = "28",
    privacy_status: str = "public",
    made_for_kids: bool = False,
    skip_ai: bool = False,
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

    if not skip_ai:
        if captions_path and captions_path.is_file():
            print(f"\n📂 Found captions file: {captions_path.name}")

            # 1. Generate Timestamps
            try:
                print("\n[1/3] Generating Timestamps...")
                timestamps = generate_timestamps_for_project(captions_path, metadata_path=metadata_path)
                metadata["timestamps"] = timestamps
                print(f"✓ Generated {len(timestamps)} timestamps.")
            except Exception as e:
                print(f"⚠️  Failed to generate timestamps: {e}")

            # 2. Generate Description
            try:
                print("\n[2/3] Generating Description...")
                desc = generate_description_for_project(captions_path, metadata_path=metadata_path)
                metadata["description"] = desc
                print("✓ Generated video description.")
            except Exception as e:
                print(f"⚠️  Failed to generate description: {e}")

            # 3. Generate Tweet Template
            try:
                print("\n[3/3] Generating Promotional Tweet Template...")
                tweet = generate_tweet_for_project(captions_path, metadata_path=metadata_path)
                metadata["tweetTemplate"] = tweet
                print("✓ Generated promotional tweet template.")
            except Exception as e:
                print(f"⚠️  Failed to generate tweet template: {e}")

        else:
            print("\n⚠️  No phrase-level .srt captions file found in project folder.")
            print("   Skipping AI generation of timestamps, description, and tweet template.")
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
        help="Skip AI generation of timestamps, description, and tweet template.",
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
        )
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
