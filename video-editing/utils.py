import re
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


def check_dependencies(tools=("ffmpeg", "ffprobe")):
    """Verify that required external tools are available in system PATH."""
    for cmd in tools:
        if not shutil.which(cmd):
            print_error(f"Error: Required tool '{cmd}' is not installed or not in system PATH.")
            sys.exit(1)


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


SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]

def format_srt_time(seconds: float) -> str:
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds % 1) * 1000))
    if milliseconds == 1000:
        milliseconds = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def parse_timestamp(val: str) -> float:
    """Parses float seconds or HH:MM:SS,mmm formatted timestamps into float seconds."""
    val = val.strip()
    if not val:
        return 0.0
    try:
        return float(val)
    except ValueError:
        pass
    
    val = val.replace(',', '.')
    parts = val.split(':')
    if len(parts) == 3:
        # HH:MM:SS.mmm
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        # MM:SS.mmm
        m = int(parts[0])
        s = float(parts[1])
        return m * 60 + s
    else:
        raise ValueError(f"Invalid timestamp format: '{val}'. Expected float seconds or HH:MM:SS,mmm")


def parse_srt(srt_path: str | Path) -> list[tuple[float, float, str]]:
    """
    Parses an SRT subtitle file into a list of (start_seconds, end_seconds, text) tuples.
    """
    path = Path(srt_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"SRT captions file not found: {path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    content = content.replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", content)

    timestamp_pattern = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}(?:[,\.]\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}:\d{2}(?:[,\.]\d{1,3})?)"
    )

    captions: list[tuple[float, float, str]] = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        time_line = lines[1] if len(lines) >= 2 and ("-->" in lines[1]) else lines[0]
        match = timestamp_pattern.search(time_line)
        if not match:
            continue

        start_t = parse_timestamp(match.group(1))
        end_t = parse_timestamp(match.group(2))

        time_line_idx = lines.index(time_line)
        raw_text = " ".join(lines[time_line_idx + 1 :]).strip()
        cleaned_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if cleaned_text:
            captions.append((start_t, end_t, cleaned_text))

    return captions


def parse_srt_to_text(srt_path: str | Path) -> str:
    """
    Parses an SRT subtitle file into clean continuous plain text.
    """
    captions = parse_srt(srt_path)
    full_text = " ".join(text for _, _, text in captions)
    return re.sub(r"\s+", " ", full_text).strip()


