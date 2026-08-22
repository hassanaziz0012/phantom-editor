#!/usr/bin/env python3
"""
YouTube Comment Scraper
=======================
Scrapes top comments from a YouTube video URL or ID using the YouTube Data API v3.
Outputs comments in a clean, human-readable terminal format or structured JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.ideas.comments")

DEFAULT_TOKEN_FILE = repo_root / "youtube_api" / "tokens" / "token.json"


# ── Terminal Styling / Colors ──────────────────────────────────────────────────

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    BRIGHT_CYAN = "\033[96m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    YELLOW = "\033[33m"
    BRIGHT_YELLOW = "\033[93m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    GRAY = "\033[90m"


def format_relative_time(iso_str: str) -> str:
    """Converts ISO 8601 UTC timestamp to a human-friendly relative or date string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"
    except Exception:
        return iso_str


# ── Video ID Extraction ────────────────────────────────────────────────────────

def extract_video_id(input_str: str) -> str:
    """
    Extracts the 11-character YouTube video ID from various URL formats or raw ID.

    Supported formats:
      - https://www.youtube.com/watch?v=dQw4w9WgXcQ
      - https://youtu.be/dQw4w9WgXcQ
      - https://www.youtube.com/shorts/dQw4w9WgXcQ
      - https://www.youtube.com/embed/dQw4w9WgXcQ
      - https://www.youtube.com/v/dQw4w9WgXcQ
      - dQw4w9WgXcQ
    """
    input_str = input_str.strip()

    # Raw 11-char ID
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


# ── YouTube Client Initialization ──────────────────────────────────────────────

def get_youtube_service(
    token_path: Optional[Path | str] = None,
    api_key: Optional[str] = None,
):
    """
    Builds and returns an authorized YouTube Data API v3 client.
    Checks OAuth tokens first, then falls back to YOUTUBE_API_KEY from .env or CLI.
    """
    token_file = Path(token_path) if token_path else DEFAULT_TOKEN_FILE
    env_api_key = api_key or os.getenv("YOUTUBE_API_KEY")

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds:
                has_read_scope = any(
                    s in (creds.scopes or [])
                    for s in (
                        "https://www.googleapis.com/auth/youtube.readonly",
                        "https://www.googleapis.com/auth/youtube",
                        "https://www.googleapis.com/auth/youtube.force-ssl",
                    )
                )
                if has_read_scope:
                    if creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                        try:
                            with open(token_file, "w", encoding="utf-8") as f:
                                f.write(creds.to_json())
                        except Exception:
                            pass
                    if creds.valid:
                        return build("youtube", "v3", credentials=creds)
        except Exception as e:
            logger.debug("OAuth load failed: %s", e)

    if env_api_key:
        return build("youtube", "v3", developerKey=env_api_key)

    raise EnvironmentError(
        "No valid authentication found. Please set YOUTUBE_API_KEY in .env or pass --api-key."
    )


# ── Comment Scraping ───────────────────────────────────────────────────────────

def scrape_comments(
    youtube,
    video_id: str,
    limit: int = 100,
    order: str = "relevance",
    include_replies: bool = False,
) -> List[Dict[str, Any]]:
    """
    Scrapes up to `limit` top-level comments for a given YouTube video.

    :param youtube: YouTube Data API v3 client resource.
    :param video_id: 11-character YouTube video ID.
    :param limit: Maximum number of comments to fetch (default: 100).
    :param order: Sorting order: 'relevance' (top comments) or 'time' (newest first).
    :param include_replies: Whether to extract inline top replies if present.
    :return: List of structured comment dictionaries.
    """
    comments: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    part = "snippet,replies" if include_replies else "snippet"

    while len(comments) < limit:
        max_batch = min(100, limit - len(comments))
        try:
            req = youtube.commentThreads().list(
                part=part,
                videoId=video_id,
                maxResults=max_batch,
                order=order,
                textFormat="plainText",
                pageToken=page_token,
            )
            response = req.execute()
        except HttpError as err:
            if err.resp.status == 403 and "commentsDisabled" in str(err):
                logger.warning("Comments are disabled for video %s.", video_id)
                break
            elif err.resp.status == 404:
                raise ValueError(f"Video '{video_id}' not found on YouTube.")
            raise err

        items = response.get("items", [])
        if not items:
            break

        for item in items:
            thread_id = item.get("id")
            thread_snippet = item.get("snippet", {})
            top_level = thread_snippet.get("topLevelComment", {})
            comment_snippet = top_level.get("snippet", {})

            comment_id = top_level.get("id", thread_id)
            author_name = comment_snippet.get("authorDisplayName", "Unknown")
            author_channel_url = comment_snippet.get("authorChannelUrl", "")
            author_channel_id = (
                comment_snippet.get("authorChannelId", {}).get("value", "")
            )
            author_profile_image = comment_snippet.get("authorProfileImageUrl", "")
            text = comment_snippet.get("textDisplay", "")
            like_count = int(comment_snippet.get("likeCount", 0))
            reply_count = int(thread_snippet.get("totalReplyCount", 0))
            published_at = comment_snippet.get("publishedAt", "")
            updated_at = comment_snippet.get("updatedAt", "")

            comment_data: Dict[str, Any] = {
                "id": comment_id,
                "author": author_name,
                "author_channel_id": author_channel_id,
                "author_channel_url": author_channel_url,
                "author_profile_image": author_profile_image,
                "text": text,
                "like_count": like_count,
                "reply_count": reply_count,
                "published_at": published_at,
                "updated_at": updated_at,
            }

            if include_replies:
                replies_data = []
                replies_snippet = item.get("replies", {}).get("comments", [])
                for reply in replies_snippet:
                    r_snip = reply.get("snippet", {})
                    replies_data.append({
                        "id": reply.get("id"),
                        "author": r_snip.get("authorDisplayName", "Unknown"),
                        "text": r_snip.get("textDisplay", ""),
                        "like_count": int(r_snip.get("likeCount", 0)),
                        "published_at": r_snip.get("publishedAt", ""),
                    })
                comment_data["replies"] = replies_data

            comments.append(comment_data)
            if len(comments) >= limit:
                break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return comments


