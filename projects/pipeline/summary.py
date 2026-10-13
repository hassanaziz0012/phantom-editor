"""Run summary: per-step outcomes, review flags, and the summary.md report."""

from dataclasses import dataclass, field
from pathlib import Path

from pipeline.common import to_float

# Audio-review confidence below which a flag is called out as borderline.
BORDERLINE_CONFIDENCE = 0.25
# Video flags ending within this many seconds of the end are marked as such.
END_OF_VIDEO_MARGIN = 3.0


@dataclass
class StepRecord:
    name: str
    status: str  # "ran", "skipped" or "failed"
    duration_seconds: float


@dataclass
class AudioFlag:
    label: str
    start: float
    end: float
    confidence: float

    @classmethod
    def from_json(cls, item: dict) -> "AudioFlag":
        return cls(
            label=str(item.get("label", item.get("class", "Unknown"))),
            start=to_float(item.get("start")),
            end=to_float(item.get("end")),
            confidence=to_float(item.get("confidence")),
        )


@dataclass
class VideoFlag:
    kind: str  # "black" or "freeze"
    start: float
    end: float
    duration: float

    @classmethod
    def from_json(cls, kind: str, item: dict) -> "VideoFlag":
        return cls(
            kind=kind,
            start=to_float(item.get("start")),
            end=to_float(item.get("end")),
            duration=to_float(item.get("duration")),
        )


@dataclass
class RunSummary:
    project_title: str
    raw_input_filename: str
    mode: str  # "raw pre-composed" or "webcam overlay"
    bgm_track: str | None
    resolution: str  # e.g. "1920x1080"
    fps: float
    final_duration: float = 0.0
    # Keyed by step number so the report keeps pipeline order even though the
    # background steps finish out of order.
    steps: dict[int, StepRecord] = field(default_factory=dict)
    audio_flags: list[AudioFlag] = field(default_factory=list)
    video_flags: list[VideoFlag] = field(default_factory=list)
    # True when Step 4 already ran black-screen detection inline, so Step 6
    # does not need the standalone video inspector.
    inline_video_qc: bool = False
    # False when Step 5 found final.mp4 truncated or stuck on one frame.
    encode_integrity_ok: bool = True
    output_files: list[str] = field(default_factory=list)
    total_execution_time: float = 0.0

    def record_step(self, index: int, name: str, status: str, duration_seconds: float) -> None:
        self.steps[index] = StepRecord(name, status, duration_seconds)

    def ordered_steps(self) -> list[StepRecord]:
        return [self.steps[i] for i in sorted(self.steps)]


def format_timestamp(seconds: float) -> str:
    """Format seconds as mm:ss, or hh:mm:ss from one hour up."""
    hours, remainder = divmod(int(round(max(0.0, seconds))), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """Format a duration as '9.9s', '2m52s' or '1h02m52s'."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    hours, remainder = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def _review_lines(summary: RunSummary) -> list[str]:
    lines = []
    for flag in summary.audio_flags:
        conf = f"conf {flag.confidence:.2f}"
        if flag.confidence < BORDERLINE_CONFIDENCE:
            conf += ", borderline"
        lines.append(
            f"- Audio: {flag.label} @ {format_timestamp(flag.start)}-{format_timestamp(flag.end)} ({conf})"
        )
    for flag in summary.video_flags:
        at_end = ""
        if summary.final_duration > 0 and flag.end >= summary.final_duration - END_OF_VIDEO_MARGIN:
            at_end = " (at end of video)"
        lines.append(
            f"- Video: {flag.kind} @ {format_timestamp(flag.start)}-{format_timestamp(flag.end)} "
            f"({flag.duration:.1f}s){at_end}"
        )
    return lines


def write_summary(summary: RunSummary, review_step_index: int, output_dir: Path) -> Path:
    """Write summary.md for the run and return its path."""
    fps = summary.fps
    fps_str = f"{int(fps)}" if fps.is_integer() else f"{fps:g}"

    ran, skipped, timing = [], [], []
    for step in summary.ordered_steps():
        if step.status == "ran":
            ran.append(step.name)
            timing.append(f"{step.name} {format_duration(step.duration_seconds)}")
        elif step.status == "failed":
            ran.append(f"{step.name} (failed)")
            timing.append(f"{step.name} failed")
        else:
            skipped.append(step.name)
    timing.append(f"total {format_duration(summary.total_execution_time)}")

    steps_line = f"Steps: {', '.join(ran)}"
    if skipped:
        steps_line += f" (skipped: {', '.join(skipped)})"

    review_step = summary.steps.get(review_step_index)
    if review_step is None or review_step.status != "ran":
        review_body = "review not run"
    else:
        review_body = "\n".join(_review_lines(summary)) or "none"

    md = f"""# phantom pipeline: {summary.project_title}
Mode: {summary.mode}, {summary.bgm_track or "no BGM"}
Input: {summary.raw_input_filename} ({summary.resolution}, {fps_str}fps) -> final.mp4 ({format_timestamp(summary.final_duration)})
{steps_line}

## Review flags
{review_body}

## Timing
{" | ".join(timing)}

## Outputs
{", ".join(summary.output_files)}
"""
    summary_path = output_dir / "summary.md"
    summary_path.write_text(md)
    return summary_path
