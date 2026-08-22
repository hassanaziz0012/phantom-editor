"""
Analytics & Outlier Database Queries
====================================
Responsible only for query operations retrieving outlier videos and top rankings.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .connection import get_db_connection

logger = logging.getLogger("phantom.db.queries")


def get_outlier_videos(
    channel_id: Optional[str] = None,
    min_score: float = 2.0,
    min_views: int = 500,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Queries videos marked as outliers or exceeding the outlier score threshold,
    ordered from highest outlier score to lowest.
    """
    conditions = ["(v.is_outlier = TRUE OR v.score >= %s)", "v.view_count >= %s"]
    params: list[Any] = [min_score, min_views]

    if channel_id:
        conditions.append("v.channel_id = %s")
        params.append(channel_id)

    params.append(limit)

    sql = f"""
    SELECT
        v.video_id,
        v.title,
        v.url,
        v.published_at,
        v.duration_seconds,
        v.is_short,
        v.view_count,
        v.like_count,
        v.comment_count,
        v.view_score,
        v.like_score,
        v.base_score,
        v.score,
        v.is_outlier,
        c.channel_id,
        c.title AS channel_title,
        c.avg_views AS channel_avg_views,
        c.avg_likes AS channel_avg_likes
    FROM videos v
    JOIN channels c ON v.channel_id = c.channel_id
    WHERE {" AND ".join(conditions)}
    ORDER BY v.score DESC, v.view_score DESC
    LIMIT %s;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


def get_top_videos(
    channel_id: Optional[str] = None,
    sort_by: str = "score",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetches top videos sorted by score, view_score, view_count, or published_at.
    """
    allowed_sorts = {
        "score": "v.score DESC",
        "view_score": "v.view_score DESC",
        "like_score": "v.like_score DESC",
        "view_count": "v.view_count DESC",
        "views": "v.view_count DESC",
        "published_at": "v.published_at DESC",
        "recent": "v.published_at DESC",
    }
    sort_expr = allowed_sorts.get(sort_by, "v.score DESC")

    conditions = []
    params: list[Any] = []
    if channel_id:
        conditions.append("v.channel_id = %s")
        params.append(channel_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    sql = f"""
    SELECT
        v.video_id,
        v.title,
        v.url,
        v.published_at,
        v.duration_seconds,
        v.is_short,
        v.view_count,
        v.like_count,
        v.view_score,
        v.like_score,
        v.base_score,
        v.score,
        v.is_outlier,
        c.channel_id,
        c.title AS channel_title,
        c.avg_views,
        c.avg_likes
    FROM videos v
    JOIN channels c ON v.channel_id = c.channel_id
    {where_clause}
    ORDER BY {sort_expr}
    LIMIT %s;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()
