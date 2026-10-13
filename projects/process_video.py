#!/usr/bin/env python3
"""
Full Video Processing Pipeline Orchestrator.
Chains together transcription, silence trimming / webcam masking, audio processing,
background music addition, and final review file creation.
Supports raw pre-composed video (--raw) or separate webcam + screen recordings.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

# The local imports below need these directories on sys.path, so the path setup
# has to run at import time rather than inside main().
for _script_dir in (video_editing_dir, pipeline_dir):
    if str(_script_dir) not in sys.path:
        sys.path.insert(0, str(_script_dir))

from trim_silences import get_silence_trim_expressions, get_speech_intervals
from utils import (
    VideoQCCollector, get_video_info, print_error, print_info, print_success,
    print_warning, run_ffmpeg_with_progress
)

from pipeline_status import (
    compute_pipeline_status, confirm_execution, get_pipeline_outputs, is_4k_video,
    is_valid_file, print_pipeline_overview
)
from single_pass_mask_trim import run_single_pass_mask_trim


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
    bgm_track: str | None
    resolution: str  # e.g., "1920x1080"
    fps: float
    final_duration: float  # seconds
    # Steps are keyed by pipeline position (1-7) so summary.md keeps pipeline
    # order even when a step finishes out of order: the background transcription
    # and metadata steps complete after the steps they overlap.
    steps: dict[int, StepSummary] = field(default_factory=dict)
    audio_flags: list[dict] = field(default_factory=list)
    video_flags: list[dict] = field(default_factory=list)
    # True when black-screen detection ran inline during the Step 4 final encode
    # (raw mode), so Step 6 must not re-run the standalone video inspector as a
    # separate pass.
    inline_video_qc: bool = False
    # Set by the Step 5 structural encode-integrity check; False means final.mp4
    # looks truncated or stuck on a single frame.
    encode_integrity_ok: bool = True
    output_files: list[str] = field(default_factory=list)
    total_execution_time: float = 0.0

    def record_step(self, index: int, name: str, status: str, duration_seconds: float) -> None:
        """Record one step outcome under its pipeline position."""
        self.steps[index] = StepSummary(name, status, duration_seconds)

    def ordered_steps(self) -> list[StepSummary]:
        """Return the recorded steps in pipeline order."""
        return [self.steps[i] for i in sorted(self.steps)]


SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
# Pipeline position of the audio & video review step, whose flags summary.md
# renders under "## Review flags".
REVIEW_STEP_INDEX = 6
# Audio-review confidence below which a flag is worth calling out as borderline.
BORDERLINE_CONFIDENCE = 0.25


def format_timestamp(seconds: float) -> str:
    """Format seconds into mm:ss (or hh:mm:ss if >= 1 hour). Never raw seconds."""
    total_sec = int(round(max(0.0, seconds)))
    if total_sec < SECONDS_PER_HOUR:
        minutes, secs = divmod(total_sec, SECONDS_PER_MINUTE)
        return f"{minutes:02d}:{secs:02d}"
    hours, remainder = divmod(total_sec, SECONDS_PER_HOUR)
    minutes, secs = divmod(remainder, SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration_concise(seconds: float) -> str:
    """Format a duration as '9.9s', '2m52s' or '1h02m52s'."""
    seconds = max(0.0, seconds)
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    total_sec = int(round(seconds))
    if total_sec < SECONDS_PER_HOUR:
        minutes, secs = divmod(total_sec, SECONDS_PER_MINUTE)
        return f"{minutes}m{secs:02d}s"
    hours, remainder = divmod(total_sec, SECONDS_PER_HOUR)
    minutes, secs = divmod(remainder, SECONDS_PER_MINUTE)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


# Downscaled QC branch run alongside the encode. 320px is plenty for the
# luminance math in blackdetect while adding negligible cost to the x264 encode.
# NOTE: freeze detection is deliberately NOT done here. This branch sees the
# frames *entering* the encoder, so freezedetect would only describe the source
# content (and floods on static screen recordings). Encoding failures are caught
# structurally on the encoded file by verify_encode_integrity() in Step 5.
INLINE_QC_VIDEO_FILTER = (
    "scale=320:-2:flags=fast_bilinear,"
    "blackdetect=d=0.1:pic_th=0.98:pix_th=0.10"
)


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
    for step in summary.ordered_steps():
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
    review_step = summary.steps.get(REVIEW_STEP_INDEX)
    if review_step is None or review_step.status != "ran":
        review_body = "review not run"
    else:
        flag_lines = []
        for af in summary.audio_flags:
            start_ts = format_timestamp(af.get("start", 0))
            end_ts = format_timestamp(af.get("end", 0))
            label = af.get("label", af.get("class", "Unknown"))
            conf = float(af.get("confidence", 0))
            if conf < BORDERLINE_CONFIDENCE:
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
    for step in summary.ordered_steps():
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


def get_main_input(args: argparse.Namespace) -> str | None:
    """Resolve the primary input path from ``--raw <path>``, ``--webcam`` or the positionals."""
    if isinstance(args.raw, str):
        return args.raw
    return args.webcam_flag or args.webcam


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
        help="Delete intermediate files (raw-audio.wav or after-trim-silences.mp4) after final.mp4 is verified."
    )

    args = parser.parse_args()

    # Argument-level check only. File existence is validated in
    # validate_pipeline_inputs(), which resolves the path the same way via
    # get_main_input().
    if not get_main_input(args):
        if args.raw is not None:
            print_error("Error: Input raw video file is required when using --raw.")
        else:
            print_error("Error: Input video file (webcam video) is required.")
        parser.print_help()
        sys.exit(1)

    return args


def validate_pipeline_inputs(args: argparse.Namespace) -> tuple[Path, Path | None, Path, bool]:
    """Validate input video files and return (main_video_path, screen_path, video_dir, is_raw_mode)."""
    main_path = Path(get_main_input(args)).resolve()

    if args.raw is not None:
        if not main_path.is_file():
            print_error(f"Error: Raw video file not found at '{main_path}'")
            sys.exit(1)
        return main_path, None, main_path.parent, True

    if not main_path.is_file():
        print_error(f"Error: Webcam video file not found at '{main_path}'")
        sys.exit(1)

    screen_input = args.screen_flag or args.screen
    if screen_input:
        screen_path = Path(screen_input).resolve()
        if not screen_path.is_file():
            print_error(f"Error: Screen video file not found at '{screen_path}'")
            sys.exit(1)
    else:
        screen_path = main_path

    return main_path, screen_path, main_path.parent, False


def verify_script_dependencies(pipeline_dir: Path, repo_root: Path) -> dict[str, Path]:
    """Verify all required scripts exist and return a dictionary of their paths."""
    v_dir = repo_root / "video-editing"
    m_dir = repo_root / "metadata"
    r_dir = repo_root / "review"
    scripts = {
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

    for script_path in scripts.values():
        if not script_path.is_file():
            print_error(f"Error: Required pipeline script not found at '{script_path}'")
            sys.exit(1)

    return scripts


# ---------------------------------------------------------------------------
# Shared step helpers
# ---------------------------------------------------------------------------


def print_step_banner(step_no: int, title: str) -> None:
    """Print the ``--- [Step N/7] Title ---`` header that opens every step."""
    print_info(f"\n--- [Step {step_no}/7] {title} ---")


def step_already_complete(step_no: int, output: Path, force_run: bool, label: str) -> bool:
    """Report whether a step can be skipped because its output is already valid."""
    if not force_run and is_valid_file(output):
        print_success(f"[SKIP] Step {step_no} complete: {label} already exists -> {output.name}")
        return True
    return False


def execute_command(cmd: list[str], step_no: int) -> None:
    """Run an external step command, aborting the pipeline on a non-zero exit."""
    print(f"Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print_error(f"[ERROR] Step {step_no} failed with exit code {e.returncode}")
        sys.exit(e.returncode)


def require_step_output(step_no: int, output: Path, label: str = "file") -> None:
    """Abort the pipeline when a step did not leave a usable output on disk."""
    if not is_valid_file(output):
        print_error(f"[ERROR] Step {step_no} output {label} invalid or missing at '{output}'")
        sys.exit(1)


def remove_quietly(path: Path | str) -> None:
    """Delete a temporary file, ignoring the case where it is already gone."""
    try:
        os.unlink(path)
    except OSError:
        pass


def get_total_speech_duration(speech_intervals: list[tuple[float, float]]) -> float:
    """Total length of the kept speech intervals (the trimmed timeline length)."""
    return get_silence_trim_expressions(speech_intervals)[2]


def to_float(value: object, default: float = 0.0) -> float:
    """Coerce a JSON field to float, falling back to ``default`` for junk values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def run_review_script(script_path: Path, script_args: list[str], description: str) -> object:
    """Run a review script that writes its findings to ``--json-out`` and parse them.

    Returns the parsed payload, or None when the script wrote no readable JSON,
    so a malformed report only costs the flags it would have contributed.
    """
    json_fd, json_path = tempfile.mkstemp(suffix=".json")
    os.close(json_fd)
    try:
        cmd = [sys.executable, str(script_path), *script_args, "--json-out", json_path]
        print(f"Executing: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print_error(f"[ERROR] Step 6 {description} failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        if not os.path.exists(json_path):
            return None
        try:
            with open(json_path) as f:
                return json.load(f)
        except Exception:
            return None
    finally:
        remove_quietly(json_path)


def run_step1_transcription(
    webcam_path: Path,
    step1_srt_output: Path,
    step1_1word_srt: Path,
    transcribe_cloud_py: Path,
    force_run: bool
) -> bool:
    """Step 1: Transcribe Video using Groq Cloud.

    Returns the force flag for the downstream steps: a transcription that really
    ran regenerates the captions, so later steps must not be skipped.
    """
    print_step_banner(1, "Transcribing Video using Groq Cloud")
    if not force_run and is_valid_file(step1_1word_srt) and is_valid_file(step1_srt_output):
        print_success(f"[SKIP] Step 1 complete: Transcribed SRT file already exists -> {step1_1word_srt.name}")
        return False

    execute_command(
        [
            sys.executable,
            str(transcribe_cloud_py),
            str(webcam_path),
            "--output", str(step1_srt_output)
        ],
        1,
    )
    require_step_output(1, step1_1word_srt, "1word SRT file")
    print_success(f"[SUCCESS] Step 1 complete: Transcribed video -> {step1_1word_srt.name}")
    return True


def build_concat_list_file(raw_video_path: Path, speech_intervals: list[tuple[float, float]]) -> str:
    """Write an FFmpeg concat-demuxer script that selects only speech intervals.

    Returns the temporary script path (the caller must delete it). Step 2 reuses
    this for the audio-only extraction and Step 4 for the final video encode, so
    both passes rebuild byte-for-byte the same timeline from the same source.
    """
    fd, concat_file_path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as concat_file:
        # Escape apostrophes for the concat demuxer's quoting rules: a literal '
        # inside the single-quoted filename would terminate the quote, so it
        # becomes...\''... (e.g. "it's" -> "it'\''s").
        absolute_raw_video = str(raw_video_path.resolve()).replace("'", "'\\''")
        for start, end in speech_intervals:
            concat_file.write(f"file '{absolute_raw_video}'\n")
            concat_file.write(f"inpoint {start:.3f}\n")
            concat_file.write(f"outpoint {end:.3f}\n")
    return concat_file_path


def run_step2_trim_silences_raw(
    raw_video_path: Path,
    raw_audio_output: Path,
    force_run: bool
) -> tuple[bool, list[tuple[float, float]] | None]:
    """Step 2 (Raw Mode): Speech-interval detection + instant audio-only extraction.

    Phase A: Silero VAD detects the speech intervals to keep.
    Phase B: FFmpeg's concat demuxer seeks between those intervals and writes
    the trimmed audio straight to ``raw_audio_output`` with ``-vn``. No video
    frame is decoded, so this finishes in well under a second and the
    multi-gigabyte intermediate video (``after-trim-silences.mp4``) is never
    produced.

    Returns: (force_run, speech_intervals). ``speech_intervals`` is None when the
    step is skipped, in which case Step 4 re-detects them before encoding. As in
    Step 1, the returned force flag is True when the step really ran.
    """
    print_step_banner(2, "Speech Extraction (Silero VAD + Audio-Only Trim)")
    if step_already_complete(2, raw_audio_output, force_run, "Trimmed raw audio"):
        return force_run, None

    print_info("Analyzing audio for speech intervals with Silero VAD...")
    speech_intervals = get_speech_intervals(raw_video_path)
    print_info(f"Detected {len(speech_intervals)} active speech intervals.")

    total_speech_duration = get_total_speech_duration(speech_intervals)

    raw_audio_output.parent.mkdir(parents=True, exist_ok=True)

    concat_file_path = None
    try:
        if speech_intervals:
            # Concat demuxer with inpoint/outpoint so FFmpeg seeks straight
            # between speech intervals instead of decoding every frame, and -vn
            # keeps the whole pass audio-only.
            concat_file_path = build_concat_list_file(raw_video_path, speech_intervals)
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
                "-threads", "0",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file_path,
                "-vn",
                "-map", "0:a:0",
                "-af", "aresample=async=1:first_pts=0,asetpts=N/SR/TB",
                "-c:a", "pcm_s16le",
                "-ar", "48000",
                "-ac", "1",
                "-t", f"{total_speech_duration:.3f}",
                str(raw_audio_output),
            ]
            run_ffmpeg_with_progress(cmd, total_speech_duration, "Speech Audio Extraction")
        else:
            print_warning("No silence intervals to cut. Extracting the full audio track.")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
                "-threads", "0",
                "-i", str(raw_video_path),
                "-vn",
                "-map", "0:a:0",
                "-af", "aresample=async=1:first_pts=0",
                "-c:a", "pcm_s16le",
                "-ar", "48000",
                "-ac", "1",
                str(raw_audio_output),
            ]
            run_ffmpeg_with_progress(cmd, 0.0, "Speech Audio Extraction")
    finally:
        if concat_file_path is not None:
            remove_quietly(concat_file_path)

    require_step_output(2, raw_audio_output, "raw audio")

    print_success(f"[SUCCESS] Step 2 complete: Raw speech audio extracted -> {raw_audio_output.name}")
    return True, speech_intervals