def parse_srt_to_timestamped_transcript(srt_path: str | Path) -> str:
    """
    Parses an SRT subtitle file into a formatted transcript with [MM:SS] or [HH:MM:SS] cues.
    """
    captions = parse_srt(srt_path)
    if not captions:
        raise ValueError(f"No subtitle segments could be extracted from: {srt_path}")

    lines = []
    for start_t, _, text in captions:
        h = int(start_t // 3600)
        m = int((start_t % 3600) // 60)
        s = int(start_t % 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        lines.append(f"[{ts}] {text}")

    return "\n".join(lines)


def slugify(text: str) -> str:
    """Safely converts text to a lowercase hyphenated slug filename (alphanumeric and hyphens only)."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')


GDOCS_TOKEN_FILE = Path(__file__).resolve().parent / "tokens/gdocs_token.json"


def find_client_secrets():
    """Locate client_secret.json across common directories."""
    # 1. video-editing/tokens/client_secret.json
    p1 = Path(__file__).resolve().parent / "tokens/client_secret.json"
    if p1.exists():
        return p1
    # 2. youtube_api/tokens/client_secret.json
    p2 = Path(__file__).resolve().parent.parent / "youtube_api/tokens/client_secret.json"
    if p2.exists():
        return p2
    # 3. root tokens/client_secret.json
    p3 = Path(__file__).resolve().parent.parent / "tokens/client_secret.json"
    if p3.exists():
        return p3
    return p1  # Default fallback path


def get_google_doc_shorts(doc_id: str, credentials_file: Path, token_file: Path):
    """Fetches the Google Doc content and parses it into {title, body} shorts."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if not doc_id:
        raise ValueError("Google Doc ID is missing. Set SHORTS_GOOGLE_DOC_ID in your environment or .env file.")

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            print(f"⚠️  Error reading token file {token_file}: {e}. Re-authenticating...")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️  Failed to refresh credentials: {e}. Re-running auth flow...")
                creds = None
        
        if not creds:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found.\n"
                    f"Searched paths:\n"
                    f" - {Path(__file__).resolve().parent / 'tokens/client_secret.json'}\n"
                    f" - {Path(__file__).resolve().parent.parent / 'youtube_api/tokens/client_secret.json'}\n"
                    f"Please verify client_secret.json exists."
                )
            
            # Ensure tokens parent folder exists
            token_file.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    service = build("docs", "v1", credentials=creds)
    print(f"📖 Fetching Google Doc ID: {doc_id}...")
    doc = service.documents().get(documentId=doc_id).execute()
    
    shorts = []
    current_short = None
    
    body_elements = doc.get("body", {}).get("content", [])
    for elem in body_elements:
        if "paragraph" in elem:
            para = elem["paragraph"]
            style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            
            # Extract plain text content
            text = ""
            for part in para.get("elements", []):
                if "textRun" in part:
                    text += part["textRun"].get("content", "")
            
            text_str = text.strip()
            if not text_str:
                continue
                
            if style == "HEADING_2":
                if current_short:
                    shorts.append(current_short)
                current_short = {
                    "title": text_str,
                    "body": ""
                }
            elif style == "NORMAL_TEXT":
                if current_short:
                    if current_short["body"]:
                        current_short["body"] += "\n" + text_str
                    else:
                        current_short["body"] = text_str
                        
    if current_short:
        shorts.append(current_short)
        
    return shorts


# --- Inline video quality-control (blackdetect / freezedetect) parsing ---
# FFmpeg emits these filter logs at AV_LOG_INFO on stderr while the encode runs,
# so an encode that captures QC inline must run with -loglevel info.
BLACK_DETECT_RE = re.compile(
    r'black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)'
)
FREEZE_START_RE = re.compile(r'freeze_start:\s*([0-9.]+)')
FREEZE_END_RE = re.compile(
    r'freeze_end:\s*([0-9.]+)\s+freeze_duration:\s*([0-9.]+)'
)
# Newer FFmpeg releases (7+) log freeze_duration and freeze_end on separate
# lines and prefix them with the filter metadata name (e.g.
# "lavfi.freezedetect.freeze_end: 3"). Capture those individually as a fallback.
FREEZE_END_ONLY_RE = re.compile(r'freeze_end:\s*([0-9.]+)')
FREEZE_DURATION_ONLY_RE = re.compile(r'freeze_duration:\s*([0-9.]+)')


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
        self._active_freeze_start: float | None = None
        self._pending_freeze_duration: float | None = None

    def _append_black(self, start: float, end: float, duration: float) -> None:
        self.glitches.append(
            {"type": "black", "start": start, "end": end, "duration": duration}
        )
        print_warning(f"⚠️  BLACK DETECTED: {start:.2f}s -> {end:.2f}s ({duration:.2f}s)")

    def _append_freeze(self, start: float, end: float, duration: float) -> None:
        self.glitches.append(
            {"type": "freeze", "start": start, "end": end, "duration": duration}
        )
        print_warning(f"⚠️  FREEZE DETECTED: {start:.2f}s -> {end:.2f}s ({duration:.2f}s)")

    def handle_line(self, line: str) -> None:
        """Parse a single FFmpeg stderr line, appending any glitch it reports."""
        if not line:
            return

        black_match = BLACK_DETECT_RE.search(line)
        if black_match:
            self._append_black(
                float(black_match.group(1)),
                float(black_match.group(2)),
                float(black_match.group(3)),
            )

        freeze_start_match = FREEZE_START_RE.search(line)
        if freeze_start_match:
            self._active_freeze_start = float(freeze_start_match.group(1))

        # Older FFmpeg logs freeze_end and freeze_duration on one line.
        combined_end_match = FREEZE_END_RE.search(line)
        if combined_end_match:
            end = float(combined_end_match.group(1))
            duration = float(combined_end_match.group(2))
            start = (
                self._active_freeze_start
                if self._active_freeze_start is not None
                else max(0.0, end - duration)
            )
            self._append_freeze(start, end, duration)
            self._active_freeze_start = None
            self._pending_freeze_duration = None
            return

        # Newer FFmpeg logs freeze_duration and freeze_end separately.
        duration_only_match = FREEZE_DURATION_ONLY_RE.search(line)
        if duration_only_match:
            self._pending_freeze_duration = float(duration_only_match.group(1))

        end_only_match = FREEZE_END_ONLY_RE.search(line)
        if end_only_match:
            end = float(end_only_match.group(1))
            if self._active_freeze_start is not None:
                start = self._active_freeze_start
                duration = max(0.0, end - start)
            elif self._pending_freeze_duration is not None:
                duration = self._pending_freeze_duration
                start = max(0.0, end - duration)
            else:
                start = end
                duration = 0.0
            self._append_freeze(start, end, duration)
            self._active_freeze_start = None
            self._pending_freeze_duration = None

    def finalize(self, total_duration: float = 0.0) -> None:
        """Flush a freeze that was still active when the video ended.

        Mirrors the standalone inspector's tail-freeze behaviour: only report
        trailing freezes at least ``tail_freeze_duration`` seconds long.
        """
        if (
            self._active_freeze_start is not None
            and total_duration > self._active_freeze_start
        ):
            duration = total_duration - self._active_freeze_start
            if duration >= self.tail_freeze_duration:
                self._append_freeze(self._active_freeze_start, total_duration, duration)
        self._active_freeze_start = None
        self._pending_freeze_duration = None


def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    description: str = "Encoding",
    stderr_line_callback: Callable[[str], None] | None = None,
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
                        except Exception:
                            pass
            except Exception:
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
            sys.exit(process.returncode)
        
        print()  # New line after progress bar
        
    except FileNotFoundError:
        print_error("Error: ffmpeg not found in system PATH")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print_error("Processing cancelled by user.")
        process.terminate()
        sys.exit(1)
