"""Steps 2 and 4: cut a single pre-composed recording (e.g. OBS).

Step 2 extracts only the speech audio, so Step 3 can clean it before any video
is encoded. Step 4 then encodes the video straight from the source through the
same speech-interval timeline, muxing in the processed audio (and BGM) in one
pass. No trimmed intermediate video is ever written.

Both steps cut with the same select expression over a 1/30 s grid: intervals
are snapped to video frames and the audio is chunked into frame-sized blocks,
so each speech interval keeps exactly the same length in both streams and the
cuts cannot drift apart. (The concat demuxer was tried here and dropped: its
inpoint seeks land on the previous keyframe, which on OBS's 4 s GOP leaked the
lead-in into the output and shifted every cut.)

Ranges listed in the project's cuts.txt (bad takes) are removed from the speech
intervals before snapping, so they are cut by this same encode.

With --relight, Step 4 on Colab splits into decode -> face relight -> encode on
the VM (see colab.py and remote/relight.py); the cut, audio mix, BGM and inline
QC are the same.
"""

from pathlib import Path

from video_editing.utils import VideoQCCollector, print_info, print_warning, run_ffmpeg_with_progress
from video_editing.trim_silences import get_silence_trim_expressions, get_speech_intervals

from projects.pipeline.colab import ColabUnavailable, RelightFailed
from projects.pipeline.common import PipelineError, is_valid_file, require_output
from projects.pipeline.context import PipelineContext
from projects.pipeline.cuts import subtract_cuts
from projects.pipeline.ffmpeg import AAC_ARGS, X264_ARGS, ffmpeg_cmd, resolve_bgm_path
from projects.pipeline.summary import VideoFlag

FPS = 30
AUDIO_RATE = 48000
SAMPLES_PER_FRAME = AUDIO_RATE // FPS

# Black-frame QC branch split off the encode's filtergraph. 320px wide is
# plenty for blackdetect's luminance math and costs almost nothing.
# Freeze detection is deliberately absent: this branch sees the encoder's
# *input*, where static screen recordings look frozen. Real encoder stalls are
# caught on the finished file by Step 5.
INLINE_QC_FILTER = "scale=320:-2:flags=fast_bilinear,blackdetect=d=0.1:pic_th=0.98:pix_th=0.10"

# The face relight is tuned for (and only tested on) 1080p frames.
RELIGHT_SIZE = (1920, 1080)
# The relight works in BT.709 limited range; tag both its raw frames and the output so.
BT709_TAGS = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _detect_speech(ctx: PipelineContext) -> list[tuple[float, float]]:
    """Silero VAD speech intervals minus the manual cuts, snapped to the 1/30 s video frame grid."""
    snapped = []
    for start, end in subtract_cuts(get_speech_intervals(ctx.main_video), ctx.cuts):
        first, last = round(start * FPS), round(end * FPS)
        if last > first:
            snapped.append((first / FPS, last / FPS))
    if ctx.cuts and not snapped:
        raise PipelineError(f"{ctx.cuts_file.name} removes all of the speech")
    return snapped


def _speech_duration(intervals: list[tuple[float, float]]) -> float:
    return get_silence_trim_expressions(intervals)[2]


def _select_expr(intervals: list[tuple[float, float]]) -> str:
    """Select expression keeping the grid frames inside ``intervals``.

    Bounds sit half a frame early, so a frame starting exactly on an interval
    edge is never ambiguous: frames ``start*FPS`` up to ``end*FPS - 1`` pass.
    """
    half = 0.5 / FPS
    return get_silence_trim_expressions([(s - half, e - half) for s, e in intervals])[0]


def extract_speech_skip_reason(ctx: PipelineContext, force: bool) -> str | None:
    """Skip Step 2 when raw-audio.wav exists and cuts.txt has not changed since."""
    output = ctx.outputs.step2
    if force or not is_valid_file(output):
        return None
    if ctx.cuts_file is not None and ctx.cuts_file.stat().st_mtime > output.stat().st_mtime:
        return None
    return f"already complete -> {output.name}"


