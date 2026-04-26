#!/usr/bin/env python3
"""
Create YouTube video metadata interactively.
Usage: python create_metadata.py /path/to/video.mp4
"""

import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Create metadata.json for a YouTube video.")
    parser.add_argument("video_path", help="Path to the .mp4 file.")
    args = parser.parse_args()

    video_path = Path(args.video_path).resolve()
    
    # Grab project name from the parent directory
    project_name = video_path.parent.name
    print(f"🎬 Project: {project_name}")
    print(f"Video file: {video_path.name}")
    print("-" * 40)
    print("Please provide the following metadata (press Enter to use the default):")

    default_title = video_path.stem
    title = input(f"Title [{default_title}]: ").strip() or default_title

    print("Description (Enter 'EOF' on a new line to finish):")
    description_lines = []
    while True:
        try:
            line = input()
            if line.strip() == "EOF":
                break
            description_lines.append(line)
        except EOFError:
            break
    description = "\n".join(description_lines)

    tags_input = input("Tags (comma-separated) []: ").strip()
    tags = [tag.strip() for tag in tags_input.split(",") if tag.strip()]

    category_id = input("Category ID [28 (Science & Technology)]: ").strip() or "28"
    
    privacy_status = input("Privacy Status (public, private, unlisted) [public]: ").strip().lower() or "public"
    if privacy_status not in ["public", "private", "unlisted"]:
        print(f"Warning: '{privacy_status}' is not a standard privacy status, defaulting to 'public'")
        privacy_status = "public"
    
    made_for_kids_input = input("Made for Kids? (y/N) [N]: ").strip().lower()
    made_for_kids = made_for_kids_input in ['y', 'yes']

    default_tweet = "🎬 New video just dropped! {url}"
    tweet_template = input(f"Tweet Template [{default_tweet}]: ").strip() or default_tweet

    metadata = {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": category_id,
        "privacyStatus": privacy_status,
        "madeForKids": made_for_kids,
        "tweetTemplate": tweet_template
    }

    metadata_path = video_path.parent / "metadata.json"
    
    # Display summary
    print("\n" + "=" * 40)
    print("Generated Metadata:")
    print(json.dumps(metadata, indent=4))
    print("=" * 40)
    
    confirm = input(f"Write to {metadata_path}? [Y/n]: ").strip().lower()
    if confirm in ['', 'y', 'yes']:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        print(f"\n✅ Created {metadata_path} successfully!")
    else:
        print("\n❌ Aborted. No file was written.")

if __name__ == "__main__":
    main()
