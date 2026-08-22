#!/usr/bin/env python3
"""
YouTube Video Titles Printer
============================
Fetches and prints all video titles for a given YouTube channel (or your channel from .env).

Usage:
    python -m youtube_api.print_titles [@channel_or_id] [--fresh] [--limit N] [--json]
    phantom yt print-titles [@channel_or_id] [--fresh]
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure project root is in sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api.fetch_videos import fetch_channel_videos
from youtube_api.utils import get_youtube_client, resolve_channel_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print all video titles from a YouTube channel."
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="YouTube channel ID, handle (@channel), or custom URL. Defaults to YOUTUBE_CHANNEL_ID in .env."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Force a fresh fetch from the YouTube API, bypassing and updating the local cache."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of video titles to output."
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Print oldest videos first (default is newest first)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output titles as a JSON array."
    )
    parser.add_argument(
        "--with-id",
        action="store_true",
        help="Include video ID with each title (format: '<title> (<video_id>)')."
    )
    parser.add_argument(
        "--numbered",
        action="store_true",
        help="Prefix each title with a line number."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress logs to stderr while fetching."
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("YOUTUBE_API_KEY")
    channel_env = os.getenv("YOUTUBE_CHANNEL_ID")

    if not api_key:
        print("Error: YOUTUBE_API_KEY must be set in your .env file or environment.", file=sys.stderr)
        sys.exit(1)

    target_channel = args.channel or channel_env
    if not target_channel:
        print(
            "Error: A YouTube channel must be specified (as an argument or in YOUTUBE_CHANNEL_ID in .env).",
            file=sys.stderr
        )
        sys.exit(1)

    try:
        youtube_client = get_youtube_client(api_key)
        resolved_id = resolve_channel_id(youtube_client, target_channel)
    except Exception as e:
        print(f"Error resolving channel ID for {target_channel!r}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        videos = fetch_channel_videos(
            api_key,
            resolved_id,
            fresh=args.fresh,
            quiet=not args.verbose
        )
    except Exception as e:
        print(f"Error fetching channel videos: {e}", file=sys.stderr)
        sys.exit(1)

    if args.reverse:
        videos.reverse()

    if args.limit is not None and args.limit > 0:
        videos = videos[:args.limit]

    if args.json:
        if args.with_id:
            output = [{"title": v.title, "video_id": v.video_id, "url": v.url} for v in videos]
        else:
            output = [v.title for v in videos]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        for i, v in enumerate(videos, start=1):
            title_text = f"{v.title} ({v.video_id})" if args.with_id else v.title
            if args.numbered:
                print(f"{i}. {title_text}")
            else:
                print(title_text)


if __name__ == "__main__":
    main()
