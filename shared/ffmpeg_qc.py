"""Parsing of ffmpeg's ``blackdetect`` / ``freezedetect`` log lines (shown at -loglevel info)."""

import re
from typing import NamedTuple

# [blackdetect @ 0x...] black_start:10.5 black_end:12.3 black_duration:1.8
BLACK_DETECT_RE = re.compile(
    r'black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)'
)
# [freezedetect @ 0x...] freeze_start:15.2
FREEZE_START_RE = re.compile(r'freeze_start:\s*([0-9.]+)')
# [freezedetect @ 0x...] freeze_end:17.5 freeze_duration:2.3
FREEZE_END_RE = re.compile(
    r'freeze_end:\s*([0-9.]+)\s+freeze_duration:\s*([0-9.]+)'
)
# Newer FFmpeg releases (7+) log freeze_duration and freeze_end on separate
# lines and prefix them with the filter metadata name (e.g.
# "lavfi.freezedetect.freeze_end: 3"). Capture those individually as a fallback.
FREEZE_END_ONLY_RE = re.compile(r'freeze_end:\s*([0-9.]+)')
FREEZE_DURATION_ONLY_RE = re.compile(r'freeze_duration:\s*([0-9.]+)')


class Glitch(NamedTuple):
    kind: str  # "black" or "freeze"
    start: float
    end: float
    duration: float


class QCLogParser:
    """Turns ffmpeg stderr lines into black-screen and freeze-frame glitches.

    Feed each line to :meth:`handle_line`, which returns the glitches that line
    completed, then call :meth:`finish` once the video has ended to flush a freeze
    that was still running.
    """

    def __init__(self) -> None:
        self._active_freeze_start: float | None = None
        self._pending_freeze_duration: float | None = None

    def _freeze_done(self, start: float, end: float, duration: float) -> Glitch:
        self._active_freeze_start = None
        self._pending_freeze_duration = None
        return Glitch("freeze", start, end, duration)

    def handle_line(self, line: str) -> list[Glitch]:
        glitches: list[Glitch] = []
        if not line:
            return glitches

        black_match = BLACK_DETECT_RE.search(line)
        if black_match:
            glitches.append(Glitch(
                "black",
                float(black_match.group(1)),
                float(black_match.group(2)),
                float(black_match.group(3)),
            ))

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
            glitches.append(self._freeze_done(start, end, duration))
            return glitches

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
            glitches.append(self._freeze_done(start, end, duration))
        return glitches

    def finish(self, total_duration: float, min_tail_freeze: float) -> list[Glitch]:
        """A freeze still active when the video ended, if it lasted at least ``min_tail_freeze`` seconds."""
        glitches: list[Glitch] = []
        start = self._active_freeze_start
        if start is not None and total_duration > start:
            duration = total_duration - start
            if duration >= min_tail_freeze:
                glitches.append(Glitch("freeze", start, total_duration, duration))
        self._active_freeze_start = None
        self._pending_freeze_duration = None
        return glitches
