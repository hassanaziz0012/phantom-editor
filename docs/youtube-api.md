# YouTube API Integration

This directory contains scripts and helper utilities for interacting with the YouTube Data API v3. They enable video uploading, metadata generation, upload counting, and related video recommendations.

## Configuration
Requires `YOUTUBE_API_KEY` and OAuth client/token JSON files in `youtube_api/tokens/` (for video uploading).

## Scripts

### [count_youtube_uploads.py](../youtube_api/count_youtube_uploads.py)
Calculates and displays a channel's upload counts for today, the last 7 days, 14 days, and 30 days in a formatted ASCII table.
* **Usage**: `python youtube_api/count_youtube_uploads.py [<channel_id_or_handle>] [--fresh]` or via `phantom yt count-uploads [<channel>] [--fresh]`

### [create_metadata.py](../youtube_api/create_metadata.py)
Interactively prompts for and creates a `metadata.json` file used when uploading a video.
* **Usage**: `python youtube_api/create_metadata.py /path/to/video.mp4` or via `phantom yt create-metadata <video_path>`

### [fetch_videos.py](../youtube_api/fetch_videos.py)
Core retrieval module that handles fetching all videos for a channel and caching them in local JSON files.
* **Usage**: `python youtube_api/fetch_videos.py [<channel_id_or_handle>] [--fresh]`

### [models.py](../youtube_api/models.py)
Defines shared dataclasses (`Video`, `VideoSeed`, `RankedVideo`) for storing video metadata, stats, and recommendation rankings.

### [recommend_related_videos.py](../youtube_api/recommend_related_videos.py)
Calculates and lists recommended related videos from the channel inventory using a weighted similarity algorithm.
* **Usage**: `python youtube_api/recommend_related_videos.py (--metadata <path> | --video-id <id>) [--limit <int>] [--json]` or via `phantom yt recommend-related`

### [upload_video.py](../youtube_api/upload_video.py)
Uploads an MP4 video, attaches metadata from a local `metadata.json`, uploads a custom `thumbnail.png`, and automatically shares the link on Twitter (X).
* **Usage**: `python youtube_api/upload_video.py /path/to/video.mp4` or via `phantom yt upload <video_path>`

### [utils.py](../youtube_api/utils.py)
Utility functions containing API authentication, channel ID resolution, Jaccard text similarity helpers, and logarithmic scoring algorithms.
