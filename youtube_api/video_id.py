"""
YouTube video-ID parsing, the one place that knows YouTube's URL formats.

Kept free of the Google API client so light callers (the status pipeline, the
metadata model) can import it without pulling in googleapiclient.
"""

import re
from typing import Optional
from urllib.parse import urlparse

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}

_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")
_URL_ID_RE = re.compile(r"(?:[?&]v=|/v/|/video/|embed/|shorts/|live/|youtu\.be/|/e/)([a-zA-Z0-9_-]{11})(?![a-zA-Z0-9_-])")


def extract_video_id(input_str: str) -> str:
    """
    Extract the 11-character YouTube video ID from a raw ID or any common URL format
    (watch?v=, youtu.be/, shorts/, embed/, v/, live/, studio.youtube.com/video/).
    """
    input_str = input_str.strip()

    if _ID_RE.fullmatch(input_str):
        return input_str

    match = _URL_ID_RE.search(input_str)
    if match:
        return match.group(1)

    raise ValueError(f"Could not extract a valid YouTube video ID from: '{input_str}'")


def video_id_or_none(input_str: Optional[str]) -> Optional[str]:
    """extract_video_id, but None for empty or unparseable input."""
    if not input_str or not isinstance(input_str, str):
        return None
    try:
        return extract_video_id(input_str)
    except ValueError:
        return None


def is_valid_yt_url(url: str) -> bool:
    """True if ``url`` is an http(s) YouTube video URL with a well-formed video ID (no HTTP requests)."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        return False
    return video_id_or_none(url) is not None
