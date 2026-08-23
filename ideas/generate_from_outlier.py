#!/usr/bin/env python3
"""
YouTube Outlier Video Idea Generator
====================================
Accepts a YouTube video URL, locates the video in the PostgreSQL outliers database,
summarizes the video via OpenRouter and Groq Cloud if not already summarized,
gathers the creator's recent channel video titles, constructs a contextual prompt,
queries Claude via BrowserLLM (headful mode), and exports the resulting content ideas
to the Google Sheets Content Calendar.

Usage:
    python -m ideas.generate_from_outlier <video_url_or_id> [options]
    phantom ideas generate-from-outlier <video_url_or_id> [options]
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
from typing import Any, Dict, List, Optional, Tuple, Union

# Add repository root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.outliers_generator")

PROMPT_TEMPLATE_PATH = repo_root / "agentic" / "prompts" / "generate_ideas_from_outliers.md"


# ── Terminal Styling / Colors ──────────────────────────────────────────────────

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    CYAN = "\033[36m"
    BRIGHT_CYAN = "\033[96m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    YELLOW = "\033[33m"
    BRIGHT_YELLOW = "\033[93m"
    BLUE = "\033[34m"
    BRIGHT_BLUE = "\033[94m"
    MAGENTA = "\033[35m"
    BRIGHT_MAGENTA = "\033[95m"
    RED = "\033[31m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"


# ── Video ID Extraction ────────────────────────────────────────────────────────

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

    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", input_str):
        return input_str

    patterns = [
        r"(?:v=|\/v\/|embed\/|shorts\/|youtu\.be\/|\/e\/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)

    raise ValueError(f"Could not extract a valid YouTube video ID from: '{input_str}'")


# ── Database Lookup ────────────────────────────────────────────────────────────

def find_video_in_db(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Queries the PostgreSQL database for a video record and its channel information.
    """
    try:
        from ideas.outliers_db.connection import get_db_connection

        sql = """
        SELECT
            v.video_id,
            v.channel_id,
            v.title,
            v.description,
            v.url,
            v.summary,
            v.takeaways,
            v.view_count,
            v.like_count,
            v.comment_count,
            v.score,
            v.view_score,
            v.is_outlier,
            c.title AS channel_title,
            c.custom_url AS channel_custom_url,
            c.avg_views AS channel_avg_views
        FROM videos v
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        WHERE v.video_id = %s;
        """
        with get_db_connection(auto_start=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (video_id,))
                row = cur.fetchone()
                if row:
                    return dict(row)
    except Exception as e:
        logger.warning("Could not query PostgreSQL database: %s", e)

    return None


# ── Video Metadata Fetching (Fallback) ─────────────────────────────────────────

