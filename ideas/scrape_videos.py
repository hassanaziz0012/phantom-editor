#!/usr/bin/env python3
"""
YouTube Channel & Video Scraper (Last 12 Months)
=================================================
Scrapes a given YouTube channel, fetches all videos uploaded in the past 12 months,
computes channel averages and outlier performance scores (with 10% 30-day recency boost),
and persists all channel and video records into the PostgreSQL database.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Add project root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests

from ideas.outliers_db.channels import get_channel, upsert_channel
from ideas.outliers_db.connection import get_db_connection
from ideas.outliers_db.models import ChannelRecord, VideoRecord
from ideas.outliers_db.queries import get_outlier_videos
from ideas.outliers_db.schema import init_db
from ideas.outliers_db.scoring import compute_video_score_components, update_video_scores
from ideas.outliers_db.videos import upsert_videos_batch

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.scraper")


class IssueCaptureHandler(logging.Handler):
    """Captures warning, error, and critical log messages into a list for JSON output."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self.issues: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        if msg and msg not in self.issues:
            self.issues.append(msg)


def get_existing_channel_video_ids(channel_id: str) -> set[str]:
    """Retrieves the set of video IDs already stored in PostgreSQL for a given channel."""
    sql = "SELECT video_id FROM videos WHERE channel_id = %s;"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (channel_id,))
            rows = cur.fetchall()
            return {r["video_id"] if isinstance(r, dict) else r[0] for r in rows}


def serialize_video(v: VideoRecord) -> dict[str, Any]:
    """Serializes a VideoRecord dataclass into a JSON-serializable dictionary."""
    return {
        "video_id": v.video_id,
        "channel_id": v.channel_id,
        "title": v.title,
        "description": v.description,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "duration": v.duration,
        "duration_seconds": v.duration_seconds,
        "thumbnail_url": v.thumbnail_url,
        "tags": v.tags,
        "category_id": v.category_id,
        "url": v.url,
        "view_count": v.view_count,
        "like_count": v.like_count,
        "comment_count": v.comment_count,
        "is_short": v.is_short,
        "view_score": round(v.view_score, 4),
        "like_score": round(v.like_score, 4),
        "base_score": round(v.base_score, 4),
        "score": round(v.score, 4),
        "is_outlier": v.is_outlier,
        "last_scraped_at": v.last_scraped_at.isoformat() if v.last_scraped_at else None,
    }


def serialize_video_summary(v: VideoRecord) -> dict[str, Any]:
    """Serializes a compact summary of a VideoRecord for lists."""
    return {
        "video_id": v.video_id,
        "channel_id": v.channel_id,
        "title": v.title,
        "url": v.url,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "duration_seconds": v.duration_seconds,
        "is_short": v.is_short,
        "view_count": v.view_count,
        "like_count": v.like_count,
        "comment_count": v.comment_count,
        "view_score": round(v.view_score, 4),
        "like_score": round(v.like_score, 4),
        "base_score": round(v.base_score, 4),
        "score": round(v.score, 4),
        "is_outlier": v.is_outlier,
    }


def serialize_channel(c: ChannelRecord) -> dict[str, Any]:
    """Serializes a ChannelRecord dataclass into a JSON-serializable dictionary."""
    return {
        "channel_id": c.channel_id,
        "title": c.title,
        "description": c.description,
        "custom_url": c.custom_url,
        "published_at": c.published_at.isoformat() if c.published_at else None,
        "subscriber_count": c.subscriber_count,
        "video_count": c.video_count,
        "total_view_count": c.total_view_count,
        "avg_views": round(c.avg_views, 2),
        "avg_likes": round(c.avg_likes, 2),
        "thumbnail_url": c.thumbnail_url,
        "country": c.country,
        "last_scraped_at": c.last_scraped_at.isoformat() if c.last_scraped_at else None,
    }

# Default paths
DEFAULT_TOKEN_FILE = repo_root / "youtube_api" / "tokens" / "token.json"
DEFAULT_CLIENT_SECRET = repo_root / "youtube_api" / "tokens" / "client_secret.json"


# ── Authentication & Service Setup ─────────────────────────────────────────────

