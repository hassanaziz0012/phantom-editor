#!/usr/bin/env python3
"""
YouTube Comment Video Idea Generator
====================================
Scrapes top comments for a YouTube video using `ideas/comments/scrape_comments.py`,
filters out short comments (< 5 characters), formats them as a numbered list,
and queries Claude via BrowserLLM to extract actionable content ideas.
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

# Add repository root to sys.path
repo_root = Path(__file__).resolve().parent.parent.parent
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
logger = logging.getLogger("phantom.ideas.generate")

SCRAPE_COMMENTS_SCRIPT = repo_root / "ideas" / "comments" / "scrape_comments.py"
PROMPT_TEMPLATE_PATH = repo_root / "agentic" / "prompts" / "generate_ideas_from_comments.md"


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


# ── Fetch Video Details (Title & Description) ─────────────────────────────────

def fetch_video_metadata(
    video_id: str,
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Fetches the video title and description using the YouTube API, falling back to yt-dlp.
    """
    # 1. Try YouTube Data API via scrape_comments helper
    try:
        from ideas.comments.scrape_comments import get_youtube_service
        youtube = get_youtube_service(api_key=api_key)
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items", [])
        if items:
            snippet = items[0].get("snippet", {})
            title = snippet.get("title", "").strip()
            description = snippet.get("description", "").strip()
            if title:
                return title, description
    except Exception as e:
        logger.debug("YouTube API metadata fetch failed: %s", e)

    # 2. Fallback to yt-dlp
    try:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--dump-json",
            "--skip-download",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        if title:
            return title, description
    except Exception as e:
        logger.debug("yt-dlp metadata fetch failed: %s", e)

    return f"YouTube Video ({video_id})", "No description available."


# ── Scrape Comments Calling scrape_comments.py ─────────────────────────────────