def fetch_video_metadata(video_id: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches title, description, and channel_title using YouTube API or yt-dlp.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # 1. Try YouTube Data API
    api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    if api_key:
        try:
            from youtube_api.utils import get_youtube_client
            youtube = get_youtube_client(api_key)
            resp = youtube.videos().list(part="snippet,statistics", id=video_id).execute()
            items = resp.get("items", [])
            if items:
                snippet = items[0].get("snippet", {})
                stats = items[0].get("statistics", {})
                return {
                    "title": snippet.get("title", "").strip(),
                    "channel_title": snippet.get("channelTitle", "").strip(),
                    "channel_id": snippet.get("channelId", "").strip(),
                    "description": snippet.get("description", "").strip(),
                    "view_count": int(stats.get("viewCount", 0)),
                }
        except Exception as e:
            logger.debug("YouTube API metadata fetch failed: %s", e)

    # 2. Fallback to yt-dlp
    try:
        from ideas.summarize_video import get_video_metadata as ytdlp_metadata
        data = ytdlp_metadata(video_url)
        return {
            "title": data.get("title", "").strip(),
            "channel_title": data.get("channel_title", "").strip(),
            "channel_id": data.get("channel_id", "").strip(),
            "description": data.get("description", "").strip(),
            "view_count": data.get("view_count", 0),
        }
    except Exception as e:
        logger.debug("yt-dlp metadata fetch failed: %s", e)

    return {
        "title": f"YouTube Video ({video_id})",
        "channel_title": "Unknown Channel",
        "channel_id": "unknown_channel",
        "description": "No description available.",
        "view_count": 0,
    }


# ── Video Summarization ────────────────────────────────────────────────────────

def get_or_create_summary(
    video_id: str,
    db_record: Optional[Dict[str, Any]] = None,
    force_fresh: bool = False,
    model: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Retrieves summary and takeaways from the database if present, or executes
    ideas/summarize_video.py to download, transcribe, summarize, and save to DB.

    Returns:
        (summary_text, takeaways_list)
    """
    # Check if existing summary in DB is valid
    if not force_fresh and db_record:
        summary = db_record.get("summary")
        takeaways = db_record.get("takeaways")
        if summary and summary.strip():
            takeaways_list: List[str] = []
            if isinstance(takeaways, list):
                takeaways_list = [str(t).strip() for t in takeaways if str(t).strip()]
            elif isinstance(takeaways, str) and takeaways.strip():
                takeaways_list = [t.strip() for t in takeaways.split("\n") if t.strip()]
            return summary.strip(), takeaways_list

    # Execute summarization via ideas/summarize_video.py
    from ideas.summarize_video import DEFAULT_MODEL, summarize_video

    summary_model = model or DEFAULT_MODEL
    logger.info("Summarizing video %s via OpenRouter (%s)...", video_id, summary_model)
    summary_obj, _ = summarize_video(video_id, model=summary_model, save_to_db=True)

    return summary_obj.summary, summary_obj.takeaways


# ── Fetch Recent Channel Video Titles ──────────────────────────────────────────

def fetch_recent_channel_titles(
    channel: Optional[str] = None,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> List[str]:
    """
    Fetches the latest `limit` video titles from the user's YouTube channel.
    Uses YOUTUBE_CHANNEL_ID and YOUTUBE_API_KEY from .env.
    """
    api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    channel_target = channel or os.getenv("YOUTUBE_CHANNEL_ID")
    if not api_key or not channel_target:
        logger.debug("YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID not set. Skipping channel titles.")
        return []

    try:
        from youtube_api.fetch_videos import fetch_channel_videos
        from youtube_api.utils import get_youtube_client, resolve_channel_id

        youtube_client = get_youtube_client(api_key)
        resolved_id = resolve_channel_id(youtube_client, channel_target)
        videos = fetch_channel_videos(api_key, resolved_id, quiet=True)
        return [v.title for v in videos[:limit]]
    except Exception as e:
        logger.warning("Failed to fetch recent channel video titles: %s", e)
        return []


# ── Prompt Formatting ─────────────────────────────────────────────────────────

def build_outlier_prompt(
    title: str,
    channel_name: str,
    description: str,
    summary: str,
    takeaways: Union[List[str], str],
    recent_videos: Optional[List[str]] = None,
) -> str:
    """
    Fills in the prompt placeholders from agentic/prompts/generate_ideas_from_outliers.md:
    {title}, {channel_name}, {description}, {summary}, {takeaways}, {last_20_videos}.
    """
    if not PROMPT_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Prompt template file not found at: {PROMPT_TEMPLATE_PATH}")

    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    # Format takeaways
    if isinstance(takeaways, list):
        formatted_takeaways = "\n".join(f"{i}. {t}" for i, t in enumerate(takeaways, 1))
    else:
        formatted_takeaways = str(takeaways).strip() or "No takeaways available."

    # Format recent videos (last 20)
    if recent_videos:
        formatted_recent = "\n".join(f"{i}. {t}" for i, t in enumerate(recent_videos, 1))
    else:
        formatted_recent = "No recent video titles available."

    prompt = template.replace("{title}", title or "Untitled Video")
    prompt = prompt.replace("{channel_name}", channel_name or "Unknown Channel")
    prompt = prompt.replace("{description}", description or "No description provided.")
    prompt = prompt.replace("{summary}", summary or "No summary provided.")
    prompt = prompt.replace("{takeaways}", formatted_takeaways)
    prompt = prompt.replace("{last_20_videos}", formatted_recent)

    return prompt


from agentic.ask_browserllm import ask_claude


# ── Query BrowserLLM (Claude - Headful Mode) ──────────────────────────────────

def query_browserllm_claude(prompt_text: str, **kwargs: Any) -> str:
    """
    Executes BrowserLLM with provider Claude via agentic.ask_browserllm.
    """
    logger.info("Executing BrowserLLM Claude query...")
    return str(ask_claude(user_prompt=prompt_text))


# ── Parse Claude Response ─────────────────────────────────────────────────────

def parse_claude_response(response_text: str) -> List[Dict[str, Any]]:
    """
    Parses Claude's response into a list of idea objects with source, idea, and confidence_score.
    """
    text = response_text.strip()
    if not text:
        return []

    # 1. Try parsing raw JSON
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _validate_ideas_list(data)
    except Exception:
        pass

    # 2. Try extracting JSON from markdown code fences
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(fence_pattern, text)
    for match in matches:
        try:
            data = json.loads(match.strip())
            if isinstance(data, list):
                return _validate_ideas_list(data)
        except Exception:
            continue

    # 3. Try finding outermost JSON array [...]
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        json_candidate = text[first_bracket : last_bracket + 1]
        try:
            data = json.loads(json_candidate)
            if isinstance(data, list):
                return _validate_ideas_list(data)
        except Exception:
            pass

    logger.warning("Could not parse structured JSON array from response. Returning raw text.")
    return [{"source": "BrowserLLM Raw Response", "idea": text, "confidence_score": ""}]


def _validate_ideas_list(data: List[Any]) -> List[Dict[str, Any]]:
    """Validates and cleans the list of idea objects."""
    results: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            idea = str(item.get("idea", "")).strip()
            confidence_score = str(item.get("confidence_score", item.get("confidenceScore", ""))).strip()
            if source or idea:
                entry: Dict[str, Any] = {"source": source, "idea": idea}
                if confidence_score:
                    entry["confidence_score"] = confidence_score
                results.append(entry)
        elif isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                results.append({"source": "", "idea": cleaned, "confidence_score": ""})
    return results


# ── Terminal Display ──────────────────────────────────────────────────────────

def print_terminal_results(
    video_id: str,
    title: str,
    channel_title: str,
    outlier_score: Optional[float],
    view_count: Optional[int],
    is_outlier: bool,
    ideas: List[Dict[str, Any]],
    exported: bool = False,
    summary_preview: str = "",
):
    """
    Renders generated video ideas in a styled, readable terminal format.
    """
    c = Colors
    print()
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}╔══════════════════════════════════════════════════════════════════════════════╗{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}║ YouTube Outlier Idea Generator (Powered by Claude via BrowserLLM)             ║{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}╚══════════════════════════════════════════════════════════════════════════════╝{c.RESET}")
    print(f"  {c.BOLD}Video:{c.RESET}          {c.WHITE}{title}{c.RESET}")
    print(f"  {c.BOLD}Channel:{c.RESET}        {c.BRIGHT_CYAN}{channel_title}{c.RESET}")
    print(f"  {c.BOLD}Video URL:{c.RESET}      {c.BRIGHT_YELLOW}https://youtu.be/{video_id}{c.RESET}")

    status_badges = []
    if is_outlier:
        status_badges.append(f"{c.BRIGHT_GREEN}[OUTLIER VIDEO]{c.RESET}")
    if outlier_score is not None and outlier_score > 0:
        status_badges.append(f"{c.YELLOW}Outlier Score: {outlier_score:.2f}x{c.RESET}")
    if view_count:
        status_badges.append(f"{c.CYAN}Views: {view_count:,}{c.RESET}")

    if status_badges:
        print(f"  {c.BOLD}DB Metrics:{c.RESET}     {' | '.join(status_badges)}")

    if summary_preview:
        short_summary = (summary_preview[:200] + "...") if len(summary_preview) > 200 else summary_preview
        print(f"  {c.BOLD}Summary:{c.RESET}        {c.DIM}{short_summary}{c.RESET}")

    print(f"{c.GRAY}{'─' * 78}{c.RESET}\n")

    if not ideas:
        print(f"  {c.YELLOW}No content ideas could be extracted from the video.{c.RESET}\n")
        return

    for idx, item in enumerate(ideas, 1):
        idea = item.get("idea", "").strip()
        source = item.get("source", "").strip()
        confidence = str(item.get("confidence_score", "")).strip()

        score_tag = f" {c.GRAY}[Confidence: {c.BRIGHT_GREEN}{confidence}/10{c.GRAY}]{c.RESET}" if confidence else ""
        print(f"  {c.BOLD}{c.BRIGHT_YELLOW}Idea #{idx:<2}{c.RESET}  {c.BOLD}{c.BRIGHT_CYAN}{idea}{c.RESET}{score_tag}")
        if source:
            print(f"  {c.GRAY}Source Element:{c.RESET}")
            for line in source.split("\n"):
                print(f"    {c.DIM}{c.ITALIC}\"{line.strip()}\"{c.RESET}")
        print(f"  {c.GRAY}{'┄' * 74}{c.RESET}\n")

    print(f"  {c.BOLD}{c.BRIGHT_GREEN}✔ Extracted {len(ideas)} high-value content idea(s).{c.RESET}")
    if exported:
        print(f"  {c.BOLD}{c.BRIGHT_CYAN}✔ Exported {len(ideas)} idea(s) to Google Sheets ('Ideas' tab).{c.RESET}")
    print()


# ── Main Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate content ideas from an outlier YouTube video using BrowserLLM, Claude, and PostgreSQL."
    )
    parser.add_argument(
        "video",
        help="YouTube video URL or 11-character video ID.",
    )
    parser.add_argument(
        "--no-export", "--no-sheet",
        action="store_true",
        dest="no_export",
        help="Skip saving generated ideas to Google Sheets.",
    )
    parser.add_argument(
        "--fresh-summary", "--resummarize",
        action="store_true",
        dest="fresh_summary",
        help="Force a fresh video summary from OpenRouter and Groq Whisper even if already stored in database.",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="OpenRouter model for summarization if summarization is performed.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Browser profile name for browserllm (e.g. cdp, 0012, default).",
    )
    parser.add_argument(
        "--profile-dir",
        type=str,
        default=None,
        help="Custom browser profile directory for browserllm.",
    )
    parser.add_argument(
        "--no-chrome",
        action="store_true",
        help="Use bundled Chromium instead of system Google Chrome for browserllm.",
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="Google Sheets ID (defaults to CONTENT_CALENDAR_SHEET_ID in .env).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted terminal output.",
    )
    parser.add_argument(
        "--output", "-O",
        type=str,
        default=None,
        help="Path to file where results should be saved (JSON format).",
    )

    args = parser.parse_args()

    # 1. Extract Video ID
    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # 2. Database Lookup
    if not args.json:
        print(f"{Colors.CYAN}Looking up video {Colors.BOLD}{video_id}{Colors.RESET}{Colors.CYAN} in outliers database...{Colors.RESET}")

    db_record = find_video_in_db(video_id)

    title = ""
    channel_name = ""
    description = ""
    view_count = None
    outlier_score = None
    is_outlier = False

    if db_record:
        title = db_record.get("title") or ""
        channel_name = db_record.get("channel_title") or ""
        description = db_record.get("description") or ""
        view_count = db_record.get("view_count")
        outlier_score = db_record.get("score")
        is_outlier = bool(db_record.get("is_outlier"))
        if not args.json:
            print(f"  {Colors.GREEN}✓ Found video record in database!{Colors.RESET}")
    else:
        if not args.json:
            print(f"  {Colors.YELLOW}Video not currently in DB. Will summarize and register it.{Colors.RESET}")

    # Fallback metadata fetching if title or channel_name are missing
    if not title or not channel_name:
        meta = fetch_video_metadata(video_id)
        title = title or meta.get("title", "")
        channel_name = channel_name or meta.get("channel_title", "")
        description = description or meta.get("description", "")
        if view_count is None:
            view_count = meta.get("view_count")

    # 3. Summarize the Video (Step 1)
    if not args.json:
        if db_record and db_record.get("summary") and not args.fresh_summary:
            print(f"{Colors.CYAN}Using existing video summary and takeaways from database.{Colors.RESET}")
        else:
            print(f"{Colors.CYAN}Summarizing video audio and generating takeaways via OpenRouter...{Colors.RESET}")

    try:
        summary, takeaways = get_or_create_summary(
            video_id=video_id,
            db_record=db_record,
            force_fresh=args.fresh_summary,
            model=args.model,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Summarization failed: {e}"}, indent=2))
        else:
            print(f"{Colors.RED}Error summarizing video:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Fetch Recent 20 Channel Videos (Step 3)
    if not args.json:
        print(f"{Colors.CYAN}Fetching your channel's last 20 videos for personalization...{Colors.RESET}")
    recent_videos = fetch_recent_channel_titles(limit=20)
    if not args.json and recent_videos:
        print(f"  {Colors.GREEN}✓ Loaded {len(recent_videos)} recent channel video titles.{Colors.RESET}")

    # 5. Build the Prompt (Step 2 & 3)
    prompt_text = build_outlier_prompt(
        title=title,
        channel_name=channel_name,
        description=description,
        summary=summary,
        takeaways=takeaways,
        recent_videos=recent_videos,
    )

    # 6. Query Claude via BrowserLLM (Headful Mode) (Step 4)
    if not args.json:
        print(f"{Colors.CYAN}Querying Claude via BrowserLLM (headful mode)...{Colors.RESET}")

    try:
        claude_raw_response = query_browserllm_claude(
            prompt_text=prompt_text,
            profile=args.profile,
            profile_dir=args.profile_dir,
            chrome=not args.no_chrome,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"BrowserLLM query failed: {e}"}, indent=2))
        else:
            print(f"{Colors.RED}Error querying Claude via BrowserLLM:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    # 7. Parse the Generated Ideas (Step 5)
    ideas = parse_claude_response(claude_raw_response)

    # 8. Save Ideas into Google Sheet (Step 5)
    exported_count = 0
    should_export = not args.no_export

    if should_export and ideas:
        if not args.json:
            print(f"{Colors.CYAN}Exporting {len(ideas)} ideas to Google Sheets...{Colors.RESET}")
        try:
            from ideas.export_ideas_to_sheet import export_ideas_to_sheet
            export_payload = []
            for item in ideas:
                source_elem = item.get("source", "").strip()
                source_formatted = f"{source_elem} - {video_url}" if source_elem else video_url
                export_payload.append({
                    "idea": item.get("idea", "").strip(),
                    "source": source_formatted,
                    "source_type": "YT Outliers",
                    "confidence_score": item.get("confidence_score", ""),
                })
            res = export_ideas_to_sheet(export_payload, spreadsheet_id=args.sheet_id)
            exported_count = res.get("appended_count", len(export_payload))
        except Exception as e:
            logger.error("Failed to export ideas to Google Sheet: %s", e)
            if not args.json:
                print(f"{Colors.RED}Warning: Failed to export ideas to Google Sheet:{Colors.RESET} {e}", file=sys.stderr)

    # 9. Output Results
    payload = {
        "video_id": video_id,
        "video_url": video_url,
        "title": title,
        "channel_name": channel_name,
        "is_outlier": is_outlier,
        "outlier_score": outlier_score,
        "summary": summary,
        "takeaways": takeaways,
        "ideas": ideas,
        "exported_to_sheet": bool(exported_count > 0),
        "exported_count": exported_count,
    }

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            if not args.json:
                print(f"  {Colors.GREEN}Saved results to:{Colors.RESET} {args.output}")
        except Exception as e:
            logger.error("Failed to save output to %s: %s", args.output, e)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_terminal_results(
            video_id=video_id,
            title=title,
            channel_title=channel_name,
            outlier_score=outlier_score,
            view_count=view_count,
            is_outlier=is_outlier,
            ideas=ideas,
            exported=bool(exported_count > 0),
            summary_preview=summary,
        )


if __name__ == "__main__":
    main()
