"""The pipeline's step list for raw and webcam mode."""

from pipeline.context import PipelineContext
from pipeline.runner import Step
from pipeline.steps import raw, webcam
from pipeline.steps.audio import process_audio
from pipeline.steps.metadata import create_metadata
from pipeline.steps.review import review, review_skip_reason
from pipeline.steps.transcribe import transcribe
from pipeline.steps.verify import verify_final

REVIEW_STEP = 6


def build_steps(ctx: PipelineContext) -> list[Step]:
    """Steps in execution order.

    Step 7 (metadata) is listed before Step 6 (review): it only needs the
    captions, so it starts in the background and its LLM call overlaps the
    review. Step 1 also runs in the background unless Step 2 needs the
    captions to find the "webcam start/stop" voice commands.
    """
    args = ctx.args
    captions_needed_by_step2 = not ctx.raw_mode and not args.all

    if ctx.raw_mode:
        step2 = Step(
            2, "Speech Extraction", "Speech Extraction (Silero VAD + Audio-Only Trim)",
            "Extract speech (Silero VAD) -> raw-audio.wav",
            raw.extract_speech, skip_check=raw.extract_speech_skip_reason,
        )
        step4 = Step(
            4, "Final Encode & Multiplex", "Final Encode & Multiplex (Single Pass)",
            f"Encode video + processed audio{' + BGM' if args.bgm else ''} -> final.mp4",
            raw.encode_final, outputs=lambda o: (o.final,),
        )
    else:
        step2 = Step(
            2, "Mask & Silence Trim", "Webcam Mask + Silence Trim (Single Pass)",
            "Webcam mask + silence trim (single pass) -> after-trim-silences.mp4",
            webcam.mask_and_trim, outputs=lambda o: (o.step2,),
        )
        step4 = Step(
            4, "BGM + Finalize", "Finalize Output (Processed Audio + BGM Remux)",
            f"Remux with processed audio{' + BGM' if args.bgm else ''} -> final.mp4",
            webcam.finalize, outputs=lambda o: (o.final,),
        )

    return [
        Step(
            1, "Transcription", "Transcribe Video (Groq Cloud)",
            "Transcribe via Groq cloud -> captions .srt",
            transcribe, outputs=lambda o: (o.srt_1word, o.srt),
            background=not captions_needed_by_step2,
        ),
        step2,
        Step(
            3, "Audio Processing", "Process Audio (DeepFilterNet + Normalize)",
            "Process audio (DeepFilterNet + normalize) -> processed-audio.wav",
            process_audio, outputs=lambda o: (o.processed_audio,),
        ),
        step4,
        Step(
            5, "Verify Final", "Verify Final Output",
            "Verify final output (encode integrity)",
            verify_final, skip_check=lambda ctx, force: None, invalidates_downstream=False,
        ),
        Step(
            7, "Auto Create Metadata", "Generate Project Metadata",
            "Generate project metadata -> metadata.json",
            create_metadata, outputs=lambda o: (o.metadata,), invalidates_downstream=False,
            background=True, waits_for=(1,),
        ),
        Step(
            REVIEW_STEP, "Audio & Video Review", "Review Final Video (Audio & Video Inspection)",
            "Review final video (audio_review.py + video_inspector.py)",
            review, skip_check=review_skip_reason, invalidates_downstream=False,
        ),
    ]
