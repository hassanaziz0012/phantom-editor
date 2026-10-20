"""Manual cuts: time ranges listed in a project's cuts.txt are removed in raw mode.

One range per line, ``START-END``, each side as ``SS``, ``MM:SS`` or
``HH:MM:SS`` with optional fractional seconds, e.g. ``02:05-02:17.5``.
Times are on the raw recording's timeline (what a video player shows).
Blank lines and ``#`` comments are ignored.
"""

import re
from pathlib import Path

from pipeline.common import PipelineError

CUTS_FILENAME = "cuts.txt"

_TIMESTAMP = r"\d+(?::\d+){0,2}(?:\.\d+)?"
_RANGE_RE = re.compile(rf"^({_TIMESTAMP})\s*[-–]\s*({_TIMESTAMP})$")


def parse_timestamp(text: str) -> float:
    """Seconds for ``SS``, ``MM:SS`` or ``HH:MM:SS`` (fractions allowed on the seconds)."""
    *head, seconds = text.split(":")
    if any("." in part for part in head):
        raise ValueError(f"only the seconds may have a fraction: '{text}'")
    total = 0.0
    for part in head:
        total = total * 60 + int(part)
    return total * 60 + float(seconds)


def format_cut(cut: tuple[float, float]) -> str:
    def fmt(seconds: float) -> str:
        minutes, secs = divmod(seconds, 60)
        return f"{int(minutes):02d}:{secs:04.1f}" if secs % 1 else f"{int(minutes):02d}:{int(secs):02d}"
    return f"{fmt(cut[0])}-{fmt(cut[1])}"


def load_cuts(path: Path, video_duration: float) -> list[tuple[float, float]]:
    """Parse ``path`` into sorted, merged (start, end) ranges, rejecting bad lines."""
    cuts = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _RANGE_RE.match(line)
        if not match:
            raise PipelineError(f"{path.name} line {number}: expected START-END like 02:05-02:17, got '{line}'")
        try:
            start, end = parse_timestamp(match[1]), parse_timestamp(match[2])
        except ValueError as e:
            raise PipelineError(f"{path.name} line {number}: {e}") from e
        if end <= start:
            raise PipelineError(f"{path.name} line {number}: end must be after start in '{line}'")
        if video_duration > 0 and start >= video_duration:
            raise PipelineError(
                f"{path.name} line {number}: '{line}' starts after the video ends ({video_duration:.1f}s)"
            )
        cuts.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(cuts):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_cuts(
    intervals: list[tuple[float, float]], cuts: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Remove every cut range from the speech intervals, splitting intervals a cut lands inside."""
    result = []
    for start, end in intervals:
        for cut_start, cut_end in cuts:
            if cut_end <= start or cut_start >= end:
                continue
            if cut_start > start:
                result.append((start, cut_start))
            start = max(start, cut_end)
            if start >= end:
                break
        if start < end:
            result.append((start, end))
    return result
