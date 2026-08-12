#!/usr/bin/env python3
"""
Pipeline Status, Validation, and Terminal UI Overview.
"""

import sys
import json
import subprocess
import argparse
from pathlib import Path

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from utils import (
    COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_BLUE, COLOR_RESET, COLOR_BOLD,
    print_info, print_success, print_warning, print_error,
    get_video_info
)


from urllib.parse import urlparse, parse_qs


def is_valid_yt_url(url: str) -> bool:
    """Check if a URL string is a valid YouTube URL (fast, no HTTP requests)."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        valid_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
        if hostname not in valid_hosts:
            return False

        if hostname in ("youtu.be", "www.youtu.be"):
            path = parsed.path.strip("/")
            return len(path) > 0
        else:
            if parsed.path == "/watch":
                qs = parse_qs(parsed.query)
                v_param = qs.get("v", [""])[0]
                return len(v_param) > 0
            elif parsed.path.startswith(("/embed/", "/v/", "/shorts/", "/live/")):
                parts = [p for p in parsed.path.split("/") if p]
                return len(parts) >= 2
            elif len(parsed.path.strip("/")) > 0:
                return True
        return False
    except Exception:
        return False


def is_4k_video(video_path: Path) -> bool:
    """Check if the video resolution is 4K (e.g., width >= 3840 or height >= 2160)."""
    info = get_video_info(video_path)
    return info.width >= 3840 or info.height >= 2160 or max(info.width, info.height) >= 3840 or min(info.width, info.height) >= 2160


def is_valid_video_file(video_path: Path) -> bool:
    """Check if a video file exists, is non-empty, and can be read by ffprobe."""
    if not video_path.is_file() or video_path.stat().st_size == 0:
        return False
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        return duration > 0
    except Exception:
        return False


def is_valid_file(path: Path) -> bool:
    """Check if an output file exists and is valid."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    ext = path.suffix.lower()
    if ext in [".mp4", ".mov", ".mkv", ".avi", ".webm"]:
        return is_valid_video_file(path)
    return True


def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    return f"{minutes}m {rem_seconds:.2f}s"


def get_pipeline_outputs(webcam_path: Path, video_dir: Path, bgm: str | None) -> dict:
    """Compute and return step output file paths given webcam input and options."""
    ext = webcam_path.suffix or ".mp4"
    step4_needed = bool(bgm)
    return {
        "step1_srt": video_dir / f"{webcam_path.stem}.srt",
        "step1_1word_srt": video_dir / f"{webcam_path.stem}-1word.srt",
        "step2_output": video_dir / "after-trim-silences.mp4",
        "step3_output": video_dir / "after-audio-processing.mp4",
        "step4_needed": step4_needed,
        "step4_output": video_dir / "after-audio-processing-bgm.mp4",
        "final_output": video_dir / f"to-review{ext}",
    }


def compute_pipeline_status(outputs: dict, force: bool) -> tuple[dict[int, bool], dict[int, bool]]:
    """Determine step completion statuses and execution plan."""
    step1_complete = is_valid_file(outputs["step1_1word_srt"]) and is_valid_file(outputs["step1_srt"])
    step2_complete = is_valid_file(outputs["step2_output"])
    step3_complete = is_valid_file(outputs["step3_output"])
    step4_complete = is_valid_file(outputs["step4_output"]) if outputs["step4_needed"] else True

    latest_output = outputs["step4_output"] if outputs["step4_needed"] else outputs["step3_output"]
    step5_complete = is_valid_file(outputs["final_output"]) and (
        not is_valid_file(latest_output) or outputs["final_output"].stat().st_mtime >= latest_output.stat().st_mtime
    )

    statuses = {
        1: step1_complete,
        2: step2_complete,
        3: step3_complete,
        4: step4_complete,
        5: step5_complete,
    }

    if force:
        run_plan = {1: True, 2: True, 3: True, 4: outputs["step4_needed"], 5: True}
    else:
        run1 = not step1_complete
        run2 = run1 or not step2_complete
        run3 = run2 or not step3_complete
        run4 = outputs["step4_needed"] and (run3 or not step4_complete)
        run5 = (run4 if outputs["step4_needed"] else run3) or not step5_complete
        run_plan = {1: run1, 2: run2, 3: run3, 4: run4, 5: run5}

    return statuses, run_plan


