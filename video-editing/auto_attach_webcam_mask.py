#!/usr/bin/env python3
import os
import sys
import re
import argparse
import subprocess
import shutil
import json
from pathlib import Path

# Add project root and video-editing directory to sys.path so we can import transcribe
video_editing_dir = Path(__file__).resolve().parent
repo_root = video_editing_dir.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from transcribe import transcribe_video
from utils import parse_timestamp, format_srt_time

def check_dependencies():
    """Verify that ffmpeg and ffprobe are available in the system PATH."""
    for cmd in ["ffmpeg", "ffprobe"]:
        if not shutil.which(cmd):
            print(f"Error: Required tool '{cmd}' is not installed or not in system PATH.", file=sys.stderr)
            sys.exit(1)

def get_video_duration(video_path):
    """Determine the duration of a video file in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error: Could not determine video duration for '{video_path}': {e}", file=sys.stderr)
        return None

def has_audio_stream(video_path):
    """Check if a video file contains at least one audio stream using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return "audio" in result.stdout.lower()
    except Exception as e:
        print(f"Warning: Could not check audio streams for '{video_path}': {e}", file=sys.stderr)
        return False

def get_video_properties(video_path):
    """Determine resolution, video codec, and pixel format of a video file using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name,pix_fmt",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if "streams" in data and len(data["streams"]) > 0:
            stream = data["streams"][0]
            return {
                "width": int(stream.get("width", 0)),
                "height": int(stream.get("height", 0)),
                "codec": stream.get("codec_name", ""),
                "pix_fmt": stream.get("pix_fmt", "")
            }
        return None
    except Exception as e:
        print(f"Error: Could not determine video properties for '{video_path}': {e}", file=sys.stderr)
        return None

def parse_srt(srt_path: Path):
    """Parses SRT captions into a list of (start_time, end_time, text) tuples."""
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT captions file not found: {srt_path}")
        
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    content = content.replace('\r\n', '\n').strip()
    blocks = re.split(r'\n\s*\n', content)
    
    captions = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        time_line = lines[1]
        match = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', time_line)
        if not match:
            continue
            
        start_t = parse_timestamp(match.group(1))
        end_t = parse_timestamp(match.group(2))
        text = " ".join(lines[2:]).strip()
        captions.append((start_t, end_t, text))
        
    return captions

def detect_overlay_ranges(captions, default_overlay=False, total_duration=None):
    """
    Scans the captions to identify "webcam start" and "webcam stop" voice commands.
    Resolves them into overlay time ranges using a state machine.
    
    Returns:
        list of (start_time, end_time) tuples.
    """
    overlay_ranges = []
    current_state = "overlay" if default_overlay else "raw"
    current_start = 0.0 if default_overlay else None
    
    i = 0
    n = len(captions)
    while i < n:
        # Check Case 1: "webcam start/stop" (single word or hyphenated)
        if i + 1 < n:
            w1_start, w1_end, w1_text = captions[i]
            w2_start, w2_end, w2_text = captions[i+1]
            
            w1_clean = w1_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            w2_clean = w2_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            
            if w1_clean == "webcam" and (w2_start - w1_end < 1.5):
                if w2_clean == "start":
                    print(f"📍 Found 'webcam start' command at {w1_start:.3f}s (words: '{w1_text} {w2_text}')")
                    if current_state == "raw":
                        current_state = "overlay"
                        current_start = w1_start
                    else:
                        print("   (Already in overlay state, ignoring start command)")
                    i += 2
                    continue
                elif w2_clean == "stop":
                    print(f"📍 Found 'webcam stop' command at {w2_end:.3f}s (words: '{w1_text} {w2_text}')")
                    if current_state == "overlay":
                        current_state = "raw"
                        overlay_ranges.append((current_start, w2_end))
                        current_start = None
                    else:
                        print("   (Already in raw state, ignoring stop command)")
                    i += 2
                    continue
                    
        # Check Case 2: "web cam start/stop"
        if i + 2 < n:
            w1_start, w1_end, w1_text = captions[i]
            w2_start, w2_end, w2_text = captions[i+1]
            w3_start, w3_end, w3_text = captions[i+2]
            
            w1_clean = w1_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            w2_clean = w2_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            w3_clean = w3_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            
            if w1_clean == "web" and w2_clean == "cam" and (w2_start - w1_end < 1.0) and (w3_start - w2_end < 1.5):
                if w3_clean == "start":
                    print(f"📍 Found 'web cam start' command at {w1_start:.3f}s (words: '{w1_text} {w2_text} {w3_text}')")
                    if current_state == "raw":
                        current_state = "overlay"
                        current_start = w1_start
                    else:
                        print("   (Already in overlay state, ignoring start command)")
                    i += 3
                    continue
                elif w3_clean == "stop":
                    print(f"📍 Found 'web cam stop' command at {w3_end:.3f}s (words: '{w1_text} {w2_text} {w3_text}')")
                    if current_state == "overlay":
                        current_state = "raw"
                        overlay_ranges.append((current_start, w3_end))
                        current_start = None
                    else:
                        print("   (Already in raw state, ignoring stop command)")
                    i += 3
                    continue
        
        i += 1
        
    # Handle end of video while still in overlay mode
    if current_state == "overlay" and current_start is not None:
        end_time = total_duration if total_duration is not None else captions[-1][1]
        overlay_ranges.append((current_start, end_time))
        
    return overlay_ranges

def get_timeline_segments(overlay_ranges, total_duration):
    """
    Splits the total duration into contiguous segments of type 'raw' or 'overlay'.
    """
    segments = []
    current_time = 0.0
    
    # Sort by start time, filter invalid, and merge overlapping ranges
    sorted_ranges = sorted(overlay_ranges, key=lambda x: x[0])
    merged_ranges = []
    for r_start, r_end in sorted_ranges:
        r_start = max(0.0, min(r_start, total_duration))
        r_end = max(r_start, min(r_end, total_duration))
        if r_end <= r_start:
            continue
        if not merged_ranges:
            merged_ranges.append((r_start, r_end))
        else:
            prev_start, prev_end = merged_ranges[-1]
            if r_start <= prev_end:
                # Overlap or adjacent - merge them
                merged_ranges[-1] = (prev_start, max(prev_end, r_end))
            else:
                merged_ranges.append((r_start, r_end))
                
    for start, end in merged_ranges:
        if start > current_time:
            segments.append({
                "start": current_time,
                "end": start,
                "type": "raw"
            })
        segments.append({
            "start": start,
            "end": end,
            "type": "overlay"
        })
        current_time = end
        
    if current_time < total_duration:
        segments.append({
            "start": current_time,
            "end": total_duration,
            "type": "raw"
        })
        
    return segments

def main():
    parser = argparse.ArgumentParser(
        description="Overlay webcam video in the top-right corner of screen footage with rounded corners "
                    "dynamically using 'webcam start' and 'webcam stop' voice commands."
    )
    parser.add_argument(
        "--screen",
        type=str,
        required=True,
        help="Path to the screen recording video file."
    )
    parser.add_argument(
        "--webcam",
        type=str,
        required=True,
        help="Path to the webcam recording video file."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Path to save the output video file (default: [screen_basename]_auto_webcam.mp4)."
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=400,
        help="Width of the webcam overlay in pixels (default: 400)."
    )
    parser.add_argument(
        "--radius", "-r",
        type=int,
        default=20,
        help="Corner radius for the webcam overlay rounded rectangle in pixels (default: 20)."
    )
    parser.add_argument(
        "--offset", "-d",
        type=int,
        default=20,
        help="Margin/offset from the top-right corner in pixels (default: 20)."
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default="medium",
        help="Whisper model size/path (default: 'medium')."
    )
    parser.add_argument(
        "--captions",
        type=str,
        default=None,
        help="Path to save/reuse the one-word captions file (default: [webcam_basename]_1word.srt)."
    )
    parser.add_argument(
        "--default-overlay",
        action="store_true",
        help="Start the video in overlay mode (default: False, starts in full-screen raw webcam mode)."
    )
    parser.add_argument(
        "--force-reencode",
        action="store_true",
        help="Force re-encoding of all video segments, bypassing stream copy optimization for raw segments."
    )

    args = parser.parse_args()

    screen_path = Path(args.screen).resolve()
    webcam_path = Path(args.webcam).resolve()

    if not screen_path.is_file():
        print(f"Error: Screen recording file not found at '{args.screen}'", file=sys.stderr)
        sys.exit(1)
    if not webcam_path.is_file():
        print(f"Error: Webcam recording file not found at '{args.webcam}'", file=sys.stderr)
        sys.exit(1)

    # Determine default captions file path
    if args.captions:
        captions_path = Path(args.captions).resolve()
    else:
        captions_path = webcam_path.parent / f"{webcam_path.stem}_1word.srt"

    check_dependencies()

    # Determine durations
    print(f"🔍 Probing webcam video duration...")
    webcam_duration = get_video_duration(webcam_path)
    if webcam_duration is None:
        print("Error: Could not retrieve webcam duration.", file=sys.stderr)
        sys.exit(1)
    print(f"🎬 Webcam duration: {webcam_duration:.3f} seconds.")

    # 1. Generate/reuse captions
    if not captions_path.is_file():
        print(f"📝 Auto-transcribing webcam file '{webcam_path}' using Whisper '{args.model}' model...")
        try:
            transcribe_video(
                video_path=str(webcam_path),
                model_path_or_size=args.model,
                output_srt_path=str(captions_path),
                max_words=1,
                uppercase=False,
                preview=False,
                vad_filter=True
            )
        except Exception as e:
            print(f"❌ Error during auto-transcription: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"📖 Using existing captions file at '{captions_path}'")

    # 2. Parse captions and detect commands
    try:
        captions = parse_srt(captions_path)
    except Exception as e:
        print(f"❌ Error parsing SRT file: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"💬 Parsed {len(captions)} caption intervals from SRT.")
    if not captions:
        print("⚠️ Captions file contains no valid subtitle intervals. Defaulting to empty overlay ranges.")
        if args.default_overlay:
            overlay_ranges = [(0.0, webcam_duration)]
        else:
            overlay_ranges = []
    else:
        overlay_ranges = detect_overlay_ranges(
            captions,
            default_overlay=args.default_overlay,
            total_duration=webcam_duration
        )

    print(f"⏰ Overlay timelines: {overlay_ranges}")

    # Determine audio streams
    has_webcam_audio = has_audio_stream(webcam_path)
    has_screen_audio = has_audio_stream(screen_path)

    if has_webcam_audio:
        print("🎵 Using webcam audio track.")
    elif has_screen_audio:
        print("⚠️ Webcam video has no audio. Falling back to screen audio.")
    else:
        print("ℹ️ Neither webcam nor screen video contains audio. Output will be video-only.")

    # Determine screen resolution
    screen_props = get_video_properties(screen_path)
    if screen_props is None:
        print("Warning: Could not check screen recording resolution. Defaulting to 1920x1080.")
        screen_w, screen_h = 1920, 1080
    else:
        screen_w, screen_h = screen_props["width"], screen_props["height"]
    print(f"🖥️  Screen properties: {screen_w}x{screen_h}")

    # Determine webcam properties
    webcam_props = get_video_properties(webcam_path)
    if webcam_props is None:
        print("Error: Could not retrieve webcam properties.", file=sys.stderr)
        sys.exit(1)

    webcam_w = webcam_props["width"]
    webcam_h = webcam_props["height"]

    # Resolve output path
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = screen_path.parent / f"{screen_path.stem}_auto_webcam{screen_path.suffix}"

    # Segment the timeline
    segments = get_timeline_segments(overlay_ranges, webcam_duration)
    print(f"🎬 Timeline split into {len(segments)} segments:")
    for idx, seg in enumerate(segments):
        print(f"   Segment {idx}: {seg['type']} from {seg['start']:.3f}s to {seg['end']:.3f}s (duration: {seg['end']-seg['start']:.3f}s)")

    temp_dir = output_path.parent / f"_tmp_{output_path.stem}_mask"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Pre-generate the static rounded corner mask to avoid using the extremely slow geq filter on every frame
    mask_path = temp_dir / "webcam_mask.png"
    w = args.width
    r = args.radius
    scaled_h = int(round((w * webcam_h / webcam_w) / 2) * 2)
    geq_expr = (
        f"if((lt(X,{r})+gt(X,W-{r}))*(lt(Y,{r})+gt(Y,H-{r})),"
        f"if(gt(sqrt(pow(X-if(lt(X,{r}),{r},W-{r}),2)+pow(Y-if(lt(Y,{r}),{r},H-{r}),2)),{r}),0,255),255)"
    )
    mask_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=white:s={w}x{scaled_h}:d=1",
        "-vf", f"format=gray,geq=lum='{geq_expr}'",
        "-frames:v", "1",
        str(mask_path)
    ]
    print(f"\n🖼️ Generating static rounded corner mask ({w}x{scaled_h})...")
    print(f"   Executing: {' '.join(mask_cmd)}")
    subprocess.run(mask_cmd, check=True)

    try:
        # Build conditions for raw and overlay modes based on segments
        raw_conditions = []
        overlay_conditions = []
        for seg in segments:
            if seg["type"] == "raw":
                raw_conditions.append(f"between(t,{seg['start']:.3f},{seg['end']:.3f})")
            elif seg["type"] == "overlay":
                overlay_conditions.append(f"between(t,{seg['start']:.3f},{seg['end']:.3f})")

        raw_enable_expr = "+".join(raw_conditions) if raw_conditions else "0"
        overlay_enable_expr = "+".join(overlay_conditions) if overlay_conditions else "0"

        # Build the dynamic single-pass FFmpeg command using filter complex
        # 1. Pad screen background indefinitely so it won't end before the webcam video.
        # 2. Split webcam video into raw and overlay paths.
        # 3. Scale raw webcam path to full screen, cropping to match aspect ratio.
        # 4. Scale overlay webcam path to corner size, apply mask.
        # 5. Overlay corner webcam onto padded screen (active during overlay segments).
        # 6. Overlay full webcam onto the result (active during raw segments).
        offset = args.offset
        filter_complex = (
            f"[0:v]tpad=stop_mode=clone:stop=-1[bg];"
            f"[1:v]split[webcam_full_src][webcam_small_src];"
            f"[webcam_full_src]scale=w={screen_w}:h={screen_h}:force_original_aspect_ratio=increase,crop={screen_w}:{screen_h}[webcam_full];"
            f"[webcam_small_src]scale=w={w}:h={scaled_h},format=rgba[scaled_webcam];"
            f"[scaled_webcam][2:v]alphamerge[masked_webcam];"
            f"[bg][masked_webcam]overlay=x=W-w-{offset}:y={offset}:enable='{overlay_enable_expr}':eof_action=pass[screen_with_overlay];"
            f"[screen_with_overlay][webcam_full]overlay=x=0:y=0:enable='{raw_enable_expr}':eof_action=pass[out_v]"
        )

        audio_opts = []
        if has_webcam_audio:
            audio_opts = ["-map", "1:a", "-c:a", "copy"]
        elif has_screen_audio:
            audio_opts = ["-map", "0:a", "-c:a", "copy"]

        cmd = [
            "ffmpeg", "-y",
            "-i", str(screen_path),
            "-i", str(webcam_path),
            "-loop", "1", "-i", str(mask_path),
            "-filter_complex", filter_complex,
            "-map", "[out_v]"
        ] + audio_opts + [
            "-t", f"{webcam_duration:.3f}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "18",
            str(output_path)
        ]

        print(f"\n🚀 Processing video using single-pass FFmpeg...")
        print(f"   Executing: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print(f"\n🎉 Success! Output video saved to: {output_path}")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ FFmpeg command failed with exit code {e.returncode}.", file=sys.stderr)
        sys.exit(e.returncode)
    finally:
        # Clean up temporary mask directory
        if temp_dir.exists():
            print(f"\n🧹 Cleaning up temporary files...")
            try:
                shutil.rmtree(temp_dir)
                print("   Temporary files cleaned up successfully.")
            except Exception as e:
                print(f"   Warning: Could not remove temp directory '{temp_dir}': {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