def extract_speech(ctx: PipelineContext) -> None:
    """Step 2: detect speech with Silero VAD and extract just that audio (no video decode)."""
    print_info("Analyzing audio for speech intervals with Silero VAD...")
    intervals = _detect_speech(ctx)
    ctx.speech_intervals = intervals
    print_info(f"Detected {len(intervals)} speech intervals.")
    if ctx.cuts:
        print_info(f"Removed {len(ctx.cuts)} manual cut(s) from {ctx.cuts_file.name}.")
    if not intervals:
        print_warning("No speech detected. Extracting the full audio track.")

    duration = _speech_duration(intervals)
    output = ctx.outputs.step2
    output.parent.mkdir(parents=True, exist_ok=True)

    # async fills any timestamp gaps first, so the frame-sized chunks stay on
    # the same 1/30 s grid that Step 4 cuts the video on.
    audio_filter = f"aresample={AUDIO_RATE}:async=1:first_pts=0"
    if intervals:
        audio_filter += (
            f",asetnsamples=n={SAMPLES_PER_FRAME}:p=0"
            f",aselect='{_select_expr(intervals)}',asetpts=N/SR/TB"
        )
    cmd = [
        *ffmpeg_cmd(), "-i", str(ctx.main_video),
        "-vn", "-map", "0:a:0", "-af", audio_filter,
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "1",
    ]
    if duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd.append(str(output))

    run_ffmpeg_with_progress(cmd, duration, "Speech Audio Extraction")
    require_output(output)


def _drop_stale_face_copy(ctx: PipelineContext) -> None:
    """Delete final-face.mp4 once final.mp4 has been re-encoded.

    The excalidraw-broll skill replaces final.mp4 with its B-roll cut and keeps
    the face-only original as final-face.mp4. A fresh encode makes final.mp4
    face-only again, so that copy belongs to a cut that no longer exists and
    would otherwise be used as the face source of the next B-roll build.
    """
    face_copy = ctx.outputs.final.with_name(f"final-face{ctx.outputs.final.suffix}")
    if face_copy.is_file():
        face_copy.unlink()
        print_info(f"Removed {face_copy.name}: it was the face-only copy of the previous {ctx.outputs.final.name}.")


def _encode_on_colab(ctx: PipelineContext, build_cmd, relight_cmds, bgm_file: Path | None, duration: float,
                     description: str) -> VideoQCCollector | None:
    """Run the encode on the Colab T4. Returns its QC collector, or None to encode locally.

    Under --relight an unusable Colab raises PipelineError instead: a local encode can't relight.

    With ``relight_cmds`` it first tries the face relight; if that fails it retries
    as a plain Colab encode, with a fresh QC collector so no partial flags leak in.
    """
    if ctx.colab is None:
        return None
    extra_inputs = [ctx.outputs.processed_audio] + ([bgm_file] if bgm_file is not None else [])
    try:
        if relight_cmds is not None:
            qc = VideoQCCollector()
            try:
                ctx.colab.encode(build_cmd, extra_inputs, ctx.outputs.final, duration, description, qc.handle_line,
                                 relight_cmds=relight_cmds)
                ctx.summary.encoder = ctx.colab.label
                return qc
            except RelightFailed as e:
                print_warning(f"Face relight failed ({e}); encoding on Colab without it.")
        qc = VideoQCCollector()
        ctx.colab.encode(build_cmd, extra_inputs, ctx.outputs.final, duration, description, qc.handle_line)
    except ColabUnavailable as e:
        ctx.colab.stop()
        if relight_cmds is not None:
            # --relight only runs on the T4; a silent local encode would drop it.
            raise PipelineError(f"Colab encode failed ({e}), so the face relight can't run. Re-run to try "
                                "Colab again, or drop --relight to accept a local x264 encode.") from e
        print_warning(f"Colab encode unavailable ({e}); encoding locally with x264 instead.")
        return None
    ctx.summary.encoder = ctx.colab.label
    return qc


