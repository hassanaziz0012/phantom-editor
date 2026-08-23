#!/usr/bin/env python3
"""
Summarize YouTube Channel Videos
================================
Fetches all videos from your YouTube channel, downloads audio via yt-dlp to a
temporary location, transcribes using Groq Cloud Whisper API, generates AI video
summaries with ChatGPT via BrowserLLM, and persists results into my_videos.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Add video-editing to sys.path
VIDEO_EDITING_DIR = REPO_ROOT / "video-editing"
if str(VIDEO_EDITING_DIR) not in sys.path:
    sys.path.insert(0, str(VIDEO_EDITING_DIR))

from dotenv import load_dotenv

from agentic.ask_browserllm import ask_chatgpt, load_prompt, parse_json_response
from transcribe_cloud import transcribe_video_cloud
from utils import parse_srt_to_text
from youtube_api.fetch_videos import fetch_channel_videos
from youtube_api.models import Video
from youtube_api.utils import get_youtube_client, resolve_channel_id

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.metadata.summarize_channel")

DEFAULT_OUTPUT_PATH = REPO_ROOT / "metadata" / "recommendations" / "my_videos.json"


def download_youtube_audio(video_url: str, output_dir: Path) -> Path:
    """Downloads the audio track of a YouTube video using yt-dlp."""
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
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        logger.error("yt-dlp audio extraction failed: %s", err_msg)
        raise RuntimeError(f"yt-dlp failed to download audio: {err_msg}")

    for ext in (".flac", ".m4a", ".mp3", ".wav", ".opus", ".ogg"):
        candidate = output_dir / f"audio{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate

    files = [f for f in output_dir.iterdir() if f.is_file() and f.stat().st_size > 0]
    if files:
        return files[0]

    raise FileNotFoundError("No audio file found after yt-dlp execution.")


def generate_video_ai_summary(transcript_text: str) -> str:
    """Generates a concise 2-3 sentence AI summary using ChatGPT via BrowserLLM."""
    logger.info("Generating AI summary with ChatGPT via BrowserLLM...")
    prompt = load_prompt("generate_video_description.md", transcript=transcript_text)
    raw_response = ask_chatgpt(prompt)

    raw_str = str(raw_response).strip()
    print(f"\n[BrowserLLM Response]:\n{raw_str}\n", flush=True)

    if "FATAL ERROR" in raw_str or "hard rate limit reached" in raw_str.lower():
        raise RuntimeError(f"FATAL ERROR: ChatGPT hard rate limit reached: {raw_str}")

    try:
        parsed = parse_json_response(raw_str)
    except Exception as e:
        logger.error("ChatGPT response is not valid JSON. Response snippet: %r", raw_str[:200])
        raise ValueError(f"Failed to parse valid JSON from ChatGPT response: {e}") from e

    summary = ""
    if isinstance(parsed, dict):
        summary = str(parsed.get("description") or parsed.get("summary") or parsed.get("text") or "").strip()
    elif isinstance(parsed, str):
        summary = parsed.strip()

    if not summary:
        raise ValueError(f"Parsed JSON did not contain a valid summary/description string: {parsed}")

    return summary


def summarize_single_video(video: Video) -> str:
    """Downloads audio, transcribes with Groq, and generates AI summary for a video."""
    with tempfile.TemporaryDirectory(prefix=f"phantom_summary_{video.video_id}_") as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Download audio
        audio_file = download_youtube_audio(video.url, temp_path)

        # 2. Transcribe via Groq Cloud Whisper
        srt_file = temp_path / f"{video.video_id}.srt"
        transcribe_video_cloud(input_path=str(audio_file), output_srt_path=str(srt_file))

        # 3. Parse transcript to plain text
        transcript_text = parse_srt_to_text(srt_file)
        if not transcript_text:
            raise ValueError("Transcript is empty.")

        # 4. Generate AI summary with ChatGPT
        return generate_video_ai_summary(transcript_text)


def load_existing_videos(json_path: Path) -> List[Dict[str, Any]]:
    """Loads existing video records from my_videos.json if it exists."""
    if json_path.exists() and json_path.stat().st_size > 0:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning("Could not read existing %s: %s", json_path, e)
    return []


def save_videos(json_path: Path, videos_data: List[Dict[str, Any]]) -> None:
    """Saves video records to the specified JSON file."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(videos_data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def summarize_channel(
    channel: Optional[str] = None,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    limit: Optional[int] = None,
    fresh: bool = False,
    force: bool = False,
    reverse: bool = False,
) -> List[Dict[str, Any]]:
    """Fetches channel videos, downloads audio, transcribes, and summarizes with ChatGPT."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    channel_input = channel or os.getenv("YOUTUBE_CHANNEL_ID")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY is not set in environment or .env")
    if not channel_input:
        raise ValueError("YOUTUBE_CHANNEL_ID is not set and no channel was provided")

    youtube_client = get_youtube_client(api_key)
    channel_id = resolve_channel_id(youtube_client, channel_input)
    logger.info("Resolved channel ID: %s", channel_id)

    videos = fetch_channel_videos(api_key, channel_id, fresh=fresh)
    logger.info("Total videos retrieved from channel: %d", len(videos))

    if reverse:
        videos.reverse()

    if limit is not None and limit > 0:
        videos = videos[:limit]

    # Load existing records
    existing_records = load_existing_videos(output_path)
    records_by_id: Dict[str, Dict[str, Any]] = {
        r["video_id"]: r for r in existing_records if "video_id" in r
    }

    results: List[Dict[str, Any]] = []

    for idx, v in enumerate(videos, start=1):
        existing = records_by_id.get(v.video_id)
        if existing and existing.get("ai_summary") and not force:
            logger.info("[%d/%d] Skipping '%s' (%s) - already summarized.", idx, len(videos), v.title, v.video_id)
            # Keep view / like stats fresh
            existing["views"] = v.view_count
            existing["likes"] = v.like_count
            existing["title"] = v.title
            results.append(existing)
            continue

        logger.info("[%d/%d] Processing video: '%s' (%s)...", idx, len(videos), v.title, v.video_id)
        try:
            ai_summary = summarize_single_video(v)
            pub_date = (
                v.published_at.strftime("%Y-%m-%d")
                if hasattr(v.published_at, "strftime")
                else str(v.published_at)
            )
            record = {
                "video_id": v.video_id,
                "title": v.title,
                "publish_date": pub_date,
                "views": v.view_count,
                "likes": v.like_count,
                "url": v.url,
                "ai_summary": ai_summary,
            }
            records_by_id[v.video_id] = record
            results.append(record)

            # Reconstruct list maintaining channel video order followed by any other cached entries
            ordered_records = []
            seen_ids = set()
            for vid in videos:
                if vid.video_id in records_by_id:
                    ordered_records.append(records_by_id[vid.video_id])
                    seen_ids.add(vid.video_id)
            for r in existing_records:
                vid_id = r.get("video_id")
                if vid_id and vid_id not in seen_ids and vid_id in records_by_id:
                    ordered_records.append(records_by_id[vid_id])
                    seen_ids.add(vid_id)

            save_videos(output_path, ordered_records)
            logger.info("Saved summary for '%s' to %s", v.title, output_path)

        except Exception as e:
            logger.error("Failed to summarize '%s' (%s): %s", v.title, v.video_id, e)
            err_msg = str(e)
            if "fatal error" in err_msg.lower() or "hard rate limit reached" in err_msg.lower() or "rate limit" in err_msg.lower():
                logger.critical("Halting channel summarization due to fatal rate limit / error: %s", e)
                raise
            if existing:
                results.append(existing)

    logger.info("Channel summarization completed. Total records saved: %d", len(results))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download, transcribe with Groq Whisper, and summarize YouTube channel videos using ChatGPT via BrowserLLM."
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="YouTube channel ID, handle (@channel), or custom URL (default: YOUTUBE_CHANNEL_ID in .env).",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"Path to save output JSON (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Maximum number of videos to process.",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-summarization of videos that already have an ai_summary.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Force fresh video list fetch from YouTube API, bypassing local cache.",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Process oldest videos first (default is newest first).",
    )

    args = parser.parse_args()

    try:
        summarize_channel(
            channel=args.channel,
            output_path=Path(args.output).resolve(),
            limit=args.limit,
            fresh=args.fresh,
            force=args.force,
            reverse=args.reverse,
        )
    except Exception as e:
        logger.error("Channel summarization pipeline failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
