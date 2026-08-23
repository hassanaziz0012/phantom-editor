#!/usr/bin/env python3
"""
YouTube Video Uploader
Usage: python upload_video.py /path/to/video.mp4
Expects metadata.json and thumbnail.png (optional) in the same folder as the video.
"""

import argparse
import asyncio
from datetime import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Union

# Add project root to sys.path to import global config
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))
import config
from metadata.read_metadata import read_metadata, VideoMetadata


import httplib2
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = Path(__file__).parent / "tokens/client_secret.json"
TOKEN_FILE = Path(__file__).parent / "tokens/token.json"
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"
CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB — balances throughput with progress granularity




# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_authenticated_service():
    creds = None

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            print(f"⚠️ Error reading token file {TOKEN_FILE}: {e}. Re-authenticating...")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️ Failed to refresh token: {e}. Re-running auth flow...")
                creds = None

        if not creds:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found at {CLIENT_SECRETS_FILE}.\n"
                    f"Please verify client_secret.json exists."
                )

            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=creds)


# ---------------------------------------------------------------------------
# Timestamp & Description Formatting
# ---------------------------------------------------------------------------

def format_timestamps_for_description(raw_timestamps: Any) -> str:
    """Formats timestamps from metadata.json into a multiline string for the YouTube description."""
    if not raw_timestamps:
        return ""

    if isinstance(raw_timestamps, str):
        return raw_timestamps.strip()

    if isinstance(raw_timestamps, (list, tuple, set)):
        lines: list[str] = []
        for item in raw_timestamps:
            if isinstance(item, dict):
                ts = str(item.get("timestamp") or item.get("time") or "").strip()
                topic = str(item.get("topic") or item.get("title") or "").strip()
                if ts and topic:
                    lines.append(f"{ts} {topic}")
                elif ts or topic:
                    lines.append(f"{ts}{topic}".strip())
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        return "\n".join(lines).strip()

    return ""


def format_recommendations_for_description(raw_recommendations: Any) -> str:
    """Formats recommendations from metadata.json into a multiline string of URLs for the YouTube description."""
    if not raw_recommendations:
        return ""

    if isinstance(raw_recommendations, str):
        return raw_recommendations.strip()

    if isinstance(raw_recommendations, (list, tuple, set)):
        items = list(raw_recommendations)
        if all(isinstance(item, dict) for item in items):
            try:
                items.sort(key=lambda x: int(x.get("rank", 999)))
            except (ValueError, TypeError):
                pass

        lines: list[str] = []
        for item in items:
            if isinstance(item, dict):
                url = str(item.get("url") or item.get("link") or "").strip()
                if not url and item.get("video_id"):
                    url = f"https://www.youtube.com/watch?v={item['video_id']}"
                if url:
                    lines.append(url)
                elif (title := str(item.get("title") or item.get("topic") or item.get("name") or "").strip()):
                    lines.append(title)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        return "\n".join(lines).strip()

    return ""


def validate_metadata_for_upload(
    metadata: Union[VideoMetadata, dict],
    require_timestamps: bool = True,
    require_recommendations: bool = True,
) -> tuple[str, str, str]:
    """
    Validates metadata fields before uploading.
    Ensures 'timestamps' and 'recommendations' exist and are non-empty if required.
    Returns (raw_description, formatted_timestamps, formatted_recommendations).
    """
    raw_description = metadata.get("description", "")

    raw_timestamps = metadata.get("timestamps")
    formatted_timestamps = format_timestamps_for_description(raw_timestamps)

    if require_timestamps and not formatted_timestamps:
        raise ValueError(
            "Missing or empty 'timestamps' in metadata.json.\n"
            "   Timestamps/chapters are required before uploading to YouTube.\n"
            "   Please run `phantom metadata timestamps <target>` or `phantom metadata auto <target>` to generate them."
        )

    raw_recommendations = metadata.get("recommendations")
    formatted_recommendations = format_recommendations_for_description(raw_recommendations)

    if require_recommendations and not formatted_recommendations:
        raise ValueError(
            "Missing or empty 'recommendations' in metadata.json.\n"
            "   Video recommendations are required before uploading to YouTube.\n"
            "   Please run `phantom metadata recommend <target>` or `phantom metadata auto <target>` to generate them."
        )

    return raw_description, formatted_timestamps, formatted_recommendations


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def upload_video(
    youtube,
    video_path: Path,
    metadata: Union[VideoMetadata, dict],
    require_timestamps: bool = True,
    require_recommendations: bool = True,
) -> str:
    """Upload the video and return its YouTube video ID."""

    raw_description, formatted_timestamps, formatted_recommendations = validate_metadata_for_upload(
        metadata,
        require_timestamps=require_timestamps,
        require_recommendations=require_recommendations,
    )
    full_description = config.DESCRIPTION_TEMPLATE.format(
        video_description=raw_description,
        timestamps=formatted_timestamps,
        recommended=formatted_recommendations,
    )

    body = {
        "snippet": {
            "title":       metadata.get("title", video_path.stem),
            "description": full_description,
            "tags":        metadata.get("tags", []),
            "categoryId":  str(metadata.get("categoryId", "22")),  # 22 = People & Blogs
        },
        "status": {
            "privacyStatus":           metadata.get("privacyStatus", "public"),
            "selfDeclaredMadeForKids": metadata.get("madeForKids", False),
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    file_size = video_path.stat().st_size
    uploaded = 0
    response = None

    print(f"\n📤 Uploading: {video_path.name}")
    with tqdm(total=file_size, unit="B", unit_scale=True, unit_divisor=1024,
              desc="Progress", ncols=80) as pbar:
        while response is None:
            status, response = request.next_chunk()
            if status:
                new_uploaded = status.resumable_progress
                pbar.update(new_uploaded - uploaded)
                uploaded = new_uploaded

        # Final update to 100 %
        pbar.update(file_size - uploaded)

    video_id = response["id"]
    print(f"✅ Upload complete — video ID: {video_id}")
    return video_id


def set_thumbnail(youtube, video_id: str, video_path: Path):
    thumbnail_file = video_path.parent / "thumbnail.png"
    if not thumbnail_file.exists():
        print("⚠️  thumbnail.png not found in the video folder.")
        answer = input("   Proceed without a custom thumbnail? YouTube will auto-select one. [y/N] ").strip().lower()
        if answer != "y":
            print("❌ Aborted. Re-run the script once you've added thumbnail.png.")
            sys.exit(1)
        print("⏭  Skipping thumbnail — YouTube will pick one automatically.")
        return

    print("🖼  Setting thumbnail…")
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumbnail_file), mimetype="image/png"),
    ).execute()
    print("✅ Thumbnail set.")


