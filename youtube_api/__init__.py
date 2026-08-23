"""
YouTube Data API Package
=========================
A robust Python package for interfacing with the YouTube Data API v3,
enabling video fetching, outlier reporting, and smart video recommendations.
"""

from youtube_api.models import Video, VideoSeed, RankedVideo, VideoMetadata
from metadata.read_metadata import read_metadata, load_metadata, find_metadata_file

__all__ = [
    "Video",
    "VideoSeed",
    "RankedVideo",
    "VideoMetadata",
    "read_metadata",
    "load_metadata",
    "find_metadata_file",
    "fetch_channel_videos",
    "fetch_video_details",
    "get_uploads_playlist_id",
    "get_youtube_client",
    "resolve_channel_id",
    "supports_color",
]


def __getattr__(name: str):
    if name in ("fetch_channel_videos", "fetch_video_details", "get_uploads_playlist_id"):
        import youtube_api.fetch_videos as _fv
        return getattr(_fv, name)
    if name in ("get_youtube_client", "resolve_channel_id", "supports_color"):
        import youtube_api.utils as _u
        return getattr(_u, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


