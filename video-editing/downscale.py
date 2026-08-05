#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
from pathlib import Path

video_editing_dir = Path(__file__).resolve().parent
if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from utils import resolve_output_path

def downscale_video(input_path: Path, output_path: Path):
    if not input_path.is_file():
        print(f"Error: Input video file not found at '{input_path}'", file=sys.stderr)
        sys.exit(1)

    print(f"Downscaling video: {input_path}")
    print(f"Output video path: {output_path}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-vf", "scale=1920:1080:flags=lanczos",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "12",
        "-c:a", "aac",
        str(output_path)
    ]

    try:
        subprocess.run(cmd, check=True)
        print(f"Successfully downscaled video to: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error executing ffmpeg command: {e}", file=sys.stderr)
        sys.exit(e.returncode)

def main():
    parser = argparse.ArgumentParser(
        description="Downscale a video file to 1080p resolution using FFmpeg."
    )
    parser.add_argument(
        "video_path",
        help="Path to the input video file."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the downscaled output video (default: <input_basename>-1080.<ext> in the input file's directory)."
    )

    args = parser.parse_args()

    input_path = Path(args.video_path).resolve()
    output_path = resolve_output_path(input_path, args.output, "-1080")

    downscale_video(input_path, output_path)

if __name__ == "__main__":
    main()
