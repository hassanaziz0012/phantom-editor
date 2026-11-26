import os
import subprocess
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import groq
from groq import Groq

from video_editing.utils import format_srt_time, get_video_info
from shared.media import extract_audio

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

def init_groq_clients():
    """Loads API keys and returns a list of initialized Groq clients."""
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

CHUNK_TARGET_SECONDS = 300.0   # aim for 5-minute chunks
SILENCE_SEARCH_WINDOW = 30.0   # look for a silence within +/- this many seconds of the target cut


def detect_silences(audio_path):
    """Returns a list of (start, end) silence intervals found by ffmpeg silencedetect."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", audio_path,
         "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", result.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: (-?[\d.]+)", result.stderr)]
    return list(zip(starts, ends))


def choose_cut(silences, start_time, total_duration, max_duration):
    """Returns the end time of the chunk starting at start_time.

    Cuts in the middle of the silence closest to the target, so no word is split;
    falls back to a hard cut at the target if there is no silence nearby.
    """
    target = start_time + max_duration
    if target >= total_duration - 1.0:
        return total_duration
    best = None
    for s_start, s_end in silences:
        mid = (s_start + s_end) / 2.0
        if abs(mid - target) <= SILENCE_SEARCH_WINDOW and mid > start_time + 10.0:
            if best is None or abs(mid - target) < abs(best - target):
                best = mid
    return best if best is not None else target


def prepare_audio_chunks(input_path, base_audio, scratch_dir, temp_files=None):
    """Checks audio file size and splits into chunks (guaranteeing <= 20 MB each) if exceeding Groq limit.

    Chunk files are written to scratch_dir. Cuts are placed at silences near the
    5-minute mark so words are not lost at the seams.
    """
    file_size = os.path.getsize(base_audio)

    # If base_audio itself is already within safe limit (<= 20 MB), process directly
    if file_size <= SAFE_CHUNK_BYTES:
        return [{
            "index": 0,
            "path": base_audio,
            "offset": 0.0
        }]

    print(f"Audio size ({file_size / (1024*1024):.2f} MB) exceeds the Groq limit of 25 MB.")
    print("Splitting audio into chunks at silences using FFmpeg...")

    # Use original video duration as authoritative source; FLAC duration from ffprobe can be unreliable
    video_info = get_video_info(input_path)
    total_duration = video_info.duration
    if total_duration <= 0:
        audio_info = get_video_info(base_audio)
        total_duration = audio_info.duration

    silences = detect_silences(base_audio)

    audio_chunks = []
    chunk_index = 0
    start_time = 0.0
    max_duration = CHUNK_TARGET_SECONDS

    while start_time < total_duration:
        end_time = choose_cut(silences, start_time, total_duration, max_duration)
        chunk_duration = end_time - start_time
        if chunk_duration <= 0.1:
            break

        chunk_file = os.path.join(scratch_dir, f"chunk_{chunk_index:03d}.flac")

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
            try:
                subprocess.run(split_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError as e:
                # If seek fails (e.g., start_time beyond actual audio duration), stop chunking
                if e.returncode == 234 or "Invalid data found" in str(e):
                    print(f"Warning: ffmpeg seek failed at {start_time:.1f}s (exit code {e.returncode}). Stopping chunk split.")
                    return audio_chunks
                raise

            c_size = os.path.getsize(chunk_file) if os.path.exists(chunk_file) else 0
            if c_size <= SAFE_CHUNK_BYTES or chunk_duration <= 10.0:
                break

            # Chunk is larger than the safe threshold: halve the target and re-pick a silence
            if os.path.exists(chunk_file):
                os.remove(chunk_file)
            max_duration = chunk_duration / 2.0
            chunk_duration = choose_cut(silences, start_time, total_duration, max_duration) - start_time

        if os.path.exists(chunk_file) and os.path.getsize(chunk_file) > 0:
            audio_chunks.append({
                "index": chunk_index,
                "path": chunk_file,
                "offset": start_time
            })
            chunk_index += 1

        start_time += chunk_duration
        max_duration = CHUNK_TARGET_SECONDS

    return audio_chunks

def _retry_delay(exc, attempt):
    """Seconds to wait before the next attempt (honours Retry-After when present)."""
    delay = min(2.0 * (attempt + 1), 20.0)
    response = getattr(exc, "response", None)
    try:
        retry_after = float(response.headers.get("retry-after")) if response is not None else None
    except (TypeError, ValueError):
        retry_after = None
    if retry_after is not None:
        delay = max(delay, min(retry_after, 60.0))
    return delay


def _is_transient(exc):
    """True for rate limits (429), timeouts, 5xx and connection errors."""
    if isinstance(exc, (groq.APIConnectionError, groq.APITimeoutError)):
        return True
    if isinstance(exc, groq.APIStatusError):
        return exc.status_code in (408, 409, 429) or exc.status_code >= 500
    return False


def transcribe_single_chunk(chunk_info, clients, model_name, total_chunks, start_client=0):
    """Transcribes a single audio chunk via Groq, retrying transient failures with the next key.

    Rate limits (429) and transient errors (5xx, timeouts, connection errors) are retried
    by rotating through the configured keys with a short backoff. Gives up with a clear
    error once the attempt cap is reached.
    """
    if not isinstance(clients, (list, tuple)):
        clients = [clients]
    max_attempts = min(max(3, len(clients) * 2), 8)
    last_exc = None
    for attempt in range(max_attempts):
        client = clients[(start_client + attempt) % len(clients)]
        try:
            return _transcribe_chunk_once(chunk_info, client, model_name, total_chunks)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            delay = _retry_delay(exc, attempt)
            print(f"Chunk {chunk_info['index'] + 1}/{total_chunks}: {type(exc).__name__} "
                  f"({exc}); retrying with next key in {delay:.0f}s "
                  f"(attempt {attempt + 2}/{max_attempts})...")
            time.sleep(delay)
    raise RuntimeError(
        f"Chunk {chunk_info['index'] + 1}/{total_chunks} failed after {max_attempts} attempts "
        f"across {len(clients)} Groq key(s); last error: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _transcribe_chunk_once(chunk_info, client, model_name, total_chunks):
    """Single Groq request for one chunk; adjusts segment/word offsets."""
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
                clients,
                model_name,
                total_chunks,
                i % len(clients)
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

    # Scratch dir lives inside the input file's folder (not /tmp, not cwd); only it is removed.
    scratch_dir = tempfile.mkdtemp(prefix=".transcribe-", dir=os.path.dirname(os.path.abspath(input_path)))
    try:

        # 1. Determine if input is audio or video
        is_audio = is_audio_file(input_path)

        if is_audio and not preview:
            # Audio files do not need raw audio extraction unless preview slice is requested
            base_audio = input_path
        else:
            extracted_audio = os.path.join(scratch_dir, "extracted.flac")
            duration = 5 if preview else None
            print(f"Extracting audio to {extracted_audio}...")
            extract_audio(input_path, extracted_audio, sample_rate=16000, codec="flac", duration=duration)
            base_audio = extracted_audio

        # 2. Determine and split into audio chunks if needed
        audio_chunks = prepare_audio_chunks(input_path, base_audio, scratch_dir)

        # 3. Transcribe audio chunks in parallel
        merged_segments, merged_words = transcribe_chunks_parallel(
            audio_chunks, clients, model_name, max_threads=max_threads
        )

        # 4. Format and save SRT files
        write_srt_subtitles(
            output_srt_path, merged_segments, merged_words, max_words=max_words, uppercase=uppercase
        )

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

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
