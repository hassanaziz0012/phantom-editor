#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path

video_editing_dir = Path(__file__).resolve().parent
if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from utils import check_dependencies, get_video_info, resolve_output_path

def main():
    parser = argparse.ArgumentParser(
        description="Overlay webcam video in the top-right corner of screen footage with rounded corners."
    )
    parser.add_argument(
        "--screen",
        type=str,
        required=True,
        help="Path to the screen recording video file."
    )
    parser.add_argument(
        "--webcam",
        type=str,
        required=True,
        help="Path to the webcam recording video file."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Path to save the output video file (default: [screen_basename]_webcam.mp4)."
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=600,
        help="Width of the webcam overlay in pixels (default: 600)."
    )
    parser.add_argument(
        "--radius", "-r",
        type=int,
        default=20,
        help="Corner radius for the webcam overlay rounded rectangle in pixels (default: 20)."
    )
    parser.add_argument(
        "--offset", "-d",
        type=int,
        default=20,
        help="Margin/offset from the top-right corner in pixels (default: 20)."
    )

    args = parser.parse_args()

    # Verify input files exist
    screen_path = Path(args.screen).resolve()
    webcam_path = Path(args.webcam).resolve()

    if not screen_path.is_file():
        print(f"Error: Screen recording file not found at '{args.screen}'", file=sys.stderr)
        sys.exit(1)
    if not webcam_path.is_file():
        print(f"Error: Webcam recording file not found at '{args.webcam}'", file=sys.stderr)
        sys.exit(1)

    # Resolve output path
    output_path = resolve_output_path(screen_path, args.output, "_webcam")

    check_dependencies()

    # Probe webcam and screen video info
    print(f"🔍 Probing webcam video duration...")
    webcam_info = get_video_info(webcam_path)
    screen_info = get_video_info(screen_path)

    webcam_duration = webcam_info.duration
    if not webcam_duration or webcam_duration <= 0:
        print("Error: Could not retrieve webcam duration.", file=sys.stderr)
        sys.exit(1)
    print(f"🎬 Webcam duration: {webcam_duration:.3f} seconds.")

    # Target frame rate for smooth overlay output
    webcam_fps = webcam_info.fps
    screen_fps = screen_info.fps
    target_fps = int(round(max(webcam_fps, screen_fps)))
    target_fps = max(24, min(60, target_fps))
    print(f"🎞️  Target output frame rate: {target_fps} FPS")

    # Determine audio streams
    has_webcam_audio = webcam_info.has_audio
    has_screen_audio = screen_info.has_audio

    audio_map = []
    if has_webcam_audio:
        print("🎵 Using webcam audio track.")
        audio_map = ["-map", "1:a"]
    elif has_screen_audio:
        print("⚠️ Webcam video has no audio. Falling back to screen audio.")
        audio_map = ["-map", "0:a"]
    else:
        print("ℹ️ Neither webcam nor screen video contains audio. Output will be video-only.")

    # Build the complex filtergraph
    # 1. Reset PTS and set target constant frame rate for screen background, padding indefinitely using tpad
    # 2. Reset PTS, set target constant frame rate for webcam, and scale to target width (even height)
    # 3. Split webcam video to generate alpha mask via geq filter on grayscale
    # 4. Merge color and mask using alphamerge
    # 5. Overlay on background at top-right corner with offset
    r = args.radius
    w = args.width
    offset = args.offset

    geq_expr = (
        f"if((lt(X,{r})+gt(X,W-{r}))*(lt(Y,{r})+gt(Y,H-{r})),"
        f"if(gt(sqrt(pow(X-if(lt(X,{r}),{r},W-{r}),2)+pow(Y-if(lt(Y,{r}),{r},H-{r}),2)),{r}),0,255),255)"
    )

    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,fps=fps={target_fps}[bg];"
        f"[1:v]setpts=PTS-STARTPTS,fps=fps={target_fps},scale=w={w}:h=-2,format=rgba[scaled_webcam];"
        f"[scaled_webcam]split[w1][w2];"
        f"[w2]format=gray,geq=lum='{geq_expr}'[mask];"
        f"[w1][mask]alphamerge[masked_webcam];"
        f"[bg][masked_webcam]overlay=x=W-w-{offset}:y={offset}:eof_action=pass[out_v]"
    )

    # Construct the full FFmpeg command
    cmd = [
        "ffmpeg", "-y",
        "-threads", "0",
        "-i", str(screen_path),
        "-i", str(webcam_path),
        "-filter_complex", filter_complex,
        "-map", "[out_v]"
    ] + audio_map + [
        "-t", f"{webcam_duration:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "20",
        "-movflags", "+faststart",
        "-c:a", "aac",
        str(output_path)
    ]

    print("\n🚀 Executing FFmpeg command to attach webcam overlay...")
    print(f"Command: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd, check=True)
        print(f"🎉 Success! Output video saved to: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error: FFmpeg failed with exit code {e.returncode}.", file=sys.stderr)
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
