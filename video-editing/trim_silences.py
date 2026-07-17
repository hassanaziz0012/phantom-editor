import os
import sys
import argparse
import subprocess
import gc
import numpy as np

# Ensure the directory containing this script is in sys.path to resolve local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import parse_timestamp

# Import faster-whisper VAD tools
from faster_whisper.vad import get_speech_timestamps, VadOptions

def load_audio_with_ffmpeg(file_path, sampling_rate=16000):
    """
    Decodes the audio from a file using an ffmpeg subprocess.
    This is extremely robust against codec and packet errors compared to PyAV.
    """
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads",
        "0",
        "-i",
        file_path,
        "-f",
        "s16le",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sampling_rate),
        "-"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        out = result.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to load audio: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)}") from e

    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

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

    # Build select expression as a balanced binary tree
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

def process_video(video_path, output_path, padding, min_silence, threshold, is_recursive=False):
    print(f"\nDecoding audio from {video_path}...")
    # Decode audio to 16000Hz mono numpy float32 array using ffmpeg subprocess
    try:
        audio = load_audio_with_ffmpeg(video_path, sampling_rate=16000)
    except Exception as e:
        print(f"Error decoding audio from video: {e}")
        raise

    print(f"Running voice activity detection on decoded audio...")
    # Setup VAD options
    # padding: in seconds -> convert to ms
    # min_silence: in seconds -> convert to ms
    vad_options = VadOptions(
        threshold=threshold,
        min_silence_duration_ms=int(min_silence * 1000),
        speech_pad_ms=int(padding * 1000)
    )

    speech_timestamps = get_speech_timestamps(audio, vad_options, sampling_rate=16000)

    # Convert speech timestamps from sample indices to seconds
    intervals = []
    for item in speech_timestamps:
        start_sec = item['start'] / 16000.0
        end_sec = item['end'] / 16000.0
        intervals.append((start_sec, end_sec))

    print(f"Detected {len(intervals)} speech intervals.")
    if not intervals:
        print("No active speech intervals found to keep. Skipping trimming.")
        return

    print(f"Trimming silences in video: {video_path}...")
    cut_video_with_ffmpeg(video_path, output_path, intervals)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trim silences from a video using Silero VAD to detect speech.")
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
        "--threshold",
        type=float,
        default=0.5,
        help="Speech threshold. Probabilities above this value are considered speech (default: 0.5)."
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
                    padding=args.padding,
                    min_silence=args.min_silence,
                    threshold=args.threshold,
                    is_recursive=True
                )
            except Exception as e:
                print(f"❌ Error processing '{video_file}': {e}", file=sys.stderr)
        print("\n🎉 Recursive trim silences processing complete!")

    else:
        if not os.path.isfile(args.video_path):
            print(f"Error: Video file does not exist at '{args.video_path}'. If you meant to process a directory, please use -R/--recursive.", file=sys.stderr)
            sys.exit(1)

        video_dir = os.path.dirname(os.path.abspath(args.video_path))
        if args.output:
            output_video = args.output
        else:
            output_video = os.path.join(video_dir, "trimmed_output.mp4")

        process_video(
            video_path=args.video_path,
            output_path=output_video,
            padding=args.padding,
            min_silence=args.min_silence,
            threshold=args.threshold,
            is_recursive=False
        )
        print("\n🎉 Trim silences complete!")
