"""State shared by every step of one pipeline run."""

import argparse
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from video_editing.utils import VideoInfo

from shared.paths import REPO_ROOT
from projects.pipeline.colab import ColabEncoder
from projects.pipeline.common import PipelineError
from projects.pipeline.summary import RunSummary


@dataclass(frozen=True)
class Outputs:
    """Files the pipeline produces inside the project directory."""

    srt: Path
    srt_1word: Path
    # Captions of final.mp4 (silences and cuts removed), for the metadata step.
    trimmed_srt: Path
    # raw-audio.wav (trimmed speech audio).
    step2: Path
    processed_audio: Path
    final: Path
    metadata: Path

    @classmethod
    def for_project(cls, main_video: Path, video_dir: Path) -> "Outputs":
        stem = main_video.stem
        return cls(
            srt=video_dir / f"{stem}.srt",
            srt_1word=video_dir / f"{stem}-1word.srt",
            trimmed_srt=video_dir / "trimmed.srt",
            step2=video_dir / "raw-audio.wav",
            processed_audio=video_dir / "processed-audio.wav",
            final=video_dir / f"final{main_video.suffix or '.mp4'}",
            metadata=video_dir / "metadata.json",
        )


@dataclass(frozen=True)
class Scripts:
    """External scripts the steps shell out to: shell scripts by path, Python by module name."""

    transcribe_cloud: str
    process_audio: Path
    add_bgm: Path
    auto_create_metadata: str
    audio_review: str
    video_inspector: str

    @classmethod
    def locate(cls) -> "Scripts":
        scripts = cls(
            transcribe_cloud="video_editing.transcribe_cloud",
            process_audio=REPO_ROOT / "audio_processing" / "process_audio.sh",
            add_bgm=REPO_ROOT / "video_editing" / "add_bgm_to_video.sh",
            auto_create_metadata="metadata.auto_create_metadata",
            audio_review="review.audio_review",
            video_inspector="review.video_inspector",
        )
        for script in vars(scripts).values():
            found = script.is_file() if isinstance(script, Path) else importlib.util.find_spec(script) is not None
            if not found:
                raise PipelineError(f"Required pipeline script not found: '{script}'")
        return scripts


@dataclass
class PipelineContext:
    args: argparse.Namespace
    main_video: Path
    video_dir: Path
    video_info: VideoInfo
    outputs: Outputs
    scripts: Scripts
    summary: RunSummary
    # Set once a step really runs: every later step must then re-run too,
    # because its inputs changed. Seeded from --force.
    force: bool = False
    # Speech intervals detected in Step 2, reused by the Step 4 encode.
    speech_intervals: list[tuple[float, float]] | None = None
    # The project's cuts.txt (if any) and the ranges it removes.
    cuts_file: Path | None = None
    cuts: list[tuple[float, float]] = field(default_factory=list)
    # Set with --encoder colab when Step 4 will run: the T4 session that encodes it.
    colab: ColabEncoder | None = None

    @property
    def needs_downscale(self) -> bool:
        return self.video_info.width >= 3840 or self.video_info.height >= 2160
