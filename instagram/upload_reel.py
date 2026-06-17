import os
import sys
import argparse
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired

# Add project root to sys.path to import global config
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))
import config

# Load environment variables from .env in the project root
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Upload a video to Instagram as a Reel.")
    parser.add_argument("video", type=str, help="Path to the video (.mp4) file to upload.")
    args = parser.parse_args()

    # Resolve and validate the video file path
    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found at '{video_path}'", file=sys.stderr)
        sys.exit(1)
    if not video_path.is_file():
        print(f"Error: Path '{video_path}' is not a file", file=sys.stderr)
        sys.exit(1)

    # Inform user of file format recommendation
    if video_path.suffix.lower() not in ['.mp4', '.mov']:
        print(f"Warning: File extension '{video_path.suffix}' might not be supported. Instagram Reels typically require .mp4 or .mov formats.", file=sys.stderr)

    # Load metadata from shorts.json if it exists
    shorts_json_path = repo_root / "shorts" / "shorts.json"
    if not shorts_json_path.exists():
        print(f"Error: shorts.json not found at '{shorts_json_path}'", file=sys.stderr)
        sys.exit(1)

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

    # Check if already posted to Instagram
    posted = metadata.get("posted")
    if isinstance(posted, dict) and posted.get("instagram") is True:
        print(f"Video '{video_path.name}' is already marked as posted to Instagram in shorts.json. Skipping upload.")
        sys.exit(0)

    print("Found video metadata in shorts.json.")
    short_desc = metadata.get("description", "")
    caption = config.DESCRIPTION_TEMPLATE.format(video_description=short_desc)

    thumbnail_path = None
    if metadata.get("thumbnail"):
        thumb_cand = Path(metadata["thumbnail"])
        if not thumb_cand.is_absolute():
            # Resolve relative to video folder or repo root
            resolved_thumb = video_path.parent / thumb_cand
            if not resolved_thumb.exists():
                resolved_thumb = repo_root / thumb_cand
        else:
            resolved_thumb = thumb_cand

        if resolved_thumb.exists() and resolved_thumb.is_file():
            thumbnail_path = resolved_thumb
            print(f"Using thumbnail: '{thumbnail_path}'")
        else:
            print(f"Warning: Thumbnail file specified in metadata not found at '{resolved_thumb}'", file=sys.stderr)

    # Load and validate credentials securely from the environment
    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")

    if not username or not password:
        print("Error: INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD environment variables must be set.", file=sys.stderr)
        sys.exit(1)

    # Resolve session file path to project root to align with .gitignore rule
    session_file = repo_root / "instagram_session.json"

    cl = Client()

    # 1. Attempt to load existing session
    session_loaded = False
    if session_file.exists():
        try:
            cl.load_settings(session_file)
            session_loaded = True
            print("Loaded cached Instagram session.")
        except Exception as e:
            # Handle logging/errors securely without exposing credentials
            print(f"Warning: Failed to load cached session settings: {e}", file=sys.stderr)

    # 2. Check session validity or perform a login
    logged_in = False
    if session_loaded:
        try:
            # Perform a lightweight API call to verify if the session is still active/valid
            cl.get_timeline_feed()
            logged_in = True
            print("Session is valid. Logged in successfully.")
        except LoginRequired:
            print("Session has expired. Re-authenticating...")
        except Exception as e:
            print(f"Warning: Failed to verify session: {e}. Re-logging in...", file=sys.stderr)

    if not logged_in:
        try:
            print("Logging in to Instagram...")
            cl.login(username, password)
            cl.dump_settings(session_file)
            print("Logged in successfully and updated session cache.")
        except ChallengeRequired as e:
            print(f"Error: Instagram authentication challenge required: {e}", file=sys.stderr)
            print("Please log in via the official app/browser on this network first.", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: Login failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Auto-generate thumbnail if none specified or found
    temp_thumbnail = False
    if not thumbnail_path:
        print("No thumbnail specified. Generating thumbnail using ffmpeg...")
        generated_thumb = video_path.with_suffix(".jpg")
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", "00:00:01",
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                str(generated_thumb)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if generated_thumb.exists():
                thumbnail_path = generated_thumb
                temp_thumbnail = True
                print(f"Generated thumbnail: '{thumbnail_path}'")
        except Exception as e:
            print(f"Warning: Failed to generate thumbnail with ffmpeg: {e}", file=sys.stderr)

    # 3. Upload the Reel
    print(f"Uploading Reel: '{video_path}'...")
    try:
        media = cl.clip_upload(
            path=video_path,
            caption=caption,
            thumbnail=thumbnail_path
        )
        print("Reel uploaded successfully!")
        media_id = getattr(media, "id", None) or getattr(media, "pk", "unknown")
        print(f"Media ID: {media_id}")

        # Update posted.instagram to True in shorts.json
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
                                entry["posted"]["instagram"] = True
                                updated = True
                                break
                if updated:
                    f.seek(0)
                    json.dump(shorts_data, f, indent=2, ensure_ascii=False)
                    f.truncate()
                    print("Successfully updated posted.instagram to true in shorts.json.")
                else:
                    print("Warning: Could not find video entry in shorts.json to update posted status.", file=sys.stderr)
        except Exception as update_err:
            print(f"Warning: Failed to update shorts.json: {update_err}", file=sys.stderr)

    except Exception as e:
        print(f"Error: Upload failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if temp_thumbnail and thumbnail_path and thumbnail_path.exists():
            try:
                thumbnail_path.unlink()
                print("Cleaned up temporary thumbnail.")
            except Exception as e:
                print(f"Warning: Failed to clean up temporary thumbnail: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
