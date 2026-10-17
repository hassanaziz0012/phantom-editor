"""Webcam-mode Steps 2 and 4: separate webcam + screen recordings.

Step 2 overlays the masked webcam on the screen recording and cuts the
silences in one FFmpeg pass, writing after-trim-silences.mp4. Step 4 swaps in
the processed audio (and BGM) without re-encoding the video.
"""

import shutil
import sys

from utils import get_video_info, parse_srt, print_info, print_warning, run_ffmpeg_with_progress
from auto_attach_webcam_mask import (
    build_webcam_mask_filter_string, detect_overlay_ranges, generate_mask_png, get_timeline_segments
)
from trim_silences import get_silence_trim_expressions, get_speech_intervals

from pipeline.common import PipelineError, require_output, run_command
from pipeline.context import PipelineContext
from pipeline.ffmpeg import AAC_ARGS, X264_ARGS, ffmpeg_cmd

PRESET_OVERLAY_WIDTHS = {"portrait": 400, "landscape": 550}
MASK_CORNER_RADIUS = 20
OVERLAY_OFFSET = 20


def overlay_width(args) -> int:
    return args.width if args.width is not None else PRESET_OVERLAY_WIDTHS[args.preset]


def _ask_layout(skip_confirm: bool) -> str:
    """Ask how to render a video with no webcam voice commands: 'screen' or 'webcam'."""
    print_warning("\n" + "=" * 60)
    print_warning("⚠️  No voice commands ('webcam start' / 'webcam stop') detected.")
    print_warning("=" * 60)
    print("Please choose how to render the video:")
    print("  1) screen : Overlay webcam in the top-right corner over the screen recording (entire video)")
    print("  2) webcam : Full-screen webcam video only (no screen recording displayed)")
    print()

    if skip_confirm or not sys.stdin.isatty():
        print_info("Non-interactive / --yes mode: defaulting to option 1 ('screen').")
        return "screen"
    try:
        answer = input("Select layout option [1=screen / 2=webcam] (default: screen): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        raise PipelineError("Processing cancelled by user.")
    return "webcam" if answer in ("2", "webcam", "w") else "screen"


def _overlay_ranges(ctx: PipelineContext, duration: float) -> list[tuple[float, float]]:
    """Time ranges where the webcam overlay is shown."""
    if ctx.args.all:
        return [(0.0, duration)]

    try:
        captions = parse_srt(ctx.outputs.srt_1word)
    except Exception as e:
        raise PipelineError(f"could not parse captions: {e}") from e

    if captions:
        ranges, commands = detect_overlay_ranges(captions, default_overlay=False, total_duration=duration)
        if commands:
            return ranges
    else:
        print_warning("⚠️ Captions file contains no valid subtitle intervals.")

    if _ask_layout(ctx.args.yes) == "webcam":
        print_info("⏩ Selected 'webcam': full-screen webcam video (no screen overlay).")
        return []
    print_info("⏩ Selected 'screen': webcam overlaid top-right for the entire video.")
    return [(0.0, duration)]


def mask_and_trim(ctx: PipelineContext) -> None:
    """Step 2: webcam mask overlay + silence trimming in a single FFmpeg pass."""
    webcam_path, screen_path = ctx.main_video, ctx.screen_video
    webcam = ctx.video_info
    screen = get_video_info(screen_path)
    if webcam.duration <= 0:
        raise PipelineError("could not read the webcam video's duration")

    screen_w = screen.width or 1920
    screen_h = screen.height or 1080
    target_fps = max(24, min(60, int(round(max(webcam.fps, screen.fps)))))
    if webcam.has_audio:
        audio_src = "1:a"
    elif screen.has_audio:
        audio_src = "0:a"
    else:
        audio_src = None

    segments = get_timeline_segments(_overlay_ranges(ctx, webcam.duration), webcam.duration)

    intervals = get_speech_intervals(webcam_path)
    select_expr, shift_expr, speech_duration = get_silence_trim_expressions(intervals)

    width = overlay_width(ctx.args)
    scaled_h = int(round((width * webcam.height / webcam.width) / 2) * 2)
    temp_dir = ctx.video_dir / f"_tmp_{webcam_path.stem}_singlepass"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        mask_path = temp_dir / "webcam_mask.png"
        generate_mask_png(mask_path, width, scaled_h, r=MASK_CORNER_RADIUS)
        mask_filter = build_webcam_mask_filter_string(
            segments=segments,
            target_fps=target_fps,
            w=width,
            scaled_h=scaled_h,
            offset=OVERLAY_OFFSET,
            screen_w=screen_w,
            screen_h=screen_h,
            is_all=ctx.args.all,
            output_label="composite_v",
        )

        if select_expr and shift_expr:
            filters = [mask_filter, f"[composite_v]select='{select_expr}',setpts='(T-({shift_expr}))/TB',fps=30[out_v]"]
            if audio_src:
                filters.append(
                    f"[{audio_src}]aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB',"
                    "aresample=async=1:first_pts=0[out_a]"
                )
            audio_map = "[out_a]"
            duration = speech_duration
        else:
            filters = [mask_filter, "[composite_v]fps=30[out_v]"]
            audio_map = audio_src
            duration = webcam.duration

        cmd = [
            *ffmpeg_cmd(),
            "-i", str(screen_path),
            "-i", str(webcam_path),
            "-loop", "1", "-i", str(mask_path),
            "-filter_complex", ";".join(filters),
            "-map", "[out_v]",
        ]
        if audio_src:
            cmd += ["-map", audio_map, *AAC_ARGS]
        cmd += [
            "-fps_mode", "cfr", "-t", f"{duration:.3f}", *X264_ARGS,
            "-movflags", "+faststart", str(ctx.outputs.step2),
        ]
        run_ffmpeg_with_progress(cmd, duration, "Single-Pass Encoding (Mask + Silence Trim)")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    require_output(ctx.outputs.step2)


def finalize(ctx: PipelineContext) -> None:
    """Step 4: mux the processed audio (plus BGM, if any) onto the trimmed video."""
    trimmed, processed_audio, final = ctx.outputs.step2, ctx.outputs.processed_audio, ctx.outputs.final
    if ctx.args.bgm:
        print_info(f"Adding BGM track: {ctx.args.bgm} at {ctx.args.volume}% volume")
        cmd = [
            "bash", str(ctx.scripts.add_bgm), str(trimmed), ctx.args.bgm,
            "--volume", str(ctx.args.volume),
            "--pipeline-mode", "--processed-audio", str(processed_audio),
            str(final),
        ]
        label = "add_bgm_to_video.sh"
    else:
        print_info("No BGM specified. Remuxing video with processed audio only.")
        cmd = [
            *ffmpeg_cmd(),
            "-i", str(trimmed), "-i", str(processed_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            # Mono like the processed voice: a stereo upmix is ~3.4x slower to encode.
            "-c:v", "copy", *AAC_ARGS, "-ac", "1",
            "-movflags", "+faststart", str(final),
        ]
        label = "ffmpeg"
    run_command(cmd, label=label)
    require_output(final)
