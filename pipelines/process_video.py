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
import tempfile
from pathlib import Path

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))
if str(pipeline_dir) not in sys.path:
    sys.path.insert(0, str(pipeline_dir))

from utils import (
    print_info, print_success, print_warning, print_error, get_video_info,
    run_ffmpeg_with_progress
)
from pipeline_status import (
    is_4k_video, is_valid_file, format_duration,
    get_pipeline_outputs, compute_pipeline_status,
    print_pipeline_overview, confirm_execution
)
from single_pass_mask_trim import run_single_pass_mask_trim
from trim_silences import get_speech_intervals, get_silence_trim_expressions
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class StepSummary:
    """Summary of a single pipeline step."""
    name: str
    status: str  # "ran", "skipped", "failed"
    duration_seconds: float


@dataclass
class RunSummary:
    """Complete summary of a pipeline run."""
    project_title: str
    raw_input_filename: str
    mode: str  # "raw pre-composed" or "webcam overlay"
    bgm_track: Optional[str]
    resolution: str  # e.g., "1920x1080"
    fps: float
    final_duration: float  # seconds
    steps: list[StepSummary] = field(default_factory=list)
    speech_interval_count: int = 0
    audio_flags: list[dict] = field(default_factory=list)
    video_flags: list[dict] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    total_execution_time: float = 0.0


def format_timestamp(seconds: float) -> str:
    """Format seconds into mm:ss (or hh:mm:ss if >= 1 hour). Never raw seconds."""
    seconds = max(0.0, seconds)
    total_sec = int(round(seconds))
    if total_sec < 3600:
        minutes = total_sec // 60
        secs = total_sec % 60
        return f"{minutes:02d}:{secs:02d}"
    else:
        hours = total_sec // 3600
        minutes = (total_sec % 3600) // 60
        secs = total_sec % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration_concise(seconds: float) -> str:
    """Format duration in seconds as '9.9s' or '2m52s'."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    total_sec = int(round(seconds))
    if total_sec < 3600:
        minutes = total_sec // 60
        secs = total_sec % 60
        return f"{minutes}m{secs:02d}s"
    else:
        hours = total_sec // 3600
        minutes = (total_sec % 3600) // 60
        secs = total_sec % 60
        return f"{hours}h{minutes:02d}m{secs:02d}s"


def write_summary(summary: RunSummary, output_dir: Path) -> Path:
    """Write the run summary as a Markdown file according to exact spec."""
    summary_path = output_dir / "summary.md"

    # Title
    title_line = f"# phantom pipeline: {summary.project_title}"

    # Mode line
    bgm_info = summary.bgm_track if summary.bgm_track else "no BGM"
    mode_line = f"Mode: {summary.mode}, {bgm_info}"

    # Input line: Input: <raw filename> (<resolution>, <fps>fps) -> final.mp4 (<mm:ss>)
    fps_val = summary.fps
    fps_str = f"{int(fps_val)}" if fps_val.is_integer() else f"{fps_val:g}"
    final_dur_str = format_timestamp(summary.final_duration)
    input_line = f"Input: {summary.raw_input_filename} ({summary.resolution}, {fps_str}fps) -> final.mp4 ({final_dur_str})"

    # Steps line: Steps: <ran steps, comma-separated> (skipped: <skipped steps>)
    ran_parts = []
    skipped_parts = []
    for step in summary.steps:
        if step.status == "ran":
            ran_parts.append(step.name)
        elif step.status == "failed":
            ran_parts.append(f"{step.name} (failed)")
        elif step.status == "skipped":
            skipped_parts.append(step.name)

    steps_line = f"Steps: {', '.join(ran_parts)}"
    if skipped_parts:
        steps_line += f" (skipped: {', '.join(skipped_parts)})"

    # Review flags section
    review_step = next((s for s in summary.steps if "Review" in s.name), None)
    if review_step is None or review_step.status != "ran":
        review_body = "review not run"
    else:
        flag_lines = []
        for af in summary.audio_flags:
            start_ts = format_timestamp(af.get("start", 0))
            end_ts = format_timestamp(af.get("end", 0))
            label = af.get("label", af.get("class", "Unknown"))
            conf = float(af.get("confidence", 0))
            if conf < 0.25:
                conf_str = f"conf {conf:.2f}, borderline"
            else:
                conf_str = f"conf {conf:.2f}"
            flag_lines.append(f"- Audio: {label} @ {start_ts}-{end_ts} ({conf_str})")

        for vf in summary.video_flags:
            start_ts = format_timestamp(vf.get("start", 0))
            end_ts = format_timestamp(vf.get("end", 0))
            dur = float(vf.get("duration", 0))
            vtype = vf.get("type", "Unknown")
            end_sec = float(vf.get("end", 0))
            at_end = ""
            if summary.final_duration > 0 and end_sec >= summary.final_duration - 3.0:
                at_end = " (at end of video)"
            flag_lines.append(f"- Video: {vtype} @ {start_ts}-{end_ts} ({dur:.1f}s){at_end}")

        if flag_lines:
            review_body = "\n".join(flag_lines)
        else:
            review_body = "none"

    # Timing section: <step> <duration> | <step> <duration> | ... | total <duration>
    timing_parts = []
    for step in summary.steps:
        if step.status == "ran":
            timing_parts.append(f"{step.name} {format_duration_concise(step.duration_seconds)}")
        elif step.status == "failed":
            timing_parts.append(f"{step.name} failed")
    timing_parts.append(f"total {format_duration_concise(summary.total_execution_time)}")
    timing_line = " | ".join(timing_parts)

    # Outputs section: <comma-separated relative filenames that exist on disk>
    outputs_line = ", ".join(summary.output_files)

    md = f"""{title_line}
{mode_line}
{input_line}
{steps_line}

