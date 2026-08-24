import os
import subprocess
import uuid
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from groq import Groq

# Add the script's directory to the sys.path to guarantee importing from utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import format_srt_time, get_video_info

# Limit for Groq API in bytes (25MB hard limit, using 20MB threshold for safety)
GROQ_LIMIT_BYTES = 25 * 1024 * 1024
SAFE_CHUNK_BYTES = 20 * 1024 * 1024

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".alac", ".pcm"
}

def is_audio_file(file_path):
    """Determines if the given file path is an audio file."""
    ext = Path(file_path).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return True
    info = get_video_info(file_path)
    if info.has_audio and info.width == 0 and info.height == 0:
        return True
    return False

def extract_audio(input_path, output_audio_path, duration=None):
    """Extracts audio from a video or audio file into a FLAC file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-threads", "0",
        "-i", str(input_path)
    ]
    if duration:
        cmd.extend(["-t", str(duration)])
    cmd.extend([
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "flac",
        str(output_audio_path)
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

def init_groq_clients():
    """Loads API keys and returns a list of initialized Groq clients."""
    load_dotenv()
    api_keys = []
    if os.environ.get("GROQ_API_KEY"):
        api_keys.append(os.environ["GROQ_API_KEY"])

    numbered_keys = sorted([k for k in os.environ.keys() if k.startswith("GROQ_API_KEY_")])
    for k in numbered_keys:
        val = os.environ.get(k)
        if val and val not in api_keys:
            api_keys.append(val)

    if not api_keys:
        print("Error: GROQ_API_KEY environment variable is not set. Please set it in your environment or .env file.")
        sys.exit(1)

    return [Groq(api_key=k, timeout=300.0, max_retries=0) for k in api_keys]

def prepare_audio_chunks(input_path, base_audio, temp_id, temp_files):
    """Checks audio file size and splits into chunks (guaranteeing <= 20 MB each) if exceeding Groq limit."""
    file_size = os.path.getsize(base_audio)

    # If base_audio itself is already within safe limit (<= 20 MB), process directly
    if file_size <= SAFE_CHUNK_BYTES:
        return [{
            "index": 0,
            "path": base_audio,
            "offset": 0.0
        }]

    print(f"Audio size ({file_size / (1024*1024):.2f} MB) exceeds the Groq limit of 25 MB.")
    print("Splitting audio into chunks using FFmpeg...")

    audio_info = get_video_info(base_audio)
    total_duration = audio_info.duration
    if total_duration <= 0:
        total_duration = get_video_info(input_path).duration

    audio_chunks = []
    chunk_index = 0
    start_time = 0.0
    default_chunk_duration = 300.0  # 5 minutes default chunk length

    while start_time < total_duration:
        chunk_duration = min(default_chunk_duration, total_duration - start_time)
        if chunk_duration <= 0.1:
            break

        chunk_file = f"temp_chunk_{temp_id}_{chunk_index:03d}.flac"

        while True:
            split_cmd = [
                "ffmpeg", "-y",
                "-threads", "0",
                "-ss", str(start_time),
                "-t", str(chunk_duration),
                "-i", base_audio,
                "-vn",
                "-ar", "16000",
                "-ac", "1",
                "-c:a", "flac",
                chunk_file
            ]
            subprocess.run(split_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            c_size = os.path.getsize(chunk_file) if os.path.exists(chunk_file) else 0
            if c_size <= SAFE_CHUNK_BYTES or chunk_duration <= 10.0:
                break

            # If chunk is larger than safe threshold and can be halved, retry
            if os.path.exists(chunk_file):
                os.remove(chunk_file)
            chunk_duration /= 2.0

        if os.path.exists(chunk_file) and os.path.getsize(chunk_file) > 0:
            temp_files.append(chunk_file)
            audio_chunks.append({
                "index": chunk_index,
                "path": chunk_file,
                "offset": start_time
            })
            chunk_index += 1

        start_time += chunk_duration

    return audio_chunks

def transcribe_single_chunk(chunk_info, client, model_name, total_chunks):
    """Transcribes a single audio chunk via Groq API and adjusts segment/word offsets."""
    chunk_path = chunk_info["path"]
    offset = chunk_info["offset"]
    idx = chunk_info["index"]
    chunk_size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
    print(f"Uploading chunk {idx + 1}/{total_chunks} '{chunk_path}' (Size: {chunk_size_mb:.2f} MB, offset: {offset:.1f}s) to Groq API...")

    with open(chunk_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(chunk_path), f),
            model=model_name,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
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

    chunk_segments = []
    for seg in resp_dict.get("segments", []):
        new_seg = dict(seg)
        new_seg["start"] = seg.get("start", 0.0) + offset
        new_seg["end"] = seg.get("end", 0.0) + offset
        chunk_segments.append(new_seg)

    chunk_words = []
    for w in resp_dict.get("words", []):
        new_w = dict(w)
        new_w["start"] = w.get("start", 0.0) + offset
        new_w["end"] = w.get("end", 0.0) + offset
        chunk_words.append(new_w)

    return idx, chunk_segments, chunk_words

def transcribe_chunks_parallel(audio_chunks, clients, model_name, max_threads=4):
    """Transcribes audio chunks in parallel using ThreadPoolExecutor."""
    total_chunks = len(audio_chunks)
    num_threads = min(max_threads, total_chunks)
    print(f"Transcribing {total_chunks} chunk(s) using {num_threads} thread(s)...")

    results = [None] * total_chunks
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_idx = {
            executor.submit(
                transcribe_single_chunk,
                chunk,
                clients[i % len(clients)],
                model_name,
                total_chunks
            ): chunk["index"]
            for i, chunk in enumerate(audio_chunks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                res_idx, chunk_segments, chunk_words = future.result()
                results[res_idx] = (chunk_segments, chunk_words)
                print(f"Chunk {res_idx + 1}/{total_chunks} transcribed successfully.")
            except Exception as exc:
                print(f"Error transcribing chunk {idx + 1}/{total_chunks}: {exc}")
                raise exc

    merged_segments = []
    merged_words = []
    for res_idx in range(total_chunks):
        seg_list, word_list = results[res_idx]
        merged_segments.extend(seg_list)
        merged_words.extend(word_list)

    return merged_segments, merged_words

def write_srt_subtitles(output_srt_path, merged_segments, merged_words, max_words=None, uppercase=False):
    """Formats and writes standard SRT and 1-word-per-timestamp SRT files."""
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
                    caption_idx += 1
        else:
            for i, segment in enumerate(merged_segments, start=1):
                start_time = format_srt_time(segment.get("start", 0.0))
                end_time = format_srt_time(segment.get("end", 0.0))
                text = segment.get("text", "").strip()
                if uppercase:
                    text = text.upper()

                f_out.write(f"{i}\n{start_time} --> {end_time}\n{text}\n\n")

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

def transcribe_video_cloud(input_path=None, model_name="whisper-large-v3", output_srt_path=None, max_words=None, uppercase=False, preview=False, max_threads=4, video_path=None):
    """Main orchestrator function to extract audio (if video), split chunks, transcribe, and save SRT."""
    if input_path is None:
        input_path = video_path
    if not input_path or not os.path.exists(input_path):
        print(f"Error: Input file not found at {input_path}")
        sys.exit(1)

    clients = init_groq_clients()

    temp_files = []
    try:
        temp_id = uuid.uuid4().hex[:8]

        # 1. Determine if input is audio or video
        is_audio = is_audio_file(input_path)

        if is_audio and not preview:
            # Audio files do not need raw audio extraction unless preview slice is requested
            base_audio = input_path
        else:
            extracted_audio = f"temp_extracted_{temp_id}.flac"
            temp_files.append(extracted_audio)
            duration = 5 if preview else None
            extract_audio(input_path, extracted_audio, duration=duration)
            base_audio = extracted_audio

        # 2. Determine and split into audio chunks if needed
        audio_chunks = prepare_audio_chunks(input_path, base_audio, temp_id, temp_files)

        # 3. Transcribe audio chunks in parallel
        merged_segments, merged_words = transcribe_chunks_parallel(
            audio_chunks, clients, model_name, max_threads=max_threads
        )

        # 4. Format and save SRT files
        write_srt_subtitles(
            output_srt_path, merged_segments, merged_words, max_words=max_words, uppercase=uppercase
        )

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
        description="Transcribe a video or audio file using Groq Cloud Whisper API and generate subtitles SRT file."
    )
    parser.add_argument("input_path", help="Path to the input video or audio file (e.g. mp4, mp3, wav, flac).")
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
        help="Only process the first 5 seconds for preview."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the generated subtitles SRT file (default: same name as input file with .srt extension in the same directory)."
    )
    parser.add_argument(
        "--threads", "-t",
        type=positive_int,
        default=4,
        help="Number of threads for sending parallel transcription requests to Groq (default: 4)."
    )

    args = parser.parse_args()

    if args.output:
        output_srt = args.output
    else:
        # Place output SRT in the same directory as the input file with the same name
        input_dir = os.path.dirname(os.path.abspath(args.input_path))
        input_name_without_ext, _ = os.path.splitext(os.path.basename(args.input_path))
        output_srt = os.path.join(input_dir, f"{input_name_without_ext}.srt")

    transcribe_video_cloud(
        input_path=args.input_path,
        model_name=args.model,
        output_srt_path=output_srt,
        max_words=args.max_words,
        uppercase=args.uppercase,
        preview=args.preview,
        max_threads=args.threads
    )