def get_share_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Upload a video to YouTube.")
    parser.add_argument("video_path", help="Path to the .mp4 file to upload.")
    args = parser.parse_args()

    video_path = Path(args.video_path).resolve()
    if not video_path.exists():
        print(f"❌ File not found: {video_path}")
        sys.exit(1)

    # Load metadata
    print("📋 Loading metadata…")
    try:
        metadata = read_metadata(video_path)
    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Pre-check metadata requirements (timestamps & recommendations) before authentication or uploading
    print("🔍 Validating metadata...")
    try:
        _, formatted_timestamps, formatted_recommendations = validate_metadata_for_upload(
            metadata,
            require_timestamps=True,
            require_recommendations=True,
        )
        ts_count = len(formatted_timestamps.splitlines())
        rec_count = len(formatted_recommendations.splitlines())
        print(f"✅ Found {ts_count} timestamps for video chapters.")
        print(f"✅ Found {rec_count} recommended videos for 'WATCH THESE NEXT'.")
    except ValueError as e:
        print(f"\n❌ Error: {e}\n", file=sys.stderr)
        sys.exit(1)

    # Authenticate
    print("🔐 Authenticating with YouTube…")
    youtube = get_authenticated_service()

    # Upload video
    video_id = upload_video(
        youtube,
        video_path,
        metadata,
        require_timestamps=True,
        require_recommendations=True,
    )

    # Set thumbnail
    set_thumbnail(youtube, video_id, video_path)

    # Get share URL
    share_url = get_share_url(video_id)
    print(f"\n🔗 Share URL: {share_url}")

    # Save URL and uploadedDate to metadata.json
    metadata.url = share_url
    metadata.uploaded_date = datetime.now().strftime("%Y-%m-%d")
    try:
        metadata.save()
        print("✅ Saved YouTube URL and uploadedDate to metadata.json")
    except Exception as e:
        print(f"⚠️  Failed to save metadata to metadata.json: {e}")

    # Post tweet
    tweet_template = metadata.get(
        "tweetTemplate",
        "🎬 New video just dropped! {url}"
    )
    tweet_content = tweet_template.format(
        url=share_url,
        title=metadata.get("title", ""),
    )
    print(f"\n🐦 Posting tweet:\n   {tweet_content}")
    try:
        subprocess.run(["phantom", "tweet", tweet_content], check=True)
        print("✅ Tweet posted!")
    except Exception as e:
        print(f"⚠️  Tweet step failed or browser closed: {e}")
        print("⏭  Moving on gracefully...")

    # Send email broadcast
    title = metadata.get("title", video_path.stem)
    description = metadata.get("description", "")
    resend_script = repo_root / "emails" / "resend_broadcast.py"

    print(f"\n📧 Sending email broadcast for '{title}'...")
    subprocess.run(
        [
            sys.executable,
            str(resend_script),
            "--title",
            title,
            "--description",
            description,
            "--url",
            share_url,
        ],
        check=True,
    )
    print("✅ Email broadcast sent!")

    print("\n🎉 All done!")



if __name__ == "__main__":
    main()
