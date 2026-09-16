#!/usr/bin/env python3
"""
YouTube Short Uploader
Usage: python upload_short.py /path/to/short_video.mp4
Fetches metadata from shorts/shorts.json and uses upload_video.py under the hood.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path to import global config and modules
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

# Ensure the folder containing upload_video.py is in sys.path
youtube_api_dir = repo_root / "youtube_api"
if str(youtube_api_dir) not in sys.path:
    sys.path.append(str(youtube_api_dir))

import upload_video
from googleapiclient.http import MediaFileUpload
from shorts.metadata_utils import (
    get_platform_entry,
    mark_posted,
    resolve_shorts_thumbnail,
)


def main():
    parser = argparse.ArgumentParser(description="Upload a Short video to YouTube.")
    parser.add_argument("video_path", help="Path to the short video (.mp4) file to upload.")
    args = parser.parse_args()

    # Resolve and validate the video file path
    video_path = Path(args.video_path).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found at '{video_path}'", file=sys.stderr)
        sys.exit(1)
    if not video_path.is_file():
        print(f"Error: Path '{video_path}' is not a file", file=sys.stderr)
        sys.exit(1)

    # Load metadata from shorts.json and check posted status
    metadata, shorts_json_path = get_platform_entry(video_path, "youtube")
    if metadata is None:
        sys.exit(0)

    # Authenticate with YouTube using helper from upload_video.py
    print("🔐 Authenticating with YouTube…")
    try:
        youtube = upload_video.get_authenticated_service()
    except Exception as e:
        print(f"Error: YouTube authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Upload video using helper from upload_video.py
    try:
        video_id = upload_video.upload_video(youtube, video_path, metadata, require_timestamps=False)
    except Exception as e:
        print(f"Error: YouTube upload failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Set custom thumbnail if provided in metadata and file exists (non-blocking)
    resolved_thumb = resolve_shorts_thumbnail(metadata, video_path)
    if resolved_thumb:
        print(f"🖼  Setting thumbnail: '{resolved_thumb}'...")
        mime_type = "image/png"
        if resolved_thumb.suffix.lower() in [".jpg", ".jpeg"]:
            mime_type = "image/jpeg"
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(resolved_thumb), mimetype=mime_type),
            ).execute()
            print("✅ Thumbnail set.")
        except Exception as e:
            print(f"Warning: Failed to set thumbnail: {e}", file=sys.stderr)

    # Update posted.youtube to True in shorts.json
    mark_posted(shorts_json_path, video_path, "youtube")

    print("\n🎉 Short upload task complete!")

if __name__ == "__main__":
    main()
