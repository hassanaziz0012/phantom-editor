import os
import re
import sys
import argparse
import subprocess

# Ensure the directory containing this script is in sys.path to resolve local imports like transcribe
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from transcribe import transcribe_video

def srt_time_to_seconds(time_str):
    """Converts SRT timestamp (HH:MM:SS,mmm) to total seconds."""
    hours, minutes, seconds = time_str.split(':')
    seconds, milliseconds = seconds.split(',')
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000.0

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
        start = srt_time_to_seconds(start_str)
        end = srt_time_to_seconds(end_str)
        raw_intervals.append((start, end))

    # Sort intervals just in case
    raw_intervals.sort(key=lambda x: x[0])

    # Merge intervals that are close together to keep speech pacing natural
    merged_intervals = []
    current_start, current_end = raw_intervals[0]

    for next_start, next_end in raw_intervals[1:]:
        # If gap between words is smaller than our minimum silence threshold, merge them
        if next_start - current_end < min_silence:
            current_end = next_end
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

    # Build the complex filtergraph rules
    v_filter = ""
    a_filter = ""
    for start, end in intervals:
        v_filter += f"between(t,{start:.3f},{end:.3f})+"
        a_filter += f"between(t,{start:.3f},{end:.3f})+"
    
    v_filter = v_filter.rstrip("+")
    a_filter = a_filter.rstrip("+")

    # Select frames and reset presentation timestamps (PTS) to maintain audio/video sync
    v_script = f"select='{v_filter}',setpts=N/FRAME_RATE/TB"
    a_script = f"aselect='{a_filter}',asetpts=N/SR/TB"

    cmd = [
        'ffmpeg', '-y',
        '-i', input_video,
        '-vf', v_script,
        '-af', a_script,
        output_video
    ]

    print(f"Executing single-pass jump-cuts into {output_video}...")
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    default_model = os.path.join(os.path.dirname(__file__), "models", "faster-whisper-small.en")

    parser = argparse.ArgumentParser(description="Trim silences from a video using speech/caption intervals.")
    parser.add_argument("video_path", help="Path to the input video file (e.g. mp4).")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the trimmed output video (default: trimmed_output.mp4 in the input video's directory)."
    )
    parser.add_argument(
        "--model", "-m",
        default=default_model,
        help="Path to local model directory or Hugging Face model size (default: video-editing/models/faster-whisper-small.en)."
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

    args = parser.parse_args()

    video_dir = os.path.dirname(os.path.abspath(args.video_path))

    # Resolve output trimmed video path
    if args.output:
        output_video = args.output
    else:
        output_video = os.path.join(video_dir, "trimmed_output.mp4")

    srt_path = os.path.join(video_dir, "captions_1word.srt")

    # Generate captions if they do not exist
    if os.path.exists(srt_path):
        print(f"Captions file already exists: {srt_path}. Skipping transcription.")
    else:
        print(f"Generating captions using transcribe_video with max_words=1...")
        transcribe_video(
            video_path=args.video_path,
            model_path_or_size=args.model,
            output_srt_path=srt_path,
            max_words=1,
            uppercase=False,
            preview=False,
            vad_filter=True
        )

    print(f"Parsing speech intervals from: {srt_path} (padding={args.padding}, min_silence={args.min_silence})")
    speech_blocks = parse_speech_intervals_from_srt(srt_path, padding=args.padding, min_silence=args.min_silence)

    print(f"Trimming silences in video: {args.video_path}...")
    cut_video_with_ffmpeg(args.video_path, output_video, speech_blocks)
    print("Done!")