def scrape_comments_json(
    video: str,
    limit: int = 100,
    order: str = "relevance",
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Calls scrape_comments.py as a subprocess with --json to retrieve comments.
    """
    cmd = [
        sys.executable,
        str(SCRAPE_COMMENTS_SCRIPT),
        video,
        "--limit",
        str(limit),
        "--order",
        order,
        "--json",
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])

    logger.info("Executing comment scraper: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)

    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip() or f"Process exited with code {res.returncode}"
        raise RuntimeError(f"scrape_comments.py failed: {err_msg}")

    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse scrape_comments.py JSON output: {e}\nRaw output: {res.stdout[:500]}")

    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"scrape_comments.py reported an error: {data['error']}")

    comments = data.get("comments", []) if isinstance(data, dict) else data
    return comments


# ── Filter Comments ───────────────────────────────────────────────────────────

def filter_comments(comments: List[Dict[str, Any]], min_length: int = 5) -> List[str]:
    """
    Filters out comments with text length less than `min_length` characters.
    Returns a list of cleaned comment text strings.
    """
    valid_comments: List[str] = []
    seen = set()

    for c in comments:
        text = c.get("text", "") if isinstance(c, dict) else str(c)
        cleaned = " ".join(text.strip().split())
        if len(cleaned) >= min_length and cleaned not in seen:
            seen.add(cleaned)
            valid_comments.append(cleaned)

    return valid_comments


# ── Fetch Recent Channel Video Titles ──────────────────────────────────────────

def fetch_recent_channel_titles(
    channel: Optional[str] = None,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> List[str]:
    """
    Fetches the latest `limit` video titles for the channel using YouTube Data API.
    Falls back gracefully if API key or channel is not configured.
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
        logger.warning("Failed to fetch channel video titles: %s", e)
        return []


# ── Build Prompt ──────────────────────────────────────────────────────────────

def build_prompt(
    title: str,
    description: str,
    comments: List[str],
    recent_videos: Optional[List[str]] = None,
) -> str:
    """
    Loads prompt template from agentic/prompts/generate_ideas_from_comments.md
    and fills in the placeholders: {title}, {description}, {recent_videos}, and {comments}.
    Comments and recent videos are formatted as numbered lists.
    """
    if not PROMPT_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Prompt template file not found at: {PROMPT_TEMPLATE_PATH}")

    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    # Format comments as numbered list
    numbered_comments = "\n".join(f"{i}. {c}" for i, c in enumerate(comments, 1))

    # Format recent videos as numbered list
    if recent_videos:
        formatted_recent = "\n".join(f"{i}. {t}" for i, t in enumerate(recent_videos, 1))
    else:
        formatted_recent = "No recent video titles available."

    # Safely replace placeholders (avoids issues with curly braces in descriptions)
    prompt = template.replace("{title}", title)
    prompt = prompt.replace("{description}", description or "No description provided.")
    prompt = prompt.replace("{recent_videos}", formatted_recent)
    prompt = prompt.replace("{comments}", numbered_comments)

    return prompt


# ── Query BrowserLLM (Claude) ─────────────────────────────────────────────────

def query_browserllm_claude(
    prompt_text: str,
    headless: bool = False,
    profile: Optional[str] = None,
    profile_dir: Optional[str] = None,
    chrome: bool = True,
) -> str:
    """
    Calls browserllm with provider 'claude' passing the prompt via a temporary file.
    """
    browserllm_bin = shutil.which("browserllm")
    if not browserllm_bin:
        # Fallback check for ~/.local/bin/browserllm
        local_bin = Path.home() / ".local" / "bin" / "browserllm"
        if local_bin.exists():
            browserllm_bin = str(local_bin)
        else:
            raise FileNotFoundError("browserllm executable not found in PATH or ~/.local/bin/browserllm.")

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as prompt_file, \
         tempfile.NamedTemporaryFile(mode="r+", suffix=".json", delete=False, encoding="utf-8") as output_file:
        prompt_path = prompt_file.name
        output_path = output_file.name
        prompt_file.write(prompt_text)
        prompt_file.flush()

    try:
        cmd = [
            browserllm_bin,
            "-p", prompt_path,
            "-P", "claude",
            "-o", output_path,
        ]

        if headless:
            cmd.append("--headless")
        if profile:
            cmd.extend(["--profile", profile])
        if profile_dir:
            cmd.extend(["--profile-dir", profile_dir])
        if not chrome:
            cmd.append("--no-chrome")

        logger.info("Executing BrowserLLM query with Claude...")
        res = subprocess.run(cmd, capture_output=True, text=True)

        # First check if output file has content
        output_content = ""
        if Path(output_path).exists():
            output_content = Path(output_path).read_text(encoding="utf-8").strip()

        if not output_content:
            # Fallback to stdout
            output_content = res.stdout.strip()

        if res.returncode != 0 and not output_content:
            err_msg = res.stderr.strip() or res.stdout.strip() or f"BrowserLLM failed with exit code {res.returncode}"
            raise RuntimeError(f"BrowserLLM Claude query failed: {err_msg}")

        return output_content
    finally:
        # Clean up temp files
        for p in (prompt_path, output_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# ── Parse Claude Response ─────────────────────────────────────────────────────

def parse_claude_response(response_text: str) -> List[Dict[str, str]]:
    """
    Parses Claude's response into a list of {source, idea} dicts.
    Handles raw JSON, markdown code fences, or embedded JSON blocks.
    """
    text = response_text.strip()
    if not text:
        return []

    # 1. Try parsing full text as JSON directly
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _validate_ideas_list(data)
    except Exception:
        pass

    # 2. Try extracting JSON from markdown code fences ```json ... ``` or ``` ... ```
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(fence_pattern, text)
    for match in matches:
        try:
            data = json.loads(match.strip())
            if isinstance(data, list):
                return _validate_ideas_list(data)
        except Exception:
            continue

    # 3. Try finding the outermost JSON array `[...]`
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

    logger.warning("Could not parse JSON list from response. Returning raw text as single entry.")
    return [{"source": "BrowserLLM Raw Response", "idea": text}]


def _validate_ideas_list(data: List[Any]) -> List[Dict[str, Any]]:
    """Validates and cleans the list of idea objects."""
    results = []
    for item in data:
        if isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            idea = str(item.get("idea", "")).strip()
            confidence_score = str(item.get("confidence_score", item.get("confidenceScore", ""))).strip()
            if source or idea:
                entry = {"source": source, "idea": idea}
                if confidence_score:
                    entry["confidence_score"] = confidence_score
                results.append(entry)
        elif isinstance(item, str):
            results.append({"source": "", "idea": item.strip()})
    return results


# ── Terminal Display ──────────────────────────────────────────────────────────

def print_terminal_results(
    video_id: str,
    title: str,
    total_scraped: int,
    filtered_count: int,
    ideas: List[Dict[str, Any]],
    exported: bool = False,
):
    """
    Renders generated video ideas in a styled, readable terminal format.
    """
    c = Colors
    print()
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}╔══════════════════════════════════════════════════════════════════════════════╗{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}║ YouTube Comment Idea Generator (Powered by Claude via BrowserLLM)             ║{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_MAGENTA}╚══════════════════════════════════════════════════════════════════════════════╝{c.RESET}")
    print(f"  {c.BOLD}Video:{c.RESET}      {c.WHITE}{title}{c.RESET}")
    print(f"  {c.BOLD}Video ID:{c.RESET}   {c.BRIGHT_YELLOW}{video_id}{c.RESET} (https://youtu.be/{video_id})")
    print(f"  {c.BOLD}Comments:{c.RESET}   {c.BRIGHT_GREEN}{total_scraped}{c.RESET} scraped {c.GRAY}→{c.RESET} {c.BRIGHT_CYAN}{filtered_count}{c.RESET} analyzed (≥ 5 chars) {c.GRAY}→{c.RESET} {c.BRIGHT_YELLOW}{len(ideas)}{c.RESET} ideas generated")
    print(f"{c.GRAY}{'─' * 78}{c.RESET}\n")

    if not ideas:
        print(f"  {c.YELLOW}No video ideas could be extracted from the comments.{c.RESET}\n")
        return

    for idx, item in enumerate(ideas, 1):
        idea = item.get("idea", "").strip()
        source = item.get("source", "").strip()
        confidence = str(item.get("confidence_score", "")).strip()

        score_tag = f" {c.GRAY}[Confidence: {c.BRIGHT_GREEN}{confidence}/10{c.GRAY}]{c.RESET}" if confidence else ""
        print(f"  {c.BOLD}{c.BRIGHT_YELLOW}Idea #{idx:<2}{c.RESET}  {c.BOLD}{c.BRIGHT_CYAN}{idea}{c.RESET}{score_tag}")
        if source:
            # Wrap source in quotes with subtle styling
            print(f"  {c.GRAY}Source Comment:{c.RESET}")
            for line in source.split("\n"):
                print(f"    {c.DIM}{c.ITALIC}\"{line.strip()}\"{c.RESET}")
        print(f"  {c.GRAY}{'┄' * 74}{c.RESET}\n")

    print(f"  {c.BOLD}{c.BRIGHT_GREEN}✔ Extracted {len(ideas)} high-value content ideas.{c.RESET}")
    if exported:
        print(f"  {c.BOLD}{c.BRIGHT_CYAN}✔ Exported {len(ideas)} idea(s) to Google Sheets ('Ideas' tab).{c.RESET}")
    print()


# ── Main Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate content ideas from YouTube comments using BrowserLLM and Claude."
    )
    parser.add_argument(
        "video",
        help="YouTube video URL or 11-character video ID.",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=100,
        help="Number of comments to scrape (default: 100).",
    )
    parser.add_argument(
        "--order",
        "-o",
        choices=["relevance", "time"],
        default="relevance",
        help="Comment sort order: 'relevance' (top comments) or 'time' (newest). Default: relevance.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=5,
        help="Minimum character length for comments to be sent to the LLM (default: 5).",
    )
    parser.add_argument(
        "--no-export", "--no-sheet",
        action="store_true",
        dest="no_export",
        help="Skip saving generated ideas to Google Sheets (exported by default).",
    )
    parser.add_argument(
        "--export",
        action="store_false",
        dest="no_export",
        help="Export generated ideas to Google Sheets (default: enabled).",
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="Google Sheets spreadsheet ID (defaults to GOOGLE_SHEET_ID from .env).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browserllm browser in headless mode.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Browser profile name for browserllm (e.g., cdp, 0012, default).",
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
        "--api-key",
        type=str,
        default=None,
        help="YouTube Data API key (defaults to YOUTUBE_API_KEY from .env).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted terminal output.",
    )
    parser.add_argument(
        "--output",
        "-O",
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

    # 2. Fetch Video Metadata (Title & Description)
    if not args.json:
        print(f"{Colors.CYAN}Fetching video details for {Colors.BOLD}{video_id}{Colors.RESET}...")
    title, description = fetch_video_metadata(video_id, api_key=args.api_key)

    # 3. Fetch Recent Channel Video Titles (Last 20)
    recent_video_titles = fetch_recent_channel_titles(limit=20, api_key=args.api_key)

    # 4. Scrape Comments via scrape_comments.py
    if not args.json:
        print(f"{Colors.CYAN}Scraping top {args.limit} comments via scrape_comments.py...{Colors.RESET}")
    try:
        raw_comments = scrape_comments_json(
            video=video_id,
            limit=args.limit,
            order=args.order,
            api_key=args.api_key,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Scraping failed: {e}"}, indent=2))
        else:
            print(f"{Colors.RED}Error scraping comments:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    total_scraped = len(raw_comments)
    if total_scraped == 0:
        if args.json:
            print(json.dumps({
                "video_id": video_id,
                "title": title,
                "total_scraped": 0,
                "filtered_count": 0,
                "ideas": [],
            }, indent=2))
        else:
            print(f"{Colors.YELLOW}No comments found for video {video_id}.{Colors.RESET}")
        return

    # 5. Filter comments (< 5 characters)
    filtered_comments = filter_comments(raw_comments, min_length=args.min_length)
    filtered_count = len(filtered_comments)

    if filtered_count == 0:
        if args.json:
            print(json.dumps({
                "video_id": video_id,
                "title": title,
                "total_scraped": total_scraped,
                "filtered_count": 0,
                "ideas": [],
            }, indent=2))
        else:
            print(f"{Colors.YELLOW}All scraped comments were under {args.min_length} characters.{Colors.RESET}")
        return

    # 6. Build prompt with recent video titles
    prompt_text = build_prompt(
        title=title,
        description=description,
        comments=filtered_comments,
        recent_videos=recent_video_titles,
    )

    # 7. Send to Claude via BrowserLLM
    if not args.json:
        print(f"{Colors.CYAN}Sending {filtered_count} comments to Claude via BrowserLLM...{Colors.RESET}")
    try:
        claude_raw_response = query_browserllm_claude(
            prompt_text=prompt_text,
            headless=args.headless,
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

    # 8. Parse Claude's response
    ideas = parse_claude_response(claude_raw_response)

    # 9. Handle export to Google Sheets (enabled by default unless --no-export is passed)
    exported_count = 0
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    should_export = not args.no_export
    if should_export and ideas:
        try:
            if not args.json:
                print(f"{Colors.CYAN}Exporting {len(ideas)} ideas to Google Sheets...{Colors.RESET}")
            from ideas.export_ideas_to_sheet import export_ideas_to_sheet
            export_payload = []
            for item in ideas:
                comment_text = item.get("source", "").strip()
                source_formatted = f"{comment_text} - {video_url}" if comment_text else video_url
                export_payload.append({
                    "idea": item.get("idea", "").strip(),
                    "source": source_formatted,
                    "source_type": "YT Comments",
                    "confidence_score": item.get("confidence_score", ""),
                })
            res = export_ideas_to_sheet(export_payload, spreadsheet_id=args.sheet_id)
            exported_count = res.get("appended_count", len(export_payload))
        except Exception as e:
            logger.error("Failed to export ideas to Google Sheet: %s", e)
            if not args.json:
                print(f"{Colors.RED}Warning: Failed to export ideas to Google Sheet:{Colors.RESET} {e}", file=sys.stderr)

    # 10. Output results
    payload = {
        "video_id": video_id,
        "video_url": video_url,
        "title": title,
        "total_scraped": total_scraped,
        "filtered_count": filtered_count,
        "ideas": ideas,
        "exported_to_sheet": bool(exported_count > 0),
        "exported_count": exported_count,
    }

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            if not args.json:
                print(f"  {Colors.GREEN}Saved ideas to:{Colors.RESET} {args.output}")
        except Exception as e:
            logger.error("Failed to save output to %s: %s", args.output, e)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_terminal_results(
            video_id=video_id,
            title=title,
            total_scraped=total_scraped,
            filtered_count=filtered_count,
            ideas=ideas,
            exported=bool(exported_count > 0),
        )


if __name__ == "__main__":
    main()
