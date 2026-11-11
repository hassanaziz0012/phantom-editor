"""FFmpeg argument presets and input helpers shared by the encode steps."""

import os
from pathlib import Path

from projects.pipeline.common import PipelineError

# A keyframe every second (30 frames at the pipeline's 30 fps), instead of x264's
# default of up to 250 frames apart. Frame-accurate seeking needs close keyframes:
# the excalidraw-broll kit (HyperFrames) shows frozen frames otherwise, and used to
# re-encode all of final.mp4 just to get them. Its setup.sh checks for this spacing.
KEYFRAME_INTERVAL = 30
X264_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-g", str(KEYFRAME_INTERVAL), "-keyint_min", str(KEYFRAME_INTERVAL),
]
AAC_ARGS = ["-c:a", "aac", "-b:a", "384k"]

DEFAULT_BGM_DIR = Path.home() / "Videos" / "Asset Library" / "BGM"


def ffmpeg_cmd(loglevel: str = "error") -> list[str]:
    """Common leading FFmpeg arguments; inputs and outputs are appended by the caller."""
    return ["ffmpeg", "-hide_banner", "-loglevel", loglevel, "-stats", "-y", "-threads", "0"]


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