def run_step3_process_audio(
    step2_output: Path,
    processed_audio_output: Path,
    process_audio_sh: Path,
    force_run: bool
) -> bool:
    """Step 3: Process Audio (pipeline mode - outputs processed-audio.wav only).

    Like Step 1, returns True when the step really ran so the downstream steps
    consuming this audio are re-executed rather than skipped.
    """
    print_step_banner(3, "Processing Audio (DeepFilterNet + Normalize)")
    if step_already_complete(3, processed_audio_output, force_run, "Processed audio file"):
        return force_run

    execute_command(
        ["bash", str(process_audio_sh), "--pipeline-mode", "--output-file", str(processed_audio_output), str(step2_output)],
        3,
    )
    require_step_output(3, processed_audio_output)
    print_success(f"[SUCCESS] Step 3 complete: Processed audio saved -> {processed_audio_output.name}")
    return True


def resolve_bgm_path(bgm: str) -> Path:
    """Resolve a ``--bgm`` value (path, filename or basename) to an existing file.

    Mirrors ``add_bgm_to_video.sh``'s BGM directory lookup so the single-pass
    encode can mix the track inline via FFmpeg instead of shelling out to a
    script that would copy the (now non-existent) intermediate video stream.
    """
    candidate = Path(bgm).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    bgm_dir = Path(os.environ.get("BGM_DIR", str(Path.home() / "Videos" / "Asset Library" / "BGM")))
    for name in (bgm, f"{bgm}.mp3"):
        track = bgm_dir / name
        if track.is_file():
            return track.resolve()

    print_error(f"[ERROR] BGM track '{bgm}' not found in '{bgm_dir}'.")
    sys.exit(1)