def get_youtube_service(
    token_path: Optional[Path | str] = None,
    api_key: Optional[str] = None,
):
    """
    Builds and returns an authorized YouTube Data API v3 client.
    Intelligently checks OAuth token scopes in youtube_api/tokens/token.json,
    and uses/falls back to YOUTUBE_API_KEY from .env for reading data.
    """
    token_file = Path(token_path) if token_path else DEFAULT_TOKEN_FILE
    env_api_key = api_key or os.getenv("YOUTUBE_API_KEY")

    # 1. Check OAuth token credentials
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds:
                # Check if scopes are suitable for reading data (or if only upload scope)
                has_read_scope = any(
                    s in (creds.scopes or [])
                    for s in (
                        "https://www.googleapis.com/auth/youtube.readonly",
                        "https://www.googleapis.com/auth/youtube",
                        "https://www.googleapis.com/auth/youtube.force-ssl",
                    )
                )

                if has_read_scope:
                    if creds.expired and creds.refresh_token:
                        logger.info("OAuth token expired, refreshing...")
                        creds.refresh(Request())
                        try:
                            with open(token_file, "w", encoding="utf-8") as f:
                                f.write(creds.to_json())
                        except Exception as write_err:
                            logger.warning("Could not persist refreshed token: %s", write_err)

                    if creds.valid:
                        logger.info("Authenticated successfully using OAuth credentials.")
                        return build("youtube", "v3", credentials=creds)
                elif env_api_key:
                    logger.info("OAuth token has upload scope; using YOUTUBE_API_KEY for channel scraping.")
                    return build("youtube", "v3", developerKey=env_api_key)
        except Exception as oauth_err:
            logger.warning("OAuth load error: %s. Falling back to API key.", oauth_err)

    # 2. Use API Key
    if env_api_key:
        logger.info("Authenticated successfully using YOUTUBE_API_KEY.")
        return build("youtube", "v3", developerKey=env_api_key)

    raise RuntimeError(
        "No valid YouTube credentials found! Please set 'YOUTUBE_API_KEY' in your .env file "
        f"or provide an authorized token in '{DEFAULT_TOKEN_FILE}'."
    )


# ── Channel Resolution & Metadata ──────────────────────────────────────────────

def resolve_channel_id(youtube, channel_input: str) -> str:
    """
    Resolves a YouTube channel ID from various input formats:
    - Direct Channel ID (UC...)
    - Channel URL (https://www.youtube.com/channel/UC...)
    - Handle URL or @handle (https://www.youtube.com/@handle or @handle)
    - Custom URL (https://www.youtube.com/c/name)
    - User URL (https://www.youtube.com/user/name)
    - Search query fallback
    """
    channel_input = channel_input.strip()

    # 1. Direct Channel ID
    if re.fullmatch(r"UC[\w-]{22}", channel_input):
        return channel_input

    # 2. Channel URL containing /channel/UC...
    match = re.search(r"channel/(UC[\w-]{22})", channel_input)
    if match:
        return match.group(1)

    # 3. Handle (starts with @ or URL contains /@)
    handle_match = re.search(r"@([\w.-]+)", channel_input)
    if handle_match:
        handle = handle_match.group(1)
        try:
            response = youtube.channels().list(
                part="id",
                forHandle=f"@{handle}",
            ).execute()
            items = response.get("items", [])
            if items:
                return items[0]["id"]
        except Exception as e:
            logger.debug("forHandle lookup failed: %s, trying search fallback...", e)

    # 4. User URL containing /user/...
    user_match = re.search(r"/user/([\w.-]+)", channel_input)
    if user_match:
        username = user_match.group(1)
        try:
            response = youtube.channels().list(
                part="id",
                forUsername=username,
            ).execute()
            items = response.get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass

    # 5. Search fallback
    query = channel_input.split("/")[-1] if "/" in channel_input else channel_input
    response = youtube.search().list(
        part="snippet",
        q=query,
        type="channel",
        maxResults=1,
    ).execute()
    items = response.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]

    raise ValueError(f"Could not resolve YouTube channel ID for input: '{channel_input}'")


def _best_thumbnail(thumbnails: dict[str, Any]) -> Optional[str]:
    """Selects the highest resolution thumbnail URL available."""
    for quality in ("maxres", "standard", "high", "medium", "default"):
        if quality in thumbnails and "url" in thumbnails[quality]:
            return thumbnails[quality]["url"]
    return None


def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """Parses an ISO 8601 duration string (e.g. PT1H4M13S) into total seconds."""
    if not value:
        return None
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def fetch_channel_details(youtube, channel_id: str) -> tuple[dict[str, Any], str]:
    """
    Fetches channel metadata and returns a dictionary of attributes
    plus the uploads playlist ID.
    """
    response = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        id=channel_id,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise ValueError(f"No YouTube channel found with ID '{channel_id}'")

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})

    uploads_playlist_id = content.get("relatedPlaylists", {}).get("uploads")
    if not uploads_playlist_id:
        raise ValueError(f"Channel '{channel_id}' has no public uploads playlist.")

    published_str = snippet.get("publishedAt")
    published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00")) if published_str else None

    channel_data = {
        "channel_id": channel_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "custom_url": snippet.get("customUrl"),
        "published_at": published_dt,
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "total_view_count": int(stats.get("viewCount", 0)),
        "thumbnail_url": _best_thumbnail(snippet.get("thumbnails", {})),
        "country": snippet.get("country"),
        "last_scraped_at": datetime.now(timezone.utc),
    }

    return channel_data, uploads_playlist_id


# ── Fetching Videos from the Last 12 Months ────────────────────────────────────