## Review flags
{review_body}

## Timing
{timing_line}

## Outputs
{outputs_line}
"""

    with open(summary_path, "w") as f:
        f.write(md)

    return summary_path


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
    parser.add_argument(
        "--clean-intermediates",
        action="store_true",
        help="Delete intermediate files (after-trim-silences.mp4) after final.mp4 is verified."
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
    scripts = {
        "downscale_py": v_dir / "downscale.py",
        "auto_attach_webcam_py": v_dir / "auto_attach_webcam_mask.py",
        "process_audio_sh": repo_root / "audio-processing" / "process_audio.sh",
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
    force_run: bool
) -> tuple[bool, int]:
    """Step 2 (Raw Mode): Silence Trimming on pre-composed video (skips webcam mask attachment).
    Returns: (force_run, speech_interval_count)"""
    print_info("\n--- [Step 2/7] Silence Trimming on Raw Video ---")
    if not force_run and is_valid_file(step2_output):
        print_success(f"[SKIP] Step 2 complete: Trimmed video file already exists -> {step2_output.name}")
        return force_run, 0

    video_info = get_video_info(raw_video_path)
    needs_downscale = is_4k_video(raw_video_path)

    if needs_downscale:
        print_warning(f"Video resolution {video_info.width}x{video_info.height} exceeds 1080p. Downscaling to 1080p during trimming.")
    else:
        print_info(f"Video resolution {video_info.width}x{video_info.height} is 1080p HD. No downscaling needed.")

    print_info("Analyzing audio for speech intervals with Silero VAD...")
    speech_intervals = get_speech_intervals(raw_video_path)
    print_info(f"Detected {len(speech_intervals)} active speech intervals.")
    speech_interval_count = len(speech_intervals)

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

    run_ffmpeg_with_progress(cmd, total_speech_duration, "Silence Trimming")

    if not is_valid_file(step2_output):
        print_error(f"[ERROR] Step 2 output file invalid or missing at '{step2_output}'")
        sys.exit(1)

    print_success(f"[SUCCESS] Step 2 complete: Silences trimmed -> {step2_output.name}")
    return True, speech_interval_count


def run_step3_process_audio(
    step2_output: Path,
    processed_audio_output: Path,
    process_audio_sh: Path,
    force_run: bool
) -> bool:
    """Step 3: Process Audio (pipeline mode - outputs processed-audio.wav only)."""
    print_info("\n--- [Step 3/7] Processing Audio (DeepFilterNet + Normalize) ---")
    if not force_run and is_valid_file(processed_audio_output):
        print_success(f"[SKIP] Step 3 complete: Processed audio file already exists -> {processed_audio_output.name}")
        return force_run

    cmd_step3 = ["bash", str(process_audio_sh), "--pipeline-mode", "--output-file", str(processed_audio_output), str(step2_output)]
    print(f"Executing: {' '.join(cmd_step3)}")
    try:
        subprocess.run(cmd_step3, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 3 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(processed_audio_output):
        print_error(f"[ERROR] Step 3 output file invalid or missing at '{processed_audio_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 3 complete: Processed audio saved -> {processed_audio_output.name}")
    return True


def run_step4_finalize_with_bgm(
    step2_output: Path,
    processed_audio_output: Path,
    final_output: Path,
    bgm: str | None,
    volume: int,
    add_bgm_sh: Path,
    force_run: bool
) -> bool:
    """Step 4: Add BGM (if specified) and Finalize in a single remux pass.
    
    Takes after-trim-silences.mp4 (video) + processed-audio.wav (audio) + BGM (optional)
    and writes directly to final.mp4.
    """
    print_info("\n--- [Step 4/7] Finalizing Output (Add BGM + Single Remux Pass) ---")
    if not force_run and is_valid_file(final_output):
        print_success(f"[SKIP] Step 4 complete: Final video file already exists -> {final_output.name}")
        return force_run

    if bgm:
        print_info(f"Adding BGM track: {bgm} at {volume}% volume")
        cmd_step4 = [
            "bash", str(add_bgm_sh),
            str(step2_output), bgm,
            "--volume", str(volume),
            "--pipeline-mode",
            "--processed-audio", str(processed_audio_output),
            str(final_output)
        ]
    else:
        print_info("No BGM specified. Remuxing video with processed audio only.")
        # Simple remux: video from step2_output + audio from processed_audio_output
        cmd_step4 = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-threads", "0",
            "-i", str(step2_output),
            "-i", str(processed_audio_output),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "384k",
            "-movflags", "+faststart",
            str(final_output)
        ]

    print(f"Executing: {' '.join(cmd_step4)}")
    try:
        subprocess.run(cmd_step4, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step 4 failed with exit code {e.returncode}")
        sys.exit(e.returncode)

    if not is_valid_file(final_output):
        print_error(f"[ERROR] Step 4 output file invalid or missing at '{final_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 4 complete: Final video saved -> {final_output.name}")
    return True


def run_step5_verify_final(
    final_output: Path,
    force_run: bool
) -> bool:
    """Step 5: Verify Final Output File."""
    print_info("\n--- [Step 5/7] Verifying Final Output ---")
    if not force_run and is_valid_file(final_output):
        print_success(f"[SKIP] Step 5 complete: Final video file verified -> {final_output.name}")
        return force_run

    if not is_valid_file(final_output):
        print_error(f"[ERROR] Step 5 failed: Final video file missing or invalid at '{final_output}'")
        sys.exit(1)
    print_success(f"[SUCCESS] Step 5 complete: Final video verified -> {final_output.name}")
    return True


def run_step6_review(
    final_output: Path,
    processed_audio_output: Path,
    audio_review_py: Path,
    video_inspector_py: Path,
    force_run: bool,
    skip_review: bool = False,
    summary: RunSummary | None = None
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

    audio_flags = []
    video_flags = []

    # Temporary files for JSON output
    audio_json_fd, audio_json_path = tempfile.mkstemp(suffix=".json")
    os.close(audio_json_fd)
    video_json_fd, video_json_path = tempfile.mkstemp(suffix=".json")
    os.close(video_json_fd)

    try:
        print_info(f"Running audio review on '{processed_audio_output.name}'...")
        cmd_audio = [sys.executable, str(audio_review_py), str(processed_audio_output)]
        print(f"Executing: {' '.join(cmd_audio)}")
        try:
            subprocess.run(cmd_audio + ["--json-out", audio_json_path], check=True)
        except subprocess.CalledProcessError as e:
            print_error(f"[ERROR] Step 6 audio review failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        if os.path.exists(audio_json_path):
            try:
                with open(audio_json_path, "r") as f:
                    audio_data = json.load(f)
                for item in audio_data:
                    audio_flags.append({
                        "start": float(item.get("start", 0)),
                        "end": float(item.get("end", 0)),
                        "label": str(item.get("label", item.get("class", "Unknown"))),
                        "confidence": float(item.get("confidence", 0))
                    })
            except Exception:
                pass

        print()
        print_info(f"Running video quality inspector on '{final_output.name}'...")
        cmd_video = [sys.executable, str(video_inspector_py), str(final_output)]
        print(f"Executing: {' '.join(cmd_video)}")
        try:
            subprocess.run(cmd_video + ["--json-out", video_json_path], check=True)
        except subprocess.CalledProcessError as e:
            print_error(f"[ERROR] Step 6 video inspection failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        if os.path.exists(video_json_path):
            try:
                with open(video_json_path, "r") as f:
                    video_data = json.load(f)
                for item in video_data.get("black_glitches", []):
                    video_flags.append({
                        "type": "black",
                        "start": float(item.get("start", 0)),
                        "end": float(item.get("end", 0)),
                        "duration": float(item.get("duration", 0))
                    })
                for item in video_data.get("freeze_glitches", []):
                    video_flags.append({
                        "type": "freeze",
                        "start": float(item.get("start", 0)),
                        "end": float(item.get("end", 0)),
                        "duration": float(item.get("duration", 0))
                    })
            except Exception:
                pass
    finally:
        if os.path.exists(audio_json_path):
            try:
                os.remove(audio_json_path)
            except OSError:
                pass
        if os.path.exists(video_json_path):
            try:
                os.remove(video_json_path)
            except OSError:
                pass

    if summary is not None:
        summary.audio_flags = audio_flags
        summary.video_flags = video_flags

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
    
    # Initialize RunSummary
    video_info = get_video_info(main_video_path)
    project_title = args.title if args.title else video_dir.name.replace("-", " ").replace("_", " ").title()
    mode_str = "raw pre-composed" if is_raw_mode else "webcam overlay"
    bgm_track = args.bgm if args.bgm else None
    resolution_str = f"{video_info.width}x{video_info.height}"
    
    summary = RunSummary(
        project_title=project_title,
        raw_input_filename=main_video_path.name,
        mode=mode_str,
        bgm_track=bgm_track,
        resolution=resolution_str,
        fps=video_info.fps,
        final_duration=0.0,  # Will be updated after final output
    )

    step_names = {
        1: "Transcription",
        2: "Silence Trimming" if is_raw_mode else "Mask & Silence Trim",
        3: "Audio Processing",
        4: "BGM + Finalize",
        5: "Verify Final",
        6: "Audio & Video Review",
        7: "Auto Create Metadata",
    }

    current_step_idx = None
    step_start_time = 0.0

    step1_duration = 0.0
    step2_duration = 0.0
    step3_duration = 0.0
    step4_duration = 0.0
    step5_duration = 0.0
    step6_duration = 0.0
    step7_duration = 0.0

    try:
        # Step 1: Transcribe Video using Groq Cloud
        current_step_idx = 1
        step_start_time = time.perf_counter()
        force_run = run_step1_transcription(
            webcam_path=main_video_path,
            step1_srt_output=outputs["step1_srt"],
            step1_1word_srt=outputs["step1_1word_srt"],
            transcribe_cloud_py=scripts["transcribe_cloud_py"],
            force_run=force_run
        )
        step1_duration = time.perf_counter() - step_start_time
        summary.steps.append(StepSummary(step_names[1], "ran" if run_plan[1] else "skipped", step1_duration))
        current_step_idx = None

        # Step 2: Video Processing (Silence Trimming for --raw, or Single-Pass Mask + Trim)
        current_step_idx = 2
        step_start_time = time.perf_counter()
        speech_interval_count = 0
        if is_raw_mode:
            force_run, speech_interval_count = run_step2_trim_silences_raw(
                raw_video_path=main_video_path,
                step2_output=outputs["step2_output"],
                force_run=force_run
            )
        else:
            force_run, speech_interval_count = run_single_pass_mask_trim(
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
        step2_duration = time.perf_counter() - step_start_time
        summary.speech_interval_count = speech_interval_count
        summary.steps.append(StepSummary(step_names[2], "ran" if run_plan[2] else "skipped", step2_duration))
        current_step_idx = None

        # Step 3: Process Audio (pipeline mode - outputs processed-audio.wav only)
        current_step_idx = 3
        step_start_time = time.perf_counter()
        force_run = run_step3_process_audio(
            step2_output=outputs["step2_output"],
            processed_audio_output=outputs["processed_audio_output"],
            process_audio_sh=scripts["process_audio_sh"],
            force_run=force_run
        )
        step3_duration = time.perf_counter() - step_start_time
        summary.steps.append(StepSummary(step_names[3], "ran" if run_plan[3] else "skipped", step3_duration))
        current_step_idx = None

        # Step 4: Add BGM (if specified) and Finalize in single remux pass
        current_step_idx = 4
        step_start_time = time.perf_counter()
        force_run = run_step4_finalize_with_bgm(
            step2_output=outputs["step2_output"],
            processed_audio_output=outputs["processed_audio_output"],
            final_output=outputs["final_output"],
            bgm=args.bgm,
            volume=args.volume,
            add_bgm_sh=scripts["add_bgm_sh"],
            force_run=force_run
        )
        step4_duration = time.perf_counter() - step_start_time
        step4_status = "ran" if run_plan[4] else "skipped"
        summary.steps.append(StepSummary(step_names[4], step4_status, step4_duration))
        current_step_idx = None

        # Step 5: Verify Final Output
        current_step_idx = 5
        step_start_time = time.perf_counter()
        force_run = run_step5_verify_final(
            final_output=outputs["final_output"],
            force_run=force_run
        )
        step5_duration = time.perf_counter() - step_start_time
        summary.steps.append(StepSummary(step_names[5], "ran" if run_plan[5] else "skipped", step5_duration))
        current_step_idx = None

        # Step 6: Review Final Video (Audio & Video Inspection)
        current_step_idx = 6
        step6_start_time = time.perf_counter()
        force_run_step6 = force_run
        force_run = run_step6_review(
            final_output=outputs["final_output"],
            processed_audio_output=outputs["processed_audio_output"],
            audio_review_py=scripts["audio_review_py"],
            video_inspector_py=scripts["video_inspector_py"],
            force_run=force_run,
            skip_review=getattr(args, "skip_review", False),
            summary=summary
        )
        step6_duration = time.perf_counter() - step6_start_time
        step6_skipped = getattr(args, "skip_review", False) or (not force_run_step6 and not statuses[6])
        summary.steps.append(StepSummary(step_names[6], "skipped" if step6_skipped else "ran", step6_duration))
        current_step_idx = None

        # Step 7: Generate Project Metadata
        current_step_idx = 7
        step_start_time = time.perf_counter()
        run_step7_create_metadata(
            video_dir=video_dir,
            metadata_output=outputs["metadata_output"],
            title=args.title,
            auto_create_metadata_py=scripts["auto_create_metadata_py"],
            force_run=force_run
        )
        step7_duration = time.perf_counter() - step_start_time
        summary.steps.append(StepSummary(step_names[7], "ran" if run_plan[7] else "skipped", step7_duration))
        current_step_idx = None

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
        print(f"  Step 4 (BGM + Finalize):        {format_duration(step4_duration)}")
        print(f"  Step 5 (Verify Final):          {format_duration(step5_duration)}")
        print(f"  Step 6 (Audio & Video Review):  {format_duration(step6_duration)}")
        print(f"  Step 7 (Auto Create Metadata):  {format_duration(step7_duration)}")
        print_info("------------------------------------------------------------")
        print_success(f" Total Execution Time:            {format_duration(total_duration)}")
        print_success("============================================================")

    except BaseException:
        if current_step_idx is not None:
            failed_name = step_names[current_step_idx]
            failed_dur = time.perf_counter() - step_start_time
            summary.steps.append(StepSummary(failed_name, "failed", failed_dur))
            for rem_idx in range(current_step_idx + 1, 8):
                summary.steps.append(StepSummary(step_names[rem_idx], "skipped", 0.0))
        raise
    finally:
        total_duration = time.perf_counter() - pipeline_start_time
        summary.total_execution_time = total_duration

        if outputs["final_output"].exists():
            try:
                final_video_info = get_video_info(outputs["final_output"])
                summary.final_duration = final_video_info.duration
            except Exception:
                summary.final_duration = 0.0
        else:
            summary.final_duration = 0.0

        # Clean up intermediate files if requested
        if getattr(args, "clean_intermediates", False) and outputs["final_output"].exists():
            step2_file = outputs["step2_output"]
            if step2_file.exists():
                try:
                    step2_file.unlink()
                    print_info(f"Cleaned up intermediate file: {step2_file.name}")
                except Exception as e:
                    print_warning(f"Could not delete intermediate file {step2_file}: {e}")

        candidate_files = [
            outputs["step1_srt"],
            outputs["step1_1word_srt"],
            outputs["step2_output"],
            outputs["processed_audio_output"],
            outputs["final_output"],
            outputs["metadata_output"],
            video_dir / "trimmed.srt",
            video_dir / "trimmed-1word.srt",
        ]
        output_files = []
        for p in candidate_files:
            if p.exists() and p.name not in output_files:
                output_files.append(p.name)
        summary.output_files = output_files

        write_summary(summary, video_dir)
        print_success(f" Run summary saved at: {video_dir / 'summary.md'}")


if __name__ == "__main__":
    main()


