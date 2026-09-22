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
from concurrent.futures import ThreadPoolExecutor

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))
if str(pipeline_dir) not in sys.path:
    sys.path.insert(0, str(pipeline_dir))

from utils import (
    print_info, print_success, print_warning, print_error, get_video_info,
    get_intel_hardware_encoder_args
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
        "--title", "-t",
        type=str,
        default=None,
        help="Optional custom title for the video project (default: project folder name)."
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
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip audio and video inspection review step."
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
    m_dir = repo_root / "metadata"
    r_dir = repo_root / "review"
    a_dir = repo_root / "audio-processing"
    scripts = {
        "downscale_py": v_dir / "downscale.py",
        "auto_attach_webcam_py": v_dir / "auto_attach_webcam_mask.py",
        "process_audio_sh": a_dir / "process_audio.sh",
        "noise_reduction_sh": a_dir / "noise_reduction.sh",
        "transcribe_cloud_py": v_dir / "transcribe_cloud.py",
        "trim_silences_py": v_dir / "trim_silences.py",
        "add_bgm_sh": v_dir / "add_bgm_to_video.sh",
        "single_pass_mask_trim_py": pipeline_dir / "single_pass_mask_trim.py",
        "auto_create_metadata_py": m_dir / "auto_create_metadata.py",
        "audio_review_py": r_dir / "audio_review.py",
        "video_inspector_py": r_dir / "video_inspector.py",
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
    print_info("\n--- [Step 1/7] Transcribing Video using Groq Cloud ---")
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
    force_run: bool,
    speech_intervals: list | None = None
) -> bool:
    """Step 2 (Raw Mode): Silence Trimming on pre-composed video (skips webcam mask attachment)."""
    print_info("\n--- [Step 2/7] Silence Trimming on Raw Video ---")
    if not force_run and is_valid_file(step2_output):
        print_success(f"[SKIP] Step 2 complete: Trimmed video file already exists -> {step2_output.name}")
        return force_run

    video_info = get_video_info(raw_video_path)
    needs_downscale = is_4k_video(raw_video_path)

    if needs_downscale:
        print_warning(f"Video resolution {video_info.width}x{video_info.height} exceeds 1080p. Downscaling to 1080p during trimming.")
    else:
        print_info(f"Video resolution {video_info.width}x{video_info.height} is 1080p HD. No downscaling needed.")

    if speech_intervals is None:
        print_info("Analyzing audio for speech intervals with Silero VAD...")
        speech_intervals = get_speech_intervals(raw_video_path)
    print_info(f"Detected {len(speech_intervals)} active speech intervals.")

    select_expr, shift_expr, total_speech_duration = get_silence_trim_expressions(speech_intervals)

    hw_info = get_intel_hardware_encoder_args()
    print_info(f"Using video encoder: {hw_info['desc']}")
    v_suffix = hw_info["filter_suffix"]

    if select_expr and shift_expr:
        v_filter = f"select='{select_expr}',setpts='(T-({shift_expr}))/TB',fps=30"
        if needs_downscale:
            v_filter = f"scale=1920:1080:flags=bicubic,{v_filter}"
        v_filter = f"{v_filter}{v_suffix}"
        a_filter = f"aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB',aresample=async=1:first_pts=0"

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-threads", "0",
        ] + hw_info["hw_args"] + hw_info.get("hwaccel_args", []) + [
            "-i", str(raw_video_path),
            "-vf", v_filter,
            "-af", a_filter,
            "-fps_mode", "cfr",
        ] + hw_info["vcodec"] + [
            "-c:a", "aac",
            "-b:a", "384k",
            "-t", f"{total_speech_duration:.3f}",
            "-movflags", "+faststart",
            str(step2_output)
        ]
    else:
        print_warning("No silence intervals to cut. Re-encoding video directly.")
        vf_args = []
        if needs_downscale:
            vf_args = ["-vf", f"scale=1920:1080:flags=bicubic{v_suffix}"]
        elif v_suffix:
            vf_args = ["-vf", v_suffix.lstrip(",")]

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-threads", "0",
        ] + hw_info["hw_args"] + hw_info.get("hwaccel_args", []) + [
            "-i", str(raw_video_path),
        ] + vf_args + [
            "-fps_mode", "cfr",
        ] + hw_info["vcodec"] + [
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
    noise_reduction_sh: Path,
    temp_dir: Path,
    force_run: bool
) -> tuple[Path, bool]:
    """Step 3: Process Audio (Extract audio from trimmed video and apply DeepFilterNet)."""
    print_info("\n--- [Step 3/7] Processing Audio (DeepFilterNet Noise Reduction) ---")
    temp_dir.mkdir(parents=True, exist_ok=True)
    cleaned_wav = temp_dir / "noise-reduced.wav"
    raw_wav = temp_dir / "raw-trimmed-audio.wav"

    if not force_run and is_valid_file(cleaned_wav):
        print_success(f"[SKIP] Step 3 complete: Cleaned audio file already exists -> {cleaned_wav.name}")
        return cleaned_wav, force_run

    # Extract audio track to 48kHz WAV
    print_info("Extracting audio track from trimmed video...")
    cmd_extract = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "0",
        "-i", str(step2_output),
        "-vn",
        "-c:a", "pcm_s16le",
        "-ar", "48000",
        str(raw_wav)
    ]
    try:
        subprocess.run(cmd_extract, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 3 audio extraction failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    # Run DeepFilterNet noise reduction
    print_info("Applying DeepFilterNet noise reduction...")
    cmd_nr = ["bash", str(noise_reduction_sh), str(raw_wav), "--output-file", str(cleaned_wav)]
    print(f"Executing: {' '.join(cmd_nr)}")
    try:
        subprocess.run(cmd_nr, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 3 noise reduction failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if raw_wav.exists():
        try:
            raw_wav.unlink()
        except Exception:
            pass

    if not is_valid_file(cleaned_wav):
        print_error(f"[ERROR] Step 3 output file invalid or missing at '{cleaned_wav}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 3 complete: Audio cleaned -> {cleaned_wav.name}")
    return cleaned_wav, True


def resolve_bgm_track(bgm_input: str | None) -> Path | None:
    """Resolve BGM track by full path or search in BGM directory."""
    if not bgm_input:
        return None
    p = Path(bgm_input).expanduser().resolve()
    if p.is_file():
        return p
    bgm_dir = Path(os.environ.get("BGM_DIR", Path.home() / "Videos/Asset Library/BGM"))
    if (bgm_dir / bgm_input).is_file():
        return bgm_dir / bgm_input
    if (bgm_dir / f"{bgm_input}.mp3").is_file():
        return bgm_dir / f"{bgm_input}.mp3"
    return None


def run_step4_add_bgm_and_mux(
    step2_output: Path,
    cleaned_wav: Path,
    temp_dir: Path,
    bgm: str | None,
    volume: int,
    force_run: bool
) -> tuple[Path, bool]:
    """Step 4: Normalize Audio, Mix Background Music (if requested), and Mux into Video."""
    print_info("\n--- [Step 4/7] Normalizing Audio, Mixing BGM & Single Video Multiplex ---")
    processed_audio = temp_dir / "final-processed-audio.m4a"
    muxed_video = temp_dir / "single-pass-muxed-video.mp4"

    bgm_file = resolve_bgm_track(bgm) if bgm else None
    if bgm and not bgm_file:
        print_warning(f"[WARNING] BGM track '{bgm}' not found. Proceeding without BGM.")

    # 1. Process audio (Loudnorm + BGM mix)
    if bgm_file:
        print_info(f"Mixing BGM track '{bgm_file.name}' at {volume}% volume and applying loudnorm...")
        cmd_audio = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-threads", "0",
            "-i", str(cleaned_wav),
            "-stream_loop", "-1", "-i", str(bgm_file),
            "-filter_complex",
            f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];[1:a]volume={volume}/100[music];[voice][music]amix=inputs=2:duration=first[aout]",
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", "384k",
            str(processed_audio)
        ]
    else:
        print_info("Applying loudnorm normalization to audio...")
        cmd_audio = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-threads", "0",
            "-i", str(cleaned_wav),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "aac",
            "-b:a", "384k",
            str(processed_audio)
        ]

    print(f"Executing audio filter: {' '.join(cmd_audio)}")
    try:
        subprocess.run(cmd_audio, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 4 audio processing failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    # 2. Single Video Multiplex (stream copy video, stream copy processed audio)
    print_info("🚀 Multiplexing trimmed video with processed audio (single mux pass)...")
    cmd_mux = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "0",
        "-i", str(step2_output),
        "-i", str(processed_audio),
        "-c:v", "copy",
        "-c:a", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-movflags", "+faststart",
        str(muxed_video)
    ]
    print(f"Executing: {' '.join(cmd_mux)}")
    try:
        subprocess.run(cmd_mux, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 4 video multiplexing failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(muxed_video):
        print_error(f"[ERROR] Step 4 output file invalid or missing at '{muxed_video}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 4 complete: Muxed video and audio into {muxed_video.name}")
    return muxed_video, True


def run_step5_finalize(
    current_latest_video: Path,
    final_output: Path,
    temp_dir: Path | None,
    force_run: bool
) -> bool:
    """Step 5: Finalize Output File Name (Move file atomically to avoid copying)."""
    print_info("\n--- [Step 5/7] Finalizing Output File Name ---")
    if not force_run and is_valid_file(final_output) and (not is_valid_file(current_latest_video) or final_output.stat().st_mtime >= current_latest_video.stat().st_mtime):
        print_success(f"[SKIP] Step 5 complete: Final review video file already exists -> {final_output.name}")
        return force_run
    else:
        if final_output.exists():
            print_warning(f"Overwriting existing output file: {final_output.name}")
            try:
                final_output.unlink()
            except Exception as e:
                print_error(f"Error removing existing file '{final_output}': {e}")

        # Move atomically rather than copying
        shutil.move(str(current_latest_video), str(final_output))
        print_success(f"[SUCCESS] Step 5 complete: Moved final video file to -> {final_output.name}")

        # Clean up temporary audio processing directory
        if temp_dir and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

        return True


def run_step6_review(
    final_output: Path,
    audio_review_py: Path,
    video_inspector_py: Path,
    force_run: bool,
    skip_review: bool = False
) -> bool:
    """Step 6: Review Final Video (Audio & Video Inspection)."""
    print_info("\n--- [Step 6/7] Reviewing Final Video (Audio & Video Inspection) ---")
    if skip_review:
        print_warning("[SKIP] Step 6 skipped: Review explicitly skipped via --skip-review.")
        return force_run

    if not force_run:
        print_success(f"[SKIP] Step 6 complete: Video review skipped (no changes to {final_output.name})")
        return force_run

    if not is_valid_file(final_output):
        print_error(f"[ERROR] Step 6 review failed: Final review video file missing or invalid at '{final_output}'")
        sys.exit(1)

    print_info(f"Running audio review on '{final_output.name}'...")
    cmd_audio = [sys.executable, str(audio_review_py), str(final_output)]
    print(f"Executing: {' '.join(cmd_audio)}")
    try:
        subprocess.run(cmd_audio, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 6 audio review failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    print()
    print_info(f"Running video quality inspector on '{final_output.name}'...")
    cmd_video = [sys.executable, str(video_inspector_py), str(final_output)]
    print(f"Executing: {' '.join(cmd_video)}")
    try:
        subprocess.run(cmd_video, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 6 video inspection failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    print_success(f"[SUCCESS] Step 6 complete: Finished audio & video review for -> {final_output.name}")
    return True


def run_step7_create_metadata(
    video_dir: Path,
    metadata_output: Path,
    title: str | None,
    auto_create_metadata_py: Path,
    force_run: bool
) -> bool:
    """Step 7: Generate Project Metadata (metadata.json)."""
    print_info("\n--- [Step 7/7] Generating Project Metadata ---")
    if not force_run and is_valid_file(metadata_output):
        print_success(f"[SKIP] Step 7 complete: Metadata file already exists -> {metadata_output.name}")
        return force_run

    cmd_step7 = [
        sys.executable,
        str(auto_create_metadata_py),
        str(video_dir)
    ]
    if title:
        cmd_step7.extend(["--title", title])

    print(f"Executing: {' '.join(cmd_step7)}")
    try:
        subprocess.run(cmd_step7, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 7 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(metadata_output):
        print_error(f"[ERROR] Step 7 output metadata file invalid or missing at '{metadata_output}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 7 complete: Generated metadata -> {metadata_output.name}")
    return True


def main():
    args = parse_cli_args()
    main_video_path, screen_path, video_dir, is_raw_mode = validate_pipeline_inputs(args)

    scripts = verify_script_dependencies(pipeline_dir, repo_root)

    video_is_4k = is_4k_video(main_video_path)
    outputs = get_pipeline_outputs(main_video_path, video_dir, args.bgm)
    statuses, run_plan = compute_pipeline_status(outputs, args.force, getattr(args, "skip_review", False))

    ext = main_video_path.suffix or ".mp4"
    print_pipeline_overview(
        main_video_path, screen_path, video_dir, args, video_is_4k, statuses, run_plan, ext, raw_mode=is_raw_mode
    )

    confirm_execution(args.yes)

    print()
    print_info("Starting video processing pipeline...")

    pipeline_start_time = time.perf_counter()
    force_run = args.force

    # Phase 1: Parallel Audio Analysis (Groq Cloud Transcription + Silero VAD)
    audio_analysis_executor = ThreadPoolExecutor(max_workers=2)
    metadata_executor = ThreadPoolExecutor(max_workers=1)
    metadata_future = None

    print_info("\n🚀 Starting parallel audio analysis (Groq Cloud Transcription + Silero VAD)...")
    step1_start = time.perf_counter()

    def run_step1_and_trigger_async_metadata():
        f_run = run_step1_transcription(
            webcam_path=main_video_path,
            step1_srt_output=outputs["step1_srt"],
            step1_1word_srt=outputs["step1_1word_srt"],
            transcribe_cloud_py=scripts["transcribe_cloud_py"],
            force_run=force_run
        )
        # Asynchronously trigger Step 7 as soon as transcription completes!
        nonlocal metadata_future
        metadata_future = metadata_executor.submit(
            run_step7_create_metadata,
            video_dir=video_dir,
            metadata_output=outputs["metadata_output"],
            title=args.title,
            auto_create_metadata_py=scripts["auto_create_metadata_py"],
            force_run=f_run
        )
        return f_run

    future_step1 = audio_analysis_executor.submit(run_step1_and_trigger_async_metadata)
    future_vad = audio_analysis_executor.submit(get_speech_intervals, main_video_path)

    # Step 2: Video Processing (Silence Trimming for --raw, or Single-Pass Mask + Trim)
    if is_raw_mode:
        speech_intervals = future_vad.result()
        step2_start = time.perf_counter()
        force_run = run_step2_trim_silences_raw(
            raw_video_path=main_video_path,
            step2_output=outputs["step2_output"],
            force_run=force_run,
            speech_intervals=speech_intervals
        )
        step2_duration = time.perf_counter() - step2_start

        # Ensure Step 1 is done
        force_run = future_step1.result()
        step1_duration = time.perf_counter() - step1_start
    else:
        if args.all:
            speech_intervals = future_vad.result()
            step2_start = time.perf_counter()
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
                skip_confirm=args.yes,
                speech_intervals=speech_intervals
            )
            step2_duration = time.perf_counter() - step2_start

            force_run = future_step1.result()
            step1_duration = time.perf_counter() - step1_start
        else:
            speech_intervals = future_vad.result()
            force_run = future_step1.result()
            step1_duration = time.perf_counter() - step1_start

            step2_start = time.perf_counter()
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
                skip_confirm=args.yes,
                speech_intervals=speech_intervals
            )
            step2_duration = time.perf_counter() - step2_start

    audio_analysis_executor.shutdown(wait=False)

    # Temporary directory for isolated audio processing
    audio_temp_dir = video_dir / f"_tmp_{main_video_path.stem}_audio"

    # Step 3: Process Audio (Audio-Only DeepFilterNet)
    step3_start = time.perf_counter()
    cleaned_wav, force_run = run_step3_process_audio(
        step2_output=outputs["step2_output"],
        noise_reduction_sh=scripts["noise_reduction_sh"],
        temp_dir=audio_temp_dir,
        force_run=force_run
    )
    step3_duration = time.perf_counter() - step3_start

    # Step 4: Add Background Music + Loudnorm and Single Video Mux
    step4_start = time.perf_counter()
    current_latest_video, force_run = run_step4_add_bgm_and_mux(
        step2_output=outputs["step2_output"],
        cleaned_wav=cleaned_wav,
        temp_dir=audio_temp_dir,
        bgm=args.bgm,
        volume=args.volume,
        force_run=force_run
    )
    step4_duration = time.perf_counter() - step4_start

    # Step 5: Finalize Output File Name (Atomic move, no file copies)
    step5_start = time.perf_counter()
    force_run = run_step5_finalize(
        current_latest_video=current_latest_video,
        final_output=outputs["final_output"],
        temp_dir=audio_temp_dir,
        force_run=force_run
    )
    step5_duration = time.perf_counter() - step5_start

    # Step 6: Review Final Video (Audio & Video Inspection)
    step6_start = time.perf_counter()
    force_run = run_step6_review(
        final_output=outputs["final_output"],
        audio_review_py=scripts["audio_review_py"],
        video_inspector_py=scripts["video_inspector_py"],
        force_run=force_run,
        skip_review=getattr(args, "skip_review", False)
    )
    step6_duration = time.perf_counter() - step6_start

    # Step 7: Await Asynchronous Metadata Generation
    step7_start = time.perf_counter()
    if metadata_future is not None:
        print_info("\n--- [Step 7/7] Awaiting Project Metadata (Generated in background) ---")
        metadata_future.result()
    else:
        run_step7_create_metadata(
            video_dir=video_dir,
            metadata_output=outputs["metadata_output"],
            title=args.title,
            auto_create_metadata_py=scripts["auto_create_metadata_py"],
            force_run=force_run
        )
    step7_duration = time.perf_counter() - step7_start
    metadata_executor.shutdown(wait=False)

    total_duration = time.perf_counter() - pipeline_start_time

    step2_label = "Step 2 (Silence Trimming):" if is_raw_mode else "Step 2 (Mask & Silence Trim):"

    print()
    print_success("============================================================")
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {outputs['final_output']}")
    print_success(f" Project metadata saved at:   {outputs['metadata_output']}")
    print_info("------------------------------------------------------------")
    print_info(" Step Execution Timing Summary:")
    print(f"  Step 1 (Transcription):        {format_duration(step1_duration)}")
    print(f"  {step2_label:<32}{format_duration(step2_duration)}")
    print(f"  Step 3 (Audio Processing):      {format_duration(step3_duration)}")
    print(f"  Step 4 (BGM & Single Mux):      {format_duration(step4_duration)}")
    print(f"  Step 5 (Finalize File):         {format_duration(step5_duration)}")
    print(f"  Step 6 (Audio & Video Review):  {format_duration(step6_duration)}")
    print(f"  Step 7 (Auto Create Metadata):  {format_duration(step7_duration)}")
    print_info("------------------------------------------------------------")
    print_success(f" Total Execution Time:            {format_duration(total_duration)}")
    print_success("============================================================")


if __name__ == "__main__":
    main()