def encode_final(ctx: PipelineContext) -> None:
    """Step 4: encode the trimmed video and mux the processed audio (+ BGM) in one pass."""
    if not is_valid_file(ctx.outputs.processed_audio):
        raise PipelineError(f"processed audio missing at '{ctx.outputs.processed_audio}'")
    bgm_file = resolve_bgm_path(ctx.args.bgm) if ctx.args.bgm else None

    intervals = ctx.speech_intervals
    if intervals is None:
        # Step 2 was skipped on this run, so the intervals were never detected.
        print_info("Re-running Silero VAD to rebuild the speech timeline...")
        intervals = _detect_speech(ctx)
    duration = _speech_duration(intervals)

    info = ctx.video_info
    # fps first puts the frames on the grid the select expression assumes.
    video_filter = f"fps={FPS}"
    if intervals:
        video_filter += f",select='{_select_expr(intervals)}'"
    video_filter += f",setpts=N/{FPS}/TB"
    if ctx.needs_downscale:
        print_warning(f"Video resolution {info.width}x{info.height} exceeds 1080p; downscaling to 1080p.")
        video_filter += ",scale=1920:1080:flags=bicubic"

    if bgm_file is not None:
        # Loop the BGM for the whole video; amix duration=first stops it at the
        # end of the speech. Both tracks go stereo first so the mono voice
        # does not collapse the music's stereo image.
        audio_filter = (
            ";[1:a]aresample=48000,aformat=channel_layouts=stereo[main_a];"
            f"[2:a]aresample=48000,volume={ctx.args.volume}/100,aformat=channel_layouts=stereo[music];"
            "[main_a][music]amix=inputs=2:duration=first:dropout_transition=0[out_a]"
        )
        audio_map = "[out_a]"
        channels = "2"
    else:
        # The processed voice is mono; upmixing it to identical stereo channels
        # made the AAC encode ~3.4x slower for no audible difference.
        audio_map = "1:a:0"
        channels = "1"
        audio_filter = ""

    def build_cmd(paths: dict[Path, str], output: str, video_args: list[str], main_input_args: list[str],
                  video_input: list[str] | None = None, vf: str = video_filter) -> list[str]:
        """The encode's ffmpeg argv, with each local input mapped through ``paths``.

        ``video_input`` replaces the source video input (the relight feeds raw frames on
        stdin, already cut, so it passes ``vf="null"``).
        """
        # [v_enc] feeds the encoder; [v_qc] runs blackdetect on the same decoded
        # frames, so black-frame QC needs no separate pass over the output.
        filter_complex = f"[0:v]{vf},split=2[v_enc][v_qc];[v_qc]{INLINE_QC_FILTER}[v_qc_out]{audio_filter}"
        if video_input is None:
            video_input = ["-i", paths[ctx.main_video]]
        input_args = [*main_input_args, *video_input, "-i", paths[ctx.outputs.processed_audio]]
        if bgm_file is not None:
            input_args += ["-stream_loop", "-1", "-i", paths[bgm_file]]
        # blackdetect reports at info level, so this encode can't use -loglevel error.
        cmd = [
            *ffmpeg_cmd("info"), *input_args,
            "-filter_complex", filter_complex,
            "-map", "[v_enc]", "-map", audio_map,
            "-fps_mode", "cfr", *video_args, *AAC_ARGS, "-ac", channels,
        ]
        if duration > 0:
            cmd += ["-t", f"{duration:.3f}"]
        return cmd + ["-movflags", "+faststart", output, "-map", "[v_qc_out]", "-f", "null", "-"]

    out_size = (1920, 1080) if ctx.needs_downscale else (info.width, info.height)

    def relight_cmds(paths: dict[Path, str], output: str, video_args: list[str]):
        """Remote decode (cut timeline -> raw NV12 on stdout) and encode (raw NV12 on stdin) argvs."""
        w, h = out_size
        decode = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-hwaccel", "cuda",
            "-i", paths[ctx.main_video], "-map", "0:v:0", "-vf", video_filter, "-fps_mode", "passthrough",
        ]
        if duration > 0:
            decode += ["-t", f"{duration:.3f}"]
        decode += ["-f", "rawvideo", "-pix_fmt", "nv12", "-"]
        raw_in = ["-f", "rawvideo", "-pix_fmt", "nv12", "-video_size", f"{w}x{h}", "-framerate", str(FPS),
                  *BT709_TAGS, "-i", "-"]
        encode = build_cmd(paths, output, [*video_args, *BT709_TAGS], [], video_input=raw_in, vf="null")
        return decode, encode, round(duration * FPS), f"{w}x{h}"

    use_relight = bool(getattr(ctx.args, "relight", False))
    if use_relight and out_size != RELIGHT_SIZE:
        print_warning(f"Face relight supports 1920x1080 output only (this is {out_size[0]}x{out_size[1]}); "
                      "encoding without it.")
        use_relight = False

    description = "Final Encode & Multiplex"
    qc = _encode_on_colab(ctx, build_cmd, relight_cmds if use_relight else None, bgm_file, duration, description)
    if qc is None:
        local_paths = {p: str(p) for p in (ctx.main_video, ctx.outputs.processed_audio, bgm_file) if p is not None}
        cmd = build_cmd(local_paths, str(ctx.outputs.final), X264_ARGS, [])
        if use_relight:
            print_warning("Face relight needs the Colab T4; this local encode is not relit.")
        qc = VideoQCCollector()
        print(f"Executing: {' '.join(cmd)}")
        run_ffmpeg_with_progress(cmd, duration, description, stderr_line_callback=qc.handle_line)
        ctx.summary.encoder = "local (libx264)"
    qc.finalize(duration)
    require_output(ctx.outputs.final)
    _drop_stale_face_copy(ctx)

    ctx.summary.video_flags = [VideoFlag.from_json(g["type"], g) for g in qc.glitches]
    ctx.summary.inline_video_qc = True
    if bgm_file is not None:
        print_info(f"Mixed in BGM: {bgm_file.name}")
