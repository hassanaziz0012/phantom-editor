import os
import subprocess
import uuid
import sys
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

# Add the script's directory to the sys.path to guarantee importing from utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import format_srt_time

# Limit for Groq API in bytes (25MB)
GROQ_LIMIT_BYTES = 25 * 1024 * 1024

def extract_audio(video_path, output_audio_path, duration=None):
    """Extracts audio from a video file into a FLAC file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path
    ]
    if duration:
        cmd.extend(["-t", str(duration)])
    cmd.extend([
        "-vn",
        "-c:a", "flac",
        output_audio_path
    ])
    
    print(f"Extracting audio to {output_audio_path}...")
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error extracting audio: ffmpeg failed with exit code {e.returncode}")
        raise
    except FileNotFoundError:
        print("Error: ffmpeg is not installed or not found in system PATH.")
        raise

def compress_audio(input_audio_path, output_audio_path):
    """Compresses/downsamples audio to a 16kHz mono FLAC file."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "flac",
        output_audio_path
    ]
    print(f"Compressing audio to 16kHz mono FLAC at {output_audio_path}...")
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error compressing audio: ffmpeg failed with exit code {e.returncode}")
        raise

def get_audio_duration(file_path):
    """Gets the duration of the audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting audio duration with ffprobe: {e}")
        raise

def transcribe_video_cloud(video_path, model_name, output_srt_path, max_words=None, uppercase=False, preview=False):
    # Ensure input file exists
    if not os.path.exists(video_path):
        print(f"Error: Input video file not found at {video_path}")
        sys.exit(1)

    # Load API Key
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("Error: GROQ_API_KEY environment variable is not set. Please set it in your environment or .env file.")
        sys.exit(1)

    client = Groq(api_key=api_key)

    temp_files = []
    try:
        # Generate temp audio paths in the current working directory to avoid external temp issues
        temp_id = uuid.uuid4().hex[:8]
        extracted_audio = f"temp_extracted_{temp_id}.flac"
        temp_files.append(extracted_audio)

        # 1. Extract audio (limit to 5 seconds if preview is enabled)
        duration = 5 if preview else None
        extract_audio(video_path, extracted_audio, duration=duration)

        # 2. Check file size and compress if necessary
        file_size = os.path.getsize(extracted_audio)
        base_audio = extracted_audio

        if file_size > GROQ_LIMIT_BYTES:
            print(f"Extracted audio size ({file_size / (1024*1024):.2f} MB) exceeds Groq limit of 25 MB. Attempting compression...")
            compressed_audio = f"temp_compressed_{temp_id}.flac"
            temp_files.append(compressed_audio)
            
            compress_audio(extracted_audio, compressed_audio)
            base_audio = compressed_audio
            file_size = os.path.getsize(base_audio)

        # 3. Determine if audio segmentation (chunking) is needed
        audio_chunks = []
        if file_size > GROQ_LIMIT_BYTES:
            print(f"Compressed audio size ({file_size / (1024*1024):.2f} MB) still exceeds the Groq limit of 25 MB.")
            print("Splitting audio into 10-minute (600s) chunks...")
            total_duration = get_audio_duration(base_audio)
            chunk_size = 600.0
            
            start_time = 0.0
            chunk_index = 0
            while start_time < total_duration:
                chunk_duration = min(chunk_size, total_duration - start_time)
                chunk_file = f"temp_chunk_{temp_id}_{chunk_index}.flac"
                temp_files.append(chunk_file)
                
                print(f"Creating chunk {chunk_index} ({start_time:.1f}s to {start_time + chunk_duration:.1f}s)...")
                split_cmd = [
                    "ffmpeg", "-y",
                    "-i", base_audio,
                    "-ss", str(start_time),
                    "-t", str(chunk_duration),
                    "-c:a", "flac",
                    chunk_file
                ]
                subprocess.run(split_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                audio_chunks.append({
                    "path": chunk_file,
                    "offset": start_time
                })
                
                start_time += chunk_duration
                chunk_index += 1
        else:
            audio_chunks.append({
                "path": base_audio,
                "offset": 0.0
            })

        # 4. Determine if audio segmentation (chunking) is needed
        # Always request word-level timestamps so we can generate the 1-word SRT file
        timestamp_granularities = ["segment", "word"]

        merged_segments = []
        merged_words = []

        for chunk in audio_chunks:
            chunk_path = chunk["path"]
            offset = chunk["offset"]
            
            chunk_size_mb = os.path.getsize(chunk_path) / (1024*1024)
            print(f"Uploading chunk '{chunk_path}' (Size: {chunk_size_mb:.2f} MB, offset: {offset:.1f}s) to Groq API...")
            
            with open(chunk_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), f),
                    model=model_name,
                    response_format="verbose_json",
                    timestamp_granularities=timestamp_granularities,
                    temperature=0.0
                )

            # Convert to dictionary safely
            if hasattr(transcription, "model_dump"):
                resp_dict = transcription.model_dump()
            elif hasattr(transcription, "dict"):
                resp_dict = transcription.dict()
            elif isinstance(transcription, dict):
                resp_dict = transcription
            else:
                resp_dict = dict(transcription)

            # Offset and append segments
            for seg in resp_dict.get("segments", []):
                new_seg = dict(seg)
                new_seg["start"] = seg.get("start", 0.0) + offset
                new_seg["end"] = seg.get("end", 0.0) + offset
                merged_segments.append(new_seg)

            # Offset and append words
            for w in resp_dict.get("words", []):
                new_w = dict(w)
                new_w["start"] = w.get("start", 0.0) + offset
                new_w["end"] = w.get("end", 0.0) + offset
                merged_words.append(new_w)

        # 5. Format and save to standard SRT format
        os.makedirs(os.path.dirname(os.path.abspath(output_srt_path)), exist_ok=True)
        
        with open(output_srt_path, "w", encoding="utf-8") as f_out:
            if max_words is not None and merged_words:
                caption_idx = 1
                for j in range(0, len(merged_words), max_words):
                    chunk = merged_words[j:j + max_words]
                    if not chunk:
                        continue
                    
                    chunk_start = format_srt_time(chunk[0].get("start", 0.0))
                    chunk_end = format_srt_time(chunk[-1].get("end", 0.0))
                    chunk_text = " ".join(w.get("word", "").strip() for w in chunk).strip()
                    if uppercase:
                        chunk_text = chunk_text.upper()
                    
                    if chunk_text:
                        f_out.write(f"{caption_idx}\n{chunk_start} --> {chunk_end}\n{chunk_text}\n\n")
                        print(f"[{chunk_start} -> {chunk_end}] {chunk_text}")
                        caption_idx += 1
            else:
                # Use segment-level text
                for i, segment in enumerate(merged_segments, start=1):
                    start_time = format_srt_time(segment.get("start", 0.0))
                    end_time = format_srt_time(segment.get("end", 0.0))
                    text = segment.get("text", "").strip()
                    if uppercase:
                        text = text.upper()
                    
                    f_out.write(f"{i}\n{start_time} --> {end_time}\n{text}\n\n")
                    print(f"[{start_time} -> {end_time}] {text}")

        # 6. Format and save the 1-word-per-timestamp version to {output_file}-1word.srt
        if merged_words:
            if output_srt_path.lower().endswith(".srt"):
                one_word_srt_path = output_srt_path[:-4] + "-1word.srt"
            else:
                one_word_srt_path = f"{output_srt_path}-1word.srt"
            print(f"Saving 1-word-per-timestamp subtitles to {one_word_srt_path}...")
            with open(one_word_srt_path, "w", encoding="utf-8") as f_1word:
                caption_idx = 1
                for word_info in merged_words:
                    word_text = word_info.get("word", "").strip()
                    if not word_text:
                        continue
                    word_start = format_srt_time(word_info.get("start", 0.0))
                    word_end = format_srt_time(word_info.get("end", 0.0))
                    if uppercase:
                        word_text = word_text.upper()
                    
                    f_1word.write(f"{caption_idx}\n{word_start} --> {word_end}\n{word_text}\n\n")
                    caption_idx += 1

        print(f"\nSuccess! Captions saved to {output_srt_path}")
        if merged_words:
            print(f"1-word captions saved to {one_word_srt_path}")

    finally:
        # Cleanup temporary files
        for path in temp_files:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"Warning: Failed to delete temporary file {path}: {e}")

if __name__ == "__main__":
    import argparse

    def positive_int(value):
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"{value} is an invalid positive int value")
        return ivalue

    parser = argparse.ArgumentParser(
        description="Transcribe a video using Groq Cloud Whisper API and generate subtitles SRT file."
    )
    parser.add_argument("video_path", help="Path to the input video file (e.g. mp4).")
    parser.add_argument(
        "--model", "-m",
        choices=["whisper-large-v3", "whisper-large-v3-turbo"],
        default="whisper-large-v3",
        help="Whisper model to use on Groq Cloud: whisper-large-v3 or whisper-large-v3-turbo (default: whisper-large-v3)."
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
        "--preview",
        action="store_true",
        help="Only process the first 5 seconds of the video for preview."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the generated subtitles SRT file (default: same name as video file with .srt extension in the same directory)."
    )

    args = parser.parse_args()

    if args.output:
        output_srt = args.output
    else:
        # Place output SRT in the same directory as the input video file with the same name
        video_dir = os.path.dirname(os.path.abspath(args.video_path))
        video_name_without_ext, _ = os.path.splitext(os.path.basename(args.video_path))
        output_srt = os.path.join(video_dir, f"{video_name_without_ext}.srt")

    transcribe_video_cloud(
        video_path=args.video_path,
        model_name=args.model,
        output_srt_path=output_srt,
        max_words=args.max_words,
        uppercase=args.uppercase,
        preview=args.preview
    )
