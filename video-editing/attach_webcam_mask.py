#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path

def check_dependencies():
    """Verify that ffmpeg and ffprobe are available in the system PATH."""
    for cmd in ["ffmpeg", "ffprobe"]:
        if not shutil.which(cmd):
            print(f"Error: Required tool '{cmd}' is not installed or not in system PATH.", file=sys.stderr)
            sys.exit(1)

def get_video_duration(video_path):
    """Determine the duration of a video file in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error: Could not determine video duration for '{video_path}': {e}", file=sys.stderr)
        return None

def has_audio_stream(video_path):
    """Check if a video file contains at least one audio stream using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return "audio" in result.stdout.lower()
    except Exception as e:
        print(f"Warning: Could not check audio streams for '{video_path}': {e}", file=sys.stderr)
        return False

def get_video_fps(video_path):
    """Determine the frame rate of a video file using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,avg_frame_rate",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if "streams" in data and len(data["streams"]) > 0:
            stream = data["streams"][0]
            for fps_key in ["r_frame_rate", "avg_frame_rate"]:
                val = stream.get(fps_key, "")
                if val and "/" in val:
                    num, den = val.split("/")
                    if float(den) > 0:
                        parsed = float(num) / float(den)
                        if 5 <= parsed <= 120:
                            return parsed
        return 30.0
    except Exception:
        return 30.0

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
        default=400,
        help="Width of the webcam overlay in pixels (default: 400)."
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
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = screen_path.parent / f"{screen_path.stem}_webcam.mp4"

    if output_path.suffix.lower() == ".webm":
        print("⚠️ WebM output container is not compatible with H.264 video encoding. Using .mp4 container instead.")
        output_path = output_path.with_suffix(".mp4")

    check_dependencies()

    # Retrieve webcam duration as the target output duration
    print(f"🔍 Probing webcam video duration...")
    webcam_duration = get_video_duration(webcam_path)
    if webcam_duration is None:
        print("Error: Could not retrieve webcam duration.", file=sys.stderr)
        sys.exit(1)
    print(f"🎬 Webcam duration: {webcam_duration:.3f} seconds.")

    # Target frame rate for smooth overlay output
    webcam_fps = get_video_fps(webcam_path)
    screen_fps = get_video_fps(screen_path)
    target_fps = int(round(max(webcam_fps, screen_fps)))
    target_fps = max(24, min(60, target_fps))
    print(f"🎞️  Target output frame rate: {target_fps} FPS")

    # Determine audio streams
    has_webcam_audio = has_audio_stream(webcam_path)
    has_screen_audio = has_audio_stream(screen_path)

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
        f"[0:v]setpts=PTS-STARTPTS,fps=fps={target_fps},tpad=stop_mode=clone:stop=-1[bg];"
        f"[1:v]setpts=PTS-STARTPTS,fps=fps={target_fps},scale=w={w}:h=-2,format=rgba[scaled_webcam];"
        f"[scaled_webcam]split[w1][w2];"
        f"[w2]format=gray,geq=lum='{geq_expr}'[mask];"
        f"[w1][mask]alphamerge[masked_webcam];"
        f"[bg][masked_webcam]overlay=x=W-w-{offset}:y={offset}:eof_action=pass[out_v]"
    )

    # Construct the full FFmpeg command
    cmd = [
        "ffmpeg", "-y",
        "-i", str(screen_path),
        "-i", str(webcam_path),
        "-filter_complex", filter_complex,
        "-map", "[out_v]"
    ] + audio_map + [
        "-t", f"{webcam_duration:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "18",
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
