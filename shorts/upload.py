#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import json
import datetime
from pathlib import Path

def find_metadata_entry(data, path_resolved, name, stem):
    # 1. Match by exact resolved path
    for entry in data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).resolve() == path_resolved:
                    return entry
            except Exception:
                pass
    # 2. Match by filename
    for entry in data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).name == name:
                    return entry
            except Exception:
                pass
    # 3. Match by stem
    for entry in data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).stem == stem:
                    return entry
            except Exception:
                pass
    return None

def main():
    parser = argparse.ArgumentParser(description="Upload a short video to YouTube, Instagram, TikTok, or all of them.")
    parser.add_argument("video_path", help="Path to the video (.mp4) file to upload.")
    parser.add_argument(
        "--platform",
        required=True,
        type=str.lower,
        choices=["youtube", "instagram", "tiktok", "all"],
        help="Target platform to upload to ('youtube', 'instagram', 'tiktok', or 'all')."
    )
    
    args = parser.parse_args()

    # Validate video path
    video_path = Path(args.video_path).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found at '{video_path}'", file=sys.stderr)
        sys.exit(1)
    if not video_path.is_file():
        print(f"Error: Path '{video_path}' is not a file", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    shorts_json_path = repo_root / "shorts" / "shorts.json"

    # Load existing metadata from shorts.json
    shorts_data = []
    if shorts_json_path.exists():
        try:
            with open(shorts_json_path, "r", encoding="utf-8") as f:
                shorts_data = json.load(f)
                if not isinstance(shorts_data, list):
                    shorts_data = []
        except Exception as e:
            print(f"Warning: Failed to load existing shorts.json: {e}", file=sys.stderr)
            shorts_data = []

    video_path_resolved = video_path.resolve()
    video_name = video_path.name
    video_stem = video_path.stem

    entry = find_metadata_entry(shorts_data, video_path_resolved, video_name, video_stem)

    is_new_short = False
    title = ""
    description = ""
    tags = []
    thumbnail = ""
    scheduled_time = ""

    if not entry:
        print(f"Warning: Video '{video_name}' is not present in the short metadata file.")
        try:
            confirm = input("Do you want to proceed? (y/n): ").strip().lower()
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)
            
        if confirm != 'y':
            print("Aborting upload.")
            sys.exit(0)

        is_new_short = True
        print("\nPlease specify the metadata fields manually:")
        default_title = video_stem.replace("-", " ").replace("_", " ").title()
        try:
            title = input(f"Title [{default_title}]: ").strip() or default_title
            description = input("Description: ").strip()
            tags_input = input("Tags (comma-separated): ").strip()
            tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
            thumbnail = input("Thumbnail path (optional): ").strip()
            scheduled_time = input("Scheduled time (YYYY-MM-DDTHH:MM:SS, optional): ").strip()
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)

        # Write this entry with posted values as False first so subprocesses find it
        new_entry = {
            "video_path": str(video_path),
            "title": title,
            "description": description,
            "thumbnail": thumbnail,
            "tags": tags,
            "scheduled_time": scheduled_time,
            "added_at": datetime.datetime.now().isoformat(),
            "posted": {
                "youtube": False,
                "instagram": False,
                "tiktok": False
            }
        }
        shorts_data.append(new_entry)
        try:
            with open(shorts_json_path, "w", encoding="utf-8") as f:
                json.dump(shorts_data, f, indent=2, ensure_ascii=False)
            print("Temporary metadata saved to shorts.json.")
        except Exception as e:
            print(f"Error saving to shorts.json: {e}", file=sys.stderr)
            sys.exit(1)

    # Map platforms to their respective upload scripts
    platform_scripts = {
        "youtube": repo_root / "youtube_api" / "upload_short.py",
        "instagram": repo_root / "instagram" / "upload_reel.py",
        "tiktok": repo_root / "tiktok" / "upload_tiktok.py"
    }

    # Determine which platforms to run
    if args.platform == "all":
        platforms_to_run = ["youtube", "instagram", "tiktok"]
    else:
        platforms_to_run = [args.platform]

    failed_platforms = []
    successful_platforms = []
    
    for plat in platforms_to_run:
        script_path = platform_scripts[plat]
        if not script_path.exists():
            print(f"Error: Script for platform '{plat}' not found at '{script_path}'", file=sys.stderr)
            failed_platforms.append(plat)
            continue

        print(f"\n========================================================")
        print(f"🚀 Uploading to {plat.upper()}...")
        print(f"Executing: {sys.executable} {script_path.name} {video_path.name}")
        print(f"========================================================\n")

        try:
            # Execute the platform-specific script using the current Python environment
            result = subprocess.run(
                [sys.executable, str(script_path), str(video_path)],
                check=False
            )
            
            if result.returncode != 0:
                print(f"\n❌ Upload to {plat.upper()} failed with exit code {result.returncode}.", file=sys.stderr)
                failed_platforms.append(plat)
            else:
                print(f"\n✅ Upload to {plat.upper()} succeeded!")
                successful_platforms.append(plat)
                
        except Exception as e:
            print(f"\n❌ Failed to run upload script for {plat.upper()}: {e}", file=sys.stderr)
            failed_platforms.append(plat)

    # If new short, update shorts.json with successful upload status and final metadata
    if is_new_short:
        print("\nUpdating shorts.json with successful upload status...")
        try:
            # Re-read shorts.json to avoid overwriting changes from other scripts
            if shorts_json_path.exists():
                with open(shorts_json_path, "r", encoding="utf-8") as f:
                    final_data = json.load(f)
                    if not isinstance(final_data, list):
                        final_data = []
            else:
                final_data = []

            # Find our entry again
            final_entry = find_metadata_entry(final_data, video_path_resolved, video_name, video_stem)
            if final_entry:
                final_entry["title"] = title
                final_entry["description"] = description
                final_entry["thumbnail"] = thumbnail
                final_entry["tags"] = tags
                final_entry["scheduled_time"] = scheduled_time
                if "posted" not in final_entry or not isinstance(final_entry["posted"], dict):
                    final_entry["posted"] = {"youtube": False, "instagram": False, "tiktok": False}
                for plat in successful_platforms:
                    final_entry["posted"][plat] = True
            else:
                # Recreate if missing
                final_entry = {
                    "video_path": str(video_path),
                    "title": title,
                    "description": description,
                    "thumbnail": thumbnail,
                    "tags": tags,
                    "scheduled_time": scheduled_time,
                    "added_at": datetime.datetime.now().isoformat(),
                    "posted": {
                        "youtube": False,
                        "instagram": False,
                        "tiktok": False
                    }
                }
                for plat in successful_platforms:
                    final_entry["posted"][plat] = True
                final_data.append(final_entry)

            with open(shorts_json_path, "w", encoding="utf-8") as f:
                json.dump(final_data, f, indent=2, ensure_ascii=False)
            print("Successfully updated shorts.json with new Short metadata and upload status.")
        except Exception as e:
            print(f"Warning: Failed to update shorts.json: {e}", file=sys.stderr)

    print(f"\n========================================================")
    if failed_platforms:
        print(f"Finished with errors. Failed platforms: {', '.join(failed_platforms).upper()}")
        sys.exit(1)
    else:
        print("All requested uploads completed successfully!")
    print(f"========================================================\n")

if __name__ == "__main__":
    main()
