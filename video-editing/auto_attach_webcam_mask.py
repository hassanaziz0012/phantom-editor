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
from utils import parse_timestamp, format_srt_time, check_dependencies, get_video_info, resolve_output_path

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
        tuple: (list of (start_time, end_time) tuples, int commands_count)
    """
    overlay_ranges = []
    current_state = "overlay" if default_overlay else "raw"
    current_start = 0.0 if default_overlay else None
    commands_count = 0
    
    i = 0
    n = len(captions)
    while i < n:
        # Check Case 0: Single caption entry containing "webcam start/stop" or "web cam start/stop"
        w1_start, w1_end, w1_text = captions[i]
        clean_text = re.sub(r'[^\w\s]', ' ', w1_text.lower())
        clean_text = " ".join(clean_text.split())
        
        if re.search(r'\bweb\s*cam\s+start\b', clean_text):
            print(f"📍 Found 'webcam start' command at {w1_start:.3f}s (words: '{w1_text}')")
            commands_count += 1
            if current_state == "raw":
                current_state = "overlay"
                current_start = w1_start
            else:
                print("   (Already in overlay state, ignoring start command)")
            i += 1
            continue
        elif re.search(r'\bweb\s*cam\s+stop\b', clean_text):
            print(f"📍 Found 'webcam stop' command at {w1_end:.3f}s (words: '{w1_text}')")
            commands_count += 1
            if current_state == "overlay":
                current_state = "raw"
                overlay_ranges.append((current_start, w1_end))
                current_start = None
            else:
                print("   (Already in raw state, ignoring stop command)")
            i += 1
            continue

        # Check Case 1: "webcam start/stop" (single word or hyphenated across two entries)
        if i + 1 < n:
            w1_start, w1_end, w1_text = captions[i]
            w2_start, w2_end, w2_text = captions[i+1]
            
            w1_clean = w1_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            w2_clean = w2_text.strip().lower().rstrip(".,?!:;\"'").replace("-", "")
            
            if w1_clean == "webcam" and (w2_start - w1_end < 1.5):
                if w2_clean == "start":
                    print(f"📍 Found 'webcam start' command at {w1_start:.3f}s (words: '{w1_text} {w2_text}')")
                    commands_count += 1
                    if current_state == "raw":
                        current_state = "overlay"
                        current_start = w1_start
                    else:
                        print("   (Already in overlay state, ignoring start command)")
                    i += 2
                    continue
                elif w2_clean == "stop":
                    print(f"📍 Found 'webcam stop' command at {w2_end:.3f}s (words: '{w1_text} {w2_text}')")
                    commands_count += 1
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
                    commands_count += 1
                    if current_state == "raw":
                        current_state = "overlay"
                        current_start = w1_start
                    else:
                        print("   (Already in overlay state, ignoring start command)")
                    i += 3
                    continue
                elif w3_clean == "stop":
                    print(f"📍 Found 'web cam stop' command at {w3_end:.3f}s (words: '{w1_text} {w2_text} {w3_text}')")
                    commands_count += 1
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
        
    return overlay_ranges, commands_count

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
        "--preset",
        type=str,
        choices=["portrait", "landscape"],
        default="portrait",
        help="Preset orientation for webcam overlay: 'portrait' (default, 450px width) or 'landscape' (600px width)."
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=None,
        help="Width of the webcam overlay in pixels (default: 450 for portrait, 600 for landscape)."
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
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Attach webcam mask throughout the entire video, skipping audio transcription and segment detection."
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt when no voice commands are detected (default: --all)."
    )

    args = parser.parse_args()

    # Determine default overlay width based on chosen preset if --width wasn't explicitly set
    if args.width is None:
        args.width = 450 if args.preset == "portrait" else 600

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

    # Determine video info for screen and webcam
    print(f"🔍 Probing webcam video duration...")
    webcam_info = get_video_info(webcam_path)
    screen_info = get_video_info(screen_path)

    webcam_duration = webcam_info.duration
    if not webcam_duration or webcam_duration <= 0:
        print("Error: Could not retrieve webcam duration.", file=sys.stderr)
        sys.exit(1)
    print(f"🎬 Webcam duration: {webcam_duration:.3f} seconds.")

    skip_overlay = False
    if args.all:
        print("⏩ '--all' flag set: Skipping transcription and segment detection. Attaching webcam mask to entire video.")
        overlay_ranges = [(0.0, webcam_duration)]
    else:
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
            print("⚠️ Captions file contains no valid subtitle intervals.")
            overlay_ranges = [(0.0, webcam_duration)] if args.default_overlay else []
            commands_count = 0
        else:
            overlay_ranges, commands_count = detect_overlay_ranges(
                captions,
                default_overlay=args.default_overlay,
                total_duration=webcam_duration
            )

        if commands_count == 0 and not args.default_overlay:
            print("\n" + "=" * 60)
            print("⚠️  No voice commands ('webcam start' / 'webcam stop') detected.")
            print("=" * 60)

            if args.yes or not sys.stdin.isatty():
                print("Notice: Non-interactive / --yes mode. Defaulting to '--all' (overlay webcam throughout entire video).")
                user_input = "y"
            else:
                try:
                    user_input = input("Overlay webcam throughout entire video (--all)? [Y/n]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nProcessing cancelled by user.")
                    sys.exit(1)

            if not user_input or user_input.lower().startswith("y"):
                print("⏩ Selected 'Yes': Overlaying webcam throughout the entire video.")
                overlay_ranges = [(0.0, webcam_duration)]
                skip_overlay = False
            else:
                print("⏩ Selected 'No': Keeping full face video as-is (no screen overlays).")
                skip_overlay = True

    print(f"⏰ Overlay timelines: {overlay_ranges}")

    # Determine audio streams
    has_webcam_audio = webcam_info.has_audio
    has_screen_audio = screen_info.has_audio

    if has_webcam_audio:
        print("🎵 Using webcam audio track.")
    elif has_screen_audio:
        print("⚠️ Webcam video has no audio. Falling back to screen audio.")
    else:
        print("ℹ️ Neither webcam nor screen video contains audio. Output will be video-only.")

    # Determine screen resolution
    screen_w = screen_info.width if screen_info.width > 0 else 1920
    screen_h = screen_info.height if screen_info.height > 0 else 1080
    print(f"🖥️  Screen properties: {screen_w}x{screen_h}")

    # Determine webcam properties
    webcam_w = webcam_info.width
    webcam_h = webcam_info.height
    if webcam_w == 0 or webcam_h == 0:
        print("Error: Could not retrieve webcam properties.", file=sys.stderr)
        sys.exit(1)

    # Target frame rate for smooth overlay output (default to webcam FPS, clamped between 24 and 60)
    webcam_fps = webcam_info.fps
    screen_fps = screen_info.fps
    target_fps = int(round(max(webcam_fps, screen_fps)))
    target_fps = max(24, min(60, target_fps))
    print(f"🎞️  Target output frame rate: {target_fps} FPS")

    # Resolve output path
    output_path = resolve_output_path(screen_path, args.output, "_auto_webcam")

    if skip_overlay:
        print(f"\n🎥 Keeping full face video as-is (saving webcam recording directly to '{output_path.name}')...")
        shutil.copy2(str(webcam_path), str(output_path))
        print(f"\n🎉 Success! Output video saved to: {output_path}")
        sys.exit(0)

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
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
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
        # 1. Reset PTS and set constant target frame rate for screen recording, padding indefinitely.
        # 2. Reset PTS and set constant target frame rate for webcam video.
        # 3. Split webcam video into raw and overlay paths.
        # 4. Scale raw webcam path to full screen, cropping to match aspect ratio.
        # 5. Scale overlay webcam path to corner size, apply mask.
        # 6. Overlay corner webcam onto padded screen (active during overlay segments).
        # 7. Overlay full webcam onto the result (active during raw segments).
        offset = args.offset
        if args.all or not raw_conditions:
            filter_complex = (
                f"[0:v]setpts=PTS-STARTPTS,fps=fps={target_fps},tpad=stop_mode=clone:stop=-1[bg];"
                f"[1:v]setpts=PTS-STARTPTS,fps=fps={target_fps},scale=w={w}:h={scaled_h},format=rgba[scaled_webcam];"
                f"[scaled_webcam][2:v]alphamerge[masked_webcam];"
                f"[bg][masked_webcam]overlay=x=W-w-{offset}:y={offset}:eof_action=pass[out_v]"
            )
        else:
            filter_complex = (
                f"[0:v]setpts=PTS-STARTPTS,fps=fps={target_fps},tpad=stop_mode=clone:stop=-1[bg];"
                f"[1:v]setpts=PTS-STARTPTS,fps=fps={target_fps}[webcam_src];"
                f"[webcam_src]split[webcam_full_src][webcam_small_src];"
                f"[webcam_full_src]scale=w={screen_w}:h={screen_h}:force_original_aspect_ratio=increase,crop={screen_w}:{screen_h}[webcam_full];"
                f"[webcam_small_src]scale=w={w}:h={scaled_h},format=rgba[scaled_webcam];"
                f"[scaled_webcam][2:v]alphamerge[masked_webcam];"
                f"[bg][masked_webcam]overlay=x=W-w-{offset}:y={offset}:enable='{overlay_enable_expr}':eof_action=pass[screen_with_overlay];"
                f"[screen_with_overlay][webcam_full]overlay=x=0:y=0:enable='{raw_enable_expr}':eof_action=pass[out_v]"
            )

        audio_opts = []
        if has_webcam_audio:
            audio_opts = ["-map", "1:a", "-c:a", "aac"]
        elif has_screen_audio:
            audio_opts = ["-map", "0:a", "-c:a", "aac"]

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-i", str(screen_path),
            "-i", str(webcam_path),
            "-loop", "1", "-i", str(mask_path),
            "-filter_complex", filter_complex,
            "-map", "[out_v]"
        ] + audio_opts + [
            "-t", f"{webcam_duration:.3f}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            "-crf", "20",
            "-movflags", "+faststart",
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