def run_step4_encode_final(
    raw_video_path: Path,
    processed_audio_output: Path,
    final_output: Path,
    speech_intervals: list[tuple[float, float]] | None,
    needs_downscale: bool,
    bgm: str | None,
    volume: int,
    force_run: bool
) -> tuple[bool, list[dict], bool]:
    """Step 4 (Raw Mode): one-pass video encode + final audio multiplex.

    The video is read straight from the concat demuxer built from the same
    speech intervals Step 2 used, so the multi-gigabyte
    ``after-trim-silences.mp4`` intermediate is never written or read back.
    Black-screen QC runs inline on a split branch of the same filtergraph, and
    the BGM track (when requested) is mixed into ``processed-audio.wav`` during
    this identical pass.

    Returns: (force_run, video_glitches, inline_qc_ran). ``inline_qc_ran`` is
    False when the step was skipped, so Step 6 still performs the standalone
    video inspection.
    """
    print_step_banner(4, "Final Encode & Multiplex (Single Pass)")
    if step_already_complete(4, final_output, force_run, "Final video file"):
        return force_run, [], False

    if not is_valid_file(processed_audio_output):
        print_error(f"[ERROR] Step 4 cannot encode: processed audio missing at '{processed_audio_output}'")
        sys.exit(1)

    if speech_intervals is None:
        # Step 2 was skipped (resumed run): re-detect the intervals so the
        # concat timeline can be rebuilt for the encode.
        print_info("Speech intervals not cached; re-running Silero VAD for the final encode...")
        speech_intervals = get_speech_intervals(raw_video_path)

    total_speech_duration = get_total_speech_duration(speech_intervals)

    video_info = get_video_info(raw_video_path)
    if needs_downscale:
        print_warning(f"Video resolution {video_info.width}x{video_info.height} exceeds 1080p. Downscaling to 1080p during the final encode.")
    else:
        print_info(f"Video resolution {video_info.width}x{video_info.height} is 1080p HD. No downscaling needed.")

    bgm_file = resolve_bgm_path(bgm) if bgm else None

    v_filter = "fps=30,setpts=N/30/TB"
    if needs_downscale:
        v_filter = f"scale=1920:1080:flags=bicubic,{v_filter}"
    # The video chain splits in two: [v_enc] feeds the real encoder output while
    # [v_qc] is downscaled and run through blackdetect before a null sink. The
    # frames are already decoded in memory, so black-screen QC costs no extra
    # pass over the finished video.
    qc_filter_complex = (
        f"[0:v]{v_filter},split=2[v_enc][v_qc];"
        f"[v_qc]{INLINE_QC_VIDEO_FILTER}[v_qc_out]"
    )

    if bgm_file is not None:
        # Force both tracks to stereo before amix so the BGM's stereo image is
        # preserved even though processed-audio.wav is mono.
        audio_filter = (
            "[1:a]aresample=48000,aformat=channel_layouts=stereo[main_a];"
            f"[2:a]aresample=48000,volume={volume}/100,aformat=channel_layouts=stereo[music];"
            "[main_a][music]amix=inputs=2:duration=first:dropout_transition=0[out_a]"
        )
        filter_complex = f"{qc_filter_complex};{audio_filter}"
        audio_map = ["-map", "[out_a]"]
    else:
        filter_complex = qc_filter_complex
        audio_map = ["-map", "1:a:0"]

    # -loglevel info is required: blackdetect logs glitches at AV_LOG_INFO, and
    # the collector parses those stderr lines live.
    qc_collector = VideoQCCollector()

    concat_file_path = None
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-stats", "-y",
            "-threads", "0",
        ]
        if speech_intervals:
            concat_file_path = build_concat_list_file(raw_video_path, speech_intervals)
            cmd += ["-f", "concat", "-safe", "0", "-i", concat_file_path]
        else:
            cmd += ["-i", str(raw_video_path)]
        cmd += ["-i", str(processed_audio_output)]
        if bgm_file is not None:
            # Loop the (usually shorter) BGM track for the whole video; amix
            # with duration=first stops at the processed audio's end.
            cmd += ["-stream_loop", "-1", "-i", str(bgm_file)]

        cmd += ["-filter_complex", filter_complex, "-map", "[v_enc]"] + audio_map + [
            "-fps_mode", "cfr",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "384k",
            "-ac", "2",
        ]
        if total_speech_duration > 0:
            cmd += ["-t", f"{total_speech_duration:.3f}"]
        cmd += [
            "-movflags", "+faststart",
            str(final_output),
            "-map", "[v_qc_out]",
            "-f", "null",
            "-",
        ]

        print(f"Executing: {' '.join(cmd)}")
        run_ffmpeg_with_progress(
            cmd, total_speech_duration, "Final Encode & Multiplex",
            stderr_line_callback=qc_collector.handle_line
        )
        qc_collector.finalize(total_speech_duration)
    finally:
        if concat_file_path is not None:
            remove_quietly(concat_file_path)

    require_step_output(4, final_output)

    bgm_note = f" + BGM ({bgm_file.name})" if bgm_file is not None else ""
    print_success(f"[SUCCESS] Step 4 complete: Encoded video with processed audio{bgm_note} -> {final_output.name}")
    return True, qc_collector.glitches, True


