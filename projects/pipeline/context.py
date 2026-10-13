"""State shared by every step of one pipeline run."""

import argparse
from dataclasses import dataclass
from pathlib import Path

from utils import VideoInfo

from pipeline import REPO_ROOT
from pipeline.common import PipelineError
from pipeline.summary import RunSummary


@dataclass(frozen=True)
class Outputs:
    """Files the pipeline produces inside the project directory."""

    srt: Path
    srt_1word: Path
    # Raw mode: raw-audio.wav (trimmed speech audio).
    # Webcam mode: after-trim-silences.mp4 (masked + trimmed video).
    step2: Path
    processed_audio: Path
    final: Path
    metadata: Path

    @classmethod
    def for_project(cls, main_video: Path, video_dir: Path, raw_mode: bool) -> "Outputs":
        stem = main_video.stem
        return cls(
            srt=video_dir / f"{stem}.srt",
            srt_1word=video_dir / f"{stem}-1word.srt",
            step2=video_dir / ("raw-audio.wav" if raw_mode else "after-trim-silences.mp4"),
            processed_audio=video_dir / "processed-audio.wav",
            final=video_dir / f"final{main_video.suffix or '.mp4'}",
            metadata=video_dir / "metadata.json",
        )


@dataclass(frozen=True)
class Scripts:
    """External scripts the steps shell out to."""

    transcribe_cloud: Path
    process_audio: Path
    add_bgm: Path
    auto_create_metadata: Path
    audio_review: Path
    video_inspector: Path

    @classmethod
    def locate(cls) -> "Scripts":
        scripts = cls(
            transcribe_cloud=REPO_ROOT / "video-editing" / "transcribe_cloud.py",
            process_audio=REPO_ROOT / "audio-processing" / "process_audio.sh",
            add_bgm=REPO_ROOT / "video-editing" / "add_bgm_to_video.sh",
            auto_create_metadata=REPO_ROOT / "metadata" / "auto_create_metadata.py",
            audio_review=REPO_ROOT / "review" / "audio_review.py",
            video_inspector=REPO_ROOT / "review" / "video_inspector.py",
        )
        for path in vars(scripts).values():
            if not path.is_file():
                raise PipelineError(f"Required pipeline script not found at '{path}'")
        return scripts


@dataclass
class PipelineContext:
    args: argparse.Namespace
    raw_mode: bool
    main_video: Path
    screen_video: Path | None  # webcam mode only; defaults to main_video
    video_dir: Path
    video_info: VideoInfo
    outputs: Outputs
    scripts: Scripts
    summary: RunSummary
    # Set once a step really runs: every later step must then re-run too,
    # because its inputs changed. Seeded from --force.
    force: bool = False
    # Raw mode: speech intervals detected in Step 2, reused by the Step 4 encode.
    speech_intervals: list[tuple[float, float]] | None = None

    @property
    def needs_downscale(self) -> bool:
        return self.video_info.width >= 3840 or self.video_info.height >= 2160
