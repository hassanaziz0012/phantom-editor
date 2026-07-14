import os
import re
import sys
import argparse
import subprocess

# Ensure the directory containing this script is in sys.path to resolve local imports like transcribe
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from transcribe import transcribe_video
from utils import parse_timestamp

# (srt_time_to_seconds is replaced by parse_timestamp imported from utils.py)

def parse_speech_intervals_from_srt(srt_path, padding=0.15, min_silence=0.4):
    """
    Parses word-level SRT file to isolate true speech blocks.
    Merges segments if the silence between them is less than min_silence.
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Regex to find SRT time blocks
    time_blocks = re.findall(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})', content)
    if not time_blocks:
        return []

    raw_intervals = []
    for start_str, end_str in time_blocks:
        start = parse_timestamp(start_str)
        end = parse_timestamp(end_str)
        raw_intervals.append((start, end))

    # Sort intervals just in case
    raw_intervals.sort(key=lambda x: x[0])

    # Merge intervals that are close together to keep speech pacing natural
    merged_intervals = []
    current_start, current_end = raw_intervals[0]

    for next_start, next_end in raw_intervals[1:]:
        # If gap between words is smaller than our minimum silence threshold, merge them
        if next_start - current_end < min_silence:
            current_end = max(current_end, next_end)
        else:
            # Apply padding to the finalized speech block boundaries
            padded_start = max(0, current_start - padding)
            padded_end = current_end + padding
            merged_intervals.append((padded_start, padded_end))
            current_start, current_end = next_start, next_end

    # Append the last block
    merged_intervals.append((max(0, current_start - padding), current_end + padding))
    return merged_intervals

def cut_video_with_ffmpeg(input_video, output_video, intervals):
    if not intervals:
        print("No active speech intervals found to keep.")
        return

    # Calculate gaps/silences to shift timestamps and maintain perfect audio/video sync
    # without relying on constant frame rate assumptions.
    gaps = []
    shift_points = []
    for i in range(len(intervals)):
        if i == 0:
            gaps.append(intervals[0][0])
        else:
            gaps.append(intervals[i][0] - intervals[i-1][1])
            # Use the midpoint of the silence gap as the trigger point.
            # This provides a safety margin (at least 200ms) to prevent boundary frames
            # from triggering the next shift early due to floating-point precision,
            # which would cause out-of-order timestamps and buffer queuing.
            midpoint = (intervals[i-1][1] + intervals[i][0]) / 2.0
            shift_points.append(midpoint)

    # Build balanced binary tree for shift expression to avoid FFmpeg recursion limit
    shift_terms = [f"gt(T,{shift_points[i]:.3f})*{gaps[i+1]:.3f}" for i in range(len(shift_points))]

    def build_shift_tree(left_idx, right_idx):
        if left_idx == right_idx:
            return shift_terms[left_idx]
        mid = (left_idx + right_idx) // 2
        left_expr = build_shift_tree(left_idx, mid)
        right_expr = build_shift_tree(mid + 1, right_idx)
        return f"({left_expr})+({right_expr})"

    if shift_terms:
        shift_expr = f"{gaps[0]:.3f}+{build_shift_tree(0, len(shift_terms) - 1)}"
    else:
        shift_expr = f"{gaps[0]:.3f}"

    # Build select expression as a balanced binary tree to avoid
    # deep recursion limits (Cannot allocate memory) in FFmpeg's expression parser.
    select_terms = [f"between(t,{start:.3f},{end:.3f})" for start, end in intervals]

    def build_select_tree(left_idx, right_idx):
        if left_idx == right_idx:
            return select_terms[left_idx]
        mid = (left_idx + right_idx) // 2
        left_expr = build_select_tree(left_idx, mid)
        right_expr = build_select_tree(mid + 1, right_idx)
        return f"({left_expr})+({right_expr})"

    select_expr = build_select_tree(0, len(select_terms) - 1)

    # Use the exact same shift expression for both video and audio
    # to guarantee identical output durations and keep streams in sync.
    # We append the 'fps' filter to force a Constant Frame Rate (CFR),
    # which is required by video editors to prevent A/V desync on import.
    v_script = f"select='{select_expr}',setpts='(T-({shift_expr}))/TB',fps=30"
    a_script = f"aselect='{select_expr}',asetpts='(T-({shift_expr}))/TB'"

    # Calculate the total duration of the selected speech intervals
    # to truncate the output video and prevent trailing dead video/audio (frozen frame/silence).
    total_duration = sum(end - start for start, end in intervals)

    cmd = [
        'ffmpeg', '-y',
        '-i', input_video,
        '-vf', v_script,
        '-af', a_script,
        '-fps_mode', 'cfr',
        '-t', f"{total_duration:.3f}",
        output_video
    ]

    print(f"Executing single-pass jump-cuts into {output_video}...")
    subprocess.run(cmd, check=True)

def process_video(video_path, output_path, model, padding, min_silence, is_recursive=False, captions_path=None):
    video_dir = os.path.dirname(os.path.abspath(video_path))
    video_name = os.path.splitext(os.path.basename(video_path))[0]

    # Resolve SRT path
    if captions_path:
        srt_path = captions_path
    else:
        # Use video-specific name to avoid collisions
        srt_path = os.path.join(video_dir, f"{video_name}-1word.srt")
        
        # In single-file mode, check if captions_1word.srt exists in the directory.
        # If so, use it for backward compatibility.
        if not is_recursive:
            legacy_srt_path = os.path.join(video_dir, "captions_1word.srt")
            if not os.path.exists(srt_path) and os.path.exists(legacy_srt_path):
                srt_path = legacy_srt_path

    # Generate captions if they do not exist
    if os.path.exists(srt_path):
        print(f"Captions file already exists: {srt_path}. Skipping transcription.")
    else:
        print(f"Generating captions using transcribe_video with max_words=1 for {video_path}...")
        transcribe_video(
            video_path=video_path,
            model_path_or_size=model,
            output_srt_path=srt_path,
            max_words=1,
            uppercase=False,
            preview=False,
            vad_filter=True
        )

    print(f"Parsing speech intervals from: {srt_path} (padding={padding}, min_silence={min_silence})")
    speech_blocks = parse_speech_intervals_from_srt(srt_path, padding=padding, min_silence=min_silence)

    print(f"Trimming silences in video: {video_path}...")
    cut_video_with_ffmpeg(video_path, output_path, speech_blocks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trim silences from a video using speech/caption intervals.")
    parser.add_argument("video_path", help="Path to the input video file (or folder path if -R/--recursive is used).")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the trimmed output video (default: trimmed_output.mp4 in the input video's directory). Cannot be used with --recursive."
    )
    parser.add_argument(
        "--recursive", "-R",
        action="store_true",
        help="Process a folder instead of a single video file, searching recursively for videos."
    )
    parser.add_argument(
        "--model", "-m",
        choices=["small", "medium", "large"],
        default="medium",
        help="Whisper model size to use locally: small, medium, or large (default: medium)."
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.15,
        help="Padding in seconds to add to the start and end of each speech interval (default: 0.15)."
    )
    parser.add_argument(
        "--min-silence",
        type=float,
        default=0.4,
        help="Minimum silence duration in seconds to split segments (default: 0.4)."
    )
    parser.add_argument(
        "--captions", "-c",
        default=None,
        help="Path to a custom SRT captions file. A 1-word timestamp caption file format is REQUIRED. If not specified, the script looks for or generates a local 1-word SRT file."
    )

    args = parser.parse_args()

    VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')

    if args.recursive:
        if not os.path.isdir(args.video_path):
            print(f"Error: Specify a valid directory path when using --recursive. '{args.video_path}' is not a directory.", file=sys.stderr)
            sys.exit(1)
        if args.output:
            print("Error: --output option cannot be used when --recursive is specified.", file=sys.stderr)
            sys.exit(1)
        if args.captions:
            print("Error: --captions option cannot be used when --recursive is specified.", file=sys.stderr)
            sys.exit(1)

        input_dir = os.path.abspath(args.video_path)
        parent_dir = os.path.dirname(input_dir)
        output_dir = os.path.join(parent_dir, "trimmed")

        # Walk through the directory tree
        videos_to_process = []
        for root, dirs, files in os.walk(args.video_path):
            for file in files:
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    filename_without_ext, ext = os.path.splitext(file)
                    # Skip files already processed to avoid duplicate/infinite trimming
                    if filename_without_ext.lower().endswith("-trim-silenced"):
                        continue
                    full_path = os.path.join(root, file)
                    videos_to_process.append((full_path, filename_without_ext, ext))

        if not videos_to_process:
            print(f"No video files found in '{args.video_path}'.")
            sys.exit(0)

        print(f"Found {len(videos_to_process)} video(s) to process recursively.")
        for idx, (video_file, name_no_ext, ext) in enumerate(videos_to_process, start=1):
            abs_video_file = os.path.abspath(video_file)
            rel_path = os.path.relpath(abs_video_file, start=input_dir)
            output_video = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(output_video), exist_ok=True)
            print(f"\n[{idx}/{len(videos_to_process)}] Processing: {video_file}")
            print(f"Target output: {output_video}")
            try:
                process_video(
                    video_path=video_file,
                    output_path=output_video,
                    model=args.model,
                    padding=args.padding,
                    min_silence=args.min_silence,
                    is_recursive=True,
                    captions_path=None
                )
            except Exception as e:
                print(f"❌ Error processing '{video_file}': {e}", file=sys.stderr)
        print("\n🎉 Recursive trim silences processing complete!")

    else:
        if not os.path.isfile(args.video_path):
            print(f"Error: Video file does not exist at '{args.video_path}'. If you meant to process a directory, please use -R/--recursive.", file=sys.stderr)
            sys.exit(1)
        if args.captions and not os.path.isfile(args.captions):
            print(f"Error: The captions file specified does not exist at '{args.captions}'. Ensure it is a 1-word timestamp caption file.", file=sys.stderr)
            sys.exit(1)

        video_dir = os.path.dirname(os.path.abspath(args.video_path))
        if args.output:
            output_video = args.output
        else:
            output_video = os.path.join(video_dir, "trimmed_output.mp4")

        process_video(
            video_path=args.video_path,
            output_path=output_video,
            model=args.model,
            padding=args.padding,
            min_silence=args.min_silence,
            is_recursive=False,
            captions_path=args.captions
        )
        print("\n🎉 Trim silences complete!")