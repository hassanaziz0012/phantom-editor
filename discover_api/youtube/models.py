"""
YouTube API Data Models
========================
Provides shared data models and dataclasses used across YouTube modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Video:
    """Represents a single YouTube video with its metadata and statistics."""

    video_id:        str
    title:           str
    description:     str
    published_at:    datetime
    thumbnail_url:   str
    channel_id:      str
    channel_title:   str

    # Stats (populated in a second API call)
    view_count:      Optional[int]  = None
    like_count:      Optional[int]  = None
    comment_count:   Optional[int]  = None
    duration:        Optional[str]  = None   # ISO 8601, e.g. "PT4M13S"

    # Extra metadata
    tags:            list[str]      = field(default_factory=list)
    category_id:     Optional[str]  = None
    live_broadcast:  Optional[str]  = None   # "none" | "live" | "upcoming"
    url:             str            = ""

    def __post_init__(self):
        self.url = f"https://www.youtube.com/watch?v={self.video_id}"

    def __repr__(self):
        return (
            f"Video(id={self.video_id!r}, title={self.title!r}, "
            f"published={self.published_at.date()}, views={self.view_count})"
        )


@dataclass
class VideoSeed:
    """Represents a target seed video used for calculating recommendations."""

    title:            str
    description:      str
    tags:             list[str]
    category_id:      Optional[str] = None
    duration_seconds: Optional[int] = None
    video_id:         Optional[str] = None


@dataclass
class RankedVideo:
    """Represents a candidate video and its calculated similarity score details."""

    video:   Video
    score:   float
    reasons: dict[str, float]
