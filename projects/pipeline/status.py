"""Pre-run overview of the inputs and what each step will do."""

import sys

from utils import COLOR_BLUE, COLOR_GREEN, COLOR_RESET, COLOR_YELLOW, print_error, print_info, print_warning

from pipeline.context import PipelineContext
from pipeline.runner import Step
from pipeline.steps.webcam import overlay_width

RULE = "=" * 60


def _status_label(reason: str | None, forced: bool) -> str:
    if reason is None:
        text = "WILL EXECUTE (FORCED)" if forced else "WILL EXECUTE"
        return f"{COLOR_YELLOW}[{text}]{COLOR_RESET}"
    if reason.startswith("already complete"):
        return f"{COLOR_GREEN}[ALREADY COMPLETE - SKIPPING]{COLOR_RESET}"
    return f"{COLOR_BLUE}[SKIPPING: {reason}]{COLOR_RESET}"


def print_overview(ctx: PipelineContext, steps: list[Step], plan: dict[int, str | None]) -> None:
    args = ctx.args
    print_info(RULE)
    print_info("            FULL VIDEO PROCESSING PIPELINE")
    print_info(RULE)
    if ctx.raw_mode:
        print(f" Raw video:        {ctx.main_video}")
        print(" Pipeline mode:    Raw Pre-Composed Video (Skip Webcam Overlay)")
    else:
        print(f" Web camera video: {ctx.main_video}")
        print(f" Screen video:     {ctx.screen_video}")
        print(f" Overlay width:    {overlay_width(args)}px")
        print(f" Overlay mode:     {'Continuous (--all)' if args.all else 'Dynamic voice commands'}")

    print(f" Output directory: {ctx.video_dir}")
    print(f" Project title:    {ctx.summary.project_title}")
    if args.bgm:
        print(f" BGM track:        {args.bgm} (Volume: {args.volume}%)")
    else:
        print_warning(" BGM track:        None specified")
    if ctx.raw_mode and ctx.needs_downscale:
        print_warning(" Resolution check: >1080p, downscaled to 1080p during the final encode")
    else:
        print_info(f" Resolution:       {ctx.summary.resolution}")
    if args.force:
        print_warning(" Force re-run:     --force option enabled (re-executing all steps)")

    print_info("-" * 60)
    print_info("Pipeline Steps Overview:")
    for step in sorted(steps, key=lambda s: s.index):
        print(f" {step.index}. {step.description:<68} {_status_label(plan[step.index], args.force)}")
    print_info(RULE)


def confirm_execution(skip_confirm: bool) -> None:
    """Ask for confirmation unless -y was given; exit when the user declines."""
    if skip_confirm:
        return
    try:
        answer = input("Proceed with video processing? [Y/n]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print_error("Processing cancelled.")
        sys.exit(1)
    if answer.lower().startswith("n"):
        print_warning("Processing cancelled by user.")
        sys.exit(0)
