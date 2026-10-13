"""FFmpeg argument presets and input helpers shared by the encode steps."""

import os
import tempfile
from pathlib import Path

from pipeline.common import PipelineError

X264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
AAC_ARGS = ["-c:a", "aac", "-b:a", "384k"]

DEFAULT_BGM_DIR = Path.home() / "Videos" / "Asset Library" / "BGM"


def ffmpeg_cmd(loglevel: str = "error") -> list[str]:
    """Common leading FFmpeg arguments; inputs and outputs are appended by the caller."""
    return ["ffmpeg", "-hide_banner", "-loglevel", loglevel, "-stats", "-y", "-threads", "0"]


def write_concat_list(video_path: Path, intervals: list[tuple[float, float]]) -> str:
    """Write an FFmpeg concat-demuxer script that plays only ``intervals``.

    The demuxer seeks straight between intervals instead of decoding the
    silences. Returns the temp file path; the caller must delete it.
    """
    # A literal ' would end the single-quoted filename, so it becomes '\''.
    quoted = str(video_path.resolve()).replace("'", "'\\''")
    fd, list_path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        for start, end in intervals:
            f.write(f"file '{quoted}'\ninpoint {start:.3f}\noutpoint {end:.3f}\n")
    return list_path


def resolve_bgm_path(bgm: str) -> Path:
    """Resolve a ``--bgm`` value (path, filename or basename) to an existing file.

    Uses the same lookup as add_bgm_to_video.sh: a direct path, else
    ``$BGM_DIR/<name>`` or ``$BGM_DIR/<name>.mp3``.
    """
    candidate = Path(bgm).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    bgm_dir = Path(os.environ.get("BGM_DIR", str(DEFAULT_BGM_DIR)))
    for name in (bgm, f"{bgm}.mp3"):
        track = bgm_dir / name
        if track.is_file():
            return track.resolve()
    raise PipelineError(f"BGM track '{bgm}' not found in '{bgm_dir}'")
