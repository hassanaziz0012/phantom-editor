#!/usr/bin/env python3
import os
import sys
import json
import shutil
import time
import argparse
import subprocess
from pathlib import Path

video_editing_dir = Path(__file__).resolve().parent
if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from utils import (
    COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_BLUE, COLOR_RESET, COLOR_BOLD,
    print_info, print_success, print_warning, print_error,
    get_video_info
)
from auto_attach_webcam_mask import parse_srt, detect_overlay_ranges, get_timeline_segments, generate_mask_png, build_webcam_mask_filter_string
from trim_silences import get_speech_intervals, get_silence_trim_expressions

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


def parse_cli_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Full video processing pipeline executing Groq cloud transcription, webcam mask auto-attachment, "
                    "audio processing, silence trimming, BGM addition, and final review file renaming."
    )
    parser.add_argument(
        "webcam",
        nargs="?",
        default=None,
        help="Path to webcam / main video file."
    )
    parser.add_argument(
        "screen",
        nargs="?",
        default=None,
        help="Path to screen recording video file (optional; defaults to webcam video)."
    )
    parser.add_argument(
        "--webcam", "-w_file",
        dest="webcam_flag",
        default=None,
        help="Explicit path to webcam video file."
    )
    parser.add_argument(
        "--screen", "-s_file",
        dest="screen_flag",
        default=None,
        help="Explicit path to screen recording video file."
    )
    parser.add_argument(
        "--preset",
        type=str,
        choices=["portrait", "landscape"],
        default="portrait",
        help="Preset orientation for webcam overlay: 'portrait' (default) or 'landscape'."
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=None,
        help="Width of webcam overlay in pixels for auto-attach webcam mask (default: 400 for portrait, 550 for landscape)."
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Attach webcam mask throughout the entire video, skipping word command detection."
    )
    parser.add_argument(
        "--bgm", "--bgm-track",
        dest="bgm",
        default=None,
        help="BGM track file path or track name in BGM directory for background music step."
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=10,
        help="Volume percentage for BGM (1-100, default: 10)."
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt and proceed automatically."
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force re-execution of all pipeline steps, ignoring existing step files."
    )

    args = parser.parse_args()

    webcam_input = args.webcam_flag or args.webcam
    if not webcam_input:
        print_error("Error: Input video file (webcam video) is required.")
        parser.print_help()
        sys.exit(1)

    return args


