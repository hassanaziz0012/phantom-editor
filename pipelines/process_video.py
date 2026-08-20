#!/usr/bin/env python3
"""
Full Video Processing Pipeline Orchestrator.
Chains together transcription, silence trimming / webcam masking, audio processing,
background music addition, and final review file creation.
Supports raw pre-composed video (--raw) or separate webcam + screen recordings.
"""

import os
import sys
import shutil
import time
import argparse
import subprocess
from pathlib import Path

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))
if str(pipeline_dir) not in sys.path:
    sys.path.insert(0, str(pipeline_dir))

from utils import (
    print_info, print_success, print_warning, print_error, get_video_info
)
from pipeline_status import (
    is_4k_video, is_valid_file, format_duration,
    get_pipeline_outputs, compute_pipeline_status,
    print_pipeline_overview, confirm_execution
)
from single_pass_mask_trim import run_single_pass_mask_trim
from trim_silences import get_speech_intervals, get_silence_trim_expressions


def parse_cli_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Full video processing pipeline executing Groq cloud transcription, silence trimming, "
                    "audio processing, BGM addition, and final review file renaming. "
                    "Supports pre-composed raw recordings (--raw) or dual webcam + screen recording."
    )
    parser.add_argument(
        "webcam",
        nargs="?",
        default=None,
        help="Path to webcam / main video file (or raw OBS video file if --raw is passed)."
    )
    parser.add_argument(
        "screen",
        nargs="?",
        default=None,
        help="Path to screen recording video file (optional; ignored if --raw is passed)."
    )
    parser.add_argument(
        "--raw", "-r",
        dest="raw",
        nargs="?",
        const=True,
        default=None,
        help="Process a single pre-composed raw video file (e.g. OBS recording with scene switches). "
             "Skips webcam overlay attachment and proceeds directly with silence trimming."
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

    # Verify that at least one valid input file is provided
    is_raw = args.raw is not None
    if is_raw:
        raw_input = args.raw if isinstance(args.raw, str) else (args.webcam_flag or args.webcam)
        if not raw_input:
            print_error("Error: Input raw video file is required when using --raw.")
            parser.print_help()
            sys.exit(1)
    else:
        webcam_input = args.webcam_flag or args.webcam
        if not webcam_input:
            print_error("Error: Input video file (webcam video) is required.")
            parser.print_help()
            sys.exit(1)

    return args


def validate_pipeline_inputs(args: argparse.Namespace) -> tuple[Path, Path | None, Path, bool]:
    """Validate input video files and return (main_video_path, screen_path, video_dir, is_raw_mode)."""
    is_raw = args.raw is not None
    if is_raw:
        raw_input = args.raw if isinstance(args.raw, str) else (args.webcam_flag or args.webcam)
        raw_path = Path(raw_input).resolve()
        if not raw_path.is_file():
            print_error(f"Error: Raw video file not found at '{raw_path}'")
            sys.exit(1)
        video_dir = raw_path.parent
        return raw_path, None, video_dir, True

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
    return webcam_path, screen_path, video_dir, False


def verify_script_dependencies(pipeline_dir: Path, repo_root: Path) -> dict[str, Path]:
    """Verify all required scripts exist and return a dictionary of their paths."""
    v_dir = repo_root / "video-editing"
    scripts = {
        "downscale_py": v_dir / "downscale.py",
        "auto_attach_webcam_py": v_dir / "auto_attach_webcam_mask.py",
        "process_audio_sh": repo_root / "audio-processing" / "process_audio.sh",
        "transcribe_cloud_py": v_dir / "transcribe_cloud.py",
        "trim_silences_py": v_dir / "trim_silences.py",
        "add_bgm_sh": v_dir / "add_bgm_to_video.sh",
        "single_pass_mask_trim_py": pipeline_dir / "single_pass_mask_trim.py",
    }

    for name, script_path in scripts.items():
        if not script_path.is_file():
            print_error(f"Error: Required pipeline script not found at '{script_path}'")
            sys.exit(1)

    return scripts


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


def run_step2_trim_silences_raw(
    raw_video_path: Path,
    step2_output: Path,
    force_run: bool
) -> bool:
    """Step 2 (Raw Mode): Silence Trimming on pre-composed video (skips webcam mask attachment)."""
    print_info("\n--- [Step 2/5] Silence Trimming on Raw Video ---")
    if not force_run and is_valid_file(step2_output):
        print_success(f"[SKIP] Step 2 complete: Trimmed video file already exists -> {step2_output.name}")
        return force_run

    video_info = get_video_info(raw_video_path)
    needs_downscale = is_4k_video(raw_video_path)

    if needs_downscale:
        print_warning(f"Video resolution {video_info.width}x{video_info.height} exceeds 1080p. Downscaling to 1080p during trimming.")
    else:
        print_info(f"Video resolution {video_info.width}x{video_info.height} is 1080p HD. No downscaling needed.")

    print_info("Analyzing audio for speech intervals with Silero VAD...")
    speech_intervals = get_speech_intervals(raw_video_path)
    print_info(f"Detected {len(speech_intervals)} active speech intervals.")

    select_expr, shift_expr, total_speech_duration = get_silence_trim_expressions(speech_intervals)

    if select_expr and shift_expr:
        v_filter = f"select='{select_expr}',setpts='(T-({shift_expr}))/TB',fps=30"
        if needs_downscale:
            v_filter = f"scale=1920:1080:flags=bicubic,{v_filter}"
        a_filter = f"aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB',aresample=async=1:first_pts=0"

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-threads", "0",
            "-i", str(raw_video_path),
            "-vf", v_filter,
            "-af", a_filter,
            "-fps_mode", "cfr",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "384k",
            "-t", f"{total_speech_duration:.3f}",
            "-movflags", "+faststart",
            str(step2_output)
        ]
    else:
        print_warning("No silence intervals to cut. Re-encoding video directly.")
        vf_args = ["-vf", "scale=1920:1080:flags=bicubic"] if needs_downscale else []
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-threads", "0",
            "-i", str(raw_video_path),
        ] + vf_args + [
            "-fps_mode", "cfr",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "384k",
            "-movflags", "+faststart",
            str(step2_output)
        ]

    print_info("🚀 Executing silence trimming...")
    print(f"Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Silence trimming failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(step2_output):
        print_error(f"[ERROR] Step 2 output file invalid or missing at '{step2_output}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 2 complete: Silences trimmed -> {step2_output.name}")
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
    main_video_path, screen_path, video_dir, is_raw_mode = validate_pipeline_inputs(args)

    scripts = verify_script_dependencies(pipeline_dir, repo_root)

    video_is_4k = is_4k_video(main_video_path)
    outputs = get_pipeline_outputs(main_video_path, video_dir, args.bgm)
    statuses, run_plan = compute_pipeline_status(outputs, args.force)

    ext = main_video_path.suffix or ".mp4"
    print_pipeline_overview(
        main_video_path, screen_path, video_dir, args, video_is_4k, statuses, run_plan, ext, raw_mode=is_raw_mode
    )

    confirm_execution(args.yes)

    print()
    print_info("Starting video processing pipeline...")

    pipeline_start_time = time.perf_counter()
    force_run = args.force

    # Step 1: Transcribe Video using Groq Cloud
    step1_start = time.perf_counter()
    force_run = run_step1_transcription(
        webcam_path=main_video_path,
        step1_srt_output=outputs["step1_srt"],
        step1_1word_srt=outputs["step1_1word_srt"],
        transcribe_cloud_py=scripts["transcribe_cloud_py"],
        force_run=force_run
    )
    step1_duration = time.perf_counter() - step1_start

    # Step 2: Video Processing (Silence Trimming for --raw, or Single-Pass Mask + Trim)
    step2_start = time.perf_counter()
    if is_raw_mode:
        force_run = run_step2_trim_silences_raw(
            raw_video_path=main_video_path,
            step2_output=outputs["step2_output"],
            force_run=force_run
        )
    else:
        force_run = run_single_pass_mask_trim(
            webcam_path=main_video_path,
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

    step2_label = "Step 2 (Silence Trimming):" if is_raw_mode else "Step 2 (Mask & Silence Trim):"

    print()
    print_success("============================================================")
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {outputs['final_output']}")
    print_info("------------------------------------------------------------")
    print_info(" Step Execution Timing Summary:")
    print(f"  Step 1 (Transcription):        {format_duration(step1_duration)}")
    print(f"  {step2_label:<32}{format_duration(step2_duration)}")
    print(f"  Step 3 (Audio Processing):      {format_duration(step3_duration)}")
    print(f"  Step 4 (Background Music):      {format_duration(step4_duration)}")
    print(f"  Step 5 (Finalize File):         {format_duration(step5_duration)}")
    print_info("------------------------------------------------------------")
    print_success(f" Total Execution Time:            {format_duration(total_duration)}")
    print_success("============================================================")


if __name__ == "__main__":
    main()

