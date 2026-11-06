"""The pipeline's step list."""

from pipeline.context import PipelineContext
from pipeline.runner import Step
from pipeline.steps import raw
from pipeline.steps.audio import process_audio
from pipeline.steps.metadata import create_metadata
from pipeline.steps.review import review, review_skip_reason
from pipeline.steps.transcribe import transcribe
from pipeline.steps.verify import verify_final

REVIEW_STEP = 6


def build_steps(ctx: PipelineContext) -> list[Step]:
    """Steps in execution order.

    Step 7 (metadata) is listed before Step 6 (review): it only needs
    final.mp4 (which it transcribes into trimmed.srt), so it starts in the
    background and its transcription and LLM calls overlap the review. Step 1
    also runs in the background: nothing before the metadata step needs it.
    """
    args = ctx.args
    return [
        Step(
            1, "Transcription", "Transcribe Video (Groq Cloud)",
            "Transcribe via Groq cloud -> captions .srt",
            transcribe, outputs=lambda o: (o.srt_1word, o.srt),
            background=True,
        ),
        Step(
            2, "Speech Extraction", "Speech Extraction (Silero VAD + Audio-Only Trim)",
            "Extract speech (Silero VAD) -> raw-audio.wav",
            raw.extract_speech, skip_check=raw.extract_speech_skip_reason,
        ),
        Step(
            3, "Audio Processing", "Process Audio (DeepFilterNet + Normalize)",
            "Process audio (DeepFilterNet + normalize) -> processed-audio.wav",
            process_audio, outputs=lambda o: (o.processed_audio,),
        ),
        Step(
            4, "Final Encode & Multiplex", "Final Encode & Multiplex (Single Pass)",
            f"Encode video + processed audio{' + BGM' if args.bgm else ''} -> final.mp4",
            raw.encode_final, outputs=lambda o: (o.final,),
        ),
        Step(
            5, "Verify Final", "Verify Final Output",
            "Verify final output (encode integrity)",
            verify_final, skip_check=lambda ctx, force: None, invalidates_downstream=False,
        ),
        Step(
            7, "Auto Create Metadata", "Generate Project Metadata",
            "Transcribe final video + generate project metadata -> metadata.json",
            create_metadata, outputs=lambda o: (o.metadata,), invalidates_downstream=False,
            background=True,
        ),
        Step(
            REVIEW_STEP, "Audio & Video Review", "Review Final Video (Audio & Video Inspection)",
            "Review final video (audio_review.py + video_inspector.py)",
            review, skip_check=review_skip_reason, invalidates_downstream=False,
        ),
    ]
