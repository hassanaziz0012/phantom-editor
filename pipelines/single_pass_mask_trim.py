#!/usr/bin/env python3
"""
Single-Pass Masking and Silence Trimming Processor.
Combines webcam mask overlay generation and silence trimming in a single FFmpeg pass.
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
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
from auto_attach_webcam_mask import (
    parse_srt, detect_overlay_ranges, get_timeline_segments,
    generate_mask_png, build_webcam_mask_filter_string
)
from trim_silences import get_speech_intervals, get_silence_trim_expressions


def run_single_pass_mask_trim(
    webcam_path: Path,
    screen_path: Path,
    step1_1word_srt: Path,
    step2_output: Path,
    preset: str = "portrait",
    width: int | None = None,
    all_overlay: bool = False,
    video_dir: Path | None = None,
    force_run: bool = False,
    skip_confirm: bool = False
) -> bool:
    """Step 2: Single-Pass Video Processing (Masking + Silence Trimming)."""
    if video_dir is None:
        video_dir = step2_output.parent

    print_info("\n--- [Step 2/5] Single-Pass Video Processing (Masking + Silence Trimming) ---")
    if not force_run and step2_output.is_file() and step2_output.stat().st_size > 0:
        cmd_check = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(step2_output)
        ]
        res = subprocess.run(cmd_check, capture_output=True, text=True)
        if res.returncode == 0:
            try:
                dur = float(json.loads(res.stdout).get("format", {}).get("duration", 0))
                if dur > 0:
                    print_success(f"[SKIP] Step 2 complete: Single-pass video file already exists -> {step2_output.name}")
                    return force_run
            except Exception:
                pass

    print_info("Analyzing audio and subtitle voice commands for single-pass processing...")

    webcam_info = get_video_info(webcam_path)
    screen_info = get_video_info(screen_path)
    webcam_duration = webcam_info.duration
    if not webcam_duration or webcam_duration <= 0:
        print_error("Error: Could not retrieve webcam duration.")
        sys.exit(1)

    webcam_w, webcam_h = webcam_info.width, webcam_info.height
    screen_w = screen_info.width if screen_info.width > 0 else 1920
    screen_h = screen_info.height if screen_info.height > 0 else 1080
    target_fps = int(round(max(webcam_info.fps, screen_info.fps)))
    target_fps = max(24, min(60, target_fps))

    if webcam_info.has_audio:
        audio_src = "1:a"
    elif screen_info.has_audio:
        audio_src = "0:a"
    else:
        audio_src = None

    try:
        captions = parse_srt(step1_1word_srt)
    except Exception as e:
        print_error(f"Error parsing SRT file: {e}")
        sys.exit(1)

    commands_count = 0
    if all_overlay:
        overlay_ranges = [(0.0, webcam_duration)]
    elif not captions:
        print_warning("⚠️ Captions file contains no valid subtitle intervals.")
        overlay_ranges = []
    else:
        overlay_ranges, commands_count = detect_overlay_ranges(captions, default_overlay=False, total_duration=webcam_duration)

    if not all_overlay and commands_count == 0:
        print_warning("\n" + "=" * 60)
        print_warning("⚠️  No voice commands ('webcam start' / 'webcam stop') detected.")
        print_warning("=" * 60)
        print("Please choose how to render the video:")
        print("  1) screen : Overlay webcam in the top-right corner over the screen recording (entire video)")
        print("  2) webcam : Full-screen webcam video only (no screen recording displayed)")
        print()

        if skip_confirm or not sys.stdin.isatty():
            print_info("Notice: Non-interactive / --yes mode. Defaulting to option 1 ('screen').")
            choice = "screen"
        else:
            try:
                user_input = input("Select layout option [1=screen / 2=webcam] (default: screen): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print()
                print_error("Processing cancelled by user.")
                sys.exit(1)

            if user_input in ["2", "webcam", "w"]:
                choice = "webcam"
            else:
                choice = "screen"

        if choice == "webcam":
            print_info("⏩ Selected 'webcam': Using full-screen webcam video (no screen overlay).")
            overlay_ranges = []
        else:
            print_info("⏩ Selected 'screen': Overlaying top-right webcam over screen recording throughout entire video.")
            overlay_ranges = [(0.0, webcam_duration)]

    segments = get_timeline_segments(overlay_ranges, webcam_duration)

    speech_intervals = get_speech_intervals(webcam_path)
    select_expr, shift_expr, total_speech_duration = get_silence_trim_expressions(speech_intervals)

    overlay_w = width if width is not None else (400 if preset == "portrait" else 550)
    scaled_h = int(round((overlay_w * webcam_h / webcam_w) / 2) * 2)
    temp_dir = video_dir / f"_tmp_{webcam_path.stem}_singlepass"
    temp_dir.mkdir(parents=True, exist_ok=True)
    mask_path = temp_dir / "webcam_mask.png"
    generate_mask_png(mask_path, overlay_w, scaled_h, r=20)

    mask_filter = build_webcam_mask_filter_string(
        segments=segments,
        target_fps=target_fps,
        w=overlay_w,
        scaled_h=scaled_h,
        offset=20,
        screen_w=screen_w,
        screen_h=screen_h,
        is_all=all_overlay,
        output_label="composite_v"
    )

    if select_expr and shift_expr:
        v_trim = f"[composite_v]select='{select_expr}',setpts='(T-({shift_expr}))/TB',fps=30[out_v]"
        if audio_src:
            a_trim = f"[{audio_src}]aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB',aresample=async=1:first_pts=0[out_a]"
            filter_complex = f"{mask_filter};{v_trim};{a_trim}"
            audio_map = ["-map", "[out_a]", "-c:a", "aac", "-b:a", "384k"]
        else:
            filter_complex = f"{mask_filter};{v_trim}"
            audio_map = []
        final_duration = total_speech_duration
    else:
        filter_complex = f"{mask_filter};[composite_v]fps=30[out_v]"
        if audio_src:
            audio_map = ["-map", audio_src, "-c:a", "aac"]
        else:
            audio_map = []
        final_duration = webcam_duration

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-threads", "0",
        "-i", str(screen_path),
        "-i", str(webcam_path),
        "-loop", "1", "-i", str(mask_path),
        "-filter_complex", filter_complex,
        "-map", "[out_v]"
    ] + audio_map + [
        "-fps_mode", "cfr",
        "-t", f"{final_duration:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "20",
        "-movflags", "+faststart",
        str(step2_output)
    ]

    print_info("\n🚀 Executing single-pass video encoding (Mask + Silence Trim)...")
    print(f"Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Single-pass video encoding failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    finally:
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    if not step2_output.is_file() or step2_output.stat().st_size == 0:
        print_error(f"[ERROR] Single-pass output file invalid or missing at '{step2_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 2 complete: Single-pass video created -> {step2_output.name}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Single-pass webcam mask auto-attachment and silence trimming processor."
    )
    parser.add_argument("webcam", help="Path to webcam video file.")
    parser.add_argument("screen", nargs="?", default=None, help="Path to screen video file (defaults to webcam).")
    parser.add_argument("--srt", required=True, help="Path to 1-word SRT captions file.")
    parser.add_argument("--output", "-o", required=True, help="Path for output MP4 file.")
    parser.add_argument("--preset", choices=["portrait", "landscape"], default="portrait", help="Preset overlay mode.")
    parser.add_argument("--width", "-w", type=int, default=None, help="Width of webcam overlay in pixels.")
    parser.add_argument("--all", "-a", action="store_true", help="Attach webcam mask throughout the entire video.")
    parser.add_argument("-f", "--force", action="store_true", help="Force re-run.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip non-interactive prompt.")

    args = parser.parse_args()

    webcam_path = Path(args.webcam).resolve()
    screen_path = Path(args.screen).resolve() if args.screen else webcam_path
    srt_path = Path(args.srt).resolve()
    output_path = Path(args.output).resolve()

    run_single_pass_mask_trim(
        webcam_path=webcam_path,
        screen_path=screen_path,
        step1_1word_srt=srt_path,
        step2_output=output_path,
        preset=args.preset,
        width=args.width,
        all_overlay=args.all,
        force_run=args.force,
        skip_confirm=args.yes
    )


if __name__ == "__main__":
    main()
