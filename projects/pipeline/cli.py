"""Command-line entry point: parse arguments, show the plan, run the steps."""

import argparse
import sys
import time
from pathlib import Path

from utils import get_video_info, print_error, print_info, print_success, print_warning

from pipeline.common import PipelineError
from pipeline.context import Outputs, PipelineContext, Scripts
from pipeline.cuts import CUTS_FILENAME, load_cuts
from pipeline.runner import PipelineRunner, plan_steps
from pipeline.status import confirm_execution, print_overview
from pipeline.steps import REVIEW_STEP, build_steps
from pipeline.summary import RunSummary, format_duration, write_summary

RULE = "=" * 60


def get_main_input(args: argparse.Namespace) -> str | None:
    """Resolve the input path from ``--raw <path>`` or the positional."""
    if isinstance(args.raw, str):
        return args.raw
    return args.video


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Full video processing pipeline for a pre-composed recording (e.g. OBS): "
                    "Groq cloud transcription, silence trimming, audio processing, BGM addition, "
                    "review and metadata generation."
    )
    parser.add_argument("video", nargs="?", default=None, help="Path to the raw video file.")
    # Raw is the only mode now. --raw is still accepted (as a flag or with the
    # path) because older callers always pass it.
    parser.add_argument("--raw", "-r", dest="raw", nargs="?", const=True, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--bgm", "--bgm-track", dest="bgm", default=None,
                        help="BGM track file path or track name in BGM directory.")
    parser.add_argument("--title", "-t", default=None,
                        help="Optional custom title for the video project (default: project folder name).")
    parser.add_argument("--volume", type=int, default=10, help="Volume percentage for BGM (1-100, default: 10).")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt and proceed automatically.")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Force re-execution of all pipeline steps, ignoring existing step files.")
    parser.add_argument("--skip-review", action="store_true", help="Skip audio and video inspection review step.")
    parser.add_argument(
        "--clean-intermediates", action="store_true",
        help="Delete the intermediate raw-audio.wav after final.mp4 is verified.",
    )

    args = parser.parse_args()
    if not get_main_input(args):
        print_error("Error: Input raw video file is required.")
        parser.print_help()
        sys.exit(1)
    return args


def resolve_input(args: argparse.Namespace) -> Path:
    """Return the raw video path, checking the file exists."""
    main_video = Path(get_main_input(args)).resolve()
    if not main_video.is_file():
        raise PipelineError(f"Raw video file not found at '{main_video}'")
    return main_video


def build_context(args: argparse.Namespace) -> PipelineContext:
    main_video = resolve_input(args)
    video_dir = main_video.parent
    video_info = get_video_info(main_video)
    summary = RunSummary(
        project_title=args.title or video_dir.name,
        raw_input_filename=main_video.name,
        bgm_track=args.bgm,
        resolution=f"{video_info.width}x{video_info.height}",
        fps=video_info.fps,
    )
    cuts_file = video_dir / CUTS_FILENAME
    if not cuts_file.is_file():
        cuts_file = None
    else:
        summary.cuts = load_cuts(cuts_file, video_info.duration)
    return PipelineContext(
        args=args,
        main_video=main_video,
        video_dir=video_dir,
        video_info=video_info,
        outputs=Outputs.for_project(main_video, video_dir),
        scripts=Scripts.locate(),
        summary=summary,
        force=args.force,
        cuts_file=cuts_file,
        cuts=summary.cuts,
    )


def existing_output_names(ctx: PipelineContext) -> list[str]:
    o = ctx.outputs
    candidates = [
        o.srt, o.srt_1word, o.step2, o.processed_audio, o.final, o.metadata,
        o.trimmed_srt, ctx.video_dir / "trimmed-1word.srt",
    ]
    return list(dict.fromkeys(p.name for p in candidates if p.exists()))


def clean_intermediates(ctx: PipelineContext) -> None:
    """Delete the Step 2 intermediate. processed-audio.wav stays: Step 6 reviews it."""
    path = ctx.outputs.step2
    if not path.exists():
        return
    try:
        path.unlink()
        print_info(f"Cleaned up intermediate file: {path.name}")
    except OSError as e:
        print_warning(f"Could not delete intermediate file {path}: {e}")


def print_completion(ctx: PipelineContext, total: float) -> None:
    print()
    print_success(RULE)
    print_success(" 🎉 FULL VIDEO PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    print_success(f" Final review video saved at: {ctx.outputs.final}")
    print_success(f" Project metadata saved at:   {ctx.outputs.metadata}")
    if not ctx.summary.encode_integrity_ok:
        print_warning(" ⚠️  ENCODE INTEGRITY WARNING: final.mp4 looks truncated or stuck on a single frame.")
    print_info("-" * 60)
    print_info(" Step Execution Timing Summary:")
    for index, step in sorted(ctx.summary.steps.items()):
        label = f"  Step {index} ({step.name}):"
        print(f"{label:<38}{format_duration(step.duration_seconds)}")
    print_info("-" * 60)
    print_success(f" Total Execution Time:            {format_duration(total)}")
    print_success(RULE)


def finish_summary(ctx: PipelineContext, total: float) -> None:
    """Fill in the end-of-run fields and write summary.md (also after a failure)."""
    summary = ctx.summary
    summary.total_execution_time = total
    final = ctx.outputs.final
    summary.final_duration = get_video_info(final).duration if final.exists() else 0.0
    if ctx.args.clean_intermediates and final.exists():
        clean_intermediates(ctx)
    summary.output_files = existing_output_names(ctx)
    path = write_summary(summary, REVIEW_STEP, ctx.video_dir)
    print_success(f" Run summary saved at: {path}")


def main() -> None:
    args = parse_cli_args()
    try:
        ctx = build_context(args)
    except PipelineError as e:
        print_error(f"Error: {e}")
        sys.exit(e.exit_code)

    steps = build_steps(ctx)
    print_overview(ctx, steps, plan_steps(steps, ctx))
    confirm_execution(args.yes)

    print()
    print_info("Starting video processing pipeline...")
    start = time.perf_counter()
    exit_code = 0
    try:
        PipelineRunner(steps, ctx).run()
        print_completion(ctx, time.perf_counter() - start)
    except PipelineError as e:
        print_error(f"[ERROR] {e}")
        exit_code = e.exit_code
    except KeyboardInterrupt:
        print()
        print_error("Processing cancelled by user.")
        exit_code = 130
    finally:
        finish_summary(ctx, time.perf_counter() - start)
    if exit_code:
        sys.exit(exit_code)
