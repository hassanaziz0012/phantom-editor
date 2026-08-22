"""
Video Scoring & Channel Statistics Engine
==========================================
Responsible only for computing video scores (view score, like score, base score,
final composite score, outlier classification) and updating channel averages.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .connection import get_db_connection

logger = logging.getLogger("phantom.db.scoring")


def compute_video_score_components(
    view_count: int,
    like_count: int,
    channel_avg_views: float,
    channel_avg_likes: float,
    published_at: Optional[datetime] = None,
    outlier_threshold: float = 1.0,
) -> dict[str, float | bool]:
    """
    Pure calculation function for scoring an individual video:
    - view_score = view_count / avg_views
    - like_score = like_count / avg_likes
    - base_score = 0.75 * view_score + 0.25 * like_score
    - recency_boost = 10% (1.10) if published within last 30 days, else 0% (1.00)
    - score = base_score * recency_boost
    - is_outlier = (view_score >= outlier_threshold OR score >= outlier_threshold)
    """
    # View Score: performance ratio against channel average
    if channel_avg_views > 0:
        view_score = float(view_count) / float(channel_avg_views)
    else:
        view_score = 1.0 if view_count > 0 else 0.0

    # Like Score: performance ratio against channel average
    if channel_avg_likes > 0:
        like_score = float(like_count) / float(channel_avg_likes)
    else:
        like_score = 1.0 if like_count > 0 else 0.0

    # Base Score: Weighted performance ratio (views 75%, likes 25%)
    base_score = (0.75 * view_score) + (0.25 * like_score)

    # Recency Boost: 10% boost for videos uploaded in the last 30 days, 0% boost otherwise
    recency_boost = 1.00
    if published_at is not None:
        try:
            from datetime import timezone
            now = datetime.now(timezone.utc)
            pub_utc = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
            if (now - pub_utc).total_seconds() <= 30 * 86400:
                recency_boost = 1.10
        except Exception:
            pass

    final_score = base_score * recency_boost

    # Outlier criteria: exceeds threshold in view multiplier or composite score
    is_outlier = bool(view_score >= outlier_threshold or final_score >= outlier_threshold)

    return {
        "view_score": round(view_score, 4),
        "like_score": round(like_score, 4),
        "base_score": round(base_score, 4),
        "score": round(final_score, 4),
        "is_outlier": is_outlier,
    }


def calculate_channel_stats(channel_id: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Computes average views and average likes for videos in each channel,
    then updates the channels table with the calculated stats.
    """
    params = (channel_id,) if channel_id else ()

    calc_sql = f"""
    WITH channel_video_aggregates AS (
        SELECT
            channel_id,
            COALESCE(AVG(view_count), 0.0)::FLOAT AS computed_avg_views,
            COALESCE(AVG(like_count), 0.0)::FLOAT AS computed_avg_likes,
            COALESCE(SUM(view_count), 0)::BIGINT AS computed_total_views,
            COUNT(*)::INT AS computed_video_count
        FROM videos
        GROUP BY channel_id
    )
    UPDATE channels c
    SET
        avg_views = COALESCE(a.computed_avg_views, 0.0),
        avg_likes = COALESCE(a.computed_avg_likes, 0.0),
        total_view_count = COALESCE(a.computed_total_views, c.total_view_count),
        video_count = CASE WHEN a.computed_video_count > 0 THEN a.computed_video_count ELSE c.video_count END,
        updated_at = CURRENT_TIMESTAMP
    FROM channel_video_aggregates a
    WHERE c.channel_id = a.channel_id
      {"AND c.channel_id = %s" if channel_id else ""}
    RETURNING c.channel_id, c.title, c.avg_views, c.avg_likes, c.video_count;
    """
    updated_channels: list[dict[str, Any]] = []
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(calc_sql, params)
            updated_channels = cur.fetchall()

    logger.info("Updated channel stats for %d channels.", len(updated_channels))
    return updated_channels


def update_video_scores(
    channel_id: Optional[str] = None,
    outlier_threshold: float = 1.0,
) -> int:
    """
    Recalculates channel stats and updates view_score, like_score, base_score,
    score (with 10% 30-day recency boost), and is_outlier on every video in the database.
    """
    # 1. Ensure channel averages are fresh
    calculate_channel_stats(channel_id)

    # 2. Update scores in Postgres with optimized SQL
    where_channel = "AND v.channel_id = %s" if channel_id else ""
    params = (outlier_threshold, outlier_threshold)
    if channel_id:
        params = (outlier_threshold, outlier_threshold, channel_id)

    sql = f"""
    UPDATE videos v
    SET
        view_score = ROUND((
            CASE WHEN c.avg_views > 0 THEN (v.view_count::FLOAT / c.avg_views)
                 WHEN v.view_count > 0 THEN 1.0
                 ELSE 0.0 END
        )::NUMERIC, 4),
        
        like_score = ROUND((
            CASE WHEN c.avg_likes > 0 THEN (v.like_count::FLOAT / c.avg_likes)
                 WHEN v.like_count > 0 THEN 1.0
                 ELSE 0.0 END
        )::NUMERIC, 4),
        
        base_score = ROUND((
            0.75 * (
                CASE WHEN c.avg_views > 0 THEN (v.view_count::FLOAT / c.avg_views)
                     WHEN v.view_count > 0 THEN 1.0
                     ELSE 0.0 END
            ) +
            0.25 * (
                CASE WHEN c.avg_likes > 0 THEN (v.like_count::FLOAT / c.avg_likes)
                     WHEN v.like_count > 0 THEN 1.0
                     ELSE 0.0 END
            )
        )::NUMERIC, 4),
        
        score = ROUND((
            (
                0.75 * (
                    CASE WHEN c.avg_views > 0 THEN (v.view_count::FLOAT / c.avg_views)
                         WHEN v.view_count > 0 THEN 1.0
                         ELSE 0.0 END
                ) +
                0.25 * (
                    CASE WHEN c.avg_likes > 0 THEN (v.like_count::FLOAT / c.avg_likes)
                         WHEN v.like_count > 0 THEN 1.0
                         ELSE 0.0 END
                )
            ) * (
                CASE WHEN v.published_at >= (CURRENT_TIMESTAMP - INTERVAL '30 days') THEN 1.10
                     ELSE 1.00 END
            )
        )::NUMERIC, 4),
        
        is_outlier = (
            (CASE WHEN c.avg_views > 0 THEN (v.view_count::FLOAT / c.avg_views) ELSE 0.0 END) >= %s
            OR
            (
                (
                    0.75 * (
                        CASE WHEN c.avg_views > 0 THEN (v.view_count::FLOAT / c.avg_views)
                             WHEN v.view_count > 0 THEN 1.0
                             ELSE 0.0 END
                    ) +
                    0.25 * (
                        CASE WHEN c.avg_likes > 0 THEN (v.like_count::FLOAT / c.avg_likes)
                             WHEN v.like_count > 0 THEN 1.0
                             ELSE 0.0 END
                    )
                ) * (
                    CASE WHEN v.published_at >= (CURRENT_TIMESTAMP - INTERVAL '30 days') THEN 1.10
                         ELSE 1.00 END
                )
            ) >= %s
        ),
        updated_at = CURRENT_TIMESTAMP
    FROM channels c
    WHERE v.channel_id = c.channel_id
      {where_channel};
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            count = cur.rowcount

    logger.info("Updated performance scores for %d videos.", count)
    return count
