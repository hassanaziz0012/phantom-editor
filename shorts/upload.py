#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
from pathlib import Path

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
                
        except Exception as e:
            print(f"\n❌ Failed to run upload script for {plat.upper()}: {e}", file=sys.stderr)
            failed_platforms.append(plat)

    print(f"\n========================================================")
    if failed_platforms:
        print(f"Finished with errors. Failed platforms: {', '.join(failed_platforms).upper()}")
        sys.exit(1)
    else:
        print("All requested uploads completed successfully!")
    print(f"========================================================\n")

if __name__ == "__main__":
    main()
