"""Step 3: clean up the speech audio (DeepFilterNet + loudness normalization)."""

from pipeline.common import require_output, run_command
from pipeline.context import PipelineContext


def process_audio(ctx: PipelineContext) -> None:
    run_command(
        ["bash", str(ctx.scripts.process_audio), "--pipeline-mode",
         "--output-file", str(ctx.outputs.processed_audio), str(ctx.outputs.step2)],
        label="process_audio.sh",
    )
    require_output(ctx.outputs.processed_audio)
