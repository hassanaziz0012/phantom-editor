import os
import shutil
import subprocess
import uuid
from faster_whisper import WhisperModel

def generate_captions(video_path, model_path_or_size="small.en", output_srt_path="captions.srt", max_words=None, output_video_path=None, srt_only=False, uppercase=True, font_size=16):
    print(f"Loading Whisper model from: {model_path_or_size}")
    model = WhisperModel(model_path_or_size, device="cpu", compute_type="int8")

    print(True, f"Processing: {video_path}")
    if max_words:
        segments, info = model.transcribe(video_path, beam_size=5, word_timestamps=True)
    else:
        segments, info = model.transcribe(video_path, beam_size=5)

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

    if srt_only:
        return

    if not output_video_path:
        base, ext = os.path.splitext(video_path)
        output_video_path = f"{base}_captioned{ext}"

    print(f"Burning captions into video and saving to: {output_video_path}")
    
    # Copy to a temporary file in CWD with a simple random name to avoid ffmpeg path escaping issues
    temp_srt = f"temp_captions_{uuid.uuid4().hex[:8]}.srt"
    shutil.copy2(output_srt_path, temp_srt)
    
    try:
        # FFmpeg command to burn subtitles with clean style:
        # Font: Liberation Sans
        # Size: 24
        # PrimaryColor: White (&H00FFFFFF&)
        # OutlineColor: Black (&H00000000&)
        # BorderStyle: 1 (Outline & shadow)
        # Outline: 3 (Thick outline)
        # Shadow: 0 (No shadow)
        # Alignment: 2 (Bottom center)
        # Bold: 1
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"subtitles={temp_srt}:force_style='Fontname=Liberation Sans,Fontsize={font_size},PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,Bold=1'",
            "-c:a", "copy",
            output_video_path
        ]
        subprocess.run(cmd, check=True)
        print(f"Success! Captioned video saved to {output_video_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error burning subtitles: ffmpeg failed with exit code {e.returncode}")
        raise
    except FileNotFoundError:
        print("Error: ffmpeg is not installed or not found in system PATH. Cannot burn captions.")
        raise
    finally:
        if os.path.exists(temp_srt):
            os.remove(temp_srt)

def format_srt_time(seconds):
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

if __name__ == "__main__":
    import argparse

    default_model = os.path.join(os.path.dirname(__file__), "models", "faster-whisper-small.en")

    def positive_int(value):
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"{value} is an invalid positive int value")
        return ivalue

    parser = argparse.ArgumentParser(description="Generate captions for a video file.")
    parser.add_argument("video_path", help="Path to the input video file (e.g. mp4).")
    parser.add_argument(
        "--model", "-m",
        default=default_model,
        help="Path to local model directory or Hugging Face model size (default: video-editing/models/faster-whisper-small.en)."
    )
    parser.add_argument(
        "--output", "-o",
        default="captions.srt",
        help="Path to the output SRT file (default: captions.srt)."
    )
    parser.add_argument(
        "--max-words", "-w",
        type=positive_int,
        default=None,
        help="Maximum words per caption segment (for short-form videos like reels/tiktoks)."
    )
    parser.add_argument(
        "--output-video", "-v",
        default=None,
        help="Path to the output video file with burned captions. If not specified, defaults to <input_basename>_captioned.mp4."
    )
    parser.add_argument(
        "--srt-only",
        action="store_true",
        help="Only generate the SRT subtitle file and skip burning it into the video."
    )
    parser.add_argument(
        "--uppercase",
        action="store_true",
        default=True,
        help="Convert captions to uppercase (default: True)."
    )
    parser.add_argument(
        "--no-uppercase",
        dest="uppercase",
        action="store_false",
        help="Disable converting captions to uppercase."
    )
    parser.add_argument(
        "--font-size", "-f",
        type=positive_int,
        default=16,
        help="Font size for the burned captions (default: 16)."
    )

    args = parser.parse_args()
    generate_captions(
        args.video_path,
        model_path_or_size=args.model,
        output_srt_path=args.output,
        max_words=args.max_words,
        output_video_path=args.output_video,
        srt_only=args.srt_only,
        uppercase=args.uppercase,
        font_size=args.font_size
    )