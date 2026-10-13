"""Raw-mode Steps 2 and 4: a single pre-composed recording (e.g. OBS).

Step 2 extracts only the speech audio, so Step 3 can clean it before any video
is encoded. Step 4 then encodes the video straight from the source through the
same speech-interval timeline, muxing in the processed audio (and BGM) in one
pass. No trimmed intermediate video is ever written.
"""

from utils import VideoQCCollector, print_info, print_warning, run_ffmpeg_with_progress
from trim_silences import get_silence_trim_expressions, get_speech_intervals

from pipeline.common import PipelineError, is_valid_file, remove_quietly, require_output
from pipeline.context import PipelineContext
from pipeline.ffmpeg import AAC_ARGS, X264_ARGS, ffmpeg_cmd, resolve_bgm_path, write_concat_list
from pipeline.summary import VideoFlag

# Black-frame QC branch split off the encode's filtergraph. 320px wide is
# plenty for blackdetect's luminance math and costs almost nothing.
# Freeze detection is deliberately absent: this branch sees the encoder's
# *input*, where static screen recordings look frozen. Real encoder stalls are
# caught on the finished file by Step 5.
INLINE_QC_FILTER = "scale=320:-2:flags=fast_bilinear,blackdetect=d=0.1:pic_th=0.98:pix_th=0.10"


def _speech_duration(intervals: list[tuple[float, float]]) -> float:
    return get_silence_trim_expressions(intervals)[2]


def _source_input(ctx: PipelineContext, intervals: list[tuple[float, float]]) -> tuple[list[str], str | None]:
    """FFmpeg input args reading only the speech intervals of the source.

    Returns ``(args, concat_list_path)``; the caller deletes the list file.
    Without intervals the whole source is used.
    """
    if not intervals:
        return ["-i", str(ctx.main_video)], None
    list_path = write_concat_list(ctx.main_video, intervals)
    return ["-f", "concat", "-safe", "0", "-i", list_path], list_path


def extract_speech(ctx: PipelineContext) -> None:
    """Step 2: detect speech with Silero VAD and extract just that audio (no video decode)."""
    print_info("Analyzing audio for speech intervals with Silero VAD...")
    intervals = get_speech_intervals(ctx.main_video)
    ctx.speech_intervals = intervals
    print_info(f"Detected {len(intervals)} speech intervals.")
    if not intervals:
        print_warning("No speech detected. Extracting the full audio track.")

    duration = _speech_duration(intervals)
    output = ctx.outputs.step2
    output.parent.mkdir(parents=True, exist_ok=True)

    input_args, list_path = _source_input(ctx, intervals)
    audio_filter = "aresample=async=1:first_pts=0"
    if intervals:
        audio_filter += ",asetpts=N/SR/TB"
    cmd = [
        *ffmpeg_cmd(), *input_args,
        "-vn", "-map", "0:a:0", "-af", audio_filter,
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
    ]
    if duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd.append(str(output))

    try:
        run_ffmpeg_with_progress(cmd, duration, "Speech Audio Extraction")
    finally:
        if list_path:
            remove_quietly(list_path)
    require_output(output)


def encode_final(ctx: PipelineContext) -> None:
    """Step 4: encode the trimmed video and mux the processed audio (+ BGM) in one pass."""
    if not is_valid_file(ctx.outputs.processed_audio):
        raise PipelineError(f"processed audio missing at '{ctx.outputs.processed_audio}'")
    bgm_file = resolve_bgm_path(ctx.args.bgm) if ctx.args.bgm else None

    intervals = ctx.speech_intervals
    if intervals is None:
        # Step 2 was skipped on this run, so the intervals were never detected.
        print_info("Re-running Silero VAD to rebuild the speech timeline...")
        intervals = get_speech_intervals(ctx.main_video)
    duration = _speech_duration(intervals)

    info = ctx.video_info
    video_filter = "fps=30,setpts=N/30/TB"
    if ctx.needs_downscale:
        print_warning(f"Video resolution {info.width}x{info.height} exceeds 1080p; downscaling to 1080p.")
        video_filter = f"scale=1920:1080:flags=bicubic,{video_filter}"

    # [v_enc] feeds the encoder; [v_qc] runs blackdetect on the same decoded
    # frames, so black-frame QC needs no separate pass over the output.
    filter_complex = (
        f"[0:v]{video_filter},split=2[v_enc][v_qc];"
        f"[v_qc]{INLINE_QC_FILTER}[v_qc_out]"
    )

    input_args, list_path = _source_input(ctx, intervals)
    input_args += ["-i", str(ctx.outputs.processed_audio)]
    if bgm_file is not None:
        # Loop the BGM for the whole video; amix duration=first stops it at the
        # end of the speech. Both tracks go stereo first so the mono voice
        # does not collapse the music's stereo image.
        input_args += ["-stream_loop", "-1", "-i", str(bgm_file)]
        filter_complex += (
            ";[1:a]aresample=48000,aformat=channel_layouts=stereo[main_a];"
            f"[2:a]aresample=48000,volume={ctx.args.volume}/100,aformat=channel_layouts=stereo[music];"
            "[main_a][music]amix=inputs=2:duration=first:dropout_transition=0[out_a]"
        )
        audio_map = "[out_a]"
    else:
        audio_map = "1:a:0"

    # blackdetect reports at info level, so this encode can't use -loglevel error.
    cmd = [
        *ffmpeg_cmd("info"), *input_args,
        "-filter_complex", filter_complex,
        "-map", "[v_enc]", "-map", audio_map,
        "-fps_mode", "cfr", *X264_ARGS, *AAC_ARGS, "-ac", "2",
    ]
    if duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", str(ctx.outputs.final),
            "-map", "[v_qc_out]", "-f", "null", "-"]

    qc = VideoQCCollector()
    print(f"Executing: {' '.join(cmd)}")
    try:
        run_ffmpeg_with_progress(cmd, duration, "Final Encode & Multiplex", stderr_line_callback=qc.handle_line)
        qc.finalize(duration)
    finally:
        if list_path:
            remove_quietly(list_path)
    require_output(ctx.outputs.final)

    ctx.summary.video_flags = [VideoFlag.from_json(g["type"], g) for g in qc.glitches]
    ctx.summary.inline_video_qc = True
    if bgm_file is not None:
        print_info(f"Mixed in BGM: {bgm_file.name}")
