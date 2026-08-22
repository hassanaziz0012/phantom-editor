"""
Video Database Operations
=========================
Responsible only for inserting, batch upserting, and fetching video records.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .connection import get_db_connection
from .models import VideoRecord

logger = logging.getLogger("phantom.db.videos")


def upsert_video(video: dict[str, Any] | VideoRecord) -> None:
    """
    Inserts or updates a single YouTube video record in PostgreSQL.
    """
    upsert_videos_batch([video])


def upsert_videos_batch(videos: Iterable[dict[str, Any] | VideoRecord]) -> int:
    """
    Batch inserts or updates multiple YouTube videos for maximum performance.
    Returns the number of videos processed.
    """
    video_list = [asdict(v) if isinstance(v, VideoRecord) else dict(v) for v in videos]
    if not video_list:
        return 0

    sql = """
    INSERT INTO videos (
        video_id, channel_id, title, description, published_at,
        duration, duration_seconds, thumbnail_url, tags, category_id,
        url, view_count, like_count, comment_count, is_short,
        summary, takeaways,
        view_score, like_score, base_score, score, is_outlier,
        last_scraped_at
    ) VALUES (
        %(video_id)s, %(channel_id)s, %(title)s, %(description)s, %(published_at)s,
        %(duration)s, %(duration_seconds)s, %(thumbnail_url)s, %(tags)s, %(category_id)s,
        %(url)s, %(view_count)s, %(like_count)s, %(comment_count)s, %(is_short)s,
        %(summary)s, %(takeaways)s,
        %(view_score)s, %(like_score)s, %(base_score)s, %(score)s, %(is_outlier)s,
        %(last_scraped_at)s
    )
    ON CONFLICT (video_id) DO UPDATE SET
        channel_id = EXCLUDED.channel_id,
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        published_at = COALESCE(EXCLUDED.published_at, videos.published_at),
        duration = COALESCE(EXCLUDED.duration, videos.duration),
        duration_seconds = COALESCE(EXCLUDED.duration_seconds, videos.duration_seconds),
        thumbnail_url = COALESCE(EXCLUDED.thumbnail_url, videos.thumbnail_url),
        tags = CASE WHEN array_length(EXCLUDED.tags, 1) > 0 THEN EXCLUDED.tags ELSE videos.tags END,
        category_id = COALESCE(EXCLUDED.category_id, videos.category_id),
        url = EXCLUDED.url,
        view_count = EXCLUDED.view_count,
        like_count = EXCLUDED.like_count,
        comment_count = EXCLUDED.comment_count,
        is_short = EXCLUDED.is_short,
        summary = COALESCE(EXCLUDED.summary, videos.summary),
        takeaways = CASE WHEN array_length(EXCLUDED.takeaways, 1) > 0 THEN EXCLUDED.takeaways ELSE videos.takeaways END,
        view_score = CASE WHEN EXCLUDED.view_score > 0 THEN EXCLUDED.view_score ELSE videos.view_score END,
        like_score = CASE WHEN EXCLUDED.like_score > 0 THEN EXCLUDED.like_score ELSE videos.like_score END,
        base_score = CASE WHEN EXCLUDED.base_score > 0 THEN EXCLUDED.base_score ELSE videos.base_score END,
        score = CASE WHEN EXCLUDED.score > 0 THEN EXCLUDED.score ELSE videos.score END,
        is_outlier = EXCLUDED.is_outlier,
        last_scraped_at = COALESCE(EXCLUDED.last_scraped_at, CURRENT_TIMESTAMP),
        updated_at = CURRENT_TIMESTAMP;
    """

    params_list = []
    for item in video_list:
        v_id = item["video_id"]
        v_url = item.get("url") or f"https://www.youtube.com/watch?v={v_id}"
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        takeaways = item.get("takeaways") or []
        if isinstance(takeaways, str):
            takeaways = [t.strip() for t in takeaways.split("\n") if t.strip()]

        params_list.append(
            {
                "video_id": v_id,
                "channel_id": item["channel_id"],
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "published_at": item.get("published_at"),
                "duration": item.get("duration"),
                "duration_seconds": item.get("duration_seconds"),
                "thumbnail_url": item.get("thumbnail_url"),
                "tags": tags,
                "category_id": item.get("category_id"),
                "url": v_url,
                "view_count": item.get("view_count", 0) or 0,
                "like_count": item.get("like_count", 0) or 0,
                "comment_count": item.get("comment_count", 0) or 0,
                "is_short": bool(item.get("is_short", False)),
                "summary": item.get("summary"),
                "takeaways": takeaways,
                "view_score": float(item.get("view_score", 0.0) or 0.0),
                "like_score": float(item.get("like_score", 0.0) or 0.0),
                "base_score": float(item.get("base_score", 0.0) or 0.0),
                "score": float(item.get("score", 0.0) or 0.0),
                "is_outlier": bool(item.get("is_outlier", False)),
                "last_scraped_at": item.get("last_scraped_at", datetime.now(timezone.utc)),
            }
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)

    logger.info("Upserted %d videos successfully.", len(params_list))
    return len(params_list)


def update_video_summary(
    video_id: str,
    summary: str,
    takeaways: list[str],
    channel_id: Optional[str] = None,
    title: Optional[str] = None,
    url: Optional[str] = None,
) -> bool:
    """
    Updates the summary and key takeaways for a video.
    If the video doesn't exist in the database yet, attempts to insert a record
    if channel_id (or a stub channel) is provided.

    Returns:
        True if updated or inserted, False otherwise.
    """
    with get_db_connection(auto_start=True) as conn:
        with conn.cursor() as cur:
            # 1. Try updating existing video
            cur.execute(
                """
                UPDATE videos
                SET summary = %s,
                    takeaways = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE video_id = %s;
                """,
                (summary, takeaways, video_id),
            )
            if cur.rowcount > 0:
                logger.info("Updated summary for video %s.", video_id)
                return True

            # 2. If video doesn't exist yet, ensure channel exists or use a default channel
            c_id = channel_id or "unknown_channel"
            cur.execute(
                """
                INSERT INTO channels (channel_id, title)
                VALUES (%s, %s)
                ON CONFLICT (channel_id) DO NOTHING;
                """,
                (c_id, f"Channel ({c_id})"),
            )

            v_title = title or f"YouTube Video ({video_id})"
            v_url = url or f"https://www.youtube.com/watch?v={video_id}"

            cur.execute(
                """
                INSERT INTO videos (
                    video_id, channel_id, title, url, summary, takeaways,
                    last_scraped_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (video_id) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    takeaways = EXCLUDED.takeaways,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (video_id, c_id, v_title, v_url, summary, takeaways),
            )
            logger.info("Inserted video %s with summary.", video_id)
            return True


def get_video(video_id: str) -> Optional[dict[str, Any]]:
    """Retrieves a single video by ID."""
    sql = "SELECT * FROM videos WHERE video_id = %s;"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (video_id,))
            return cur.fetchone()


def get_channel_videos(channel_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Fetches videos belonging to a specific channel."""
    sql = """
    SELECT * FROM videos
    WHERE channel_id = %s
    ORDER BY published_at DESC NULLS LAST
    LIMIT %s;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (channel_id, limit))
            return cur.fetchall()


def get_unsummarized_videos(limit: Optional[int] = None) -> list[dict[str, Any]]:
    """
    Fetches videos from the database that do not yet have a summary.
    If limit is specified, returns at most that many videos.
    """
    sql = """
    SELECT * FROM videos
    WHERE summary IS NULL OR TRIM(summary) = ''
    ORDER BY published_at DESC NULLS LAST
    """
    params = []
    if limit is not None and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    with get_db_connection(auto_start=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()

