"""
Recommend related videos from the current YouTube channel inventory.
Calculates Jaccard similarities and decay boosts to find similar content.
"""

import os
import logging
from typing import Any, Optional

from .models import Video, VideoSeed, RankedVideo
from .fetch_videos import fetch_channel_videos
from .utils import (
    tokenize,
    normalize_tags,
    overlap_score,
    parse_iso8601_duration,
    duration_similarity,
    recency_score,
    popularity_score,
)

logger = logging.getLogger("discover_api.youtube.recommend_related_videos")


def build_seed_from_metadata(metadata: dict[str, Any]) -> VideoSeed:
    """Build a VideoSeed from metadata dictionary."""
    return VideoSeed(
        title=metadata.get("title", ""),
        description=metadata.get("description", ""),
        tags=list(metadata.get("tags", [])),
        category_id=str(metadata["categoryId"]) if metadata.get("categoryId") is not None else None,
    )


def build_seed_from_existing_video(video: Video) -> VideoSeed:
    """Build a VideoSeed from an existing Video object."""
    return VideoSeed(
        title=video.title,
        description=video.description,
        tags=video.tags,
        category_id=video.category_id,
        duration_seconds=parse_iso8601_duration(video.duration),
        video_id=video.video_id,
    )


def score_video(seed: VideoSeed, candidate: Video) -> RankedVideo:
    """Calculate and weigh overlap/boosting heuristics between seed and candidate."""
    title_tokens = tokenize(seed.title)
    description_tokens = tokenize(seed.description)
    tag_tokens = normalize_tags(seed.tags)

    candidate_title_tokens = tokenize(candidate.title)
    candidate_description_tokens = tokenize(candidate.description)
    candidate_tag_tokens = normalize_tags(candidate.tags)
    candidate_duration_seconds = parse_iso8601_duration(candidate.duration)

    reasons = {
        "title": overlap_score(title_tokens, candidate_title_tokens) * 0.38,
        "tags": overlap_score(tag_tokens, candidate_tag_tokens) * 0.28,
        "description": overlap_score(description_tokens, candidate_description_tokens) * 0.18,
        "category": 0.08 if seed.category_id and seed.category_id == candidate.category_id else 0.0,
        "duration": duration_similarity(seed.duration_seconds, candidate_duration_seconds) * 0.05,
        "recency": recency_score(candidate.published_at) * 0.02,
        "popularity": popularity_score(candidate.view_count) * 0.01,
    }

    if seed.video_id and seed.video_id == candidate.video_id:
        reasons["self_match_penalty"] = -1.0

    return RankedVideo(
        video=candidate,
        score=sum(reasons.values()),
        reasons=reasons,
    )


def rank_related_videos(seed: VideoSeed, videos: list[Video], limit: int) -> list[RankedVideo]:
    """Score, filter out negative scores, and sort candidate videos."""
    ranked = [score_video(seed, video) for video in videos if video.live_broadcast != "upcoming"]
    ranked = [item for item in ranked if item.score > 0]
    ranked.sort(key=lambda item: (item.score, item.video.view_count or 0), reverse=True)
    return ranked[:limit]


def find_video_by_id(videos: list[Video], video_id: str) -> Video:
    """Locate video inside the channel inventory."""
    for video in videos:
        if video.video_id == video_id:
            return video
    raise ValueError(f"Video ID not found in channel inventory: {video_id}")


def get_related_recommendations(
    video_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    limit: int = 5,
    api_key: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Main business logic for related recommendations.
    Accepts either an existing `video_id` or a new video's `metadata` dictionary.
    Computes weighted similarity scores against all channel inventory,
    and returns a sorted ranking list.
    """
    target_api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    target_channel_id = channel_id or os.getenv("YOUTUBE_CHANNEL_ID")

    if not target_api_key or not target_channel_id:
        raise EnvironmentError("Both YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID must be set in env or arguments.")

    # 1. Fetch channel videos (caching enabled)
    videos = fetch_channel_videos(target_api_key, target_channel_id)

    # 2. Build video seed
    if metadata:
        seed = build_seed_from_metadata(metadata)
    elif video_id:
        seed_video = find_video_by_id(videos, video_id)
        seed = build_seed_from_existing_video(seed_video)
    else:
        raise ValueError("Must provide either 'video_id' or 'metadata' to generate recommendations.")

    # 3. Compute rankings
    ranked = rank_related_videos(seed, videos, limit=limit)

    # 4. Serialize to response payload format
    payload = [
        {
            "video_id": item.video.video_id,
            "title": item.video.title,
            "url": item.video.url,
            "score": round(item.score, 4),
            "published_at": item.video.published_at.isoformat(),
            "thumbnail_url": item.video.thumbnail_url,
            "view_count": item.video.view_count,
            "like_count": item.video.like_count,
            "reasons": {key: round(value, 4) for key, value in item.reasons.items() if value > 0},
        }
        for item in ranked
    ]
    return payload