def run_step4_finalize_with_bgm(
    step2_output: Path,
    processed_audio_output: Path,
    final_output: Path,
    bgm: str | None,
    volume: int,
    add_bgm_sh: Path,
    force_run: bool
) -> bool:
    """Step 4 (Webcam Mode): Add BGM (if specified) and Finalize in one remux pass.

    Kept for the dual-stream path, which still produces an intermediate
    ``after-trim-silences.mp4`` in Step 2. Raw mode instead uses
    :func:`run_step4_encode_final` to encode straight to ``final.mp4``.
    """
    print_step_banner(4, "Finalizing Output (Add BGM + Single Remux Pass)")
    if step_already_complete(4, final_output, force_run, "Final video file"):
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
            "-ac", "2",
            "-movflags", "+faststart",
            str(final_output)
        ]

    execute_command(cmd_step4, 4)
    require_step_output(4, final_output)
    print_success(f"[SUCCESS] Step 4 complete: Final video saved -> {final_output.name}")
    return True


def verify_encode_integrity(video_path: Path) -> tuple[bool, str]:
    """Structurally verify an encoded file without decoding a single frame.

    Catches the "encoder stalled and the picture is stuck on one frame" failure
    that content-based freeze detection both missed (it inspected the encoder's
    input) and false-fired on for static screen recordings. Two independent
    signals are checked against the container duration:

    1. The video stream duration must match the container/audio duration. A
       video that ends early - so the player holds the last frame forever -
       shows up as a large mismatch here.
    2. The frame count must roughly equal ``duration * fps``. A stalled encoder
       that emits far fewer frames than expected is caught here.

    Returns ``(ok, detail)``. Verification is best-effort: if the container
    lacks the needed metadata the check passes with an explanatory detail rather
    than blocking the pipeline.
    """
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration,avg_frame_rate,nb_frames:format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(probe.stdout)
    except Exception as exc:
        return True, f"skipped ({exc})"

    streams = data.get("streams", [])
    try:
        container_duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        container_duration = 0.0
    if not streams or container_duration <= 0:
        return True, "skipped (no duration metadata)"

    stream = streams[0]
    issues: list[str] = []

    # 1) Video stream duration vs container/audio duration (held last frame).
    try:
        video_duration = float(stream.get("duration", container_duration))
    except (TypeError, ValueError):
        video_duration = container_duration
    duration_tolerance = max(0.5, 0.02 * container_duration)
    if abs(video_duration - container_duration) > duration_tolerance:
        issues.append(
            f"video stream {video_duration:.2f}s but container {container_duration:.2f}s"
        )

    # 2) Frame count vs duration x fps (stalled encoder).
    fps = 0.0
    try:
        num, den = str(stream.get("avg_frame_rate", "")).split("/")
        if float(den) > 0:
            fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        nb_frames = int(stream.get("nb_frames", 0))
    except (TypeError, ValueError):
        nb_frames = 0
    if fps > 0 and nb_frames > 0:
        expected_frames = container_duration * fps
        frame_tolerance = max(3.0, 0.03 * expected_frames)
        if nb_frames < expected_frames - frame_tolerance:
            issues.append(
                f"only {nb_frames} frames vs ~{expected_frames:.0f} expected at {fps:g}fps"
            )

    if issues:
        return False, "; ".join(issues)

    fps_str = f"{fps:g}fps" if fps > 0 else "unknown fps"
    return True, f"{nb_frames or '?'} frames over {container_duration:.2f}s at {fps_str}, video/audio aligned"


