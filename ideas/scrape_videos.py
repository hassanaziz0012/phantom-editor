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

from ideas.db.channels import get_channel, upsert_channel
from ideas.db.models import ChannelRecord, VideoRecord
from ideas.db.queries import get_outlier_videos
from ideas.db.schema import init_db
from ideas.db.scoring import compute_video_score_components, update_video_scores
from ideas.db.videos import upsert_videos_batch

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.scraper")

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


# ── Core Scraper Pipeline ──────────────────────────────────────────────────────

def scrape_and_save_channel_videos(
    channel_input: str,
    months: int = 12,
    outlier_threshold: float = 1.0,
    api_key: Optional[str] = None,
    token_path: Optional[Path | str] = None,
) -> tuple[ChannelRecord, list[VideoRecord]]:
    """
    Full pipeline:
    1. Authenticate with YouTube API
    2. Resolve channel ID and fetch channel metadata
    3. Fetch all videos uploaded in the past 12 months
    4. Fetch video details and stats in batch
    5. Compute channel average views and likes
    6. Calculate video scores:
       - view_score = video views / avg views
       - like_score = video likes / avg likes
       - base_score = 0.75 * view_score + 0.25 * like_score
       - 10% recency boost for videos uploaded within the last 30 days (0% boost otherwise)
       - final_score = base_score * recency_boost
       - is_outlier = (view_score >= outlier_threshold OR score >= outlier_threshold)
    7. Save channel and all videos into PostgreSQL database
    8. Trigger score recalculation to keep database perfectly consistent
    """
    # 1. Initialize DB schema
    init_db()

    # 2. Authenticate
    youtube = get_youtube_service(token_path=token_path, api_key=api_key)

    # 3. Resolve Channel
    channel_id = resolve_channel_id(youtube, channel_input)
    logger.info("Resolved target channel ID: %s", channel_id)

    # 4. Fetch Channel Metadata
    channel_dict, uploads_playlist_id = fetch_channel_details(youtube, channel_id)
    logger.info("Fetched channel metadata: '%s' (%s subscribers)", channel_dict["title"], f"{channel_dict['subscriber_count']:,}")

    # 5. Fetch Video IDs from past 12 months
    video_ids = fetch_recent_video_ids(youtube, uploads_playlist_id, months=months)
    if not video_ids:
        logger.warning("No videos found in the last %d months for channel '%s'.", months, channel_dict["title"])
        channel_record = ChannelRecord(**channel_dict)
        upsert_channel(channel_record)
        return channel_record, []

    # 6. Fetch Full Video Details
    raw_videos = fetch_video_details_batch(youtube, video_ids, channel_id)
    logger.info("Retrieved complete metadata and metrics for %d videos.", len(raw_videos))

    # 7. Calculate Channel Averages (from the 12-month period)
    total_views = sum(v["view_count"] for v in raw_videos)
    total_likes = sum(v["like_count"] for v in raw_videos)
    video_count = len(raw_videos)

    avg_views = round(total_views / video_count, 2) if video_count > 0 else 0.0
    avg_likes = round(total_likes / video_count, 2) if video_count > 0 else 0.0

    channel_dict["avg_views"] = avg_views
    channel_dict["avg_likes"] = avg_likes
    channel_record = ChannelRecord(**channel_dict)

    # 8. Compute Performance & Outlier Scores for Each Video
    video_records: list[VideoRecord] = []
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
        video_records.append(VideoRecord(**video_data))

    # 9. Upsert Channel & Videos into PostgreSQL
    logger.info("Saving channel record to PostgreSQL database...")
    upsert_channel(channel_record)

    logger.info("Batch saving %d video records to PostgreSQL database...", len(video_records))
    upsert_videos_batch(video_records)

    # 10. Update SQL-side scores for database triggers and indexes
    update_video_scores(channel_id=channel_id, outlier_threshold=outlier_threshold)

    logger.info("Successfully scraped, scored, and stored %d videos for '%s'.", len(video_records), channel_record.title)
    return channel_record, video_records


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


# ── CLI Interface ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape YouTube channel videos from the past 12 months and compute outlier performance scores in PostgreSQL.",
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="Channel ID, @handle, custom URL, or YouTube channel URL (falls back to YOUTUBE_CHANNEL_ID in .env).",
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

    args = parser.parse_args()

    channel_input = args.channel or os.getenv("YOUTUBE_CHANNEL_ID")
    if not channel_input:
        print("Error: No YouTube channel specified.", file=sys.stderr)
        print("Usage: python ideas/scrape_videos.py <channel_handle_or_id>", file=sys.stderr)
        sys.exit(1)

    try:
        channel_record, video_records = scrape_and_save_channel_videos(
            channel_input=channel_input,
            months=args.months,
            outlier_threshold=args.threshold,
            api_key=args.api_key,
            token_path=args.token_file,
        )
        print_scrape_summary(channel_record, video_records, outlier_threshold=args.threshold)
    except Exception as e:
        logger.exception("Scraping execution failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
