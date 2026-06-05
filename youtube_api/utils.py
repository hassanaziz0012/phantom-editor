"""
YouTube API Utility Functions
==============================
Contains shared helper functions for credential verification, channel resolution,
terminal color validation, text tokenization/similarity, and algorithm scoring.
"""

import math
import os
import re
import sys
from datetime import UTC, datetime
from typing import Optional

from dotenv import load_dotenv
from googleapiclient.discovery import build

# Text processing configuration
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "how", "i", "if", "in", "into", "is", "it", "my", "of", "on", "or",
    "our", "out", "so", "that", "the", "this", "to", "up", "video", "vs",
    "we", "with", "you", "your",
}


# ── Credential & Client Initialization ──────────────────────────────────────────

def get_youtube_client(api_key: Optional[str] = None):
    """
    Authenticate and return an active Google API YouTube Client resource.
    Automatically reads YOUTUBE_API_KEY from environment variables if not provided.
    """
    load_dotenv()
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
        except Exception:
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
        except Exception:
            pass

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


# ── Terminal Display Utilities ──────────────────────────────────────────────────

def supports_color() -> bool:
    """Check if the current executing terminal supports ANSI escape colors."""
    if not sys.stdout.isatty():
        return False
    if os.name == 'nt':
        return 'ANSICON' in os.environ or os.environ.get('TERM') == 'xterm'
    return True


# ── Text Tokenization & Jaccard Overlaps ────────────────────────────────────────

def tokenize(text: str) -> set[str]:
    """Tokenize a string into a set of meaningful, lowercased alphanumeric words."""
    return {
        token
        for token in TOKEN_RE.findall(text.lower())
        if token not in STOP_WORDS and len(token) > 1
    }


def normalize_tags(tags: list[str]) -> set[str]:
    """Combine and tokenize a list of video tags."""
    combined = " ".join(tags)
    return tokenize(combined)


def overlap_score(left: set[str], right: set[str]) -> float:
    """Calculate the Jaccard similarity coefficient between two token sets."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if intersection == 0:
        return 0.0
    union = len(left | right)
    return intersection / union


# ── Heuristic Heuristic / Similarity Scoring Functions ─────────────────────────

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
