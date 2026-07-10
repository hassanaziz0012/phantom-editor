#!/usr/bin/env python3
import os
import re
import sys
import json
import math
import argparse
import subprocess

from utils import Colors, colorize, format_time, parse_time_to_seconds

def compute_aspect_ratio(width: int, height: int) -> str:
    """Computes simplified aspect ratio from width and height."""
    if not width or not height:
        return "Unknown"
    gcd = math.gcd(width, height)
    return f"{width // gcd}:{height // gcd}"

def get_video_metadata(video_path: str) -> dict:
    """Uses ffprobe to gather resolution, aspect ratio, frame rate, and duration."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,display_aspect_ratio,sample_aspect_ratio,r_frame_rate,duration',
        '-of', 'json',
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            # Ensure duration is converted to float if present
            if 'duration' in stream:
                try:
                    stream['duration'] = float(stream['duration'])
                except ValueError:
                    pass
            return stream
    except Exception as e:
        # Fallback to check format details if stream attributes are missing
        pass

    # Try format-level duration if stream duration wasn't found
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if 'format' in data and 'duration' in data['format']:
            return {'duration': float(data['format']['duration'])}
    except:
        pass

    return {}

def run_inspection(video_path: str, args) -> tuple:
    """Runs ffmpeg blackdetect and freezedetect filters and parses outputs in real time."""
    # Build filtergraph
    black_filter = f"blackdetect=d={args.black_duration}:pic_th={args.black_pic_th}:pix_th={args.black_pix_th}"
    freeze_filter = f"freezedetect=d={args.freeze_duration}:n={args.freeze_noise}"
    
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vf', f"{black_filter},{freeze_filter}",
        '-f', 'null', '-'
    ]

    # RegEx matching for filters
    # [blackdetect @ 0x...] black_start:10.5 black_end:12.3 black_duration:1.8
    black_regex = re.compile(r'black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)')
    # [freezedetect @ 0x...] freeze_start:15.2
    freeze_start_regex = re.compile(r'freeze_start:\s*([0-9.]+)')
    # [freezedetect @ 0x...] freeze_end:17.5 freeze_duration:2.3
    freeze_end_regex = re.compile(r'freeze_end:\s*([0-9.]+)\s+freeze_duration:\s*([0-9.]+)')
    # ffmpeg time status e.g. time=00:01:23.45
    time_regex = re.compile(r'time=([0-9:.]+)')

    black_glitches = []
    freeze_glitches = []
    
    # Track current active freeze to pair with its end log
    active_freeze_start = None

    print(colorize("Starting glitch detection scan...", Colors.BOLD + Colors.BLUE))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    buffer = ""
    total_duration = args.total_duration

    try:
        while True:
            char = process.stderr.read(1)
            if not char:
                # Check for process completion
                if process.poll() is not None:
                    break
                continue
            
            if char in ('\r', '\n'):
                if buffer:
                    line = buffer.strip()
                    
                    # Parse blackdetect logs
                    black_match = black_regex.search(line)
                    if black_match:
                        start = float(black_match.group(1))
                        end = float(black_match.group(2))
                        dur = float(black_match.group(3))
                        black_glitches.append((start, end, dur))
                        # Clear progress line to output glitch warning cleanly
                        sys.stdout.write("\r" + " " * 80 + "\r")
                        print(colorize(f"⚠️  BLACK DETECTED: {format_time(start)} --> {format_time(end)} (Duration: {dur:.2f}s)", Colors.WARNING))
                    
                    # Parse freezedetect logs
                    freeze_start_match = freeze_start_regex.search(line)
                    if freeze_start_match:
                        active_freeze_start = float(freeze_start_match.group(1))
                    
                    freeze_end_match = freeze_end_regex.search(line)
                    if freeze_end_match:
                        end = float(freeze_end_match.group(1))
                        dur = float(freeze_end_match.group(2))
                        start = active_freeze_start if active_freeze_start is not None else (end - dur)
                        freeze_glitches.append((start, end, dur))
                        active_freeze_start = None
                        # Clear progress line to output glitch warning cleanly
                        sys.stdout.write("\r" + " " * 80 + "\r")
                        print(colorize(f"⚠️  FREEZE DETECTED: {format_time(start)} --> {format_time(end)} (Duration: {dur:.2f}s)", Colors.WARNING))
                    
                    # Parse progress time
                    time_match = time_regex.search(line)
                    if time_match and total_duration > 0:
                        cur_time_str = time_match.group(1)
                        cur_sec = parse_time_to_seconds(cur_time_str)
                        pct = min(100.0, (cur_sec / total_duration) * 100.0)
                        
                        # Generate simple text progress bar (width of 20)
                        bar_len = 20
                        filled_len = int(round(bar_len * pct / 100.0))
                        bar = '=' * filled_len + '-' * (bar_len - filled_len)
                        
                        sys.stdout.write(f"\rScanning: [{bar}] {pct:.1f}% ({format_time(cur_sec)} / {format_time(total_duration)})")
                        sys.stdout.flush()
                        
                    buffer = ""
            else:
                buffer += char
    except KeyboardInterrupt:
        process.terminate()
        sys.stdout.write("\r" + " " * 80 + "\r")
        print(colorize("\nScan cancelled by user.", Colors.FAIL))
        sys.exit(1)

    # Clean the last progress line
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()

    # Check for a freeze frame that was in progress when the video ended
    if active_freeze_start is not None and total_duration > active_freeze_start:
        dur = total_duration - active_freeze_start
        freeze_glitches.append((active_freeze_start, total_duration, dur))

    return black_glitches, freeze_glitches

def print_report(video_path: str, meta: dict, black_glitches: list, freeze_glitches: list, args):
    """Formats and prints the final QC audit report."""
    print("=" * 80)
    print(colorize("VIDEO QUALITY CONTROL INSPECTION REPORT", Colors.BOLD + Colors.HEADER))
    print("=" * 80)
    print(f"File Path:       {os.path.abspath(video_path)}")
    
    # 1. Video Specifications
    width = meta.get('width', 0)
    height = meta.get('height', 0)
    
    # Resolution tag
    res_tag = ""
    if width == 1920 and height == 1080:
        res_tag = " (1080p Full HD)"
    elif width == 1280 and height == 720:
        res_tag = " (720p HD)"
    elif width == 3840 and height == 2160:
        res_tag = " (4K UHD)"
    elif width == height and width > 0:
        res_tag = " (Square 1:1)"
        
    print(f"Resolution:      {width}x{height}{res_tag}")
    
    # Aspect Ratio display (DAR / Custom aspect ratio computation)
    dar = meta.get('display_aspect_ratio', 'N/A')
    computed_ar = compute_aspect_ratio(width, height)
    
    if dar == '0:1' or dar == 'N/A':
        dar_display = f"{computed_ar} (Calculated)"
    else:
        dar_display = dar
        if dar != computed_ar:
            dar_display += f" (Computed raw pixel ratio: {computed_ar})"
            
    # Add decimal ratio representation
    if width and height:
        decimal_ar = width / height
        dar_display += f" [Ratio: {decimal_ar:.2f}]"
        
    print(f"Aspect Ratio:    {dar_display}")
    
    # Pixel aspect ratio
    sar = meta.get('sample_aspect_ratio', 'N/A')
    print(f"Pixel Aspect:    {sar}")
    
    # Duration display
    dur = meta.get('duration', 0.0)
    print(f"Duration:        {format_time(dur)} ({dur:.3f} seconds)")
    
    # Frame rate
    fps_raw = meta.get('r_frame_rate', 'N/A')
    fps_display = "N/A"
    if '/' in fps_raw:
        try:
            num, den = map(int, fps_raw.split('/'))
            if den > 0:
                fps_display = f"{num / den:.2f} fps"
        except ValueError:
            fps_display = fps_raw
    else:
        fps_display = f"{fps_raw} fps"
    print(f"Frame Rate:      {fps_display}")
    
    print("-" * 80)
    print(colorize("Scan Parameters:", Colors.BOLD + Colors.CYAN))
    print(f"  • Black Detect:  min_duration={args.black_duration}s, pixel_th={args.black_pix_th}, pic_th={args.black_pic_th}")
    print(f"  • Freeze Detect: min_duration={args.freeze_duration}s, noise_tolerance={args.freeze_noise}")
    print("-" * 80)

    # 2. Detailed glitches
    total_issues = len(black_glitches) + len(freeze_glitches)
    
    if total_issues == 0:
        print(colorize("✅ SUCCESS: No video glitches or bad frames detected under current thresholds!", Colors.BOLD + Colors.GREEN))
    else:
        print(colorize(f"❌ SCAN COMPLETED: Found {total_issues} potential issue(s). Details below:", Colors.BOLD + Colors.FAIL))
        print()
        
        # Black screens
        if black_glitches:
            print(colorize(f"■ BLACK SCREEN DETECTIONS ({len(black_glitches)}):", Colors.BOLD + Colors.WARNING))
            for idx, (start, end, dur) in enumerate(black_glitches, 1):
                timestamp_range = f"{format_time(start)} --> {format_time(end)}"
                seconds_range = f"[{start:.3f}s to {end:.3f}s]"
                print(f"  {idx}. {timestamp_range:<25} {seconds_range:<22} Duration: {dur:.3f}s")
            print()
            
        # Freeze frames
        if freeze_glitches:
            print(colorize(f"■ FREEZE FRAME DETECTIONS ({len(freeze_glitches)}):", Colors.BOLD + Colors.WARNING))
            for idx, (start, end, dur) in enumerate(freeze_glitches, 1):
                timestamp_range = f"{format_time(start)} --> {format_time(end)}"
                seconds_range = f"[{start:.3f}s to {end:.3f}s]"
                print(f"  {idx}. {timestamp_range:<25} {seconds_range:<22} Duration: {dur:.3f}s")
            print()
            
        print(colorize("Note: Review these timestamps in your video editor to fix bad frames/glitches.", Colors.DIM))

    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Inspects video for properties (resolution, aspect ratio) and scans for glitches (black screens, freeze frames).")
    parser.add_argument("video_path", help="Path to the video file to inspect.")
    parser.add_argument("--black-duration", "-bd", type=float, default=0.1, help="Minimum duration of black screen in seconds (default: 0.1).")
    parser.add_argument("--black-pix-th", "-bx", type=float, default=0.10, help="Pixel luminance threshold to count as black (default: 0.10).")
    parser.add_argument("--black-pic-th", "-bp", type=float, default=0.98, help="Threshold ratio of pixels below pixel threshold to count as black (default: 0.98).")
    parser.add_argument("--freeze-duration", "-fd", type=float, default=0.5, help="Minimum duration of frozen video in seconds (default: 0.5).")
    parser.add_argument("--freeze-noise", "-fn", type=float, default=0.003, help="Noise tolerance threshold for freeze detection (default: 0.003).")
    
    args = parser.parse_args()

    # Verify video path exists
    if not os.path.exists(args.video_path):
        print(colorize(f"Error: Video file not found at '{args.video_path}'", Colors.FAIL), file=sys.stderr)
        sys.exit(1)

    # Get metadata
    print(colorize("Analyzing video structure...", Colors.BOLD + Colors.BLUE))
    meta = get_video_metadata(args.video_path)
    
    # Store total duration for progress parsing
    args.total_duration = meta.get('duration', 0.0)

    # Run inspection
    black_glitches, freeze_glitches = run_inspection(args.video_path, args)

    # Print final QC report
    print_report(args.video_path, meta, black_glitches, freeze_glitches, args)

if __name__ == "__main__":
    main()