def run_step5_verify_final(
    final_output: Path,
    summary: RunSummary | None = None
) -> None:
    """Step 5: Verify Final Output File.

    Confirms the file exists and runs a decode-free structural encode-integrity
    check (see :func:`verify_encode_integrity`) that spots a truncated stream or
    a picture stuck on one frame.
    """
    print_step_banner(5, "Verifying Final Output")
    if not is_valid_file(final_output):
        print_error(f"[ERROR] Step 5 failed: Final video file missing or invalid at '{final_output}'")
        sys.exit(1)

    ok, detail = verify_encode_integrity(final_output)
    if summary is not None:
        summary.encode_integrity_ok = ok
    if ok:
        print_success(f"[SUCCESS] Step 5 complete: Final video verified -> {final_output.name} ({detail})")
    else:
        print_warning(f"[WARNING] Step 5: encode integrity check failed -> {detail}")
        print_warning("          final.mp4 may be truncated or stuck on one frame; re-run with --force to re-encode.")


def run_step6_review(
    final_output: Path,
    processed_audio_output: Path,
    audio_review_py: Path,
    video_inspector_py: Path,
    force_run: bool,
    skip_review: bool = False,
    summary: RunSummary | None = None
) -> None:
    """Step 6: Review Final Video (Audio & Video Inspection)."""
    print_step_banner(6, "Reviewing Final Video (Audio & Video Inspection)")
    if skip_review:
        print_warning("[SKIP] Step 6 skipped: Review explicitly skipped via --skip-review.")
        return

    if not force_run:
        print_success(f"[SKIP] Step 6 complete: Video review skipped (no changes to {final_output.name})")
        return

    if not is_valid_file(final_output):
        print_error(f"[ERROR] Step 6 review failed: Final review video file missing or invalid at '{final_output}'")
        sys.exit(1)

    # Black-screen glitches captured inline during the Step 4 final encode, if
    # any. When present we skip the standalone video inspector entirely so the
    # audio review (PyTorch) gets every CPU core to itself with no competing
    # FFmpeg decode process.
    video_flags = list(summary.video_flags) if summary is not None else []
    inline_video_qc_done = bool(summary is not None and summary.inline_video_qc)
    audio_flags = []

    print_info(f"Running audio review on '{processed_audio_output.name}'...")
    audio_data = run_review_script(
        audio_review_py, [str(processed_audio_output), "--hop-sec", "1.0"], "audio review"
    )
    for item in audio_data or []:
        audio_flags.append({
            "start": to_float(item.get("start", 0)),
            "end": to_float(item.get("end", 0)),
            "label": str(item.get("label", item.get("class", "Unknown"))),
            "confidence": to_float(item.get("confidence", 0)),
        })

    print()
    if inline_video_qc_done:
        print_success(
            "[SKIP] Video quality inspector not needed: black-screen glitches "
            "were captured inline during encoding."
        )
    else:
        print_info(f"Running video quality inspector on '{final_output.name}'...")
        video_data = run_review_script(
            video_inspector_py, [str(final_output)], "video inspection"
        ) or {}
        for flag_type, key in (("black", "black_glitches"), ("freeze", "freeze_glitches")):
            for item in video_data.get(key, []):
                video_flags.append({
                    "type": flag_type,
                    "start": to_float(item.get("start", 0)),
                    "end": to_float(item.get("end", 0)),
                    "duration": to_float(item.get("duration", 0)),
                })

    if summary is not None:
        summary.audio_flags = audio_flags
        summary.video_flags = video_flags

    print_success(f"[SUCCESS] Step 6 complete: Finished audio & video review for -> {final_output.name}")


