"""
Data Models for YouTube Channels and Videos
===========================================
Responsible only for defining typed dataclasses representing
channel records and video records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ChannelRecord:
    """Represents a YouTube channel and its aggregate statistics."""

    channel_id: str
    title: str
    description: str = ""
    custom_url: Optional[str] = None
    published_at: Optional[datetime] = None
    subscriber_count: int = 0
    video_count: int = 0
    total_view_count: int = 0
    avg_views: float = 0.0
    avg_likes: float = 0.0
    thumbnail_url: Optional[str] = None
    country: Optional[str] = None
    last_scraped_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class VideoRecord:
    """Represents a YouTube video with performance metrics and outlier scores."""

    video_id: str
    channel_id: str
    title: str
    description: str = ""
    published_at: Optional[datetime] = None
    duration: Optional[str] = None
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    category_id: Optional[str] = None
    url: str = ""
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    is_short: bool = False
    summary: Optional[str] = None
    takeaways: list[str] = field(default_factory=list)

    # Calculated scores
    view_score: float = 0.0  # video views / channel avg views
    like_score: float = 0.0  # video likes / channel avg likes
    base_score: float = 0.0  # weighted view + like performance ratio
    score: float = 0.0  # base score * 10% recency boost
    is_outlier: bool = False  # marked outlier if score >= 1.0

    last_scraped_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.url and self.video_id:
            self.url = f"https://www.youtube.com/watch?v={self.video_id}"