def fetch_recent_video_ids(
    youtube,
    uploads_playlist_id: str,
    months: int = 12,
) -> list[str]:
    """
    Paginates through the uploads playlist (which is ordered newest to oldest)
    and collects all video IDs published within the last `months` (default: 12 months / 365 days).
    Stops pagination as soon as a video exceeds the cutoff date.
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=int(months * 30.5))
    logger.info(
        "Scanning uploads playlist %s for videos uploaded since %s (last %d months)...",
        uploads_playlist_id,
        cutoff_date.strftime("%Y-%m-%d"),
        months,
    )

    video_ids: list[str] = []
    next_page_token: Optional[str] = None
    reached_cutoff = False

    while True:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()

        items = response.get("items", [])
        if not items:
            break

        for item in items:
            content_details = item.get("contentDetails", {})
            snippet = item.get("snippet", {})
            vid = content_details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            if not vid:
                continue

            # Check publication timestamp
            pub_str = content_details.get("videoPublishedAt") or snippet.get("publishedAt")
            if pub_str:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt < cutoff_date:
                    reached_cutoff = True
                    break

            video_ids.append(vid)

        if reached_cutoff:
            logger.info("Reached videos older than %d months. Completed playlist scan.", months)
            break

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    logger.info("Found %d videos uploaded within the last %d months.", len(video_ids), months)
    return video_ids


def check_is_youtube_short(
    video_id: str,
    duration_seconds: Optional[int],
    timeout: float = 5.0,
) -> bool:
    """
    Determines whether a YouTube video is a YouTube Short:
    1. If duration > 180 seconds (or duration is None), it cannot be a Short (YouTube Shorts can be up to 180 seconds).
    2. If duration <= 180 seconds, sends a HEAD request with allow_redirects=False
       to https://www.youtube.com/shorts/{video_id}.
       - Returns True if response status code == 200 (direct Short URL).
       - Returns False if redirected (status 303/302 to /watch?v=...) or non-200.
    """
    if duration_seconds is None or duration_seconds > 180:
        return False

    url = f"https://www.youtube.com/shorts/{video_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.head(url, allow_redirects=False, headers=headers, timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        logger.debug("HEAD request failed for short check on video %s: %s", video_id, e)
        return False


def fetch_video_details_batch(
    youtube,
    video_ids: list[str],
    channel_id: str,
) -> list[dict[str, Any]]:
    """
    Fetches full video snippet, statistics, and contentDetails for a list of video IDs
    in batches of 50, and determines whether each video is a YouTube Short.
    """
    if not video_ids:
        return []

    raw_videos: list[dict[str, Any]] = []
    total = len(video_ids)

    for i in range(0, total, 50):
        batch = video_ids[i : i + 50]
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            pub_str = snippet.get("publishedAt")
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")) if pub_str else None

            duration_raw = content.get("duration")
            duration_sec = parse_iso8601_duration(duration_raw)

            raw_videos.append(
                {
                    "video_id": item["id"],
                    "channel_id": channel_id,
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", ""),
                    "published_at": pub_dt,
                    "duration": duration_raw,
                    "duration_seconds": duration_sec,
                    "thumbnail_url": _best_thumbnail(snippet.get("thumbnails", {})),
                    "tags": snippet.get("tags", []),
                    "category_id": snippet.get("categoryId"),
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "is_short": False,
                    "last_scraped_at": datetime.now(timezone.utc),
                }
            )

    # Determine is_short for all videos
    # Only videos with duration <= 180s need a HEAD request check
    short_candidates = [
        v for v in raw_videos
        if v["duration_seconds"] is not None and v["duration_seconds"] <= 180
    ]

    if short_candidates:
        logger.info(
            "Checking %d potential short videos (duration <= 180s) via YouTube Shorts endpoint...",
            len(short_candidates),
        )
        with ThreadPoolExecutor(max_workers=min(15, len(short_candidates))) as executor:
            future_to_video = {
                executor.submit(check_is_youtube_short, v["video_id"], v["duration_seconds"]): v
                for v in short_candidates
            }
            for future in as_completed(future_to_video):
                video_item = future_to_video[future]
                try:
                    is_short = future.result()
                    video_item["is_short"] = is_short
                    if is_short:
                        video_item["url"] = f"https://www.youtube.com/shorts/{video_item['video_id']}"
                except Exception as err:
                    logger.debug("Error checking short status for %s: %s", video_item["video_id"], err)
                    video_item["is_short"] = False

    shorts_count = sum(1 for v in raw_videos if v["is_short"])
    logger.info(
        "Identified %d Shorts and %d long-form videos out of %d total videos.",
        shorts_count,
        len(raw_videos) - shorts_count,
        len(raw_videos),
    )

    return raw_videos


def read_channels_from_file(file_path: Path | str) -> list[str]:
    """
    Reads channel identifiers/URLs from a text file, ignoring empty lines and comments (# or //).
    Returns a deduplicated list preserving original file order.
    """
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Channels file not found: {file_path}")

    channels: list[str] = []
    seen: set[str] = set()

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            # Ignore empty lines and comments
            if not cleaned or cleaned.startswith("#") or cleaned.startswith("//"):
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                channels.append(cleaned)

    return channels


# ── Core Scraper Pipeline ──────────────────────────────────────────────────────

def scrape_and_save_channel_videos(
    channel_input: str,
    months: int = 12,
    outlier_threshold: float = 1.0,
    api_key: Optional[str] = None,
    token_path: Optional[Path | str] = None,
    youtube_service: Optional[Any] = None,
    quiet: bool = False,
) -> tuple[ChannelRecord, list[VideoRecord], dict[str, Any]]:
    """
    Full pipeline for a single channel:
    1. Authenticate with YouTube API (or reuse existing youtube_service)
    2. Resolve channel ID and fetch channel metadata
    3. Check existing channel stats & stored video IDs in PostgreSQL
    4. Fetch all videos uploaded in the past 12 months
    5. Fetch video details and stats in batch
    6. Compute channel average views and likes
    7. Calculate video scores:
       - view_score = video views / avg views
       - like_score = video likes / avg likes
       - base_score = 0.75 * view_score + 0.25 * like_score
       - 10% recency boost for videos uploaded within the last 30 days (0% boost otherwise)
       - final_score = base_score * recency_boost
       - is_outlier = (view_score >= outlier_threshold OR score >= outlier_threshold)
    8. Save channel and all videos into PostgreSQL database
    9. Trigger score recalculation to keep database perfectly consistent
    10. Return channel record, video records, and detailed channel summary data
    """
    # 1. Initialize DB schema
    init_db()

    # 2. Authenticate / reuse service
    youtube = youtube_service or get_youtube_service(token_path=token_path, api_key=api_key)

    # 3. Resolve Channel
    channel_id = resolve_channel_id(youtube, channel_input)
    logger.info("Resolved target channel ID: %s", channel_id)

    # 4. Check existing channel & stored video IDs before scraping
    existing_channel = get_channel(channel_id)
    existing_video_ids = get_existing_channel_video_ids(channel_id)
    is_new_channel = (existing_channel is None)

    # 5. Fetch Channel Metadata from YouTube
    channel_dict, uploads_playlist_id = fetch_channel_details(youtube, channel_id)
    logger.info("Fetched channel metadata: '%s' (%s subscribers)", channel_dict["title"], f"{channel_dict['subscriber_count']:,}")

    # 6. Fetch Video IDs from past months
    video_ids = fetch_recent_video_ids(youtube, uploads_playlist_id, months=months)
    if not video_ids:
        logger.warning("No videos found in the last %d months for channel '%s'.", months, channel_dict["title"])
        channel_record = ChannelRecord(**channel_dict)
        upsert_channel(channel_record)
        empty_report = {
            "channel_id": channel_record.channel_id,
            "title": channel_record.title,
            "custom_url": channel_record.custom_url,
            "subscriber_count": channel_record.subscriber_count,
            "video_count": channel_record.video_count,
            "total_view_count": channel_record.total_view_count,
            "avg_views": round(channel_record.avg_views, 2),
            "avg_likes": round(channel_record.avg_likes, 2),
            "thumbnail_url": channel_record.thumbnail_url,
            "country": channel_record.country,
            "is_new_channel": is_new_channel,
            "changes": {
                "subscriber_count_delta": (
                    channel_record.subscriber_count - existing_channel["subscriber_count"]
                    if existing_channel and existing_channel.get("subscriber_count") is not None
                    else None
                ),
                "avg_views_delta": 0.0,
                "avg_likes_delta": 0.0,
                "total_view_count_delta": (
                    channel_record.total_view_count - existing_channel["total_view_count"]
                    if existing_channel and existing_channel.get("total_view_count") is not None
                    else None
                ),
                "previous_last_scraped_at": (
                    existing_channel["last_scraped_at"].isoformat()
                    if existing_channel and existing_channel.get("last_scraped_at") and hasattr(existing_channel["last_scraped_at"], "isoformat")
                    else str(existing_channel["last_scraped_at"])
                    if existing_channel and existing_channel.get("last_scraped_at")
                    else None
                ),
            },
            "stats": {
                "videos_scraped": 0,
                "new_videos_added": 0,
                "existing_videos_updated": 0,
                "shorts_count": 0,
                "long_form_count": 0,
                "outliers_count": 0,
            },
        }
        return channel_record, [], empty_report

    # 7. Fetch Full Video Details
    raw_videos = fetch_video_details_batch(youtube, video_ids, channel_id)
    logger.info("Retrieved complete metadata and metrics for %d videos.", len(raw_videos))

    # 8. Calculate Channel Averages (from the scrape window)
    total_views = sum(v["view_count"] for v in raw_videos)
    total_likes = sum(v["like_count"] for v in raw_videos)
    video_count = len(raw_videos)

    avg_views = round(total_views / video_count, 2) if video_count > 0 else 0.0
    avg_likes = round(total_likes / video_count, 2) if video_count > 0 else 0.0

    channel_dict["avg_views"] = avg_views
    channel_dict["avg_likes"] = avg_likes
    channel_record = ChannelRecord(**channel_dict)

    # 9. Compute Performance & Outlier Scores for Each Video
    video_records: list[VideoRecord] = []
    new_videos: list[VideoRecord] = []
    updated_videos: list[VideoRecord] = []

    for raw_v in raw_videos:
        scores = compute_video_score_components(
            view_count=raw_v["view_count"],
            like_count=raw_v["like_count"],
            channel_avg_views=avg_views,
            channel_avg_likes=avg_likes,
            published_at=raw_v["published_at"],
            outlier_threshold=outlier_threshold,
        )

        video_data = {**raw_v, **scores}
        v_record = VideoRecord(**video_data)
        video_records.append(v_record)

        if v_record.video_id not in existing_video_ids:
            new_videos.append(v_record)
        else:
            updated_videos.append(v_record)

    # 10. Upsert Channel & Videos into PostgreSQL
    logger.info("Saving channel record to PostgreSQL database...")
    upsert_channel(channel_record)

    logger.info(
        "Batch saving %d video records to PostgreSQL database (%d new, %d updated)...",
        len(video_records),
        len(new_videos),
        len(updated_videos),
    )
    upsert_videos_batch(video_records)

    # 11. Update SQL-side scores for database triggers and indexes
    update_video_scores(channel_id=channel_id, outlier_threshold=outlier_threshold)

    logger.info("Successfully scraped, scored, and stored %d videos for '%s'.", len(video_records), channel_record.title)

    # 12. Calculate Changes and Summary Stats
    outliers = [v for v in video_records if v.is_outlier or v.score >= outlier_threshold]
    outliers.sort(key=lambda v: (v.score, v.view_score), reverse=True)
    shorts_count = sum(1 for v in video_records if v.is_short)
    long_count = len(video_records) - shorts_count

    sub_delta = (
        channel_record.subscriber_count - existing_channel["subscriber_count"]
        if existing_channel and existing_channel.get("subscriber_count") is not None
        else None
    )
    avg_views_delta = (
        round(channel_record.avg_views - float(existing_channel["avg_views"]), 2)
        if existing_channel and existing_channel.get("avg_views") is not None
        else None
    )
    avg_likes_delta = (
        round(channel_record.avg_likes - float(existing_channel["avg_likes"]), 2)
        if existing_channel and existing_channel.get("avg_likes") is not None
        else None
    )
    total_views_delta = (
        channel_record.total_view_count - existing_channel["total_view_count"]
        if existing_channel and existing_channel.get("total_view_count") is not None
        else None
    )
    prev_scraped_at = (
        existing_channel["last_scraped_at"].isoformat()
        if existing_channel and existing_channel.get("last_scraped_at") and hasattr(existing_channel["last_scraped_at"], "isoformat")
        else str(existing_channel["last_scraped_at"])
        if existing_channel and existing_channel.get("last_scraped_at")
        else None
    )

    channel_report = {
        "channel_id": channel_record.channel_id,
        "title": channel_record.title,
        "custom_url": channel_record.custom_url,
        "subscriber_count": channel_record.subscriber_count,
        "video_count": channel_record.video_count,
        "total_view_count": channel_record.total_view_count,
        "avg_views": round(channel_record.avg_views, 2),
        "avg_likes": round(channel_record.avg_likes, 2),
        "thumbnail_url": channel_record.thumbnail_url,
        "country": channel_record.country,
        "is_new_channel": is_new_channel,
        "changes": {
            "subscriber_count_delta": sub_delta,
            "avg_views_delta": avg_views_delta,
            "avg_likes_delta": avg_likes_delta,
            "total_view_count_delta": total_views_delta,
            "previous_last_scraped_at": prev_scraped_at,
        },
        "stats": {
            "videos_scraped": len(video_records),
            "new_videos_added": len(new_videos),
            "existing_videos_updated": len(updated_videos),
            "shorts_count": shorts_count,
            "long_form_count": long_count,
            "outliers_count": len(outliers),
        },
    }

    return channel_record, video_records, channel_report


def scrape_channels_bulk(
    channel_inputs: list[str],
    months: int = 12,
    outlier_threshold: float = 1.0,
    api_key: Optional[str] = None,
    token_path: Optional[Path | str] = None,
    stop_on_error: bool = False,
    quiet: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], list[VideoRecord]]:
    """
    Bulk pipeline:
    Iterates over multiple channels, scrapes metadata and videos from the last 12 months,
    computes outlier performance, and saves all channels and videos to PostgreSQL.
    Returns:
      - channel_reports: list of dicts with channel summary stats and details
      - failed_channels: list of (channel_input, error_message)
      - all_outliers: combined list of outlier VideoRecords sorted by score descending (used for terminal report)
    """
    init_db()
    youtube = get_youtube_service(token_path=token_path, api_key=api_key)

    channel_reports: list[dict[str, Any]] = []
    failed_channels: list[tuple[str, str]] = []
    all_outliers: list[VideoRecord] = []

    total_count = len(channel_inputs)
    logger.info("Starting bulk scraping for %d channels...", total_count)

    for index, ch_input in enumerate(channel_inputs, 1):
        if not quiet:
            print("\n" + "=" * 80)
            print(f"🚀 [{index}/{total_count}] Scraping: {ch_input}")
            print("=" * 80)

        try:
            channel_record, video_records, report = scrape_and_save_channel_videos(
                channel_input=ch_input,
                months=months,
                outlier_threshold=outlier_threshold,
                youtube_service=youtube,
                quiet=quiet,
            )

            outliers = [v for v in video_records if v.is_outlier or v.score >= outlier_threshold]
            all_outliers.extend(outliers)
            channel_reports.append(report)

            if not quiet:
                # Print single channel report
                print_scrape_summary(channel_record, video_records, outlier_threshold=outlier_threshold)

        except Exception as err:
            logger.error("Failed to scrape channel '%s': %s", ch_input, err)
            failed_channels.append((ch_input, str(err)))
            if stop_on_error:
                raise

    all_outliers.sort(key=lambda v: (v.score, v.view_score), reverse=True)
    return channel_reports, failed_channels, all_outliers


# ── Terminal Display Utilities ──────────────────────────────────────────────────

def format_number(val: Any) -> str:
    """Formats large numbers into compact human-readable strings (e.g. 1.2M, 45.3K)."""
    if val is None:
        return "-"
    try:
        num = float(val)
        if num >= 1_000_000:
            return f"{num / 1_000_000:.2f}M"
        if num >= 1_000:
            return f"{num / 1_000:.1f}K"
        return f"{num:.0f}" if num.is_integer() else f"{num:.1f}"
    except (ValueError, TypeError):
        return str(val)


def print_scrape_summary(
    channel: ChannelRecord,
    videos: list[VideoRecord],
    outlier_threshold: float = 1.0,
) -> None:
    """Prints a structured, formatted summary table of the scraped channel and outlier videos."""
    outliers = [v for v in videos if v.is_outlier or v.score >= outlier_threshold]
    # Sort by final score descending
    outliers.sort(key=lambda v: (v.score, v.view_score), reverse=True)

    print("\n" + "=" * 80)
    print(f"📺  YOUTUBE CHANNEL SCRAPING & OUTLIER REPORT")
    print("=" * 80)
    shorts_count = sum(1 for v in videos if v.is_short)
    long_count = len(videos) - shorts_count

    print(f"Channel:        {channel.title} ({channel.custom_url or channel.channel_id})")
    print(f"Subscribers:    {format_number(channel.subscriber_count)}")
    print(f"Lifetime Views: {format_number(channel.total_view_count)}")
    print(f"Scrape Window:  Past 12 Months")
    print(f"Videos (12m):   {len(videos)} ({shorts_count} Shorts, {long_count} Long-form)")
    print(f"Avg Views (12m):{format_number(channel.avg_views)} views/video")
    print(f"Avg Likes (12m):{format_number(channel.avg_likes)} likes/video")
    print(f"Outliers Found: {len(outliers)} videos exceeding performance threshold ({outlier_threshold}x)")
    print("-" * 85)

    if not outliers:
        print("No outlier videos detected based on the given threshold.")
        print("=" * 85 + "\n")
        return

    print(f"{'TITLE':<36} | {'TYPE':<6} | {'VIEWS':<8} | {'VIEW MULT':<9} | {'RECENCY':<7} | {'SCORE':<7} | {'URL'}")
    print("-" * 85)

    now = datetime.now(timezone.utc)
    for v in outliers[:15]:
        title_truncated = (v.title[:33] + "...") if len(v.title) > 36 else v.title
        type_str = "Short" if v.is_short else "Video"
        view_str = format_number(v.view_count)
        mult_str = f"{v.view_score:.2f}x"
        
        # Check if 10% recency boost applied
        is_recent = False
        if v.published_at:
            pub_utc = v.published_at if v.published_at.tzinfo else v.published_at.replace(tzinfo=timezone.utc)
            is_recent = (now - pub_utc).total_seconds() <= 30 * 86400

        recency_str = "+10%" if is_recent else "  -"
        score_str = f"{v.score:.2f}"

        print(f"{title_truncated:<36} | {type_str:<6} | {view_str:<8} | {mult_str:<9} | {recency_str:<7} | {score_str:<7} | {v.url}")

    if len(outliers) > 15:
        print(f"... and {len(outliers) - 15} more outlier videos saved in PostgreSQL.")

    print("=" * 85 + "\n")


def print_bulk_summary(
    results: list[dict[str, Any]],
    failed_channels: list[tuple[str, str]],
    all_outliers: list[VideoRecord],
    outlier_threshold: float = 1.0,
) -> None:
    """Prints a consolidated summary report after bulk channel processing."""
    total_processed = len(results) + len(failed_channels)
    total_videos = sum(r["video_count"] for r in results)
    total_shorts = sum(r["shorts_count"] for r in results)
    total_long = sum(r["long_count"] for r in results)
    total_outliers = sum(r["outliers_count"] for r in results)

    print("\n" + "═" * 90)
    print("📊  BULK SCRAPING COMPLETED SUMMARY REPORT")
    print("═" * 90)
    print(f"Channels Processed: {total_processed} ({len(results)} Succeeded, {len(failed_channels)} Failed)")
    print(f"Total Videos Saved: {total_videos} ({total_shorts} Shorts, {total_long} Long-form)")
    print(f"Total Outliers:     {total_outliers} (Threshold: {outlier_threshold}x)")
    print("-" * 90)

    if results:
        print(f"{'CHANNEL':<30} | {'SUBS':<10} | {'VIDEOS':<8} | {'AVG VIEWS':<11} | {'AVG LIKES':<11} | {'OUTLIERS':<8}")
        print("-" * 90)
        for r in results:
            ch: ChannelRecord = r["channel_record"]
            title = (ch.title[:27] + "...") if len(ch.title) > 30 else ch.title
            print(
                f"{title:<30} | "
                f"{format_number(ch.subscriber_count):<10} | "
                f"{r['video_count']:<8} | "
                f"{format_number(ch.avg_views):<11} | "
                f"{format_number(ch.avg_likes):<11} | "
                f"{r['outliers_count']:<8}"
            )
        print("-" * 90)

    if failed_channels:
        print("\n❌ Failed Channels:")
        for ch_input, err in failed_channels:
            print(f"  • {ch_input}: {err}")

    if all_outliers:
        print(f"\n🏆 Top {min(15, len(all_outliers))} Outlier Videos Across All Processed Channels:")
        print(f"{'TITLE':<36} | {'TYPE':<6} | {'VIEWS':<8} | {'MULT':<7} | {'SCORE':<7} | {'URL'}")
        print("-" * 90)
        for v in all_outliers[:15]:
            title_truncated = (v.title[:33] + "...") if len(v.title) > 36 else v.title
            type_str = "Short" if v.is_short else "Video"
            view_str = format_number(v.view_count)
            mult_str = f"{v.view_score:.2f}x"
            score_str = f"{v.score:.2f}"
            print(f"{title_truncated:<36} | {type_str:<6} | {view_str:<8} | {mult_str:<7} | {score_str:<7} | {v.url}")

    print("═" * 90 + "\n")


# ── CLI Interface ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape YouTube channel videos from the past 12 months and compute outlier performance scores in PostgreSQL (supports single channel or bulk processing from a file).",
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="Channel ID, @handle, custom URL, YouTube channel URL, or path to a text file containing channels.",
    )
    parser.add_argument(
        "--channels",
        "-c",
        "--file",
        "-f",
        dest="channels_file",
        help="Path to a text file containing a list of YouTube channels (one per line).",
    )
    parser.add_argument(
        "--months",
        "-m",
        type=int,
        default=12,
        help="Number of months back to scrape (default: 12).",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=1.0,
        help="Outlier performance threshold multiplier (default: 1.0 = outperforms average).",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        help="Optional YouTube API key to override environment variable.",
    )
    parser.add_argument(
        "--token-file",
        help="Optional path to OAuth token.json file.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately if any channel fails during bulk scraping (default: continue to next channel).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Suppress all text logs and output a structured JSON summary.",
    )

    args = parser.parse_args()

    start_time = time.time()
    issue_handler = IssueCaptureHandler(level=logging.WARNING)

    # If in JSON mode, suppress all text logging to stdout/stderr and capture warnings/errors
    if args.json:
        root_logger = logging.getLogger()
        root_logger.handlers = [issue_handler]
        root_logger.setLevel(logging.WARNING)
        logging.getLogger("googleapiclient").setLevel(logging.ERROR)
        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
        logging.getLogger("urllib3").setLevel(logging.ERROR)
        logging.getLogger("phantom").setLevel(logging.WARNING)
        logger.setLevel(logging.WARNING)

    # Determine channel(s) or file to process
    is_bulk = False
    channels_to_scrape: list[str] = []

    if args.channels_file:
        is_bulk = True
        try:
            channels_to_scrape = read_channels_from_file(args.channels_file)
        except Exception as e:
            if args.json:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "status": "error",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "error": f"Error reading channels file: {e}",
                            "issues": issue_handler.issues,
                        },
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            else:
                print(f"Error reading channels file: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.channel and Path(args.channel).is_file():
        is_bulk = True
        try:
            channels_to_scrape = read_channels_from_file(args.channel)
        except Exception as e:
            if args.json:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "status": "error",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "error": f"Error reading channels file: {e}",
                            "issues": issue_handler.issues,
                        },
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            else:
                print(f"Error reading channels file: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.channel:
        channels_to_scrape = [args.channel]

    if not channels_to_scrape:
        if args.json:
            print(
                json.dumps(
                    {
                        "success": False,
                        "status": "error",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "error": "No YouTube channel or channel list file specified.",
                        "usage": "Single Channel: phantom ideas scrape <channel> [--json] | Bulk File: phantom ideas scrape ideas/channels.txt [--json]",
                        "issues": issue_handler.issues,
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
        else:
            print("Error: No YouTube channel or channel list file specified.", file=sys.stderr)
            print("Usage:", file=sys.stderr)
            print("  Single Channel: python ideas/scrape_videos.py <channel_handle_or_id>", file=sys.stderr)
            print("  Bulk File:      python ideas/scrape_videos.py ideas/tech_channels.txt", file=sys.stderr)
            print("                  python ideas/scrape_videos.py --channels ideas/tech_channels.txt", file=sys.stderr)
        sys.exit(1)

    try:
        if is_bulk or len(channels_to_scrape) > 1:
            channel_reports, failed_channels, all_outliers = scrape_channels_bulk(
                channel_inputs=channels_to_scrape,
                months=args.months,
                outlier_threshold=args.threshold,
                api_key=args.api_key,
                token_path=args.token_file,
                stop_on_error=args.stop_on_error,
                quiet=args.json,
            )
            if not args.json:
                results = [
                    {
                        "channel_record": ChannelRecord(
                            channel_id=r["channel_id"],
                            title=r["title"],
                            custom_url=r["custom_url"],
                            subscriber_count=r["subscriber_count"],
                            video_count=r["video_count"],
                            total_view_count=r["total_view_count"],
                            avg_views=r["avg_views"],
                            avg_likes=r["avg_likes"],
                        ),
                        "video_count": r["stats"]["videos_scraped"],
                        "shorts_count": r["stats"]["shorts_count"],
                        "long_count": r["stats"]["long_form_count"],
                        "outliers_count": r["stats"]["outliers_count"],
                    }
                    for r in channel_reports
                ]
                print_bulk_summary(
                    results=results,
                    failed_channels=failed_channels,
                    all_outliers=all_outliers,
                    outlier_threshold=args.threshold,
                )
        else:
            ch_input = channels_to_scrape[0]
            channel_record, video_records, channel_report = scrape_and_save_channel_videos(
                channel_input=ch_input,
                months=args.months,
                outlier_threshold=args.threshold,
                api_key=args.api_key,
                token_path=args.token_file,
                quiet=args.json,
            )
            channel_reports = [channel_report]
            failed_channels = []

            if not args.json:
                print_scrape_summary(channel_record, video_records, outlier_threshold=args.threshold)

        if args.json:
            duration_sec = round(time.time() - start_time, 2)
            total_channels = len(channels_to_scrape)
            succeeded_count = len(channel_reports)
            failed_count = len(failed_channels)

            total_videos_scraped = sum(c["stats"]["videos_scraped"] for c in channel_reports)
            total_new_videos_count = sum(c["stats"]["new_videos_added"] for c in channel_reports)
            total_updated_videos_count = sum(c["stats"]["existing_videos_updated"] for c in channel_reports)
            total_shorts_count = sum(c["stats"]["shorts_count"] for c in channel_reports)
            total_long_count = sum(c["stats"]["long_form_count"] for c in channel_reports)
            total_outliers_count = sum(c["stats"]["outliers_count"] for c in channel_reports)

            status = "success" if failed_count == 0 else "partial_success" if succeeded_count > 0 else "error"
            success = succeeded_count > 0

            summary_str = (
                f"Scraped {succeeded_count}/{total_channels} channel(s): {total_videos_scraped} videos processed "
                f"({total_new_videos_count} new videos added, {total_updated_videos_count} updated), "
                f"{total_outliers_count} outliers found in {duration_sec}s."
            )

            payload = {
                "success": success,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": duration_sec,
                "summary": summary_str,
                "parameters": {
                    "months": args.months,
                    "threshold": args.threshold,
                    "channels_requested": channels_to_scrape,
                },
                "stats": {
                    "total_channels_requested": total_channels,
                    "channels_succeeded": succeeded_count,
                    "channels_failed": failed_count,
                    "total_videos_scraped": total_videos_scraped,
                    "new_videos_added": total_new_videos_count,
                    "existing_videos_updated": total_updated_videos_count,
                    "shorts_count": total_shorts_count,
                    "long_form_count": total_long_count,
                    "outliers_count": total_outliers_count,
                },
                "channels": channel_reports,
                "failed_channels": [
                    {"channel_input": ch, "error": err}
                    for ch, err in failed_channels
                ],
                "issues": list(issue_handler.issues),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    except Exception as e:
        if args.json:
            print(
                json.dumps(
                    {
                        "success": False,
                        "status": "error",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "error": str(e),
                        "issues": issue_handler.issues if "issue_handler" in locals() else [],
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
        else:
            logger.exception("Scraping execution failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
