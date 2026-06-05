#!/usr/bin/env python3
"""
Fetch Popular YouTube Videos
=============================
Fetches the most popular videos of a specified YouTube channel within a given
time period (weekly, monthly, 3 months ago, 6 months ago, all time).
Supports sorting by views or likes.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Add the project root directory to sys.path to allow absolute package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Reuse Data models and helper functions from package
from youtube_api.models import Video
from youtube_api.fetch_videos import fetch_video_details, get_uploads_playlist_id
from youtube_api.utils import resolve_channel_id, get_youtube_client


def fetch_video_ids_with_cutoff(
    youtube, uploads_playlist_id: str, cutoff_date: Optional[datetime]
) -> list[str]:
    """
    Page through the uploads playlist to collect video IDs.
    Since playlistItems are returned in reverse chronological order (newest first),
    we optimize API usage by stopping pagination as soon as we encounter a video
    published before the cutoff_date.
    """
    video_ids = []
    next_page_token = None

    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()

        items = response.get("items", [])
        if not items:
            break

        reached_cutoff = False
        for item in items:
            published_str = item["contentDetails"].get("videoPublishedAt")
            if published_str:
                dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                if cutoff_date and dt < cutoff_date:
                    reached_cutoff = True
                    break
            video_ids.append(item["contentDetails"]["videoId"])

        if reached_cutoff:
            break

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


def format_video(index: int, video: Video) -> str:
    """Format video details cleanly for console output."""
    views = f"{video.view_count:,}" if video.view_count is not None else "N/A"
    likes = f"{video.like_count:,}" if video.like_count is not None else "N/A"
    comments = f"{video.comment_count:,}" if video.comment_count is not None else "N/A"
    pub_date = video.published_at.strftime("%Y-%m-%d")

    return (
        f"{index}. {video.title}\n"
        f"   views={views} | likes={likes} | comments={comments} | published={pub_date}\n"
        f"   {video.url}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the most popular videos of a YouTube channel in a given time period."
    )
    parser.add_argument(
        "channel",
        help="Channel URL, handle (e.g. @channel), or channel ID (UC...).",
    )
    parser.add_argument(
        "--period",
        choices=["weekly", "week", "monthly", "month", "3months", "3m", "6months", "6m", "all"],
        default="monthly",
        help="Time period to filter videos (default: monthly).",
    )
    parser.add_argument(
        "--sort",
        choices=["views", "likes"],
        default="views",
        help="Metric to sort videos by (default: views).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of popular videos to return (default: 10).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of console text.",
    )
    parser.add_argument(
        "--api-key",
        help="YouTube Data API key (overrides YOUTUBE_API_KEY env var).",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")

    # 1. Authenticate and Build API Client
    try:
        youtube = get_youtube_client(args.api_key)
    except Exception as e:
        print(f"Error authenticating: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Resolve Channel ID
    try:
        channel_id = resolve_channel_id(youtube, args.channel)
    except Exception as e:
        print(f"Error resolving channel ID: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Determine Cutoff Date based on requested period
    now = datetime.now(UTC)
    cutoff_date: Optional[datetime] = None

    period_str = args.period.lower()
    if period_str in ("weekly", "week"):
        cutoff_date = now - timedelta(days=7)
    elif period_str in ("monthly", "month"):
        cutoff_date = now - timedelta(days=30)
    elif period_str in ("3months", "3m"):
        cutoff_date = now - timedelta(days=90)
    elif period_str in ("6months", "6m"):
        cutoff_date = now - timedelta(days=180)
    # 'all' leaves cutoff_date as None

    # 4. Fetch Uploads Playlist ID
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)

    # 5. Fetch optimized video IDs up to the cutoff date
    if not args.json:
        print(f"📥 Collecting video IDs from playlist: {uploads_playlist_id} …", file=sys.stderr)
    
    video_ids = fetch_video_ids_with_cutoff(youtube, uploads_playlist_id, cutoff_date)
    
    if not args.json:
        print(f"   Found {len(video_ids)} videos uploaded in the specified period.", file=sys.stderr)

    if not video_ids:
        if args.json:
            print(json.dumps([]))
        else:
            print("No videos found in the specified time period.")
        return

    # 6. Fetch comprehensive video details & statistics
    if not args.json:
        print("📊 Fetching statistics and metadata …", file=sys.stderr)
    
    videos = fetch_video_details(youtube, video_ids)

    # Apply strict date filtering to ensure precision at the boundary
    if cutoff_date:
        videos = [v for v in videos if v.published_at >= cutoff_date]

    # 7. Sort videos descending by requested metric
    if args.sort == "views":
        videos.sort(key=lambda v: (v.view_count or 0), reverse=True)
    else:
        videos.sort(key=lambda v: (v.like_count or 0), reverse=True)

    top_videos = videos[:args.limit]

    # 8. Output results
    if args.json:
        payload = [
            {
                "rank": index,
                "video_id": v.video_id,
                "title": v.title,
                "url": v.url,
                "published_at": v.published_at.isoformat(),
                "view_count": v.view_count,
                "like_count": v.like_count,
                "comment_count": v.comment_count,
                "duration": v.duration,
            }
            for index, v in enumerate(top_videos, start=1)
        ]
        print(json.dumps(payload, indent=2))
        return

    print(f"\n⭐ Top {len(top_videos)} Most Popular Videos ({args.period}, sorted by {args.sort}):\n")
    for index, video in enumerate(top_videos, start=1):
        print(format_video(index, video))
        print()


if __name__ == "__main__":
    main()