def format_status(needed: bool, will_run: bool, is_complete: bool, force: bool, skip_reason: str = None) -> str:
    """Return color-formatted status string for display in summary."""
    if not needed:
        reason = f" ({skip_reason})" if skip_reason else ""
        return f"{COLOR_BLUE}[SKIPPED{reason}]{COLOR_RESET}"
    if force:
        return f"{COLOR_YELLOW}[WILL EXECUTE (FORCED)]{COLOR_RESET}"
    if not will_run and is_complete:
        return f"{COLOR_GREEN}[ALREADY COMPLETE - SKIPPING]{COLOR_RESET}"
    return f"{COLOR_YELLOW}[WILL EXECUTE]{COLOR_RESET}"


def print_pipeline_overview(
    webcam_path: Path,
    screen_path: Path,
    video_dir: Path,
    args: argparse.Namespace,
    webcam_is_4k: bool,
    statuses: dict[int, bool],
    run_plan: dict[int, bool],
    ext: str,
) -> None:
    """Display the pipeline execution overview header and step statuses."""
    print_info("============================================================")
    print_info("            FULL VIDEO PROCESSING PIPELINE")
    print_info("============================================================")
    print(f" Web camera video: {webcam_path}")
    print(f" Screen video:     {screen_path}")
    print(f" Output directory: {video_dir}")
    overlay_width = args.width if getattr(args, "width", None) is not None else (400 if getattr(args, "preset", "portrait") == "portrait" else 550)
    print(f" Overlay width:    {overlay_width}px")
    print(f" Overlay mode:     {'Continuous (--all)' if args.all else 'Dynamic voice commands'}")
    if args.bgm:
        print(f" BGM track:        {args.bgm} (Volume: {args.volume}%)")
    else:
        print_warning(" BGM track:        None specified (Step 4 will be skipped)")
    if webcam_is_4k:
        print_warning(" 4K Webcam:        Detected 4K resolution (single-pass downscaled to 1080p during processing)")
    if args.force:
        print_warning(" Force re-run:     --force option enabled (re-executing all steps)")

    print_info("------------------------------------------------------------")
    print_info("Pipeline Steps Overview:")

    s1_str = format_status(True, run_plan[1], statuses[1], args.force)
    s2_str = format_status(True, run_plan[2], statuses[2], args.force)
    s3_str = format_status(True, run_plan[3], statuses[3], args.force)
    s4_str = format_status(bool(args.bgm), run_plan[4], statuses[4], args.force, "No BGM specified")
    s5_str = format_status(True, run_plan[5], statuses[5], args.force)

    print(f" 1. Transcribe video via Groq cloud (transcribe_cloud.py)    {s1_str}")
    print(f" 2. Single-pass Masking & Silence Trimming (FFmpeg combined) {s2_str}")
    print(f" 3. Process audio via process_audio.sh                       {s3_str}")
    print(f" 4. Add background music (add_bgm_to_video.sh)               {s4_str}")
    print(f" 5. Rename final video file to 'to-review{ext}'               {s5_str}")
    print_info("============================================================")


def confirm_execution(skip_confirm: bool) -> None:
    """Prompt the user for confirmation unless skip_confirm (-y) is set."""
    if not skip_confirm:
        try:
            user_input = input("Proceed with video processing? [Y/n]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print_error("Processing cancelled.")
            sys.exit(1)

        if user_input and user_input.lower().startswith("n"):
            print_warning("Processing cancelled by user.")
            sys.exit(0)
