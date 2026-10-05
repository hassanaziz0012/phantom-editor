#!/usr/bin/env python3
"""
YouTube Video Summarizer
========================
Downloads audio from a YouTube video via yt-dlp, transcribes it using Groq Cloud
via phantom transcribe-cloud, analyzes and summarizes the transcript with OpenRouter
(using nvidia/nemotron-3-super-120b-a12b:free).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add repository root and video-editing directory to sys.path
repo_root = Path(__file__).resolve().parent.parent
video_editing_dir = repo_root / "video-editing"
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

from utils import parse_srt_to_text
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agentic.ask_openrouter import ask_openrouter

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.summarizer")

DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
PROMPT_PATH = repo_root / "agentic" / "prompts" / "summarize_yt_video.md"


class VideoSummaryResponse(BaseModel):
    """Structured response schema for YouTube video summary and takeaways."""

    summary: str = Field(
        ...,
        description="A concise summary highlighting the overall overview of the YouTube video.",
    )
    takeaways: List[str] = Field(
        ...,
        description="A list of key takeaways, main lessons, or actionable insights from the YouTube video.",
    )


def extract_video_id(input_str: str) -> str:
    """
    Extracts the 11-character YouTube video ID from various URL formats or raw ID.

    Examples:
        - https://www.youtube.com/watch?v=dQw4w9WgXcQ -> dQw4w9WgXcQ
        - https://youtu.be/dQw4w9WgXcQ -> dQw4w9WgXcQ
        - https://www.youtube.com/shorts/dQw4w9WgXcQ -> dQw4w9WgXcQ
        - dQw4w9WgXcQ -> dQw4w9WgXcQ
    """
    input_str = input_str.strip()

    # Raw 11-char ID
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", input_str):
        return input_str

    # YouTube URL patterns
    patterns = [
        r"(?:v=|\/v\/|embed\/|shorts\/|youtu\.be\/|\/e\/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)

    raise ValueError(f"Could not extract a valid YouTube video ID from: '{input_str}'")


def get_video_metadata(video_url: str) -> Dict[str, Any]:
    """Fetches video metadata (title, channel_id, channel_title) using yt-dlp --dump-json."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--dump-json",
        "--skip-download",
        video_url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        return {
            "title": data.get("title", ""),
            "channel_id": data.get("channel_id") or data.get("uploader_id") or "unknown_channel",
            "channel_title": data.get("channel") or data.get("uploader") or "",
            "duration": data.get("duration"),
            "view_count": data.get("view_count", 0),
        }
    except Exception as e:
        logger.warning("Could not fetch metadata via yt-dlp: %s", e)
        return {
            "title": "",
            "channel_id": "unknown_channel",
            "channel_title": "",
            "duration": None,
            "view_count": 0,
        }


