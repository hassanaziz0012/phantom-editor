"""Small helpers shared by the pipeline steps."""

import json
import os
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


class PipelineError(Exception):
    """A pipeline failure that should stop the run with ``exit_code``."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def to_float(value: object, default: float = 0.0) -> float:
    """Coerce a JSON field to float, falling back to ``default`` for junk values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def probe_json(path: Path, *ffprobe_args: str) -> dict:
    """Run ffprobe with JSON output and return the parsed result.

    Raises ``subprocess.CalledProcessError`` or ``ValueError`` on failure.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", *ffprobe_args, "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def is_valid_file(path: Path) -> bool:
    """True when ``path`` exists and is non-empty; videos must also have a duration."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return True
    try:
        data = probe_json(path, "-show_entries", "format=duration")
    except (subprocess.CalledProcessError, ValueError):
        return False
    return to_float(data.get("format", {}).get("duration")) > 0


def run_command(cmd: list[str], label: str | None = None) -> None:
    """Run an external command, raising PipelineError on a non-zero exit."""
    print(f"Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        name = label or Path(cmd[0]).name
        raise PipelineError(f"{name} exited with code {e.returncode}", e.returncode) from e


def require_output(path: Path) -> None:
    """Raise PipelineError when a step did not leave a usable file at ``path``."""
    if not is_valid_file(path):
        raise PipelineError(f"output invalid or missing at '{path}'")


def remove_quietly(path: Path | str) -> None:
    """Delete a temporary file, ignoring the case where it is already gone."""
    try:
        os.unlink(path)
    except OSError:
        pass
