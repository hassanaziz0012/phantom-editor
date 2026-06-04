"""
YouTube Data API Package
=========================
A robust Python package for interfacing with the YouTube Data API v3,
enabling video fetching, outlier reporting, and smart video recommendations.
"""

from youtube_api.models import Video, VideoSeed, RankedVideo
from youtube_api.fetch_videos import (
    fetch_channel_videos,
    fetch_video_details,
    get_uploads_playlist_id,
)
from youtube_api.utils import (
    get_youtube_client,
    resolve_channel_id,
    supports_color,
)

__all__ = [
    "Video",
    "VideoSeed",
    "RankedVideo",
    "fetch_channel_videos",
    "fetch_video_details",
    "get_uploads_playlist_id",
    "get_youtube_client",
    "resolve_channel_id",
    "supports_color",
]