def download_youtube_audio(video_url: str, output_dir: Path) -> Path:
    """
    Downloads the audio track of a YouTube video into output_dir using yt-dlp.

    Returns:
        Path to the downloaded audio file.
    """
    logger.info("Downloading audio via yt-dlp for %s...", video_url)
    output_template = str(output_dir / "audio.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format", "flac",
        "--audio-quality", "0",
        "-o", output_template,
        video_url,
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        logger.error("yt-dlp audio extraction failed: %s", err_msg)
        raise RuntimeError(f"yt-dlp failed to download audio: {err_msg}")

    # Search for downloaded audio file
    for ext in (".flac", ".m4a", ".mp3", ".wav", ".opus", ".ogg"):
        candidate = output_dir / f"audio{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            logger.info("Audio downloaded successfully: %s (%d bytes)", candidate.name, candidate.stat().st_size)
            return candidate

    # Find any audio file in output_dir
    files = [f for f in output_dir.iterdir() if f.is_file() and f.stat().st_size > 0]
    if files:
        logger.info("Found audio file: %s", files[0].name)
        return files[0]

    raise FileNotFoundError("No audio file found after yt-dlp execution.")


def run_transcription(audio_path: Path, output_srt_path: Path) -> Path:
    """
    Invokes phantom transcribe-cloud (or transcribe_cloud.py directly) to transcribe the audio.

    Returns:
        Path to generated SRT subtitles file.
    """
    logger.info("Transcribing audio using Groq Cloud (transcribe_cloud)...")

    transcribe_script = repo_root / "video-editing" / "transcribe_cloud.py"
    phantom_bin = repo_root / "phantom"

    # Prefer invoking through python running transcribe_cloud.py or phantom CLI
    if transcribe_script.exists():
        cmd = [
            sys.executable,
            str(transcribe_script),
            str(audio_path),
            "--output", str(output_srt_path),
        ]
    elif phantom_bin.exists() and os.access(phantom_bin, os.X_OK):
        cmd = [
            str(phantom_bin),
            "edit",
            "transcribe-cloud",
            str(audio_path),
            "--output", str(output_srt_path),
        ]
    else:
        cmd = [
            "phantom",
            "edit",
            "transcribe-cloud",
            str(audio_path),
            "--output", str(output_srt_path),
        ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Transcription process failed with exit code %d", e.returncode)
        raise RuntimeError(f"transcribe-cloud failed with exit code {e.returncode}")

    if not output_srt_path.exists() or output_srt_path.stat().st_size == 0:
        raise FileNotFoundError(f"Transcript SRT file was not generated at {output_srt_path}")

    logger.info("Transcription completed: %s", output_srt_path)
    return output_srt_path


def load_system_prompt() -> str:
    """Loads system prompt from agentic/prompts/summarize_yt_video.md."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You are an expert YouTube content analyst. Provide a concise summary and "
        "a list of key takeaways from the provided transcript strictly conforming to the JSON schema."
    )


def summarize_transcript(
    transcript: str,
    video_title: str = "",
    model: str = DEFAULT_MODEL,
) -> VideoSummaryResponse:
    """
    Sends the video transcript to OpenRouter and receives structured summary & takeaways.
    """
    logger.info("Generating summary via OpenRouter (%s)...", model)
    system_prompt = load_system_prompt()

    user_prompt = f"Video Title: {video_title}\n\nTranscript:\n{transcript}" if video_title else f"Transcript:\n{transcript}"

    result = ask_openrouter(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=VideoSummaryResponse,
        model=model,
        schema_name="video_summary",
        temperature=0.2,
        strict=True,
    )

    if isinstance(result, VideoSummaryResponse):
        return result
    elif isinstance(result, dict):
        return VideoSummaryResponse.model_validate(result)
    else:
        raise ValueError(f"Unexpected response format from ask_openrouter: {type(result)}")


def summarize_video(
    video_input: str,
    model: str = DEFAULT_MODEL,
) -> Tuple[VideoSummaryResponse, str]:
    """
    Full pipeline to summarize a YouTube video:
    1. Extracts video ID and metadata.
    2. Downloads audio via yt-dlp to a temp dir.
    3. Transcribes audio via phantom transcribe-cloud.
    4. Generates summary & takeaways via OpenRouter.

    Returns:
        (VideoSummaryResponse, video_id)
    """
    video_id = extract_video_id(video_input)
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info("Starting summarization for YouTube video: %s (%s)", video_id, video_url)

    metadata = get_video_metadata(video_url)
    title = metadata.get("title", "")

    with tempfile.TemporaryDirectory(prefix=f"phantom_yt_{video_id}_") as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Download audio
        audio_file = download_youtube_audio(video_url, temp_path)

        # 2. Transcribe
        srt_file = temp_path / f"{video_id}.srt"
        run_transcription(audio_file, srt_file)

        # 3. Parse transcript
        transcript_text = parse_srt_to_text(srt_file)
        if not transcript_text:
            raise ValueError("Parsed transcript is empty.")
        logger.info("Extracted %d characters of transcript text.", len(transcript_text))

        # 4. Summarize via OpenRouter
        summary_result = summarize_transcript(transcript_text, video_title=title, model=model)

    return summary_result, video_id


def main():
    parser = argparse.ArgumentParser(
        description="Download, transcribe, and summarize YouTube videos using yt-dlp, Groq Whisper, and OpenRouter."
    )
    parser.add_argument(
        "video",
        help="YouTube video URL or 11-character Video ID (e.g. https://www.youtube.com/watch?v=... or dQw4w9WgXcQ).",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of human-readable formatting.",
    )

    args = parser.parse_args()

    try:
        summary_obj, v_id = summarize_video(
            video_input=args.video,
            model=args.model,
        )

        if args.json:
            print(json.dumps(summary_obj.model_dump(), indent=2))
        else:
            print("\n" + "=" * 60)
            print(f"  YOUTUBE VIDEO SUMMARY: {v_id}")
            print("=" * 60 + "\n")
            print("## SUMMARY\n")
            print(summary_obj.summary)
            print("\n## KEY TAKEAWAYS\n")
            for idx, takeaway in enumerate(summary_obj.takeaways, 1):
                print(f"{idx}. {takeaway}")
            print("\n" + "=" * 60 + "\n")

    except Exception as e:
        logger.error("Summarization pipeline failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
