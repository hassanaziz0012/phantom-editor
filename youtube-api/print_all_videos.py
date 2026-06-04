#!/usr/bin/env python3
"""
Print All Videos Demo
=====================
A simple utility script that prints a formatted summary of all videos retrieved
from the channel defined in your environment configuration.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add the project root directory to sys.path to allow absolute package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api.fetch_videos import fetch_channel_videos

load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")


def main():
    if not API_KEY or not CHANNEL_ID:
        print("Error: YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID environment variables are missing.")
        sys.exit(1)

    print("Fetching videos...")
    try:
        videos = fetch_channel_videos(API_KEY, CHANNEL_ID)
    except Exception as e:
        print(f"Error fetching channel videos: {e}")
        sys.exit(1)

    for i, v in enumerate(videos, 1):
        print(f"=== Video {i} ===")
        print(f"Title: {v.title}")
        print(f"Views: {v.view_count}")
        print(f"Likes count: {v.like_count}")
        print(f"Comments count: {v.comment_count}")
        print(f"Description:\n{v.description}")
        print("=" * 40)
        print()


if __name__ == "__main__":
    main()