# ── Terminal Display Formatting ────────────────────────────────────────────────

def print_clean_terminal(
    video_id: str,
    comments: List[Dict[str, Any]],
    order: str = "relevance",
):
    """Prints scraped comments in a clean, human-readable terminal layout."""
    c = Colors

    print()
    print(f"{c.BOLD}{c.BRIGHT_CYAN}╔══════════════════════════════════════════════════════════════════════════════╗{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_CYAN}║ YouTube Comments Scraper                                                     ║{c.RESET}")
    print(f"{c.BOLD}{c.BRIGHT_CYAN}╚══════════════════════════════════════════════════════════════════════════════╝{c.RESET}")
    print(f"  {c.BOLD}Video ID:{c.RESET}  {c.BRIGHT_YELLOW}{video_id}{c.RESET} (https://youtu.be/{video_id})")
    print(f"  {c.BOLD}Sorting:{c.RESET}   {c.GREEN}{order.title()}{c.RESET} ({'YouTube Top Comments' if order == 'relevance' else 'Newest First'})")
    print(f"  {c.BOLD}Count:{c.RESET}     {c.BRIGHT_GREEN}{len(comments)}{c.RESET} comments retrieved")
    print(f"{c.GRAY}{'─' * 78}{c.RESET}\n")

    if not comments:
        print(f"  {c.YELLOW}No comments found for this video or comments are disabled.{c.RESET}\n")
        return

    for idx, comment in enumerate(comments, 1):
        author = comment["author"]
        likes = comment["like_count"]
        replies = comment["reply_count"]
        time_rel = format_relative_time(comment["published_at"])
        text = comment["text"].strip()

        # Formatting like/reply badges
        like_badge = f"{c.BRIGHT_GREEN}👍 {likes:,}{c.RESET}" if likes > 0 else f"{c.GRAY}👍 0{c.RESET}"
        reply_badge = f"{c.CYAN}💬 {replies:,} {'reply' if replies == 1 else 'replies'}{c.RESET}" if replies > 0 else ""

        badges = f"{like_badge}  {reply_badge}".strip()

        print(f"{c.BOLD}{c.BRIGHT_YELLOW}#{idx:<3}{c.RESET} {c.BOLD}{c.BRIGHT_CYAN}{author}{c.RESET} {c.GRAY}• {time_rel}{c.RESET}   {badges}")

        # Indent comment text cleanly
        for line in text.split("\n"):
            print(f"     {line}")

        # Print inline replies if present
        if "replies" in comment and comment["replies"]:
            print(f"     {c.GRAY}── Replies ({len(comment['replies'])}) ──{c.RESET}")
            for reply in comment["replies"]:
                r_author = reply["author"]
                r_likes = f"👍 {reply['like_count']}" if reply["like_count"] > 0 else ""
                r_time = format_relative_time(reply["published_at"])
                print(f"       {c.MAGENTA}↳ {r_author}{c.RESET} {c.GRAY}({r_time}){c.RESET} {c.DIM}{r_likes}{c.RESET}")
                for r_line in reply["text"].strip().split("\n"):
                    print(f"         {r_line}")

        print(f"{c.GRAY}{'┄' * 78}{c.RESET}")

    print()


# ── Main Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape top comments from a YouTube video URL or ID."
    )
    parser.add_argument(
        "video",
        help="YouTube video URL (watch, youtu.be, shorts) or 11-character video ID.",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=100,
        help="Number of comments to fetch (default: 100).",
    )
    parser.add_argument(
        "--order",
        "-o",
        choices=["relevance", "time"],
        default="relevance",
        help="Comment order in YouTube API: 'relevance' (top comments) or 'time' (newest first). Default: relevance.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output comments as JSON string instead of human-readable text.",
    )
    parser.add_argument(
        "--include-replies",
        action="store_true",
        help="Include inline top replies in comment output.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="YouTube Data API key (defaults to YOUTUBE_API_KEY from .env).",
    )

    args = parser.parse_args()

    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    try:
        youtube = get_youtube_service(api_key=args.api_key)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"{Colors.RED}Authentication Error:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    try:
        comments = scrape_comments(
            youtube=youtube,
            video_id=video_id,
            limit=args.limit,
            order=args.order,
            include_replies=args.include_replies,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"{Colors.RED}API Error:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        payload = {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}",
            "order": args.order,
            "count": len(comments),
            "comments": comments,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_clean_terminal(video_id=video_id, comments=comments, order=args.order)


if __name__ == "__main__":
    main()
