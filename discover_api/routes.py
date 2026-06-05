"""
YouTube API Endpoints Router
=============================
Defines FastAPI routes for fetching channel video data, calculating outliers,
retrieving popular videos, and generating similarity-based recommendations.
"""

import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field

# Relative imports from the package
from .youtube.fetch_videos import fetch_channel_videos, video_to_dict
from .youtube.fetch_outliers import calculate_outliers
from .youtube.fetch_popular_videos import get_popular_videos
from .youtube.recommend_related_videos import get_related_recommendations
from .youtube.utils import get_youtube_client, resolve_channel_id
from .youtube.search_creators import search_youtube_creators

logger = logging.getLogger("discover_api.routes")

router = APIRouter(prefix="/api/youtube", tags=["YouTube"])


# ── Request / Response Models ──────────────────────────────────────────────────

class RecommendRequest(BaseModel):
    video_id: Optional[str] = Field(
        None, 
        description="Search recommendations using an existing video ID as seed.",
        example="dQw4w9WgXcQ"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None, 
        description="Search recommendations for a brand new video upload using its metadata.",
        example={
            "title": "Unboxing the new premium editor!",
            "description": "Today we look at advanced workspace tools...",
            "tags": ["unboxing", "editor", "productivity"],
            "categoryId": "28"
        }
    )
    limit: int = Field(
        5, 
        ge=1, 
        le=50, 
        description="Maximum number of recommendations to return.",
        example=5
    )


# ── Routes Definitions ─────────────────────────────────────────────────────────

@router.get("/fetch-videos")
async def get_videos(
    channel: Optional[str] = Query(
        None, 
        description="YouTube channel ID, handle (starts with @), or name. If empty, falls back to default."
    ),
    fresh: bool = Query(
        False, 
        description="Bypass cached data and force a fresh fetch from the YouTube API."
    )
):
    """
    Fetch all videos from a YouTube channel.
    Utilizes localized caching to speed up subsequent requests.
    """
    try:
        import os
        api_key = os.getenv("YOUTUBE_API_KEY")
        target_channel = channel or os.getenv("YOUTUBE_CHANNEL_ID")
        
        if not api_key:
            raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY environment variable is not set.")
        if not target_channel:
            raise HTTPException(status_code=400, detail="No YouTube channel provided and YOUTUBE_CHANNEL_ID is not configured.")

        # Authenticate and resolve channel ID
        youtube = get_youtube_client(api_key)
        resolved_id = resolve_channel_id(youtube, target_channel)

        # Retrieve videos
        videos = fetch_channel_videos(api_key, resolved_id, fresh=fresh)
        return {
            "channel_id": resolved_id,
            "count": len(videos),
            "videos": [video_to_dict(v) for v in videos]
        }
    except ValueError as e:
        logger.error(f"Validation error in fetch-videos: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in fetch-videos: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fetch-outliers")
async def get_outliers(
    channel: str = Query(
        ..., 
        description="YouTube channel ID, handle (starts with @), or name."
    ),
    days: Optional[float] = Query(
        None, 
        description="Optional: number of days to apply a 10% recency boost multiplier to scores."
    ),
    limit: Optional[int] = Query(
        None, 
        ge=1, 
        description="Optional: limit the number of outliers returned."
    )
):
    """
    Identify outlier videos on a YouTube channel.
    Calculates view and like ratio averages to determine which videos overperformed.
    """
    try:
        if days is not None and days <= 0:
            raise HTTPException(status_code=400, detail="The days boost parameter must be greater than zero.")
        if limit is not None and limit <= 0:
            raise HTTPException(status_code=400, detail="The limit parameter must be a positive integer.")

        outliers_report = calculate_outliers(channel_input=channel, days=days, limit=limit)
        return outliers_report
    except ValueError as e:
        logger.error(f"Validation error in fetch-outliers: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in fetch-outliers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fetch-popular_videos")
async def get_popular(
    channel: str = Query(
        ..., 
        description="YouTube channel ID, handle (starts with @), or name."
    ),
    period: str = Query(
        "monthly", 
        description="Timeframe cutoff filter: weekly, monthly, 3months, 6months, all."
    ),
    sort: str = Query(
        "views", 
        description="Metric sort target: views or likes."
    ),
    limit: int = Query(
        10, 
        ge=1, 
        le=100, 
        description="Maximum number of top performing videos to return."
    )
):
    """
    Get the top performing popular videos of a channel in a specific timeframe.
    """
    valid_periods = {"weekly", "week", "monthly", "month", "3months", "3m", "6months", "6m", "all"}
    if period.lower() not in valid_periods:
        raise HTTPException(status_code=400, detail=f"Invalid period: {period!r}. Must be one of {sorted(list(valid_periods))}")
    
    if sort.lower() not in {"views", "likes"}:
        raise HTTPException(status_code=400, detail=f"Invalid sort metric: {sort!r}. Must be 'views' or 'likes'")

    try:
        popular_list = get_popular_videos(
            channel_input=channel,
            period=period,
            sort=sort,
            limit=limit
        )
        return {
            "channel": channel,
            "period": period,
            "sort_by": sort,
            "count": len(popular_list),
            "videos": popular_list
        }
    except ValueError as e:
        logger.error(f"Validation error in fetch-popular_videos: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in fetch-popular_videos: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommend-related-videos")
async def get_recommendations(
    request: RecommendRequest = Body(...)
):
    """
    Recommend related videos from the channel inventory.
    Accepts either an existing `video_id` as the similarity seed,
    or a Pydantic `metadata` object representing a new video upload's snippet.
    """
    if not request.video_id and not request.metadata:
        raise HTTPException(
            status_code=400, 
            detail="Must provide either 'video_id' or 'metadata' in the request body."
        )
    if request.video_id and request.metadata:
        raise HTTPException(
            status_code=400,
            detail="Cannot provide both 'video_id' and 'metadata'. Please choose one as the similarity seed."
        )

    try:
        recommendations = get_related_recommendations(
            video_id=request.video_id,
            metadata=request.metadata,
            limit=request.limit
        )
        return {
            "seed_source": "video_id" if request.video_id else "metadata",
            "limit": request.limit,
            "count": len(recommendations),
            "recommendations": recommendations
        }
    except ValueError as e:
        logger.error(f"Validation error in recommend-related-videos: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except EnvironmentError as e:
        logger.error(f"Environment configuration issue in recommend-related-videos: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in recommend-related-videos: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search-creators")
async def search_creators(
    q: str = Query(..., min_length=1, description="YouTube creator name or search query")
):
    """
    Search YouTube for creators with the given query string.
    Returns: channel ID, profile pic (thumbnail_url), name, description, etc.
    """
    query = q.strip()
    if not query:
        return {
            "query": query,
            "count": 0,
            "results": []
        }

    try:
        import os
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY environment variable is not set.")

        results = search_youtube_creators(api_key=api_key, query=query, limit=10)
        return {
            "query": query,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Unexpected error in search-creators: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while searching for creators.")