def run_step7_create_metadata(
    video_dir: Path,
    metadata_output: Path,
    title: str | None,
    auto_create_metadata_py: Path,
    force_run: bool
) -> bool:
    """Step 7: Generate Project Metadata (metadata.json)."""
    print_step_banner(7, "Generating Project Metadata")
    if step_already_complete(7, metadata_output, force_run, "Metadata file"):
        return force_run

    cmd_step7 = [
        sys.executable,
        str(auto_create_metadata_py),
        str(video_dir)
    ]
    if title:
        cmd_step7.extend(["--title", title])

    execute_command(cmd_step7, 7)
    require_step_output(7, metadata_output, "metadata file")
    print_success(f"[SUCCESS] Step 7 complete: Generated metadata -> {metadata_output.name}")
    return True


def build_step_names(is_raw_mode: bool) -> dict[int, str]:
    """Human-readable name for each pipeline step, for the summary and timing lines."""
    return {
        1: "Transcription",
        2: "Speech Extraction" if is_raw_mode else "Mask & Silence Trim",
        3: "Audio Processing",
        4: "Final Encode & Multiplex" if is_raw_mode else "BGM + Finalize",
        5: "Verify Final",
        6: "Audio & Video Review",
        7: "Auto Create Metadata",
    }


class BackgroundTask:
    """Run one pipeline step on a worker thread and remember how it went.

    Used for the two network-bound steps (cloud transcription and LLM metadata
    generation) so their latency overlaps the compute-bound steps around them.
    ``elapsed`` measures the work inside the thread only: the overlapped steps
    run concurrently, so spawn-to-join wall time would absorb their cost too.
    """

    def __init__(self, name: str, work: Callable[[], None]) -> None:
        self.name = name
        self.elapsed = 0.0
        self.error: BaseException | None = None
        self._work = work
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(name=self.name, target=self._run, daemon=False)
        self._thread.start()

    @property
    def running(self) -> bool:
        """True while the worker thread is still executing."""
        return self._thread is not None and self._thread.is_alive()

    def join(self) -> None:
        """Block until the worker thread finishes."""
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        start = time.perf_counter()
        try:
            self._work()
        except BaseException as exc:
            self.error = exc
        finally:
            self.elapsed = time.perf_counter() - start


def print_timing_summary(summary: RunSummary, total_duration: float) -> None:
    """Print the per-step timing table, driven by the recorded step summaries."""
    print_info("------------------------------------------------------------")
    print_info(" Step Execution Timing Summary:")
    for index, step in sorted(summary.steps.items()):
        label = f"  Step {index} ({step.name}):"
        print(f"{label:<38}{format_duration_concise(step.duration_seconds)}")
    print_info("------------------------------------------------------------")
    print_success(f" Total Execution Time:            {format_duration_concise(total_duration)}")


def collect_output_files(video_dir: Path, outputs: dict) -> list[str]:
    """Names of the pipeline outputs that exist on disk, deduplicated.

    Raw mode lists raw-audio.wav (never after-trim-silences.mp4), matching the
    files the streamlined pipeline actually produces.
    """
    candidates = [
        outputs["step1_srt"],
        outputs["step1_1word_srt"],
        outputs["step2_output"],
        outputs["processed_audio_output"],
        outputs["final_output"],
        outputs["metadata_output"],
        video_dir / "trimmed.srt",
        video_dir / "trimmed-1word.srt",
    ]
    names: list[str] = []
    for path in candidates:
        if path.exists() and path.name not in names:
            names.append(path.name)
    return names


def cleanup_intermediates(outputs: dict) -> None:
    """Delete the Step 2 intermediate file once final.mp4 exists.

    In raw mode that is the extracted raw-audio.wav, in webcam mode the trimmed
    video. processed-audio.wav is deliberately kept because Step 6 reviews it.
    """
    step2_file = outputs["step2_output"]
    if not step2_file.exists():
        return
    try:
        step2_file.unlink()
        print_info(f"Cleaned up intermediate file: {step2_file.name}")
    except OSError as e:
        print_warning(f"Could not delete intermediate file {step2_file}: {e}")


