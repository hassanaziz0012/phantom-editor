"""Step 6: automated audio and video review of the final output."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from utils import print_info, print_success

from pipeline.common import PipelineError, remove_quietly, require_output
from pipeline.context import PipelineContext
from pipeline.summary import AudioFlag, VideoFlag


def run_review_script(script: Path, script_args: list[str]) -> object:
    """Run a review script that writes its findings to ``--json-out`` and parse them.

    Returns None when the script wrote no readable JSON, so a malformed report
    only costs the flags it would have contributed.
    """
    fd, json_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        cmd = [sys.executable, str(script), *script_args, "--json-out", json_path]
        print(f"Executing: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise PipelineError(f"{script.name} exited with code {e.returncode}", e.returncode) from e
        try:
            with open(json_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    finally:
        remove_quietly(json_path)


def review_skip_reason(ctx: PipelineContext, force: bool) -> str | None:
    if ctx.args.skip_review:
        return "skipped via --skip-review"
    if not force:
        return f"no changes to {ctx.outputs.final.name} since the last run"
    return None


def review(ctx: PipelineContext) -> None:
    final = ctx.outputs.final
    require_output(final)

    print_info(f"Running audio review on '{ctx.outputs.processed_audio.name}'...")
    audio_data = run_review_script(
        ctx.scripts.audio_review, [str(ctx.outputs.processed_audio), "--hop-sec", "1.0"]
    )
    ctx.summary.audio_flags = [AudioFlag.from_json(item) for item in audio_data or []]

    print()
    if ctx.summary.inline_video_qc:
        # Step 4 already ran blackdetect while encoding; skipping the inspector
        # leaves every CPU core to the audio review.
        print_success("Video inspector not needed: black frames were checked during the encode.")
        return

    print_info(f"Running video quality inspector on '{final.name}'...")
    video_data = run_review_script(ctx.scripts.video_inspector, [str(final)]) or {}
    for kind, key in (("black", "black_glitches"), ("freeze", "freeze_glitches")):
        ctx.summary.video_flags += [VideoFlag.from_json(kind, item) for item in video_data.get(key, [])]
