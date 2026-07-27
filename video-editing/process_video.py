#!/usr/bin/env python3
import os
import sys
import shutil
import argparse
import subprocess
from pathlib import Path

# Terminal ANSI Color Constants
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"

def print_info(text: str):
    print(f"{COLOR_BLUE}{text}{COLOR_RESET}")

def print_success(text: str):
    print(f"{COLOR_GREEN}{text}{COLOR_RESET}")

def print_warning(text: str):
    print(f"{COLOR_YELLOW}{text}{COLOR_RESET}")

def print_error(text: str):
    print(f"{COLOR_RED}{text}{COLOR_RESET}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(
        description="Full video processing pipeline executing webcam mask auto-attachment, audio processing, "
                    "Groq cloud transcription, silence trimming, BGM addition, and final review file renaming."
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

    auto_attach_webcam_py = script_dir / "auto_attach_webcam_mask.py"
    process_audio_sh = repo_root / "audio-processing" / "process_audio.sh"
    transcribe_cloud_py = script_dir / "transcribe_cloud.py"
    trim_silences_py = script_dir / "trim_silences.py"
    add_bgm_sh = script_dir / "add_bgm_to_video.sh"

    # Verify script files exist
    for required_script in [auto_attach_webcam_py, process_audio_sh, transcribe_cloud_py, trim_silences_py, add_bgm_sh]:
        if not required_script.is_file():
            print_error(f"Error: Required pipeline script not found at '{required_script}'")
            sys.exit(1)

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
    print_info("------------------------------------------------------------")
    print_info("Pipeline Steps Overview:")
    print(" 1. Auto-attach webcam mask (auto_attach_webcam_mask.py)")
    print(" 2. Process audio via process_audio.sh")
    print(" 3. Transcribe video via Groq cloud (transcribe_cloud.py)")
    print(" 4. Trim silences via Silero VAD (trim_silences.py)")
    print(" 5. Add background music (add_bgm_to_video.sh)")
    print(f" 6. Rename final video file to 'to-review{ext}'")
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

    # --- STEP 1: Auto-attach Webcam Mask ---
    print_info("\n--- [Step 1/6] Auto-attaching Webcam Mask ---")
    step1_output = video_dir / "after-webcam-mask.mp4"
    cmd_step1 = [
        sys.executable,
        str(auto_attach_webcam_py),
        "--screen", str(screen_path),
        "--webcam", str(webcam_path),
        "--output", str(step1_output),
        "--preset", args.preset
    ]
    if args.width is not None:
        cmd_step1.extend(["--width", str(args.width)])
    if args.all:
        cmd_step1.append("--all")

    print(f"Executing: {' '.join(cmd_step1)}")
    try:
        subprocess.run(cmd_step1, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 1 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not step1_output.is_file():
        print_error(f"[ERROR] Step 1 output file not found at '{step1_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 1 complete: Attached webcam mask -> {step1_output.name}")

    # --- STEP 2: Process Audio ---
    print_info("\n--- [Step 2/6] Processing Audio ---")
    step2_output = video_dir / "after-audio-processing.mp4"
    cmd_step2 = ["bash", str(process_audio_sh), str(step1_output)]
    print(f"Executing: {' '.join(cmd_step2)}")
    try:
        subprocess.run(cmd_step2, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 2 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not step2_output.is_file():
        print_error(f"[ERROR] Step 2 output file not found at '{step2_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 2 complete: Audio processed -> {step2_output.name}")

    # --- STEP 3: Transcribe Video using Groq Cloud ---
    print_info("\n--- [Step 3/6] Transcribing Video using Groq Cloud ---")
    step3_srt_output = video_dir / "after-audio-processing.srt"
    step3_1word_srt = video_dir / "after-audio-processing-1word.srt"
    cmd_step3 = [
        sys.executable,
        str(transcribe_cloud_py),
        str(step2_output),
        "--output", str(step3_srt_output)
    ]
    print(f"Executing: {' '.join(cmd_step3)}")
    try:
        subprocess.run(cmd_step3, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 3 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not step3_1word_srt.is_file():
        print_error(f"[ERROR] Step 3 output 1word SRT file not found at '{step3_1word_srt}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 3 complete: Transcribed video -> {step3_1word_srt.name}")

    # --- STEP 4: Trim Silences ---
    print_info("\n--- [Step 4/6] Trimming Silences ---")
    step4_output = video_dir / "after-trim-silences.mp4"
    cmd_step4 = [
        sys.executable,
        str(trim_silences_py),
        str(step2_output),
        "--output", str(step4_output)
    ]
    print(f"Executing: {' '.join(cmd_step4)}")
    try:
        subprocess.run(cmd_step4, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 4 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not step4_output.is_file():
        print_error(f"[ERROR] Step 4 output file not found at '{step4_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 4 complete: Trimmed silences -> {step4_output.name}")

    # --- STEP 5: Add Background Music ---
    print_info("\n--- [Step 5/6] Adding Background Music ---")
    current_latest_video = step4_output

    if args.bgm:
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

        step5_output = video_dir / "after-trim-silences-bgm.mp4"
        if not step5_output.is_file():
            print_error(f"[ERROR] Step 5 output file not found at '{step5_output}'")
            sys.exit(1)
        current_latest_video = step5_output
        print_success(f"[SUCCESS] Step 5 complete: Added BGM -> {step5_output.name}")
    else:
        print_warning("[WARNING] Step 5 skipped: No BGM track specified with --bgm.")

    # --- STEP 6: Rename Final Video File to to-review.{ext} ---
    print_info("\n--- [Step 6/6] Finalizing Output File Name ---")
    final_output = video_dir / f"to-review{ext}"

    if final_output.exists():
        print_warning(f"Overwriting existing output file: {final_output.name}")
        try:
            final_output.unlink()
        except Exception as e:
            print_error(f"Error removing existing file '{final_output}': {e}")

    shutil.move(str(current_latest_video), str(final_output))
    print_success(f"[SUCCESS] Step 6 complete: Renamed final video file to -> {final_output.name}")

    print()
    print_success("============================================================")
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {final_output}")
    print_success("============================================================")

if __name__ == "__main__":
    main()
