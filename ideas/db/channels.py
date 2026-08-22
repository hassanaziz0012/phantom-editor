"""
Channel Database Operations
===========================
Responsible only for inserting, updating, and fetching channel records.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from .connection import get_db_connection
from .models import ChannelRecord

logger = logging.getLogger("phantom.db.channels")


def upsert_channel(channel: dict[str, Any] | ChannelRecord) -> None:
    """
    Inserts or updates a YouTube channel record in PostgreSQL.
    """
    data = asdict(channel) if isinstance(channel, ChannelRecord) else dict(channel)

    sql = """
    INSERT INTO channels (
        channel_id, title, description, custom_url, published_at,
        subscriber_count, video_count, total_view_count,
        avg_views, avg_likes, thumbnail_url, country, last_scraped_at
    ) VALUES (
        %(channel_id)s, %(title)s, %(description)s, %(custom_url)s, %(published_at)s,
        %(subscriber_count)s, %(video_count)s, %(total_view_count)s,
        %(avg_views)s, %(avg_likes)s, %(thumbnail_url)s, %(country)s, %(last_scraped_at)s
    )
    ON CONFLICT (channel_id) DO UPDATE SET
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        custom_url = COALESCE(EXCLUDED.custom_url, channels.custom_url),
        published_at = COALESCE(EXCLUDED.published_at, channels.published_at),
        subscriber_count = EXCLUDED.subscriber_count,
        video_count = EXCLUDED.video_count,
        total_view_count = EXCLUDED.total_view_count,
        avg_views = CASE WHEN EXCLUDED.avg_views > 0 THEN EXCLUDED.avg_views ELSE channels.avg_views END,
        avg_likes = CASE WHEN EXCLUDED.avg_likes > 0 THEN EXCLUDED.avg_likes ELSE channels.avg_likes END,
        thumbnail_url = COALESCE(EXCLUDED.thumbnail_url, channels.thumbnail_url),
        country = COALESCE(EXCLUDED.country, channels.country),
        last_scraped_at = COALESCE(EXCLUDED.last_scraped_at, CURRENT_TIMESTAMP),
        updated_at = CURRENT_TIMESTAMP;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "channel_id": data["channel_id"],
                    "title": data.get("title", ""),
                    "description": data.get("description", ""),
                    "custom_url": data.get("custom_url"),
                    "published_at": data.get("published_at"),
                    "subscriber_count": data.get("subscriber_count", 0),
                    "video_count": data.get("video_count", 0),
                    "total_view_count": data.get("total_view_count", 0),
                    "avg_views": data.get("avg_views", 0.0),
                    "avg_likes": data.get("avg_likes", 0.0),
                    "thumbnail_url": data.get("thumbnail_url"),
                    "country": data.get("country"),
                    "last_scraped_at": data.get("last_scraped_at", datetime.now(timezone.utc)),
                },
            )
    logger.debug("Upserted channel: %s", data.get("channel_id"))


def get_channel(channel_id: str) -> Optional[dict[str, Any]]:
    """Retrieves a single channel by its YouTube Channel ID."""
    sql = "SELECT * FROM channels WHERE channel_id = %s;"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (channel_id,))
            return cur.fetchone()


def get_all_channels() -> list[dict[str, Any]]:
    """Fetches all stored channels and their summary metrics."""
    sql = """
    SELECT
        c.channel_id,
        c.title,
        c.custom_url,
        c.subscriber_count,
        c.video_count,
        c.total_view_count,
        c.avg_views,
        c.avg_likes,
        c.country,
        c.last_scraped_at,
        COUNT(v.video_id) AS stored_videos_count,
        COUNT(CASE WHEN v.is_outlier THEN 1 END) AS outlier_videos_count
    FROM channels c
    LEFT JOIN videos v ON c.channel_id = v.channel_id
    GROUP BY c.channel_id
    ORDER BY c.subscriber_count DESC, c.total_view_count DESC;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
