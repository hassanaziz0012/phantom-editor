#!/usr/bin/env python3
import os
import sys
import json
import shutil
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


def main():
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
        help="Width of webcam overlay in pixels for auto-attach webcam mask (default: 270 for portrait, 400 for landscape)."
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

    # Resolve input video paths
    webcam_input = args.webcam_flag or args.webcam
    screen_input = args.screen_flag or args.screen

    if not webcam_input:
        print_error("Error: Input video file (webcam video) is required.")
        parser.print_help()
        sys.exit(1)

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
    ext = webcam_path.suffix or ".mp4"

    # Locate script dependencies
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    downscale_py = script_dir / "downscale.py"
    auto_attach_webcam_py = script_dir / "auto_attach_webcam_mask.py"
    process_audio_sh = repo_root / "audio-processing" / "process_audio.sh"
    transcribe_cloud_py = script_dir / "transcribe_cloud.py"
    trim_silences_py = script_dir / "trim_silences.py"
    add_bgm_sh = script_dir / "add_bgm_to_video.sh"

    # Verify script files exist
    for required_script in [downscale_py, auto_attach_webcam_py, process_audio_sh, transcribe_cloud_py, trim_silences_py, add_bgm_sh]:
        if not required_script.is_file():
            print_error(f"Error: Required pipeline script not found at '{required_script}'")
            sys.exit(1)

    downscaled_webcam = video_dir / f"{webcam_path.stem}-1080{webcam_path.suffix}"
    webcam_is_4k = is_4k_video(webcam_path)
    step0_needed = webcam_is_4k or is_valid_file(downscaled_webcam)

    # Determine effective paths after Step 0
    eff_webcam_path = downscaled_webcam if step0_needed else webcam_path
    eff_screen_path = downscaled_webcam if (step0_needed and screen_path == webcam_path) else screen_path

    # Step output file definitions
    step0_output = downscaled_webcam
    step1_srt_output = video_dir / f"{eff_webcam_path.stem}.srt"
    step1_1word_srt = video_dir / f"{eff_webcam_path.stem}-1word.srt"
    step2_output = video_dir / "after-webcam-mask.mp4"
    step3_output = video_dir / "after-audio-processing.mp4"
    step4_output = video_dir / "after-trim-silences.mp4"
    step5_needed = bool(args.bgm)
    step5_output = video_dir / "after-trim-silences-bgm.mp4"
    final_output = video_dir / f"to-review{ext}"

    # Determine step completion statuses (ignoring force flag for display logic)
    step0_complete = is_valid_file(step0_output) if step0_needed else True
    step1_complete = is_valid_file(step1_1word_srt) and is_valid_file(step1_srt_output)
    step2_complete = is_valid_file(step2_output)
    step3_complete = is_valid_file(step3_output)
    step4_complete = is_valid_file(step4_output)
    step5_complete = is_valid_file(step5_output) if step5_needed else True

    latest_output = step5_output if step5_needed else step4_output
    step6_complete = is_valid_file(final_output) and (
        not is_valid_file(latest_output) or final_output.stat().st_mtime >= latest_output.stat().st_mtime
    )

    # Determine expected execution plan (cascading: if step N runs, subsequent steps run)
    if args.force:
        run_plan = {0: step0_needed, 1: True, 2: True, 3: True, 4: True, 5: step5_needed, 6: True}
    else:
        run0 = step0_needed and not step0_complete
        run1 = run0 or not step1_complete
        run2 = run1 or not step2_complete
        run3 = run2 or not step3_complete
        run4 = run3 or not step4_complete
        run5 = step5_needed and (run4 or not step5_complete)
        run6 = (run5 if step5_needed else run4) or not step6_complete
        run_plan = {0: run0, 1: run1, 2: run2, 3: run3, 4: run4, 5: run5, 6: run6}

    def format_status(needed: bool, will_run: bool, is_complete: bool, skip_reason: str = None) -> str:
        if not needed:
            reason = f" ({skip_reason})" if skip_reason else ""
            return f"{COLOR_BLUE}[SKIPPED{reason}]{COLOR_RESET}"
        if args.force:
            return f"{COLOR_YELLOW}[WILL EXECUTE (FORCED)]{COLOR_RESET}"
        if not will_run and is_complete:
            return f"{COLOR_GREEN}[ALREADY COMPLETE - SKIPPING]{COLOR_RESET}"
        return f"{COLOR_YELLOW}[WILL EXECUTE]{COLOR_RESET}"

    # Overview display
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
        print_warning(f" BGM track:        None specified (Step 5 will be skipped)")
    if webcam_is_4k:
        print_warning(f" 4K Webcam:        Detected 4K resolution (downscaling to 1080p before processing)")
    if args.force:
        print_warning(f" Force re-run:     --force option enabled (re-executing all steps)")

    print_info("------------------------------------------------------------")
    print_info("Pipeline Steps Overview:")

    s0_str = format_status(step0_needed, run_plan[0], step0_complete, "Not 4K video")
    s1_str = format_status(True, run_plan[1], step1_complete)
    s2_str = format_status(True, run_plan[2], step2_complete)
    s3_str = format_status(True, run_plan[3], step3_complete)
    s4_str = format_status(True, run_plan[4], step4_complete)
    s5_str = format_status(step5_needed, run_plan[5], step5_complete, "No BGM specified")
    s6_str = format_status(True, run_plan[6], step6_complete)

    print(f" 0. Downscale webcam to 1080p (downscale.py)                 {s0_str}")
    print(f" 1. Transcribe video via Groq cloud (transcribe_cloud.py)    {s1_str}")
    print(f" 2. Auto-attach webcam mask (auto_attach_webcam_mask.py)     {s2_str}")
    print(f" 3. Process audio via process_audio.sh                       {s3_str}")
    print(f" 4. Trim silences via Silero VAD (trim_silences.py)          {s4_str}")
    print(f" 5. Add background music (add_bgm_to_video.sh)               {s5_str}")
    print(f" 6. Rename final video file to 'to-review{ext}'               {s6_str}")
    print_info("============================================================")

    if not args.yes:
        try:
            user_input = input("Proceed with video processing? [Y/n]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print_error("Processing cancelled.")
            sys.exit(1)

        if user_input and user_input.lower().startswith("n"):
            print_warning("Processing cancelled by user.")
            sys.exit(0)

    print()
    print_info("Starting video processing pipeline...")

    force_run = args.force

    # --- STEP 0: Downscale 4K Webcam Video (if applicable) ---
    if step0_needed:
        print_info("\n--- [Step 0/6] Downscaling 4K Webcam Video to 1080p ---")
        if not force_run and is_valid_file(downscaled_webcam):
            print_success(f"[SKIP] Step 0 complete: Downscaled webcam file already exists -> {downscaled_webcam.name}")
        else:
            print_warning(f"Notice: Webcam video '{webcam_path.name}' is 4K resolution. Downscaling to 1080p...")
            cmd_downscale = [
                sys.executable,
                str(downscale_py),
                str(webcam_path),
                "--output", str(downscaled_webcam)
            ]
            print(f"Executing: {' '.join(cmd_downscale)}")
            try:
                subprocess.run(cmd_downscale, check=True)
            except subprocess.CalledProcessError as e:
                print_error(f"[ERROR] Step 0 downscaling failed with exit code {e.returncode}")
                sys.exit(e.returncode)

            if not is_valid_file(downscaled_webcam):
                print_error(f"[ERROR] Step 0 downscaled output file invalid or missing at '{downscaled_webcam}'")
                sys.exit(1)

            print_success(f"[SUCCESS] Step 0 complete: Downscaled webcam to 1080p -> {downscaled_webcam.name}")
            force_run = True

        if screen_path == webcam_path:
            screen_path = downscaled_webcam
        webcam_path = downscaled_webcam

    # --- STEP 1: Transcribe Video using Groq Cloud ---
    print_info("\n--- [Step 1/6] Transcribing Video using Groq Cloud ---")
    step1_srt_output = video_dir / f"{webcam_path.stem}.srt"
    step1_1word_srt = video_dir / f"{webcam_path.stem}-1word.srt"

    if not force_run and is_valid_file(step1_1word_srt) and is_valid_file(step1_srt_output):
        print_success(f"[SKIP] Step 1 complete: Transcribed SRT file already exists -> {step1_1word_srt.name}")
    else:
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
        force_run = True

    # --- STEP 2: Auto-attach Webcam Mask ---
    print_info("\n--- [Step 2/6] Auto-attaching Webcam Mask ---")
    step2_output = video_dir / "after-webcam-mask.mp4"

    if not force_run and is_valid_file(step2_output):
        print_success(f"[SKIP] Step 2 complete: Webcam mask video file already exists -> {step2_output.name}")
    else:
        cmd_step2 = [
            sys.executable,
            str(auto_attach_webcam_py),
            "--screen", str(screen_path),
            "--webcam", str(webcam_path),
            "--captions", str(step1_1word_srt),
            "--output", str(step2_output),
            "--preset", args.preset
        ]
        if args.width is not None:
            cmd_step2.extend(["--width", str(args.width)])
        if args.all:
            cmd_step2.append("--all")

        print(f"Executing: {' '.join(cmd_step2)}")
        try:
            subprocess.run(cmd_step2, check=True)
        except subprocess.CalledProcessError as e:
            print_error(f"[ERROR] Step 2 failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        if not is_valid_file(step2_output):
            print_error(f"[ERROR] Step 2 output file invalid or missing at '{step2_output}'")
            sys.exit(1)
        print_success(f"[SUCCESS] Step 2 complete: Attached webcam mask -> {step2_output.name}")
        force_run = True

    # --- STEP 3: Process Audio ---
    print_info("\n--- [Step 3/6] Processing Audio ---")
    step3_output = video_dir / "after-audio-processing.mp4"

    if not force_run and is_valid_file(step3_output):
        print_success(f"[SKIP] Step 3 complete: Audio processed video file already exists -> {step3_output.name}")
    else:
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
        force_run = True

    # --- STEP 4: Trim Silences ---
    print_info("\n--- [Step 4/6] Trimming Silences ---")
    step4_output = video_dir / "after-trim-silences.mp4"

    if not force_run and is_valid_file(step4_output):
        print_success(f"[SKIP] Step 4 complete: Trimmed silences video file already exists -> {step4_output.name}")
    else:
        cmd_step4 = [
            sys.executable,
            str(trim_silences_py),
            str(step3_output),
            "--output", str(step4_output)
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
        print_success(f"[SUCCESS] Step 4 complete: Trimmed silences -> {step4_output.name}")
        force_run = True

    # --- STEP 5: Add Background Music ---
    print_info("\n--- [Step 5/6] Adding Background Music ---")
    current_latest_video = step4_output

    if args.bgm:
        step5_output = video_dir / "after-trim-silences-bgm.mp4"
        if not force_run and is_valid_file(step5_output):
            print_success(f"[SKIP] Step 5 complete: BGM video file already exists -> {step5_output.name}")
            current_latest_video = step5_output
        else:
            cmd_step5 = [
                "bash",
                str(add_bgm_sh),
                str(step4_output),
                args.bgm,
                "--volume", str(args.volume)
            ]
            print(f"Executing: {' '.join(cmd_step5)}")
            try:
                subprocess.run(cmd_step5, check=True)
            except subprocess.CalledProcessError as e:
                print_error(f"[ERROR] Step 5 failed with exit code {e.returncode}")
                sys.exit(e.returncode)

            if not is_valid_file(step5_output):
                print_error(f"[ERROR] Step 5 output file invalid or missing at '{step5_output}'")
                sys.exit(1)
            current_latest_video = step5_output
            print_success(f"[SUCCESS] Step 5 complete: Added BGM -> {step5_output.name}")
            force_run = True
    else:
        print_warning("[WARNING] Step 5 skipped: No BGM track specified with --bgm.")

    # --- STEP 6: Finalize Output File Name ---
    print_info("\n--- [Step 6/6] Finalizing Output File Name ---")
    final_output = video_dir / f"to-review{ext}"

    if not force_run and is_valid_file(final_output) and (not is_valid_file(current_latest_video) or final_output.stat().st_mtime >= current_latest_video.stat().st_mtime):
        print_success(f"[SKIP] Step 6 complete: Final review video file already exists -> {final_output.name}")
    else:
        if final_output.exists():
            print_warning(f"Overwriting existing output file: {final_output.name}")
            try:
                final_output.unlink()
            except Exception as e:
                print_error(f"Error removing existing file '{final_output}': {e}")

        shutil.copy2(str(current_latest_video), str(final_output))
        print_success(f"[SUCCESS] Step 6 complete: Copied final video file to -> {final_output.name}")

    print()
    print_success("============================================================")
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {final_output}")
    print_success("============================================================")

if __name__ == "__main__":
    main()
