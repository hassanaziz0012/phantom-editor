"""Step 7: generate metadata.json (LLM title, description, etc.)."""

import sys

from pipeline.common import require_output, run_command
from pipeline.context import PipelineContext


def create_metadata(ctx: PipelineContext) -> None:
    cmd = [sys.executable, str(ctx.scripts.auto_create_metadata), str(ctx.video_dir)]
    if ctx.args.title:
        cmd += ["--title", ctx.args.title]
    run_command(cmd, label="auto_create_metadata.py")
    require_output(ctx.outputs.metadata)
