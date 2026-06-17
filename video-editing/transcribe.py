import os
import subprocess
import uuid
from faster_whisper import WhisperModel

def format_srt_time(seconds):
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

def transcribe_video(video_path, model_path_or_size, output_srt_path, max_words=None, uppercase=False, preview=False, vad_filter=True):
    preview_video_path = None
    if preview:
        print("Preview mode enabled: extracting first 5 seconds of the video...")
        preview_video_path = f"temp_preview_{uuid.uuid4().hex[:8]}.mp4"
        try:
            # Extract first 5 seconds of input video.
            # We re-encode to ensure correct timings and format.
            crop_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-t", "5",
                preview_video_path
            ]
            subprocess.run(crop_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            video_path = preview_video_path
        except subprocess.CalledProcessError as e:
            print(f"Error creating preview video: ffmpeg failed with exit code {e.returncode}")
            if os.path.exists(preview_video_path):
                os.remove(preview_video_path)
            raise
        except FileNotFoundError:
            print("Error: ffmpeg is not installed or not found in system PATH. Cannot create preview video.")
            raise

    try:
        print(f"Loading Whisper model from: {model_path_or_size}")
        model = WhisperModel(model_path_or_size, device="cpu", compute_type="int8")

        print(f"Processing: {video_path}")
        if max_words:
            segments, info = model.transcribe(video_path, beam_size=5, word_timestamps=True, vad_filter=vad_filter)
        else:
            segments, info = model.transcribe(video_path, beam_size=5, vad_filter=vad_filter)

        print(f"Detected language '{info.language}' with probability {info.language_probability:.2f}")

        # Write to standard SRT format
        with open(output_srt_path, "w", encoding="utf-8") as f:
            if max_words:
                caption_idx = 1
                for segment in segments:
                    words = getattr(segment, "words", None)
                    if not words:
                        # Fallback to standard segment logic if words are not available
                        text = segment.text.strip()
                        if uppercase:
                            text = text.upper()
                        if text:
                            start_time = format_srt_time(segment.start)
                            end_time = format_srt_time(segment.end)
                            f.write(f"{caption_idx}\n{start_time} --> {end_time}\n{text}\n\n")
                            print(f"[{start_time} -> {end_time}] {text}")
                            caption_idx += 1
                        continue
                    
                    # Split this segment's words into chunks of at most max_words
                    for j in range(0, len(words), max_words):
                        chunk = words[j:j + max_words]
                        if not chunk:
                            continue
                        
                        chunk_start = format_srt_time(chunk[0].start)
                        chunk_end = format_srt_time(chunk[-1].end)
                        chunk_text = " ".join(w.word.strip() for w in chunk).strip()
                        if uppercase:
                            chunk_text = chunk_text.upper()
                        
                        if chunk_text:
                            f.write(f"{caption_idx}\n{chunk_start} --> {chunk_end}\n{chunk_text}\n\n")
                            print(f"[{chunk_start} -> {chunk_end}] {chunk_text}")
                            caption_idx += 1
            else:
                for i, segment in enumerate(segments, start=1):
                    start_time = format_srt_time(segment.start)
                    end_time = format_srt_time(segment.end)
                    text = segment.text.strip()
                    if uppercase:
                        text = text.upper()
                    
                    f.write(f"{i}\n{start_time} --> {end_time}\n{text}\n\n")
                    print(f"[{start_time} -> {end_time}] {text}")

        print(f"\nSuccess! Captions saved to {output_srt_path}")
    finally:
        if preview_video_path and os.path.exists(preview_video_path):
            os.remove(preview_video_path)

if __name__ == "__main__":
    import argparse

    default_model = os.path.join(os.path.dirname(__file__), "models", "faster-whisper-small.en")

    def positive_int(value):
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"{value} is an invalid positive int value")
        return ivalue

    parser = argparse.ArgumentParser(description="Transcribe a video and generate subtitles SRT file.")
    parser.add_argument("video_path", help="Path to the input video file (e.g. mp4).")
    parser.add_argument(
        "--model", "-m",
        default=default_model,
        help="Path to local model directory or Hugging Face model size (default: video-editing/models/faster-whisper-small.en)."
    )
    parser.add_argument(
        "--max-words", "-w",
        type=positive_int,
        default=None,
        help="Maximum words per caption segment (for short-form videos like reels/tiktoks)."
    )
    parser.add_argument(
        "--uppercase",
        action="store_true",
        default=False,
        help="Convert captions to uppercase (default: False)."
    )
    parser.add_argument(
        "--no-uppercase",
        dest="uppercase",
        action="store_false",
        help="Disable converting captions to uppercase."
    )
    parser.add_argument(
        "--vad-filter",
        action="store_true",
        default=True,
        help="Use VAD (Voice Activity Detection) filter to ignore silences (default: True)."
    )
    parser.add_argument(
        "--no-vad-filter",
        dest="vad_filter",
        action="store_false",
        help="Disable VAD (Voice Activity Detection) filter."
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Only process the first 5 seconds of the video for preview."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the generated subtitles SRT file (default: captions.srt in the same directory as the input video)."
    )

    args = parser.parse_args()

    if args.output:
        output_srt = args.output
    else:
        # Place output captions.srt in the same directory as the input video file
        video_dir = os.path.dirname(os.path.abspath(args.video_path))
        output_srt = os.path.join(video_dir, "captions.srt")

    transcribe_video(
        video_path=args.video_path,
        model_path_or_size=args.model,
        output_srt_path=output_srt,
        max_words=args.max_words,
        uppercase=args.uppercase,
        preview=args.preview,
        vad_filter=args.vad_filter
    )
