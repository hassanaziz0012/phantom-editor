"""
YouTube Channel Video Fetcher
==============================
Fetches all videos from a YouTube channel using the YouTube Data API v3
and organizes them into a list of structured Video objects.
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .models import Video
from .utils import get_youtube_client, resolve_channel_id

# Configure standard logger
logger = logging.getLogger("discover_api.youtube.fetch_videos")

# ── Configuration ─────────────────────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("YOUTUBE_API_KEY")
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(iso_string: str) -> datetime:
    """Parse an ISO 8601 timestamp returned by the API."""
    return datetime.fromisoformat(iso_string.replace("Z", "+00:00"))


def _best_thumbnail(thumbnails: dict) -> str:
    """Return the highest-quality thumbnail URL available."""
    for quality in ("maxres", "standard", "high", "medium", "default"):
        if quality in thumbnails:
            return thumbnails[quality]["url"]
    return ""


def video_to_dict(video: Video) -> dict:
    """Serialize a Video object to a dictionary."""
    return {
        "video_id": video.video_id,
        "title": video.title,
        "description": video.description,
        "published_at": video.published_at.isoformat(),
        "thumbnail_url": video.thumbnail_url,
        "channel_id": video.channel_id,
        "channel_title": video.channel_title,
        "view_count": video.view_count,
        "like_count": video.like_count,
        "comment_count": video.comment_count,
        "duration": video.duration,
        "tags": video.tags,
        "category_id": video.category_id,
        "live_broadcast": video.live_broadcast,
    }


def dict_to_video(d: dict) -> Video:
    """Deserialize a dictionary to a Video object."""
    return Video(
        video_id=d["video_id"],
        title=d["title"],
        description=d["description"],
        published_at=_parse_dt(d["published_at"]),
        thumbnail_url=d["thumbnail_url"],
        channel_id=d["channel_id"],
        channel_title=d["channel_title"],
        view_count=d.get("view_count"),
        like_count=d.get("like_count"),
        comment_count=d.get("comment_count"),
        duration=d.get("duration"),
        tags=d.get("tags", []),
        category_id=d.get("category_id"),
        live_broadcast=d.get("live_broadcast"),
    )


# ── Core functions ────────────────────────────────────────────────────────────

def get_uploads_playlist_id(youtube, channel_id: str) -> str:
    """
    Retrieve the 'uploads' playlist ID for a channel.
    Every channel has a hidden playlist that contains all its public videos.
    """
    response = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found or no content details for ID: {channel_id!r}")

    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_all_video_ids(youtube, uploads_playlist_id: str, cached_ids: Optional[set[str]] = None) -> tuple[list[str], bool]:
    """
    Page through the uploads playlist and collect every video ID.
    If cached_ids is provided, stop paging when we encounter an ID already in cache.
    Returns a tuple: (list of new video_ids, hit_cache boolean).
    """
    video_ids = []
    next_page_token = None
    hit_cache = False

    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()

        items = response.get("items", [])
        for item in items:
            vid = item["contentDetails"]["videoId"]
            if cached_ids and vid in cached_ids:
                hit_cache = True
                break
            video_ids.append(vid)

        if hit_cache:
            break

        # Log progress status message after every 500 videos collected
        if len(video_ids) // 500 > (len(video_ids) - len(items)) // 500:
            milestone = (len(video_ids) // 500) * 500
            if milestone > 0:
                logger.info(f"      Fetched {milestone} videos")

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids, hit_cache


def fetch_video_details(youtube, video_ids: list[str]) -> list[Video]:
    """
    Fetch full metadata + statistics for a list of video IDs.
    The API accepts up to 50 IDs per request, so we batch them.
    """
    videos = []

    # Process in batches of 50
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]

        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            snippet    = item.get("snippet", {})
            stats      = item.get("statistics", {})
            content    = item.get("contentDetails", {})
            thumbnails = snippet.get("thumbnails", {})

            video = Video(
                video_id       = item["id"],
                title          = snippet.get("title", ""),
                description    = snippet.get("description", ""),
                published_at   = _parse_dt(snippet.get("publishedAt", "1970-01-01T00:00:00Z")),
                thumbnail_url  = _best_thumbnail(thumbnails),
                channel_id     = snippet.get("channelId", ""),
                channel_title  = snippet.get("channelTitle", ""),
                tags           = snippet.get("tags", []),
                category_id    = snippet.get("categoryId"),
                live_broadcast = snippet.get("liveBroadcastContent"),
                view_count     = int(stats["viewCount"])    if "viewCount"    in stats else None,
                like_count     = int(stats["likeCount"])    if "likeCount"    in stats else None,
                comment_count  = int(stats["commentCount"]) if "commentCount" in stats else None,
                duration       = content.get("duration"),
            )
            videos.append(video)

    return videos


# ── Core Pipeline ─────────────────────────────────────────────────────────────

def fetch_channel_videos(api_key: str, channel_id: str, fresh: bool = False) -> list[Video]:
    """
    Full pipeline with caching: authenticate → find uploads playlist
    → check cache for channel_id → page uploads playlist (stopping early if hit cache)
    → enrich new video IDs with stats → merge and return all Video objects.
    """
    # 1. Establish and sanitize cache path (to prevent directory traversal)
    cache_dir = Path(__file__).resolve().parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    safe_channel_id = Path(channel_id).name
    cache_file = (cache_dir / f"{safe_channel_id}.json").resolve()
    
    # Boundary confinement check (fail closed)
    if not str(cache_file).startswith(str(cache_dir.resolve()) + os.sep):
        raise ValueError(f"Unsafe channel ID provided: {channel_id}")

    # 2. Try loading cached videos
    cached_videos = []
    cached_ids = set()
    
    if not fresh and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                if isinstance(cached_data, list):
                    cached_videos = [dict_to_video(v_dict) for v_dict in cached_data]
                    cached_ids = {v.video_id for v in cached_videos}
                    logger.info(f"      Fetched {len(cached_videos)} videos from cache")
        except Exception as e:
            # Handle corrupt/invalid cache gracefully
            logger.warning(f"      Warning: Failed to load cache from {cache_file.name} ({e}). Falling back to API.")

    youtube = get_youtube_client(api_key)

    logger.info(f"[1/3] Fetching uploads playlist for channel: {channel_id}")
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)

    # 3. Fetch video IDs from API, stopping early if we encounter a cached ID
    logger.info(f"[2/3] Collecting new video IDs from playlist: {uploads_playlist_id}")
    if fresh:
        logger.info("      Bypassing cache (--fresh requested)...")
    
    new_video_ids, hit_cache = fetch_all_video_ids(
        youtube, 
        uploads_playlist_id, 
        cached_ids=cached_ids if not fresh else None
    )
    
    if hit_cache:
        logger.info(f"      Found cached video ID. Stopping API fetch.")
    
    logger.info(f"      Found {len(new_video_ids)} new videos to fetch from API")

    # 4. Fetch details for new videos from API
    new_videos = []
    if new_video_ids:
        logger.info("[3/3] Fetching full metadata and statistics for new videos …")
        new_videos = fetch_video_details(youtube, new_video_ids)
        logger.info(f"      Fetched {len(new_videos)} videos from API")

    # 5. Merge new videos and cached videos
    # Make sure we don't have duplicates, keeping the fresh/new versions if any conflicts occur.
    all_videos_dict = {v.video_id: v for v in cached_videos}
    for v in new_videos:
        all_videos_dict[v.video_id] = v
        
    all_videos = list(all_videos_dict.values())

    # Sort newest → oldest by default
    all_videos.sort(key=lambda v: v.published_at, reverse=True)

    # 6. Save updated video list to JSON cache
    try:
        serialized_videos = [video_to_dict(v) for v in all_videos]
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(serialized_videos, f, indent=2, ensure_ascii=False)
        
        logger.info(f"      Saved {len(all_videos)} videos to cache: {cache_file.name}")
    except Exception as e:
        logger.warning(f"      Warning: Failed to save cache to {cache_file.name} ({e})")

    logger.info(f"Done! Returning {len(all_videos)} Video objects.")
    return all_videos
