#!/usr/bin/env python3
"""
Add a YouTube video to one of your channel's playlists.

Usage: phantom yt add-to-playlist <video> <playlist_id_or_name> [--privacy <public|unlisted|private>]

<video> is a video ID or any YouTube URL. <playlist_id_or_name> is a playlist ID,
a playlist URL, or the exact title of one of your playlists (case-insensitive).
A title that matches none of your playlists creates a new playlist with that title.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

from googleapiclient.errors import HttpError

from youtube_api.upload_video import get_authenticated_service
from youtube_api.utils import extract_video_id

PLAYLIST_ID_RE = re.compile(r"(?:PL|UU|LL|FL|OL)[a-zA-Z0-9_-]{10,}")


def parse_playlist_id(value: str) -> Optional[str]:
    """The playlist ID in a raw ID or a URL with list=..., or None if value looks like a title."""
    value = value.strip()
    match = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    if PLAYLIST_ID_RE.fullmatch(value):
        return value
    return None


def list_my_playlists(youtube) -> list[dict]:
    playlists: list[dict] = []
    page_token = None
    while True:
        response = youtube.playlists().list(
            part="snippet,status",
            mine=True,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        playlists.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return playlists


def find_playlist(youtube, playlist: str) -> Optional[dict]:
    """Your playlist matching an ID/URL or title, or None when a title matches nothing (so it can be created).

    Raises ValueError for an ID that isn't one of your playlists, or a title shared by several.
    """
    playlists = list_my_playlists(youtube)

    playlist_id = parse_playlist_id(playlist)
    if playlist_id:
        for item in playlists:
            if item["id"] == playlist_id:
                return item
        raise ValueError(f"Playlist {playlist_id} isn't one of your channel's playlists.")

    title = playlist.strip()
    matches = [item for item in playlists if item["snippet"]["title"].strip().lower() == title.lower()]
    if len(matches) > 1:
        ids = "".join(f"\n     - {item['id']}" for item in matches)
        raise ValueError(f"Several playlists are titled '{title}'. Pass one of their IDs instead:{ids}")
    return matches[0] if matches else None


def create_playlist(youtube, title: str, privacy: str = "public") -> dict:
    playlist = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title.strip()},
            "status": {"privacyStatus": privacy},
        },
    ).execute()
    print(f"✅ Created {privacy} playlist '{title.strip()}' ({playlist['id']}).")
    return playlist


def is_in_playlist(youtube, playlist_id: str, video_id: str) -> bool:
    response = youtube.playlistItems().list(
        part="id",
        playlistId=playlist_id,
        videoId=video_id,
        maxResults=1,
    ).execute()
    return bool(response.get("items"))


def add_video_to_playlist(youtube, video_id: str, playlist: dict, check_existing: bool = True) -> None:
    """Pass check_existing=False for a playlist created moments ago: it's empty, and the
    API can still 404 on listing its items."""
    title = playlist["snippet"]["title"]
    if check_existing and is_in_playlist(youtube, playlist["id"], video_id):
        print(f"⏭  Video {video_id} is already in '{title}'.")
        return

    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist["id"],
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            },
        },
    ).execute()
    print(f"✅ Added {video_id} to '{title}' — https://www.youtube.com/playlist?list={playlist['id']}")


def add_to_playlist(youtube, video_id: str, playlist: str, privacy: str = "public") -> None:
    """Add video_id to the playlist named by an ID, URL or title, creating it if the title is new."""
    found = find_playlist(youtube, playlist)
    if found is None:
        found = create_playlist(youtube, playlist, privacy)
        add_video_to_playlist(youtube, video_id, found, check_existing=False)
    else:
        add_video_to_playlist(youtube, video_id, found)


def main():
    parser = argparse.ArgumentParser(description="Add a YouTube video to one of your playlists.")
    parser.add_argument("video", help="Video ID or URL.")
    parser.add_argument("playlist", help="Playlist ID, playlist URL, or playlist title (a new title creates the playlist).")
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="public",
        help="Privacy of the playlist if it has to be created (default: public).",
    )
    args = parser.parse_args()

    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    print("🔐 Authenticating with YouTube…")
    youtube = get_authenticated_service()

    try:
        add_to_playlist(youtube, video_id, args.playlist, args.privacy)
    except (ValueError, HttpError) as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
