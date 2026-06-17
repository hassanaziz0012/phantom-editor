#!/usr/bin/env python3
"""
TikTok Video Uploader using tiktok-uploader package
Usage: python upload_tiktok.py /path/to/short_video.mp4
Fetches metadata from shorts/shorts.json, handles authentication, and uploads to TikTok.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from tiktok_uploader.upload import TikTokUploader

# Add project root to sys.path to import global config
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

import config

# Load environment variables from .env in the project root
load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Upload a video to TikTok using tiktok-uploader.")
    parser.add_argument("video_path", help="Path to the video (.mp4) file to upload.")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode (visible).")
    args = parser.parse_args()

    # Resolve and validate the video file path
    video_path = Path(args.video_path).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found at '{video_path}'", file=sys.stderr)
        sys.exit(1)
    if not video_path.is_file():
        print(f"Error: Path '{video_path}' is not a file", file=sys.stderr)
        sys.exit(1)

    video_name = video_path.name
    video_stem = video_path.stem

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
                # 1. Match by exact resolved path
                for entry in shorts_data:
                    if isinstance(entry, dict) and "video_path" in entry:
                        if Path(entry["video_path"]).resolve() == video_path:
                            metadata = entry
                            break
                
                # 2. Match by filename
                if not metadata:
                    for entry in shorts_data:
                        if isinstance(entry, dict) and "video_path" in entry:
                            if Path(entry["video_path"]).name == video_name:
                                metadata = entry
                                break

                # 3. Match by stem (video ID/title)
                if not metadata:
                    for entry in shorts_data:
                        if isinstance(entry, dict) and "video_path" in entry:
                            if Path(entry["video_path"]).stem == video_stem:
                                metadata = entry
                                break
    except Exception as e:
        print(f"Error reading shorts.json: {e}", file=sys.stderr)
        sys.exit(1)

    if not metadata:
        print(f"Error: Metadata for video '{video_path}' not found in shorts.json", file=sys.stderr)
        sys.exit(1)

    print("Found video metadata in shorts.json.")
    
    # Construct the caption
    short_desc = metadata.get("description", "")
    title_val = metadata.get("title", "")
    tags_val = metadata.get("tags", [])
    
    caption_parts = []
    if title_val:
        caption_parts.append(title_val)
    
    # Use global template if defined
    if hasattr(config, 'DESCRIPTION_TEMPLATE') and config.DESCRIPTION_TEMPLATE:
        caption_parts.append(config.DESCRIPTION_TEMPLATE.format(video_description=short_desc))
    else:
        caption_parts.append(short_desc)
        
    if tags_val:
        formatted_tags = []
        for tag in tags_val:
            tag_clean = tag.strip()
            if not tag_clean.startswith("#"):
                tag_clean = f"#{tag_clean}"
            formatted_tags.append(tag_clean)
        caption_parts.append(" ".join(formatted_tags))
        
    caption = "\n\n".join([p for p in caption_parts if p])

    if metadata.get("thumbnail"):
        print("Note: Custom thumbnail upload is not automated on TikTok Web upload via this script. Skipping custom thumbnail.")

    # Resolve cookies file paths
    cookies_txt = repo_root / "cookies.txt"
    tiktok_cookies_txt = repo_root / "tiktok_cookies.txt"

    # Determine headless mode
    headless = not args.headed
    using_cookies = tiktok_cookies_txt.exists() or cookies_txt.exists()
    if not args.headed and not using_cookies:
        print("Note: Using environment credentials. Defaulting browser to headed mode so you can solve any CAPTCHAs.")
        headless = False

    uploader = None
    if tiktok_cookies_txt.exists():
        print(f"Using cookies file: {tiktok_cookies_txt}")
        uploader = TikTokUploader(cookies=str(tiktok_cookies_txt), headless=headless)
    elif cookies_txt.exists():
        print(f"Using cookies file: {cookies_txt}")
        uploader = TikTokUploader(cookies=str(cookies_txt), headless=headless)
    else:
        # Check environment variables
        username = os.getenv("TIKTOK_USERNAME")
        password = os.getenv("TIKTOK_PASSWORD")
        sessionid = os.getenv("TIKTOK_SESSIONID") or os.getenv("TIKTOK_SESSION_ID")
        
        if sessionid:
            print("Using TIKTOK_SESSIONID from environment...")
            # Playwright requires the cookie to have a domain/path pair, which the library
            # lacks when initializing with sessionid directly. We pass it as cookies_list.
            cookies_list = [{
                "name": "sessionid",
                "value": sessionid,
                "domain": ".tiktok.com",
                "path": "/"
            }]
            uploader = TikTokUploader(cookies_list=cookies_list, headless=headless)
        elif username and password:
            print("Using TIKTOK_USERNAME and TIKTOK_PASSWORD from environment...")
            uploader = TikTokUploader(username=username, password=password, headless=headless)
        else:
            print("Error: No authentication cookies or credentials found.", file=sys.stderr)
            print("Please define TIKTOK_USERNAME and TIKTOK_PASSWORD in your .env file, or export your TikTok cookies to 'cookies.txt'.", file=sys.stderr)
            sys.exit(1)

    print("Uploading video via tiktok-uploader...")
    try:
        # tiktok-uploader's upload_video returns True on success, False/fails on failure
        success = uploader.upload_video(filename=str(video_path), description=caption)
        if not success:
            print("Error: TikTok upload failed.", file=sys.stderr)
            sys.exit(1)
        print("TikTok upload succeeded.")
    except Exception as e:
        print(f"Error during upload: {e}", file=sys.stderr)
        sys.exit(1)

    # Update posted.tiktok to True in shorts.json
    print("Updating posted status in shorts.json...")
    try:
        with open(shorts_json_path, "r+", encoding="utf-8") as f:
            shorts_data = json.load(f)
            updated = False
            if isinstance(shorts_data, list):
                for entry in shorts_data:
                    if isinstance(entry, dict) and "video_path" in entry:
                        entry_path = Path(entry["video_path"])
                        # Match the same logic
                        if entry_path.resolve() == video_path or entry_path.name == video_name or entry_path.stem == video_stem:
                            if "posted" not in entry or not isinstance(entry["posted"], dict):
                                entry["posted"] = {"youtube": False, "instagram": False, "tiktok": False}
                            entry["posted"]["tiktok"] = True
                            updated = True
                            break
            if updated:
                f.seek(0)
                json.dump(shorts_data, f, indent=2, ensure_ascii=False)
                f.truncate()
                print("Successfully updated posted.tiktok to true in shorts.json.")
            else:
                print("Warning: Could not find video entry in shorts.json to update posted status.", file=sys.stderr)
    except Exception as update_err:
        print(f"Warning: Failed to update shorts.json: {update_err}", file=sys.stderr)

    print("\n🎉 TikTok upload task complete!")

if __name__ == "__main__":
    main()
