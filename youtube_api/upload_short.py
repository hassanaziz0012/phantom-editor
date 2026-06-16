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

    # Resolve shorts.json path
    shorts_json_path = repo_root / "shorts" / "shorts.json"
    if not shorts_json_path.exists():
        print(f"Error: shorts.json not found at '{shorts_json_path}'", file=sys.stderr)
        sys.exit(1)

    # Load metadata from shorts.json
    metadata = None
    try:
        with open(shorts_json_path, "r", encoding="utf-8") as f:
            shorts_data = json.load(f)
            if isinstance(shorts_data, list):
                for entry in shorts_data:
                    if isinstance(entry, dict) and "video_path" in entry:
                        if Path(entry["video_path"]).resolve() == video_path:
                            metadata = entry
                            break
    except Exception as e:
        print(f"Error reading shorts.json: {e}", file=sys.stderr)
        sys.exit(1)

    if not metadata:
        print(f"Error: Metadata for video '{video_path}' not found in shorts.json", file=sys.stderr)
        sys.exit(1)

    # Check if already posted to YouTube
    posted = metadata.get("posted")
    if isinstance(posted, dict) and posted.get("youtube") is True:
        print(f"Video '{video_path.name}' is already marked as posted to YouTube in shorts.json. Skipping upload.")
        sys.exit(0)

    print("Found video metadata in shorts.json.")

    # Authenticate with YouTube using helper from upload_video.py
    print("🔐 Authenticating with YouTube…")
    try:
        youtube = upload_video.get_authenticated_service()
    except Exception as e:
        print(f"Error: YouTube authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Upload video using helper from upload_video.py
    try:
        video_id = upload_video.upload_video(youtube, video_path, metadata)
    except Exception as e:
        print(f"Error: YouTube upload failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Set custom thumbnail if provided in metadata and file exists (non-blocking)
    thumbnail_val = metadata.get("thumbnail")
    if thumbnail_val:
        thumb_cand = Path(thumbnail_val)
        if not thumb_cand.is_absolute():
            resolved_thumb = video_path.parent / thumb_cand
            if not resolved_thumb.exists():
                resolved_thumb = repo_root / thumb_cand
        else:
            resolved_thumb = thumb_cand

        if resolved_thumb.exists() and resolved_thumb.is_file():
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
        else:
            print(f"Warning: Thumbnail file specified in metadata not found at '{resolved_thumb}'", file=sys.stderr)

    # Update posted.youtube to True in shorts.json
    print("Updating posted status in shorts.json...")
    try:
        with open(shorts_json_path, "r+", encoding="utf-8") as f:
            shorts_data = json.load(f)
            updated = False
            if isinstance(shorts_data, list):
                for entry in shorts_data:
                    if isinstance(entry, dict) and "video_path" in entry:
                        if Path(entry["video_path"]).resolve() == video_path:
                            if "posted" not in entry or not isinstance(entry["posted"], dict):
                                entry["posted"] = {"youtube": False, "instagram": False, "tiktok": False}
                            entry["posted"]["youtube"] = True
                            updated = True
                            break
            if updated:
                f.seek(0)
                json.dump(shorts_data, f, indent=2, ensure_ascii=False)
                f.truncate()
                print("Successfully updated posted.youtube to true in shorts.json.")
            else:
                print("Warning: Could not find video entry in shorts.json to update posted status.", file=sys.stderr)
    except Exception as update_err:
        print(f"Warning: Failed to update shorts.json: {update_err}", file=sys.stderr)

    print("\n🎉 Short upload task complete!")

if __name__ == "__main__":
    main()
