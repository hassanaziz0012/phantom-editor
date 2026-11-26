import sys
import shutil
import subprocess
import json
import uuid
import time
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Callable


# Re-exported: the video_editing and broll scripts import these from utils
from shared import colors
from shared.srt import parse_srt, parse_srt_to_text, parse_srt_to_timestamped_transcript  # noqa: F401
from shared.ffmpeg_qc import Glitch, QCLogParser
from shared.timecodes import format_srt_timestamp, parse_timestamp  # noqa: F401


def print_info(text: str):
    print(f"{colors.BLUE}{text}{colors.RESET}")

def print_success(text: str):
    print(f"{colors.GREEN}{text}{colors.RESET}")

def print_warning(text: str):
    print(f"{colors.YELLOW}{text}{colors.RESET}")

def print_error(text: str):
    print(f"{colors.RED}{text}{colors.RESET}", file=sys.stderr)


@dataclass
class VideoInfo:
    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    codec: str = ""
    pix_fmt: str = ""
    has_audio: bool = False


def get_video_info(video_path: str | Path) -> VideoInfo:
    """Probes a video file using ffprobe and returns a VideoInfo dataclass."""
    path = Path(video_path).resolve()
    info = VideoInfo(path=path)
    if not path.is_file():
        return info

    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height,codec_name,pix_fmt,r_frame_rate,avg_frame_rate",
            "-of", "json",
            str(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # Duration
        try:
            info.duration = float(data.get("format", {}).get("duration", 0.0))
        except (ValueError, TypeError):
            info.duration = 0.0

        streams = data.get("streams", [])
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and info.width == 0:
                info.width = int(stream.get("width", 0))
                info.height = int(stream.get("height", 0))
                info.codec = stream.get("codec_name", "")
                info.pix_fmt = stream.get("pix_fmt", "")
                
                # FPS calculation
                for fps_key in ["r_frame_rate", "avg_frame_rate"]:
                    val = stream.get(fps_key, "")
                    if val and "/" in val:
                        try:
                            num, den = val.split("/")
                            if float(den) > 0:
                                parsed = float(num) / float(den)
                                if 5 <= parsed <= 120:
                                    info.fps = parsed
                                    break
                        except (ValueError, ZeroDivisionError):
                            pass
            elif codec_type == "audio":
                info.has_audio = True

    except Exception as e:
        print_warning(f"Could not determine full video info for '{path}': {e}")

    return info


def resolve_output_path(input_path: str | Path, output_arg: str | Path | None = None, default_suffix: str = "") -> Path:
    """
    Resolves output path based on input file and output argument.
    Enforces .mp4 extension if .webm container is specified (due to H.264 incompatibility).
    """
    inp = Path(input_path).resolve()
    if output_arg:
        out = Path(output_arg).resolve()
    else:
        out = inp.parent / f"{inp.stem}{default_suffix}.mp4"

    if out.suffix.lower() == ".webm":
        print_warning("⚠️ WebM output container is not compatible with H.264 video encoding. Using .mp4 container instead.")
        out = out.with_suffix(".mp4")

    return out


def create_preview_clip(video_path: str | Path, duration: float = 5.0) -> Path:
    """Extracts first `duration` seconds of video to a temporary preview MP4 file."""
    preview_path = Path(f"temp_preview_{uuid.uuid4().hex[:8]}.mp4")
    print_info(f"Preview mode enabled: extracting first {duration} seconds of the video...")
    try:
        crop_cmd = [
            "ffmpeg", "-y",
            "-threads", "0",
            "-i", str(video_path),
            "-t", str(duration),
            str(preview_path)
        ]
        subprocess.run(crop_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return preview_path
    except subprocess.CalledProcessError as e:
        print_error(f"Error creating preview video: ffmpeg failed with exit code {e.returncode}")
        if preview_path.exists():
            preview_path.unlink()
        raise
    except FileNotFoundError:
        print_error("Error: ffmpeg is not installed or not found in system PATH. Cannot create preview video.")
        raise


def format_srt_time(seconds: float) -> str:
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    return format_srt_timestamp(seconds)


# --- Inline video quality-control (blackdetect / freezedetect) parsing ---
# FFmpeg emits these filter logs at AV_LOG_INFO on stderr while the encode runs,
# so an encode that captures QC inline must run with -loglevel info.
class VideoQCCollector:
    """Accumulates black-screen and freeze-frame glitches reported by FFmpeg's
    ``blackdetect`` / ``freezedetect`` filters while a video encode is running.

    Feed each stderr line to :meth:`handle_line` in real time, then call
    :meth:`finalize` once the encode exits. ``glitches`` is a list of dicts
    shaped exactly like the ``summary.video_flags`` entries rendered into
    ``summary.md``::

        {"type": "black"|"freeze", "start": float, "end": float, "duration": float}
    """

    def __init__(self, tail_freeze_duration: float = 2.0):
        self.glitches: list[dict] = []
        self.tail_freeze_duration = tail_freeze_duration
        self._parser = QCLogParser()

    def _record(self, found: list[Glitch]) -> None:
        for glitch in found:
            self.glitches.append(
                {"type": glitch.kind, "start": glitch.start, "end": glitch.end, "duration": glitch.duration}
            )
            label = "BLACK" if glitch.kind == "black" else "FREEZE"
            print_warning(f"⚠️  {label} DETECTED: {glitch.start:.2f}s -> {glitch.end:.2f}s ({glitch.duration:.2f}s)")

    def handle_line(self, line: str) -> None:
        """Parse a single FFmpeg stderr line, appending any glitch it reports."""
        self._record(self._parser.handle_line(line))

    def finalize(self, total_duration: float = 0.0) -> None:
        """Flush a freeze that was still active when the video ended.

        Mirrors the standalone inspector's tail-freeze behaviour: only report
        trailing freezes at least ``tail_freeze_duration`` seconds long.
        """
        self._record(self._parser.finish(total_duration, self.tail_freeze_duration))


def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    description: str = "Encoding",
    stderr_line_callback: Callable[[str], None] | None = None,
    raise_on_error: bool = False,
) -> None:
    """
    Run ffmpeg with a progress bar.

    Args:
        cmd: ffmpeg command list (without -progress flag)
        total_duration: Total duration of the output in seconds
        description: Description to show in progress bar
        stderr_line_callback: Optional callback invoked with every stderr line as
            it is produced (e.g. ``VideoQCCollector.handle_line``). stderr is
            always drained on a background thread so it can never fill the pipe
            and stall ffmpeg, even without a callback.
        raise_on_error: Raise ``subprocess.CalledProcessError`` when ffmpeg fails
            instead of exiting the process, so the caller can fall back.
    """
    # Insert -progress pipe:1 before the output file
    output_idx = -1
    for i, arg in enumerate(cmd):
        if arg == "-y":
            output_idx = i + 1
            break
    if output_idx == -1:
        output_idx = len(cmd) - 1
    
    progress_cmd = cmd[:output_idx] + ["-progress", "pipe:1", "-nostats"] + cmd[output_idx:]
    
    print_info(f"\n🚀 {description}...")
    
    start_time = time.time()
    last_update = 0

    # Collected for error reporting; also streamed to stderr_line_callback.
    stderr_lines: list[str] = []
    # The callback's first error, reported once ffmpeg exits. A failing callback mustn't stop
    # the drain (ffmpeg would stall on a full pipe), but its results are then incomplete.
    callback_errors: list[Exception] = []
    
    try:
        process = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        def _drain_stderr() -> None:
            try:
                for raw_line in process.stderr:
                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue
                    stderr_lines.append(line)
                    if stderr_line_callback is not None:
                        try:
                            stderr_line_callback(line)
                        except Exception as e:
                            if not callback_errors:
                                callback_errors.append(e)
            except (OSError, ValueError):
                # The pipe closed under us (ffmpeg killed or cancelled); the exit code says why.
                pass

        stderr_thread = threading.Thread(
            target=_drain_stderr, name="ffmpeg-stderr", daemon=True
        )
        stderr_thread.start()
        
        # Progress bar constants
        BAR_WIDTH = 50
        
        while True:
            line = process.stdout.readline()
            if not line:
                break
            
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(line.split("=")[1])
                    current_time = out_time_ms / 1_000_000.0
                    if total_duration > 0:
                        progress = min(current_time / total_duration, 1.0)
                    else:
                        progress = 0.0
                    
                    # Throttle updates to ~10 FPS
                    now = time.time()
                    if now - last_update >= 0.1 or progress >= 1.0:
                        elapsed = now - start_time
                        filled = int(BAR_WIDTH * progress)
                        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
                        pct = progress * 100
                        speed = ""
                        if elapsed > 0 and current_time > 0:
                            speed = f" {current_time/elapsed:.2f}x"
                        eta = ""
                        if progress > 0 and progress < 1.0:
                            eta_sec = (elapsed / progress) - elapsed
                            eta = f" ETA: {eta_sec:.0f}s"
                        
                        sys.stdout.write(f"\r  {description}: |{bar}| {pct:5.1f}%{speed}{eta}")
                        sys.stdout.flush()
                        last_update = now
                        
                except (ValueError, IndexError):
                    pass
            elif line == "progress=end":
                # Final update
                elapsed = time.time() - start_time
                filled = BAR_WIDTH
                bar = "█" * filled
                sys.stdout.write(f"\r  {description}: |{bar}| 100.0%")
                sys.stdout.flush()
        
        # stdout is exhausted; wait for ffmpeg to exit and the stderr thread to finish
        process.wait()
        stderr_thread.join()
        
        if process.returncode != 0:
            print()  # New line after progress bar
            print_error(f"[ERROR] ffmpeg failed with exit code {process.returncode}")
            if stderr_lines:
                print_error("\n".join(stderr_lines[-40:]))
            if raise_on_error:
                raise subprocess.CalledProcessError(process.returncode, progress_cmd)
            sys.exit(process.returncode)
        
        print()  # New line after progress bar
        if callback_errors:
            print_warning(f"stderr_line_callback failed, so its results are incomplete: {callback_errors[0]!r}")
        
    except FileNotFoundError:
        print_error("Error: ffmpeg not found in system PATH")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print_error("Processing cancelled by user.")
        process.terminate()
        sys.exit(1)