def main():
    args = parse_cli_args()
    main_video_path, screen_path, video_dir, is_raw_mode = validate_pipeline_inputs(args)

    scripts = verify_script_dependencies(pipeline_dir, repo_root)

    video_is_4k = is_4k_video(main_video_path)
    outputs = get_pipeline_outputs(main_video_path, video_dir, args.bgm, raw_mode=is_raw_mode)
    statuses, run_plan = compute_pipeline_status(outputs, args.force, args.skip_review)

    ext = main_video_path.suffix or ".mp4"
    print_pipeline_overview(
        main_video_path, screen_path, video_dir, args, video_is_4k, statuses, run_plan, ext, raw_mode=is_raw_mode
    )

    confirm_execution(args.yes)

    print()
    print_info("Starting video processing pipeline...")

    pipeline_start_time = time.perf_counter()
    force_run = args.force

    video_info = get_video_info(main_video_path)
    summary = RunSummary(
        project_title=args.title if args.title else video_dir.name.replace("-", " ").replace("_", " ").title(),
        raw_input_filename=main_video_path.name,
        mode="raw pre-composed" if is_raw_mode else "webcam overlay",
        bgm_track=args.bgm or None,
        resolution=f"{video_info.width}x{video_info.height}",
        fps=video_info.fps,
        final_duration=0.0,  # Updated once final.mp4 exists
    )
    step_names = build_step_names(is_raw_mode)

    # The step in flight, used to attribute a failure to the right step. Failure
    # handlers below mark the in-flight step "failed" and the rest "skipped".
    current_step_idx: int | None = None
    step_start_time = 0.0

    def begin_step(index: int) -> None:
        """Mark a step as in flight so a later exception is attributed to it."""
        nonlocal current_step_idx, step_start_time
        current_step_idx = index
        step_start_time = time.perf_counter()

    def end_step(index: int, status: str, duration_seconds: float | None = None) -> None:
        """Record a finished step and clear the in-flight marker."""
        nonlocal current_step_idx
        duration = duration_seconds if duration_seconds is not None else time.perf_counter() - step_start_time
        summary.record_step(index, step_names[index], status, duration)
        current_step_idx = None

    # Step 1 (Groq Cloud transcription) does not block Step 2 when Step 2 does
    # not need the captions:
    #   - Raw mode trims silences with Silero VAD directly on the audio.
    #   - Webcam overlay mode with --all skips word-command detection.
    # In those cases transcription runs in a background thread so the ~10-20s
    # network wait overlaps with the audio processing and final encode steps. It
    # is always joined before Step 7 (metadata generation), which parses the
    # .srt files.
    needs_step1_before_step2 = (not is_raw_mode) and (not args.all)

    step1_task: BackgroundTask | None = None
    step1_finished = False

    def _step1_outputs_present() -> bool:
        return is_valid_file(outputs["step1_1word_srt"]) and is_valid_file(outputs["step1_srt"])

    def _finish_step1(raise_errors: bool = True) -> None:
        """Join the background Step 1 task (if launched) and record its summary."""
        nonlocal step1_finished
        if step1_task is None or step1_finished:
            return
        if step1_task.running:
            print_info("[INFO] Waiting for background Step 1 (transcription) to finish...")
        step1_task.join()
        step1_finished = True
        # Step 1 always runs before the steps it overlaps, but in the background
        # path its summary is only recorded here at join time; record_step keeps
        # it in pipeline position anyway.
        if step1_task.error is None and not _step1_outputs_present():
            step1_task.error = RuntimeError(
                "Step 1 output SRT files invalid or missing after background transcription"
            )
        if step1_task.error is not None:
            summary.record_step(1, step_names[1], "failed", step1_task.elapsed)
            err = step1_task.error
            if raise_errors:
                print_error(f"[ERROR] Step 1 (background) failed: {err}")
                raise err
            print_warning(f"[WARN] Step 1 (background transcription) failed: {err}")
        else:
            # A launched transcription always runs: the launch guard below skips
            # it outright when the captions are already present.
            summary.record_step(1, step_names[1], "ran", step1_task.elapsed)

    try:
        # Step 1: Transcribe Video using Groq Cloud (background when Step 2
        # does not need the captions; synchronous otherwise).
        if not needs_step1_before_step2:
            if force_run or not _step1_outputs_present():
                step1_spawn_force = force_run

                def _run_step1() -> None:
                    run_step1_transcription(
                        webcam_path=main_video_path,
                        step1_srt_output=outputs["step1_srt"],
                        step1_1word_srt=outputs["step1_1word_srt"],
                        transcribe_cloud_py=scripts["transcribe_cloud_py"],
                        force_run=step1_spawn_force,
                    )

                step1_task = BackgroundTask("step1-transcription", _run_step1)
                step1_task.start()
                # Mirrors synchronous semantics: a real Step 1 run forces the
                # downstream steps, even while it completes asynchronously.
                force_run = True
                print_info("[INFO] Step 1 (transcription) launched in background; starting Step 2 in parallel...")
            else:
                summary.record_step(1, step_names[1], "skipped", 0.0)
                print_success(f"[SKIP] Step 1 complete: Transcribed SRT file already exists -> {outputs['step1_1word_srt'].name}")
        else:
            # Webcam overlay mode with voice commands: Step 2 parses word
            # commands (e.g. "cam on/off") from the captions, so Step 1 must
            # finish first.
            begin_step(1)
            force_run = run_step1_transcription(
                webcam_path=main_video_path,
                step1_srt_output=outputs["step1_srt"],
                step1_1word_srt=outputs["step1_1word_srt"],
                transcribe_cloud_py=scripts["transcribe_cloud_py"],
                force_run=force_run
            )
            end_step(1, "ran" if run_plan[1] else "skipped")

        # Step 2: Speech extraction for --raw; Single-Pass Mask + Trim otherwise
        begin_step(2)
        speech_intervals: list[tuple[float, float]] | None = None
        if is_raw_mode:
            # Phase A + B: detect the speech intervals with Silero VAD, then
            # extract only that audio (audio-only concat seek, no video decoded)
            # so Step 3 can finish before any video encoding starts.
            force_run, speech_intervals = run_step2_trim_silences_raw(
                raw_video_path=main_video_path,
                raw_audio_output=outputs["step2_output"],
                force_run=force_run
            )
        else:
            force_run, _ = run_single_pass_mask_trim(
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
        end_step(2, "ran" if run_plan[2] else "skipped")

        # Step 3: Process Audio (pipeline mode - outputs processed-audio.wav only)
        begin_step(3)
        force_run = run_step3_process_audio(
            step2_output=outputs["step2_output"],
            processed_audio_output=outputs["processed_audio_output"],
            process_audio_sh=scripts["process_audio_sh"],
            force_run=force_run
        )
        end_step(3, "ran" if run_plan[3] else "skipped")

        # Step 4: Final Encode & Multiplex for --raw; BGM + Finalize remux otherwise
        begin_step(4)
        if is_raw_mode:
            # Phase D: encode the video straight from the concat demuxer and
            # multiplex the processed audio (and optional BGM) in one pass. There
            # is no after-trim-silences.mp4 to read back.
            force_run, inline_video_flags, inline_video_qc_ran = run_step4_encode_final(
                raw_video_path=main_video_path,
                processed_audio_output=outputs["processed_audio_output"],
                final_output=outputs["final_output"],
                speech_intervals=speech_intervals,
                needs_downscale=video_is_4k,
                bgm=args.bgm,
                volume=args.volume,
                force_run=force_run
            )
            summary.video_flags = inline_video_flags
            summary.inline_video_qc = inline_video_qc_ran
        else:
            force_run = run_step4_finalize_with_bgm(
                step2_output=outputs["step2_output"],
                processed_audio_output=outputs["processed_audio_output"],
                final_output=outputs["final_output"],
                bgm=args.bgm,
                volume=args.volume,
                add_bgm_sh=scripts["add_bgm_sh"],
                force_run=force_run
            )
        end_step(4, "ran" if run_plan[4] else "skipped")

        # Step 5: Verify Final Output
        begin_step(5)
        run_step5_verify_final(final_output=outputs["final_output"], summary=summary)
        end_step(5, "ran" if run_plan[5] else "skipped")

        # Make sure the background transcription (if any) finished before any
        # consumer of the .srt files starts: Step 7 parses them for metadata.
        _finish_step1()

        # Step 7 does not depend on Step 6 (review flags only end up in
        # summary.md), so launch it in the background as soon as final.mp4 is
        # verified. The LLM metadata generation then runs in parallel with the
        # audio & video inspection.
        step7_spawn_force = force_run
        step7_task: BackgroundTask | None = None
        step7_launch = run_plan[7] and (step7_spawn_force or not statuses[7])
        if step7_launch:
            def _run_step7() -> None:
                run_step7_create_metadata(
                    video_dir=video_dir,
                    metadata_output=outputs["metadata_output"],
                    title=args.title,
                    auto_create_metadata_py=scripts["auto_create_metadata_py"],
                    force_run=step7_spawn_force,
                )

            step7_task = BackgroundTask("step7-metadata", _run_step7)
            step7_task.start()
            print_info("[INFO] Step 7 (metadata) launched in background; running in parallel with Step 6...")

        # Step 6: Review Final Video (Audio & Video Inspection)
        begin_step(6)
        force_run_before_step6 = force_run
        run_step6_review(
            final_output=outputs["final_output"],
            processed_audio_output=outputs["processed_audio_output"],
            audio_review_py=scripts["audio_review_py"],
            video_inspector_py=scripts["video_inspector_py"],
            force_run=force_run,
            skip_review=args.skip_review,
            summary=summary
        )
        step6_skipped = args.skip_review or (not force_run_before_step6 and not statuses[6])
        end_step(6, "skipped" if step6_skipped else "ran")

        # Step 7: Generate Project Metadata (already running in the background
        # if it was launched after Step 5; join it now that Step 6 is done)
        begin_step(7)
        if step7_task is not None:
            step7_task.join()
            if step7_task.error is not None:
                err = step7_task.error
                print_error(f"[ERROR] Step 7 (background) failed: {err}")
                raise err
            if not is_valid_file(outputs["metadata_output"]):
                err = RuntimeError(
                    f"Step 7 output metadata file invalid or missing at '{outputs['metadata_output']}'"
                )
                print_error(f"[ERROR] {err}")
                raise err
            # Timed inside the thread: Step 7 overlaps Step 6, so spawn-to-join
            # wall time would wrongly absorb Step 6's duration.
            end_step(7, "ran", step7_task.elapsed)
        else:
            end_step(7, "skipped", 0.0)

        total_duration = time.perf_counter() - pipeline_start_time

        print()
        print_success("============================================================")
        print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
        print_success(f" Final review video saved at: {outputs['final_output']}")
        print_success(f" Project metadata saved at:   {outputs['metadata_output']}")
        if not summary.encode_integrity_ok:
            print_warning(" ⚠️  ENCODE INTEGRITY WARNING: final.mp4 looks truncated or stuck on a single frame.")
        print_timing_summary(summary, total_duration)
        print_success("============================================================")

    except BaseException:
        # If a background transcription is still running, let it finish so the
        # .srt output is available for a resumed run, and record Step 1 in the
        # summary before reporting the failure.
        try:
            _finish_step1(raise_errors=False)
        except Exception:
            pass
        if current_step_idx is not None:
            failed_dur = time.perf_counter() - step_start_time
            summary.record_step(current_step_idx, step_names[current_step_idx], "failed", failed_dur)
            for rem_idx in range(current_step_idx + 1, 8):
                summary.record_step(rem_idx, step_names[rem_idx], "skipped", 0.0)
        raise
    finally:
        summary.total_execution_time = time.perf_counter() - pipeline_start_time

        if outputs["final_output"].exists():
            try:
                summary.final_duration = get_video_info(outputs["final_output"]).duration
            except Exception:
                summary.final_duration = 0.0
        else:
            summary.final_duration = 0.0

        if args.clean_intermediates and outputs["final_output"].exists():
            cleanup_intermediates(outputs)

        summary.output_files = collect_output_files(video_dir, outputs)

        write_summary(summary, video_dir)
        print_success(f" Run summary saved at: {video_dir / 'summary.md'}")


if __name__ == "__main__":
    main()


