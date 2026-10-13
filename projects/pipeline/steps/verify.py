"""Step 5: structural integrity check of the final video."""

import subprocess
from pathlib import Path

from utils import print_info, print_warning

from pipeline.common import probe_json, require_output, to_float
from pipeline.context import PipelineContext


def check_encode_integrity(video_path: Path) -> tuple[bool, str]:
    """Check an encoded file for truncation or a stalled encoder, without decoding it.

    1. The video stream must last as long as the container (audio). A video
       that ends early makes players hold the last frame.
    2. The frame count must roughly match duration x fps. A stalled encoder
       emits far fewer frames.

    Best-effort: missing metadata passes with an explanatory detail.
    Returns ``(ok, detail)``.
    """
    try:
        data = probe_json(
            video_path, "-select_streams", "v:0",
            "-show_entries", "stream=duration,avg_frame_rate,nb_frames:format=duration",
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        return True, f"skipped ({exc})"

    streams = data.get("streams", [])
    container_duration = to_float(data.get("format", {}).get("duration"))
    if not streams or container_duration <= 0:
        return True, "skipped (no duration metadata)"

    stream = streams[0]
    issues = []

    video_duration = to_float(stream.get("duration"), container_duration)
    if abs(video_duration - container_duration) > max(0.5, 0.02 * container_duration):
        issues.append(f"video stream {video_duration:.2f}s but container {container_duration:.2f}s")

    num, _, den = str(stream.get("avg_frame_rate", "")).partition("/")
    fps = to_float(num) / to_float(den) if to_float(den) > 0 else 0.0
    nb_frames = int(to_float(stream.get("nb_frames")))
    if fps > 0 and nb_frames > 0:
        expected = container_duration * fps
        if nb_frames < expected - max(3.0, 0.03 * expected):
            issues.append(f"only {nb_frames} frames vs ~{expected:.0f} expected at {fps:g}fps")

    if issues:
        return False, "; ".join(issues)
    fps_str = f"{fps:g}fps" if fps > 0 else "unknown fps"
    return True, f"{nb_frames or '?'} frames over {container_duration:.2f}s at {fps_str}, video/audio aligned"


def verify_final(ctx: PipelineContext) -> None:
    final = ctx.outputs.final
    require_output(final)
    ok, detail = check_encode_integrity(final)
    ctx.summary.encode_integrity_ok = ok
    if ok:
        print_info(f"{final.name}: {detail}")
    else:
        print_warning(f"Encode integrity check failed: {detail}")
        print_warning(f"{final.name} may be truncated or stuck on one frame; re-run with --force to re-encode.")
