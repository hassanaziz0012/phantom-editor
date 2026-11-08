"""Step 1: transcribe the main video with Groq Cloud."""

from shared.commands import python_module
from projects.pipeline.common import require_output, run_command
from projects.pipeline.context import PipelineContext


def transcribe(ctx: PipelineContext) -> None:
    run_command(
        python_module(ctx.scripts.transcribe_cloud, str(ctx.main_video),
                      "--output", str(ctx.outputs.srt)),
        label="transcribe_cloud.py",
    )
    require_output(ctx.outputs.srt)
    require_output(ctx.outputs.srt_1word)
