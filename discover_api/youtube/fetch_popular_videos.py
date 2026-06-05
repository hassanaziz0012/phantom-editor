"""
Fetch Popular YouTube Videos
=============================
Fetches the most popular videos of a specified YouTube channel within a given
time period (weekly, monthly, 3 months ago, 6 months ago, all time).
Supports sorting by views or likes.
"""

import os
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional, Any

from .models import Video
from .fetch_videos import fetch_video_details, get_uploads_playlist_id
from .utils import resolve_channel_id, get_youtube_client

logger = logging.getLogger("discover_api.youtube.fetch_popular_videos")


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


def get_popular_videos(
    channel_input: str,
    period: str = "monthly",
    sort: str = "views",
    limit: int = 10,
    api_key: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    Core computation for popular videos retrieval.
    Resolves the channel, pagination early-stopping via cutoff date, fetches stats,
    sorts descending, and returns a rank-ordered list.
    """
    target_api_key = api_key or os.getenv("YOUTUBE_API_KEY")

    # 1. Authenticate and Build API Client
    youtube = get_youtube_client(target_api_key)

    # 2. Resolve Channel ID
    channel_id = resolve_channel_id(youtube, channel_input)

    # 3. Determine Cutoff Date based on requested period
    now = datetime.now(UTC)
    cutoff_date: Optional[datetime] = None

    period_str = period.lower()
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
    video_ids = fetch_video_ids_with_cutoff(youtube, uploads_playlist_id, cutoff_date)

    if not video_ids:
        return []

    # 6. Fetch comprehensive video details & statistics
    videos = fetch_video_details(youtube, video_ids)

    # Apply strict date filtering to ensure precision at the boundary
    if cutoff_date:
        videos = [v for v in videos if v.published_at >= cutoff_date]

    # 7. Sort videos descending by requested metric
    if sort == "views":
        videos.sort(key=lambda v: (v.view_count or 0), reverse=True)
    else:
        videos.sort(key=lambda v: (v.like_count or 0), reverse=True)

    top_videos = videos[:limit]

    # 8. Return results payload
    payload = [
        {
            "rank": index,
            "video_id": v.video_id,
            "title": v.title,
            "url": v.url,
            "published_at": v.published_at.isoformat(),
            "thumbnail_url": v.thumbnail_url,
            "view_count": v.view_count,
            "like_count": v.like_count,
            "comment_count": v.comment_count,
            "duration": v.duration,
        }
        for index, v in enumerate(top_videos, start=1)
    ]
    return payload
