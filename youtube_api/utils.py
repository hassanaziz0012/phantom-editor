"""
YouTube API Utility Functions
==============================
Contains shared helper functions for credential verification, channel resolution,
terminal color validation, text tokenization/similarity, and algorithm scoring.
"""

import http.client
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from youtube_api.video_id import extract_video_id  # noqa: F401  (re-exported for existing callers)


# ── Credential & Client Initialization ──────────────────────────────────────────

def get_youtube_client(api_key: Optional[str] = None):
    """
    Authenticate and return an active Google API YouTube Client resource.
    Automatically reads YOUTUBE_API_KEY from environment variables if not provided.
    """
    key = api_key or os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise EnvironmentError(
            "YOUTUBE_API_KEY environment variable or --api-key argument must be set."
        )
    return build("youtube", "v3", developerKey=key)


# ── Channel ID Resolution ───────────────────────────────────────────────────────

def resolve_channel_id(youtube, channel_input: str) -> str:
    """
    Intelligently resolve a YouTube channel ID from various input formats:
    - Direct Channel ID (UC...)
    - Channel URL (/channel/UC...)
    - Handle URL or raw handle (@handle)
    - Custom/User URL (/c/..., /user/...)
    - Search query fallback
    """
    channel_input = channel_input.strip()

    # 1. Direct Channel ID
    if re.fullmatch(r"UC[\w-]{22}", channel_input):
        return channel_input

    # 2. URL containing /channel/UC...
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
                forHandle=f"@{handle}"
            ).execute()
            items = response.get("items", [])
            if items:
                return items[0]["id"]
        except HttpError:
            pass  # Fallback to search API if forHandle fails or is unsupported

    # 4. User URL containing /user/...
    user_match = re.search(r"/user/([\w.-]+)", channel_input)
    if user_match:
        username = user_match.group(1)
        try:
            response = youtube.channels().list(
                part="id",
                forUsername=username
            ).execute()
            items = response.get("items", [])
            if items:
                return items[0]["id"]
        except HttpError:
            pass  # Fall back to the search API below

    # 5. Robust Fallback: Search API to locate the channel reliably
    # Clean up input string for query if it's a full URL
    query = channel_input.split("/")[-1] if "/" in channel_input else channel_input
    response = youtube.search().list(
        part="snippet",
        q=query,
        type="channel",
        maxResults=1
    ).execute()

    items = response.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]

    raise ValueError(f"Could not resolve YouTube channel ID for input: {channel_input!r}")


# ── Duration Formatting ─────────────────────────────────────────────────────────

def format_duration(seconds: Optional[float], missing: str = "?") -> str:
    """Clock-style duration: M:SS, or H:MM:SS from one hour up; ``missing`` for None."""
    if seconds is None:
        return missing
    hours, rem = divmod(round(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def format_duration_ms(milliseconds: Optional[int], missing: str = "?") -> str:
    """format_duration for a duration in milliseconds (as YouTube Analytics reports them)."""
    return format_duration(milliseconds / 1000 if milliseconds is not None else None, missing)


# ── Terminal Display Utilities ──────────────────────────────────────────────────

def supports_color() -> bool:
    """Check if the current executing terminal supports ANSI escape colors."""
    if not sys.stdout.isatty():
        return False
    if os.name == 'nt':
        return 'ANSICON' in os.environ or os.environ.get('TERM') == 'xterm'
    return True


# ── Heuristic / Similarity Scoring Functions ─────────────────────────

def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """Parse an ISO 8601 duration string (e.g. PT4M13S) to total seconds."""
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


# YouTube Shorts can be up to 3 minutes long
SHORTS_MAX_SECONDS = 180
# video ID -> whether it's a Short, as YouTube answered. A video never changes format, so it's kept for good.
SHORTS_CACHE_FILE = Path(__file__).resolve().parent / "cache" / "shorts.json"
_shorts_cache: Optional[dict[str, bool]] = None


def is_short(duration_seconds: Optional[int], video_id: Optional[str] = None) -> bool:
    """
    Whether a video is a Short. Nothing over 3 minutes is. For a shorter video with a
    ``video_id`` YouTube is asked (see ask_youtube_is_short), since a short regular upload
    isn't a Short; without one, or when YouTube can't be reached, the length decides.
    """
    if duration_seconds is None or duration_seconds > SHORTS_MAX_SECONDS:
        return False
    if not video_id:
        return True

    global _shorts_cache
    if _shorts_cache is None:
        try:
            _shorts_cache = json.loads(SHORTS_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _shorts_cache = {}
    if video_id not in _shorts_cache:
        answer = ask_youtube_is_short(video_id)
        if answer is None:
            return True
        _shorts_cache[video_id] = answer
        # Cache failures shouldn't break the command; the next run just asks again.
        try:
            SHORTS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = SHORTS_CACHE_FILE.with_suffix(".json.tmp")
            tmp_file.write_text(json.dumps(_shorts_cache), encoding="utf-8")
            os.replace(tmp_file, SHORTS_CACHE_FILE)
        except OSError:
            pass
    return _shorts_cache[video_id]


def ask_youtube_is_short(video_id: str) -> Optional[bool]:
    """
    youtube.com/shorts/<id> serves a Short and redirects any other video to /watch, so a HEAD
    request tells them apart (the API has no Shorts flag). None if YouTube couldn't be reached.
    """
    conn = http.client.HTTPSConnection("www.youtube.com", timeout=5)
    try:
        conn.request("HEAD", f"/shorts/{video_id}")
        status = conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    if status == 200:
        return True
    if 300 <= status < 400:
        return False
    return None


def duration_similarity(left_seconds: Optional[int], right_seconds: Optional[int]) -> float:
    """Compute the ratio between the shorter and longer duration (0.0 to 1.0)."""
    if not left_seconds or not right_seconds:
        return 0.0

    ratio = min(left_seconds, right_seconds) / max(left_seconds, right_seconds)
    return max(0.0, min(1.0, ratio))


def recency_score(published_at: datetime) -> float:
    """
    Calculate a recency score boost between 0.0 and 1.0.
    Favors recent videos using a logarithmic decay curve.
    """
    now = datetime.now(UTC)
    age_days = max(0.0, (now - published_at.astimezone(UTC)).days)
    return 1.0 / (1.0 + math.log10(age_days + 10.0))


def popularity_score(view_count: Optional[int]) -> float:
    """
    Calculate a popularity score boost between 0.0 and 1.0.
    Uses a logarithmic scale that approaches 1.0 at 10 million views.
    """
    if not view_count or view_count <= 0:
        return 0.0
    return min(1.0, math.log10(view_count + 1) / 7.0)
