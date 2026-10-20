"""Step 7: generate metadata.json (LLM title, description, etc.)."""

import sys

from pipeline.common import require_output, run_command
from pipeline.context import PipelineContext


def create_metadata(ctx: PipelineContext) -> None:
    # Every generator (description, tweet, timestamps) reads trimmed.srt, the
    # captions of final.mp4, so cut takes and silences never reach the LLM.
    # Re-transcribed on every run: a trimmed.srt left by an earlier run would
    # describe an older cut of the video.
    trimmed_srt = ctx.outputs.trimmed_srt
    run_command(
        [sys.executable, str(ctx.scripts.transcribe_cloud), str(ctx.outputs.final),
         "--output", str(trimmed_srt)],
        label="transcribe_cloud.py",
    )
    require_output(trimmed_srt)

    cmd = [sys.executable, str(ctx.scripts.auto_create_metadata), str(trimmed_srt)]
    if ctx.args.title:
        cmd += ["--title", ctx.args.title]
    run_command(cmd, label="auto_create_metadata.py")
    require_output(ctx.outputs.metadata)
