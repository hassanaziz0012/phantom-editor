"""
YouTube Channel Video Fetcher
==============================
Fetches all videos from a YouTube channel using the YouTube Data API v3
and organizes them into a list of structured Video objects.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from googleapiclient.discovery import build
from dotenv import load_dotenv

# ── Configuration ─────────────────────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("YOUTUBE_API_KEY")
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Video:
    """Represents a single YouTube video with its metadata."""

    video_id:        str
    title:           str
    description:     str
    published_at:    datetime
    thumbnail_url:   str
    channel_id:      str
    channel_title:   str

    # Stats (populated in a second API call)
    view_count:      Optional[int]  = None
    like_count:      Optional[int]  = None
    comment_count:   Optional[int]  = None
    duration:        Optional[str]  = None   # ISO 8601, e.g. "PT4M13S"

    # Extra metadata
    tags:            list[str]      = field(default_factory=list)
    category_id:     Optional[str]  = None
    live_broadcast:  Optional[str]  = None   # "none" | "live" | "upcoming"
    url:             str            = ""

    def __post_init__(self):
        self.url = f"https://www.youtube.com/watch?v={self.video_id}"

    def __repr__(self):
        return (
            f"Video(id={self.video_id!r}, title={self.title!r}, "
            f"published={self.published_at.date()}, views={self.view_count})"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(iso_string: str) -> datetime:
    """Parse an ISO 8601 timestamp returned by the API."""
    return datetime.fromisoformat(iso_string.replace("Z", "+00:00"))


def _best_thumbnail(thumbnails: dict) -> str:
    """Return the highest-quality thumbnail URL available."""
    for quality in ("maxres", "standard", "high", "medium", "default"):
        if quality in thumbnails:
            return thumbnails[quality]["url"]
    return ""


# ── Core functions ────────────────────────────────────────────────────────────

def get_uploads_playlist_id(youtube, channel_id: str) -> str:
    """
    Retrieve the 'uploads' playlist ID for a channel.
    Every channel has a hidden playlist that contains all its public videos.
    """
    response = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found or no content details for ID: {channel_id!r}")

    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_all_video_ids(youtube, uploads_playlist_id: str) -> list[str]:
    """
    Page through the uploads playlist and collect every video ID.
    The API returns at most 50 items per page, so we follow nextPageToken.
    """
    video_ids = []
    next_page_token = None

    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()

        for item in response.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


def fetch_video_details(youtube, video_ids: list[str]) -> list[Video]:
    """
    Fetch full metadata + statistics for a list of video IDs.
    The API accepts up to 50 IDs per request, so we batch them.
    """
    videos = []

    # Process in batches of 50
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]

        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            snippet    = item.get("snippet", {})
            stats      = item.get("statistics", {})
            content    = item.get("contentDetails", {})
            thumbnails = snippet.get("thumbnails", {})

            video = Video(
                video_id       = item["id"],
                title          = snippet.get("title", ""),
                description    = snippet.get("description", ""),
                published_at   = _parse_dt(snippet.get("publishedAt", "1970-01-01T00:00:00Z")),
                thumbnail_url  = _best_thumbnail(thumbnails),
                channel_id     = snippet.get("channelId", ""),
                channel_title  = snippet.get("channelTitle", ""),
                tags           = snippet.get("tags", []),
                category_id    = snippet.get("categoryId"),
                live_broadcast = snippet.get("liveBroadcastContent"),
                view_count     = int(stats["viewCount"])    if "viewCount"    in stats else None,
                like_count     = int(stats["likeCount"])    if "likeCount"    in stats else None,
                comment_count  = int(stats["commentCount"]) if "commentCount" in stats else None,
                duration       = content.get("duration"),
            )
            videos.append(video)

    return videos


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_channel_videos(api_key: str, channel_id: str) -> list[Video]:
    """
    Full pipeline: authenticate → find uploads playlist → page through it
    → enrich each video with stats → return a sorted list of Video objects.
    """
    youtube = build("youtube", "v3", developerKey=api_key)

    print(f"[1/3] Fetching uploads playlist for channel: {channel_id}")
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)

    print(f"[2/3] Collecting all video IDs from playlist: {uploads_playlist_id}")
    video_ids = fetch_all_video_ids(youtube, uploads_playlist_id)
    print(f"      Found {len(video_ids)} videos")

    print("[3/3] Fetching full metadata and statistics …")
    videos = fetch_video_details(youtube, video_ids)

    # Sort newest → oldest by default
    videos.sort(key=lambda v: v.published_at, reverse=True)

    print(f"Done! Returning {len(videos)} Video objects.\n")
    return videos


# ── Quick demo ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    videos = fetch_channel_videos(API_KEY, CHANNEL_ID)

    # Print a summary of the first 5 videos
    for v in videos[:5]:
        print(v)
        print(f"  URL      : {v.url}")
        print(f"  Duration : {v.duration}")
        print(f"  Views    : {v.view_count:,}" if v.view_count is not None else "  Views    : N/A")
        print(f"  Tags     : {', '.join(v.tags[:5]) or 'none'}")
        print()