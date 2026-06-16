#!/usr/bin/env python3
"""
YouTube Video Uploader
Usage: python upload_video.py /path/to/video.mp4
Expects metadata.json and thumbnail.png (optional) in the same folder as the video.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path to import global config
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))
import config


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
CHUNK_SIZE = 256 * 1024  # 256 KB — controls progress-bar granularity




# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_authenticated_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=creds)


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def load_metadata(video_path: Path) -> dict:
    metadata_file = video_path.parent / "metadata.json"
    if not metadata_file.exists():
        raise FileNotFoundError(f"metadata.json not found in {video_path.parent}")
    with open(metadata_file) as f:
        return json.load(f)


def upload_video(youtube, video_path: Path, metadata: dict) -> str:
    """Upload the video and return its YouTube video ID."""

    raw_description = metadata.get("description", "")
    full_description = config.DESCRIPTION_TEMPLATE.format(video_description=raw_description)

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
    metadata = load_metadata(video_path)

    # Authenticate
    print("🔐 Authenticating with YouTube…")
    youtube = get_authenticated_service()

    # Upload video
    video_id = upload_video(youtube, video_path, metadata)

    # Set thumbnail
    set_thumbnail(youtube, video_id, video_path)

    # Get share URL
    share_url = get_share_url(video_id)
    print(f"\n🔗 Share URL: {share_url}")

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
    subprocess.run(["phantom", "tweet", tweet_content], check=True)
    print("✅ Tweet posted!")

    print("\n🎉 All done!")


if __name__ == "__main__":
    main()
