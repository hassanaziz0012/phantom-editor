"""
PostgreSQL Database Package for YouTube Ideas & Outlier Analytics
=================================================================
Provides modular access to Docker management, connections, models,
channel operations, video operations, outlier scoring, and analytical queries.
"""

from __future__ import annotations

# Re-export configuration constants
from .config import (
    POSTGRES_CONTAINER_NAME,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_VOLUME,
)

# Re-export models
from .models import ChannelRecord, VideoRecord

# Re-export connection & docker utilities
from .connection import get_connection_uri, get_db_connection
from .docker import (
    ensure_postgres_container,
    get_container_status,
    is_docker_installed,
    stop_postgres_container,
)

# Re-export schema initialization
from .schema import SCHEMA_SQL, init_db

# Re-export channel operations
from .channels import get_all_channels, get_channel, upsert_channel

# Re-export video operations
from .videos import (
    get_channel_videos,
    get_unsummarized_videos,
    get_video,
    update_video_summary,
    upsert_video,
    upsert_videos_batch,
)

# Re-export scoring & analytics
from .scoring import (
    calculate_channel_stats,
    compute_video_score_components,
    update_video_scores,
)
from .queries import get_outlier_videos, get_top_videos

# Re-export CLI
from .cli import main

__all__ = [
    # Config
    "POSTGRES_CONTAINER_NAME",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_IMAGE",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_VOLUME",
    # Models
    "ChannelRecord",
    "VideoRecord",
    # Connection & Docker
    "get_connection_uri",
    "get_db_connection",
    "ensure_postgres_container",
    "get_container_status",
    "is_docker_installed",
    "stop_postgres_container",
    # Schema
    "SCHEMA_SQL",
    "init_db",
    # Channel operations
    "get_all_channels",
    "get_channel",
    "upsert_channel",
    # Video operations
    "get_channel_videos",
    "get_unsummarized_videos",
    "get_video",
    "update_video_summary",
    "upsert_video",
    "upsert_videos_batch",
    # Scoring & Queries
    "calculate_channel_stats",
    "compute_video_score_components",
    "update_video_scores",
    "get_outlier_videos",
    "get_top_videos",
    # CLI
    "main",
]

if __name__ == "__main__":
    main()
