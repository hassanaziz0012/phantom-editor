# YouTube API Integration

This directory contains scripts and helper utilities for interacting with the YouTube Data API v3. They enable video uploading, metadata generation, and upload counting.

## Configuration
Requires `YOUTUBE_API_KEY` and OAuth client/token JSON files in `youtube_api/tokens/` (for video uploading).

## Scripts

### [count_youtube_uploads.py](../youtube_api/count_youtube_uploads.py)
Calculates and displays a channel's upload counts for today, the last 7 days, 14 days, and 30 days in a formatted ASCII table.

* **Usage**: `python youtube_api/count_youtube_uploads.py [<channel_id_or_handle>] [--fresh]` or via `phantom yt count-uploads [<channel>] [--fresh]`

### [fetch_videos.py](../youtube_api/fetch_videos.py)
Core retrieval module that handles fetching all videos for a channel and caching them in local JSON files.

* **Usage**: `python youtube_api/fetch_videos.py [<channel_id_or_handle>] [--fresh]`

### [models.py](../youtube_api/models.py)
Defines shared dataclasses (`Video`, `VideoSeed`, `RankedVideo`, `VideoMetadata`) for storing video metadata, stats, and recommendation rankings.

### [upload_video.py](../youtube_api/upload_video.py)
Uploads an MP4 video, attaches metadata from a local `metadata.json`, uploads a custom `thumbnail.png`, shares the link on Twitter (X) and Substack Notes, and sends an email broadcast to subscribers via Resend.

The description is built from `DESCRIPTION_TEMPLATE` in [`config.py`](../config.py). Its RESOURCES section lists `metadata.json`'s `resources` (from `phantom meta resources`) as `key: url` lines, and is left out when there are none. The upload stops before starting if any resource still has a blank `url`.

* **Usage**: `python youtube_api/upload_video.py /path/to/video.mp4` or via `phantom publish yt <video_path> [--playlist <playlist_id_or_name>]`
* **Playlist**: `--playlist` adds the uploaded video to a playlist (see [addtoplaylist.py](#addtoplaylistpy)). The playlist is looked up before the upload starts, so an unknown ID or an ambiguous title stops the upload. If adding fails after the upload, it only warns and prints the `phantom yt add-to-playlist` command to retry.
* **Auth**: The OAuth token needs both the `youtube.upload` and `youtube` scopes. A saved `tokens/token.json` missing either one triggers the browser sign-in again.

### [addtoplaylist.py](../youtube_api/addtoplaylist.py)
Adds a video to one of your channel's playlists.

* **Usage**: `phantom yt add-to-playlist <video> <playlist_id_or_name> [--privacy <public|unlisted|private>]`. `<video>` is a video ID or any YouTube URL.
* **Playlist lookup**: A playlist ID or a URL with `list=` must be one of your playlists. Anything else is treated as a title and matched against your playlists (case-insensitive). If no playlist has that title, a new one is created with `--privacy` (default `public`). If several playlists share the title, it stops and lists their IDs.
* **Duplicates**: Skips the add if the video is already in the playlist.
* **Reuse**: `from youtube_api.addtoplaylist import add_to_playlist, find_playlist, create_playlist, add_video_to_playlist`.

### [video_info.py](../youtube_api/video_info.py)
Shows quick details about any YouTube video: a direct thumbnail URL, views/likes/comments, and an outlier score — the video's views divided by the median views of the channel's recent uploads of the same format (Shorts vs long-form). Baseline videos younger than 14 days are excluded, and a note is shown if the video itself is that new.

* **Usage**: `python youtube_api/video_info.py <video_url_or_id> [--fresh] [--json]` or via `phantom yt info <video> [--fresh] [--json]`
* **Caching**: The channel's last 50 uploads are cached in `youtube_api/cache/baselines.json` for 24 hours. A cold lookup costs 3 quota units; a cached one costs 1. `--fresh` forces a refetch.

### [add_endscreen.py](../youtube_api/add_endscreen.py)
Adds an end screen to your videos by applying a YouTube Studio template, since the Data API has no end screen support. The default template, "Import from latest video", copies the end screen of your most recent video that has one (two videos and a subscribe element). It drives the video's Studio editor in the logged-in Chrome on port 9222. Videos that already have an end screen are skipped, and the video must have finished processing.

* **Usage**: `phantom yt endscreen <video>... [--template <name>] [--dry-run]`. Accepts video IDs, watch URLs, or Studio URLs. `--dry-run` applies the template and lists the elements without saving.

### [channel_stats.py](../youtube_api/channel_stats.py)
Your channel's long-form performance over the last 28 days, read live from YouTube Studio: impressions, CTR, views, average view duration, watch time, and subscribers. These match the Videos card on Studio's Content tab.

* **Usage**: `phantom yt channel-stats [--fresh] [--json] [--history]`
* **Reuse**: `from youtube_api.channel_stats import get_channel_stats, get_channel_history`. `get_channel_stats()` returns the latest snapshot, refetching from Studio when it's over 12 hours old.
* **Storage**: Every fetch is saved to `youtube_api/cache/channel_stats.json` as the latest snapshot plus one snapshot per day (`--history`), so the averages can be tracked over time. The 48h performance check in `phantom projects status` also refreshes it.

### [utils.py](../youtube_api/utils.py)
Utility functions containing API authentication, channel ID resolution, Jaccard text similarity helpers, and logarithmic scoring algorithms.
