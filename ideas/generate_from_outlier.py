#!/usr/bin/env python3
"""
YouTube Outlier Video Idea Generator
====================================
Accepts a YouTube video URL, fetches video metadata and transcript,
summarizes the video via OpenRouter and Groq Cloud,
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
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Add repository root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

from ideas.common import Colors, export_ideas, fetch_recent_channel_titles, parse_ideas_response
from youtube_api.utils import extract_video_id

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.outliers_generator")

PROMPT_TEMPLATE_PATH = repo_root / "agentic" / "prompts" / "generate_ideas_from_outliers.md"


# ── Video Metadata Fetching ───────────────────────────────────────────────────

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
    model: Optional[str] = None,
    title: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Executes ideas/summarize_video.py to download, transcribe, and summarize
    the video via OpenRouter and Groq Whisper.

    Returns:
        (summary_text, takeaways_list)
    """
    from ideas.summarize_video import DEFAULT_MODEL, summarize_video

    summary_model = model or DEFAULT_MODEL
    logger.info("Summarizing video %s via OpenRouter (%s)...", video_id, summary_model)
    summary_obj, _ = summarize_video(video_id, model=summary_model, title=title)

    return summary_obj.summary, summary_obj.takeaways


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


from agentic.ask_browserllm import query_claude


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
        print(f"  {c.BOLD}Metrics:{c.RESET}        {' | '.join(status_badges)}")

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
        raw_formats = item.get("best_formats", "")
        if isinstance(raw_formats, list):
            formats_str = ", ".join(raw_formats)
        else:
            formats_str = str(raw_formats).strip()

        score_tag = f" {c.GRAY}[Confidence: {c.BRIGHT_GREEN}{confidence}/10{c.GRAY}]{c.RESET}" if confidence else ""
        formats_tag = f" {c.GRAY}[Formats: {c.BRIGHT_BLUE}{formats_str}{c.GRAY}]{c.RESET}" if formats_str else ""
        print(f"  {c.BOLD}{c.BRIGHT_YELLOW}Idea #{idx:<2}{c.RESET}  {c.BOLD}{c.BRIGHT_CYAN}{idea}{c.RESET}{score_tag}{formats_tag}")
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
        description="Generate content ideas from an outlier YouTube video using BrowserLLM and Claude."
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
        help="Force a fresh video summary (retained for backward compatibility).",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="OpenRouter model for summarization if summarization is performed.",
    )
    parser.add_argument(
        "--browserllm-profile", "--profile",
        type=str,
        default=None,
        dest="profile",
        help="Browser profile name for browserllm (e.g. cdp, p9222, p9223).",
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

    # 2. Fetch Video Metadata
    if not args.json:
        print(f"{Colors.CYAN}Fetching metadata for video {Colors.BOLD}{video_id}{Colors.RESET}...{Colors.RESET}")

    meta = fetch_video_metadata(video_id)
    title = meta.get("title", "")
    channel_name = meta.get("channel_title", "")
    description = meta.get("description", "")
    view_count = meta.get("view_count")
    outlier_score = None
    is_outlier = False

    if not args.json:
        print(f"  {Colors.GREEN}✓ Title: {title}{Colors.RESET}")
        print(f"  {Colors.GREEN}✓ Channel: {channel_name}{Colors.RESET}")

    # 3. Summarize the Video
    if not args.json:
        print(f"{Colors.CYAN}Summarizing video audio and generating takeaways via OpenRouter...{Colors.RESET}")

    # 4. Fetch your channel's last 20 videos while the summary is generated
    with ThreadPoolExecutor(max_workers=1) as pool:
        titles_future = pool.submit(fetch_recent_channel_titles, limit=20)
        try:
            summary, takeaways = get_or_create_summary(
                video_id=video_id,
                model=args.model,
                title=title,
            )
        except Exception as e:
            if args.json:
                print(json.dumps({"error": f"Summarization failed: {e}"}, indent=2))
            else:
                print(f"{Colors.RED}Error summarizing video:{Colors.RESET} {e}", file=sys.stderr)
            sys.exit(1)
        recent_videos = titles_future.result()

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
        claude_raw_response = query_claude(
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
    ideas = parse_ideas_response(claude_raw_response)

    # 8. Save Ideas into Google Sheet (Step 5)
    exported_count = 0
    should_export = not args.no_export

    if should_export and ideas:
        if not args.json:
            print(f"{Colors.CYAN}Exporting {len(ideas)} ideas to Google Sheets...{Colors.RESET}")
        try:
            exported_count = export_ideas(ideas, video_url, "YT Outliers", spreadsheet_id=args.sheet_id)
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