def validate_pipeline_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Validate input video files and return resolved paths for (webcam, screen, video_dir)."""
    webcam_input = args.webcam_flag or args.webcam
    screen_input = args.screen_flag or args.screen

    webcam_path = Path(webcam_input).resolve()
    if not webcam_path.is_file():
        print_error(f"Error: Webcam video file not found at '{webcam_path}'")
        sys.exit(1)

    if screen_input:
        screen_path = Path(screen_input).resolve()
        if not screen_path.is_file():
            print_error(f"Error: Screen video file not found at '{screen_path}'")
            sys.exit(1)
    else:
        screen_path = webcam_path

    video_dir = webcam_path.parent
    return webcam_path, screen_path, video_dir


def verify_script_dependencies(script_dir: Path, repo_root: Path) -> dict[str, Path]:
    """Verify all required scripts exist and return a dictionary of their paths."""
    scripts = {
        "downscale_py": script_dir / "downscale.py",
        "auto_attach_webcam_py": script_dir / "auto_attach_webcam_mask.py",
        "process_audio_sh": repo_root / "audio-processing" / "process_audio.sh",
        "transcribe_cloud_py": script_dir / "transcribe_cloud.py",
        "trim_silences_py": script_dir / "trim_silences.py",
        "add_bgm_sh": script_dir / "add_bgm_to_video.sh",
    }

    for name, script_path in scripts.items():
        if not script_path.is_file():
            print_error(f"Error: Required pipeline script not found at '{script_path}'")
            sys.exit(1)

    return scripts


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
    print(f" Overlay width:    {args.width}px")
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


def run_step1_transcription(
    webcam_path: Path,
    step1_srt_output: Path,
    step1_1word_srt: Path,
    transcribe_cloud_py: Path,
    force_run: bool
) -> bool:
    """Step 1: Transcribe Video using Groq Cloud."""
    print_info("\n--- [Step 1/5] Transcribing Video using Groq Cloud ---")
    if not force_run and is_valid_file(step1_1word_srt) and is_valid_file(step1_srt_output):
        print_success(f"[SKIP] Step 1 complete: Transcribed SRT file already exists -> {step1_1word_srt.name}")
        return force_run

    cmd_step1 = [
        sys.executable,
        str(transcribe_cloud_py),
        str(webcam_path),
        "--output", str(step1_srt_output)
    ]
    print(f"Executing: {' '.join(cmd_step1)}")
    try:
        subprocess.run(cmd_step1, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 1 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(step1_1word_srt):
        print_error(f"[ERROR] Step 1 output 1word SRT file invalid or missing at '{step1_1word_srt}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 1 complete: Transcribed video -> {step1_1word_srt.name}")
    return True


def run_step2_mask_and_trim(
    webcam_path: Path,
    screen_path: Path,
    step1_1word_srt: Path,
    step2_output: Path,
    preset: str,
    width: int | None,
    all_overlay: bool,
    video_dir: Path,
    force_run: bool,
    skip_confirm: bool = False
) -> bool:
    """Step 2: Single-Pass Video Processing (Masking + Silence Trimming)."""
    print_info("\n--- [Step 2/5] Single-Pass Video Processing (Masking + Silence Trimming) ---")
    if not force_run and is_valid_file(step2_output):
        print_success(f"[SKIP] Step 2 complete: Single-pass video file already exists -> {step2_output.name}")
        return force_run

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
            a_trim = f"[{audio_src}]aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB'[out_a]"
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

    if not is_valid_file(step2_output):
        print_error(f"[ERROR] Single-pass output file invalid or missing at '{step2_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 2 complete: Single-pass video created -> {step2_output.name}")
    return True


def run_step3_process_audio(
    step2_output: Path,
    step3_output: Path,
    process_audio_sh: Path,
    force_run: bool
) -> bool:
    """Step 3: Process Audio."""
    print_info("\n--- [Step 3/5] Processing Audio ---")
    if not force_run and is_valid_file(step3_output):
        print_success(f"[SKIP] Step 3 complete: Audio processed video file already exists -> {step3_output.name}")
        return force_run

    cmd_step3 = ["bash", str(process_audio_sh), str(step2_output)]
    print(f"Executing: {' '.join(cmd_step3)}")
    try:
        subprocess.run(cmd_step3, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 3 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(step3_output):
        print_error(f"[ERROR] Step 3 output file invalid or missing at '{step3_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 3 complete: Audio processed -> {step3_output.name}")
    return True


def run_step4_add_bgm(
    step3_output: Path,
    step4_output: Path,
    bgm: str | None,
    volume: int,
    add_bgm_sh: Path,
    force_run: bool
) -> tuple[Path, bool]:
    """Step 4: Add Background Music."""
    print_info("\n--- [Step 4/5] Adding Background Music ---")
    if not bgm:
        print_warning("[WARNING] Step 4 skipped: No BGM track specified with --bgm.")
        return step3_output, force_run

    if not force_run and is_valid_file(step4_output):
        print_success(f"[SKIP] Step 4 complete: BGM video file already exists -> {step4_output.name}")
        return step4_output, force_run

    cmd_step4 = [
        "bash",
        str(add_bgm_sh),
        str(step3_output),
        bgm,
        "--volume", str(volume)
    ]
    print(f"Executing: {' '.join(cmd_step4)}")
    try:
        subprocess.run(cmd_step4, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 4 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(step4_output):
        print_error(f"[ERROR] Step 4 output file invalid or missing at '{step4_output}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 4 complete: Added BGM -> {step4_output.name}")
    return step4_output, True


def run_step5_finalize(
    current_latest_video: Path,
    final_output: Path,
    force_run: bool
) -> None:
    """Step 5: Finalize Output File Name."""
    print_info("\n--- [Step 5/5] Finalizing Output File Name ---")
    if not force_run and is_valid_file(final_output) and (not is_valid_file(current_latest_video) or final_output.stat().st_mtime >= current_latest_video.stat().st_mtime):
        print_success(f"[SKIP] Step 5 complete: Final review video file already exists -> {final_output.name}")
    else:
        if final_output.exists():
            print_warning(f"Overwriting existing output file: {final_output.name}")
            try:
                final_output.unlink()
            except Exception as e:
                print_error(f"Error removing existing file '{final_output}': {e}")

        shutil.copy2(str(current_latest_video), str(final_output))
        print_success(f"[SUCCESS] Step 5 complete: Copied final video file to -> {final_output.name}")


def main():
    args = parse_cli_args()
    webcam_path, screen_path, video_dir = validate_pipeline_inputs(args)

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    scripts = verify_script_dependencies(script_dir, repo_root)

    webcam_is_4k = is_4k_video(webcam_path)
    outputs = get_pipeline_outputs(webcam_path, video_dir, args.bgm)
    statuses, run_plan = compute_pipeline_status(outputs, args.force)

    ext = webcam_path.suffix or ".mp4"
    print_pipeline_overview(
        webcam_path, screen_path, video_dir, args, webcam_is_4k, statuses, run_plan, ext
    )

    confirm_execution(args.yes)

    print()
    print_info("Starting video processing pipeline...")

    pipeline_start_time = time.perf_counter()
    force_run = args.force

    # Step 1: Transcribe Video using Groq Cloud
    step1_start = time.perf_counter()
    force_run = run_step1_transcription(
        webcam_path=webcam_path,
        step1_srt_output=outputs["step1_srt"],
        step1_1word_srt=outputs["step1_1word_srt"],
        transcribe_cloud_py=scripts["transcribe_cloud_py"],
        force_run=force_run
    )
    step1_duration = time.perf_counter() - step1_start

    # Step 2: Single-Pass Video Processing (Masking + Silence Trimming)
    step2_start = time.perf_counter()
    force_run = run_step2_mask_and_trim(
        webcam_path=webcam_path,
        screen_path=screen_path,
        step1_1word_srt=outputs["step1_1word_srt"],
        step2_output=outputs["step2_output"],
        preset=args.preset,
        width=args.width,
        all_overlay=args.all,
        video_dir=video_dir,
        force_run=force_run,
        skip_confirm=args.yes
    )
    step2_duration = time.perf_counter() - step2_start

    # Step 3: Process Audio
    step3_start = time.perf_counter()
    force_run = run_step3_process_audio(
        step2_output=outputs["step2_output"],
        step3_output=outputs["step3_output"],
        process_audio_sh=scripts["process_audio_sh"],
        force_run=force_run
    )
    step3_duration = time.perf_counter() - step3_start

    # Step 4: Add Background Music
    step4_start = time.perf_counter()
    current_latest_video, force_run = run_step4_add_bgm(
        step3_output=outputs["step3_output"],
        step4_output=outputs["step4_output"],
        bgm=args.bgm,
        volume=args.volume,
        add_bgm_sh=scripts["add_bgm_sh"],
        force_run=force_run
    )
    step4_duration = time.perf_counter() - step4_start

    # Step 5: Finalize Output File Name
    step5_start = time.perf_counter()
    run_step5_finalize(
        current_latest_video=current_latest_video,
        final_output=outputs["final_output"],
        force_run=force_run
    )
    step5_duration = time.perf_counter() - step5_start

    total_duration = time.perf_counter() - pipeline_start_time

    print()
    print_success("============================================================")
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {outputs['final_output']}")
    print_info("------------------------------------------------------------")
    print_info(" Step Execution Timing Summary:")
    print(f"  Step 1 (Transcription):        {format_duration(step1_duration)}")
    print(f"  Step 2 (Mask & Silence Trim):   {format_duration(step2_duration)}")
    print(f"  Step 3 (Audio Processing):      {format_duration(step3_duration)}")
    print(f"  Step 4 (Background Music):      {format_duration(step4_duration)}")
    print(f"  Step 5 (Finalize File):         {format_duration(step5_duration)}")
    print_info("------------------------------------------------------------")
    print_success(f" Total Execution Time:            {format_duration(total_duration)}")
    print_success("============================================================")


if __name__ == "__main__":
    main()

