"""ffmpeg helpers."""

import subprocess
from pathlib import Path
from typing import Optional


def extract_audio(
    input_path: str | Path,
    output_path: str | Path,
    sample_rate: int,
    codec: Optional[str] = None,
    duration: Optional[float] = None,
) -> None:
    """
    Writes the audio of a video or audio file to ``output_path`` as mono at ``sample_rate``
    (format from the output extension, or ``codec``), keeping only the first ``duration``
    seconds if given. Raises RuntimeError with ffmpeg's output if it fails.
    """
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sample_rate)]
    if codec:
        cmd += ["-c:a", codec]
    cmd.append(str(output_path))

    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to extract audio from {input_path}:\n{result.stderr.strip()}")
